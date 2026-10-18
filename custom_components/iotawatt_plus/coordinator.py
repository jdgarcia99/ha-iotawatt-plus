"""Coordinators: fast power, accumulated energy and device diagnostics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IoTaWattClient, IoTaWattError, LiveData, Series
from .const import (
    DATALOG_STEP,
    DOMAIN,
    ENERGY_END_MARGIN,
    FAILURES_BEFORE_WARNING,
    MAX_ENERGY_GAP,
)

_LOGGER = logging.getLogger(__name__)


class FailureTracker:
    """Tolerate isolated failures: debug first, a warning after a streak, info on recovery."""

    def __init__(self, name: str, grace: float) -> None:
        self.name = name
        self.grace = grace  # s since the last success during which old data is kept
        self.failures = 0
        self.last_success: float | None = None

    def success(self) -> None:
        if self.failures >= FAILURES_BEFORE_WARNING:
            _LOGGER.info("IoTaWatt %s answering again after %d failures", self.name, self.failures)
        self.failures = 0
        self.last_success = time.monotonic()

    def failure(self, err: IoTaWattError) -> bool:
        """Log the failure; True while the last good data may still be used."""
        self.failures += 1
        if self.failures == FAILURES_BEFORE_WARNING:
            _LOGGER.warning(
                "IoTaWatt %s failed %d times in a row: %s", self.name, self.failures, err
            )
        else:
            _LOGGER.debug("IoTaWatt %s failure %d: %s", self.name, self.failures, err)
        return (
            self.last_success is not None
            and time.monotonic() - self.last_success < self.grace
        )


@dataclass
class PowerData:
    """Data published by the power coordinator."""

    live: LiveData
    grid: float | None = None
    grid_import: float | None = None
    grid_export: float | None = None
    surplus_avg: dict[int, float | None] = field(default_factory=dict)
    latency_ms: float | None = None


class PowerCoordinator(DataUpdateCoordinator[PowerData]):
    """Poll /status every few seconds and derive grid import/export and surplus."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IoTaWattClient,
        series: list[Series],
        interval: int,
        grid_series: str,
        windows: list[int],
        max_age: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} power",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )
        self.client = client
        self.series = series
        self.grid_series = grid_series
        self.windows = windows
        self.tracker = FailureTracker("power", max_age)
        self._samples: deque[tuple[float, float]] = deque()

    async def _async_update_data(self) -> PowerData:
        start = time.monotonic()
        try:
            live = await self.client.get_live(self.series)
        except IoTaWattError as err:
            # Keep the last reading (no new state is written) until it is max_age old
            if self.tracker.failure(err) and self.data is not None:
                return self.data
            raise UpdateFailed(str(err)) from err
        self.tracker.success()
        now = time.monotonic()
        data = PowerData(live=live, latency_ms=round((now - start) * 1000, 1))

        grid = live.watts.get(self.grid_series)
        if grid is not None:
            data.grid = grid
            data.grid_import = max(grid, 0.0)
            data.grid_export = max(-grid, 0.0)
            self._samples.append((now, data.grid_export))
        horizon = now - max(self.windows, default=0)
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()
        for window in self.windows:
            values = [v for t, v in self._samples if t >= now - window]
            data.surplus_avg[window] = round(sum(values) / len(values), 1) if values else None
        return data


@dataclass
class EnergyDelta:
    """Wh of one series between two times of the IoTaWatt clock."""

    wh: float
    begin: int
    end: int


class EnergyCoordinator(DataUpdateCoordinator[dict[str, EnergyDelta]]):
    """Read the Wh logged by the IoTaWatt in consecutive windows.

    Windows are chained with times of the IoTaWatt's own clock (read from
    /status?stats), so no energy is lost or counted twice and it does not
    matter if that clock differs from Home Assistant's. `anchors` holds, per
    series, the time up to which its energy has been handed to the sensors.
    Each update publishes only the new deltas, which the sensors add up.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IoTaWattClient,
        names: list[str],
        interval: int,
        max_age: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} energy",
            update_interval=timedelta(seconds=interval),
            always_update=True,  # every update carries new deltas
        )
        self.client = client
        self.names = names
        self.anchors: dict[str, int] = {}
        self.tracker = FailureTracker("energy", max(max_age, 3 * interval))

    def set_anchor(self, name: str, device_time: int) -> None:
        """Continue a series from a restored time, backfilling the energy since then."""
        self.anchors[name] = device_time

    async def _async_update_data(self) -> dict[str, EnergyDelta]:
        try:
            deltas, anchors = await self._fetch()
        except IoTaWattError as err:
            # Anchors are unchanged, so the next successful window covers the gap
            if self.tracker.failure(err):
                return {}
            raise UpdateFailed(str(err)) from err
        self.tracker.success()
        self.anchors.update(anchors)
        return deltas

    async def _fetch(self) -> tuple[dict[str, EnergyDelta], dict[str, int]]:
        now = await self.client.get_time()
        end = (now - ENERGY_END_MARGIN) // DATALOG_STEP * DATALOG_STEP
        anchors: dict[str, int] = {}
        by_begin: dict[int, list[str]] = {}
        for name in self.names:
            begin = self.anchors.get(name)
            if begin is None:
                anchors[name] = end
            elif abs(end - begin) > MAX_ENERGY_GAP:
                _LOGGER.info(
                    "Energy of %s restarts at %d: the gap since %d is too long to backfill",
                    name, end, begin,
                )
                anchors[name] = end
            elif end > begin:
                by_begin.setdefault(begin, []).append(name)
            # else nothing new yet, or the IoTaWatt clock went back (for example
            # when it starts using NTP): wait until it passes the anchor again,
            # so no logged energy is counted twice
        deltas: dict[str, EnergyDelta] = {}
        for begin, names in by_begin.items():
            values = await self.client.get_energy(names, begin, end)
            for name in names:
                if name in values:
                    deltas[name] = EnergyDelta(values[name], begin, end)
                    anchors[name] = end
        return deltas, anchors


class DeviceCoordinator(DataUpdateCoordinator[dict]):
    """Device diagnostics (firmware, Wi-Fi, uptime)."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: IoTaWattClient, minutes: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} device",
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            return await self.client.get_device()
        except IoTaWattError as err:
            raise UpdateFailed(str(err)) from err
