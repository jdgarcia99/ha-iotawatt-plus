"""Sensors for IoTaWatt Plus."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
import logging
import time
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from . import IoTaWattPlusConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import EnergyCoordinator, PowerData

_LOGGER = logging.getLogger(__name__)

# Where a sensor of v0.1.1 or earlier looks for its last value
RECORDER_LOOKBACK = timedelta(days=30)
RECORDER_STATES = 50


def _device_info(entry: IoTaWattPlusConfigEntry) -> DeviceInfo:
    dev = entry.runtime_data.device.data or {}
    info = DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        manufacturer=MANUFACTURER,
        model="IoTaWatt",
        name=entry.title,
        sw_version=dev.get("version"),
        configuration_url=entry.runtime_data.client.host,
    )
    if dev.get("mac"):
        info["connections"] = {(CONNECTION_NETWORK_MAC, dev["mac"].lower())}
    return info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IoTaWattPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    dev = _device_info(entry)
    uid = entry.unique_id or entry.entry_id
    entities: list[SensorEntity] = []

    def power_desc(key: str, name: str | None, translation_key: str | None = None, enabled: bool = True):
        return SensorEntityDescription(
            key=key,
            name=name,
            translation_key=translation_key,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfPower.WATT,
            suggested_display_precision=0,
            entity_registry_enabled_default=enabled,
        )

    watts = [s for s in rt.series if s.name in rt.channels]
    for s in watts:
        entities.append(
            PlusPowerSensor(
                rt.power, dev, uid, power_desc(f"power_{s.name}", s.name),
                lambda d, n=s.name: d.live.watts.get(n), rt.deadband, rt.max_age,
            )
        )
        if s.name in rt.energy_channels:
            signed = s.name.casefold() in rt.bidirectional
            entities.append(PlusEnergySensor(rt.energy, dev, uid, s.name, signed))

    # Grid and surplus
    entities += [
        PlusPowerSensor(rt.power, dev, uid, power_desc("grid_import", None, "grid_import"),
                        lambda d: d.grid_import, rt.deadband, rt.max_age),
        PlusPowerSensor(rt.power, dev, uid, power_desc("grid_export", None, "grid_export"),
                        lambda d: d.grid_export, rt.deadband, rt.max_age),
    ]
    for w in rt.windows:
        label = f"{w // 60} min" if w % 60 == 0 else f"{w} s"
        desc = power_desc(f"surplus_avg_{w}", None, "surplus_avg")
        entities.append(
            PlusPowerSensor(rt.power, dev, uid, desc, lambda d, w=w: d.surplus_avg.get(w),
                            rt.deadband, rt.max_age, {"window": label})
        )

    # Voltage and frequency
    entities += [
        PlusPowerSensor(
            rt.power, dev, uid,
            SensorEntityDescription(
                key="voltage", translation_key="voltage",
                device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfElectricPotential.VOLT, suggested_display_precision=1,
            ),
            lambda d: d.live.voltage, 1.0, rt.max_age,
        ),
        PlusPowerSensor(
            rt.power, dev, uid,
            SensorEntityDescription(
                key="frequency", translation_key="frequency",
                device_class=SensorDeviceClass.FREQUENCY, state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfFrequency.HERTZ, suggested_display_precision=2,
                entity_registry_enabled_default=False,
            ),
            lambda d: d.live.frequency, 0.05, rt.max_age,
        ),
        PlusPowerSensor(
            rt.power, dev, uid,
            SensorEntityDescription(
                key="latency", translation_key="latency",
                native_unit_of_measurement=UnitOfTime.MILLISECONDS, state_class=SensorStateClass.MEASUREMENT,
                entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
            ),
            lambda d: d.latency_ms, 50.0, 300,
        ),
        PlusDeviceSensor(
            rt.device, dev, uid,
            SensorEntityDescription(
                key="rssi", translation_key="rssi",
                device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
            ),
            "rssi",
        ),
        PlusDeviceSensor(
            rt.device, dev, uid,
            SensorEntityDescription(
                key="uptime", translation_key="uptime",
                device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS,
                suggested_unit_of_measurement=UnitOfTime.DAYS,
                entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
            ),
            "uptime",
        ),
    ]
    # Remove entities of channels or windows that are no longer configured
    keep = {e.unique_id for e in entities}
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == "sensor" and reg.unique_id not in keep:
            registry.async_remove(reg.entity_id)

    async_add_entities(entities)


class PlusPowerSensor(CoordinatorEntity, SensorEntity):
    """Fast-coordinator sensor that only writes a new state when it changes enough."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        device: DeviceInfo,
        uid: str,
        description: SensorEntityDescription,
        getter: Callable[[PowerData], float | None],
        deadband: float,
        max_age: int,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        if placeholders:
            self._attr_translation_placeholders = placeholders
        self._attr_device_info = device
        self._attr_unique_id = f"{uid}_{description.key}"
        self._getter = getter
        self._deadband = deadband
        self._max_age = max_age
        self._written: float | None = None
        self._written_at = 0.0
        self._written_available: bool | None = None
        self._attr_native_value = self._read()

    def _read(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        value = self._getter(data)
        return round(value, 2) if value is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        value = self._read()
        self._attr_native_value = value
        available = self.available
        now = time.monotonic()
        changed = (
            value is None
            or self._written is None
            or available != self._written_available
            or abs(value - self._written) >= self._deadband
            or (value == 0) != (self._written == 0)
            or now - self._written_at >= self._max_age
        )
        if changed:
            self._written, self._written_at, self._written_available = value, now, available
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._attr_native_value is not None


@dataclass
class EnergyStoredData(SensorExtraStoredData):
    """Restored energy total and the IoTaWatt time it was counted up to."""

    device_time: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**super().as_dict(), "device_time": self.device_time}


def _to_wh(value: Any, unit: str | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if unit and unit != UnitOfEnergy.WATT_HOUR:
        try:
            return EnergyConverter.convert(number, unit, UnitOfEnergy.WATT_HOUR)
        except HomeAssistantError:  # unknown unit, nothing to restore
            return None
    return number


class PlusEnergySensor(CoordinatorEntity[EnergyCoordinator], RestoreSensor):
    """Energy total (Wh) built by adding the deltas logged by the IoTaWatt.

    The total survives restarts: it is restored with the IoTaWatt time it was
    counted up to, and the energy since then is backfilled. Consumption
    channels (total_increasing) ignore negative deltas; bidirectional ones
    (total) add them with their sign and are never reset.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: EnergyCoordinator, device: DeviceInfo, uid: str, name: str, signed: bool
    ) -> None:
        super().__init__(coordinator)
        self._series = name
        self._signed = signed
        self._attr_translation_key = "channel_energy"
        self._attr_translation_placeholders = {"channel": name}
        self._attr_device_info = device
        self._attr_unique_id = f"{uid}_energy_{name}"
        self._attr_state_class = SensorStateClass.TOTAL if signed else SensorStateClass.TOTAL_INCREASING
        self._attr_native_value: float | None = 0.0
        self._device_time: int | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value, device_time = await self._async_restore()
        if value is not None:
            self._attr_native_value = value
        if device_time is not None:
            self._device_time = device_time
            self.coordinator.set_anchor(self._series, device_time)
        else:
            # Count from the coordinator's first window
            self._device_time = self.coordinator.anchors.get(self._series)

    async def _async_restore(self) -> tuple[float | None, int | None]:
        if (extra := await self.async_get_last_extra_data()) is not None:
            data = extra.as_dict()
            value = _to_wh(data.get("native_value"), data.get("native_unit_of_measurement"))
            if value is not None:
                device_time = data.get("device_time")
                return value, int(device_time) if device_time is not None else None
        if (state := await self.async_get_last_state()) is not None:
            value = _to_wh(state.state, state.attributes.get("unit_of_measurement"))
            if value is not None:
                return value, None
        # Sensors of v0.1.1 and earlier were not RestoreEntities, so Home Assistant
        # kept no restore data at all: continue from the recorder's last state
        return await self._async_recorded_value(), None

    async def _async_recorded_value(self) -> float | None:
        if "recorder" not in self.hass.config.components:
            return None
        # Newest first: the last rows may be unavailable, unknown or empty
        states = await get_instance(self.hass).async_add_executor_job(
            partial(
                history.state_changes_during_period,
                self.hass,
                dt_util.utcnow() - RECORDER_LOOKBACK,
                entity_id=self.entity_id,
                descending=True,
                limit=RECORDER_STATES,
                include_start_time_state=False,
            )
        )
        for state in states.get(self.entity_id, []):
            value = _to_wh(state.state, state.attributes.get("unit_of_measurement"))
            if value is not None:
                _LOGGER.debug("%s continues from its recorded state %s", self.entity_id, state.state)
                return value
        return None

    @property
    def extra_restore_state_data(self) -> EnergyStoredData:
        return EnergyStoredData(
            self._attr_native_value, self._attr_native_unit_of_measurement, self._device_time
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        delta = (self.coordinator.data or {}).get(self._series)
        if delta is not None:
            if self._device_time not in (None, delta.begin):
                # Out of step: count again from our own time on the next update
                _LOGGER.debug(
                    "%s energy window %d-%d does not start at %d",
                    self._series, delta.begin, delta.end, self._device_time,
                )
                self.coordinator.set_anchor(self._series, self._device_time)
            else:
                wh = delta.wh
                if wh < 0 and not self._signed:
                    _LOGGER.debug(
                        "Ignoring negative energy delta of %s: %.3f Wh (%d-%d)",
                        self._series, wh, delta.begin, delta.end,
                    )
                    wh = 0.0
                self._attr_native_value = round((self._attr_native_value or 0.0) + wh, 3)
                self._device_time = delta.end
        super()._handle_coordinator_update()


class PlusDeviceSensor(CoordinatorEntity, SensorEntity):
    """Device diagnostic sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device: DeviceInfo, uid: str, description: SensorEntityDescription, key: str) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device
        self._attr_unique_id = f"{uid}_{description.key}"
        self._key = key

    @property
    def native_value(self) -> Any:
        return (self.coordinator.data or {}).get(self._key)
