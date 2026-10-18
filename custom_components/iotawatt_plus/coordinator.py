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
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
        self._samples: deque[tuple[float, float]] = deque()

    async def _async_update_data(self) -> PowerData:
        start = time.monotonic()
        try:
            live = await self.client.get_live(self.series)
        except IoTaWattError as err:
            raise UpdateFailed(str(err)) from err
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


class EnergyCoordinator(DataUpdateCoordinator[dict[str, float]]):
    """Read the accumulated Wh kept by the IoTaWatt itself."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IoTaWattClient,
        names: list[str],
        begin: int,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} energy",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )
        self.client = client
        self.names = names
        self.begin = begin

    async def _async_update_data(self) -> dict[str, float]:
        try:
            return await self.client.get_energy(self.names, self.begin)
        except IoTaWattError as err:
            raise UpdateFailed(str(err)) from err


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
