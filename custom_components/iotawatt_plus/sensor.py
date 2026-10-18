"""Sensors for IoTaWatt Plus."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IoTaWattPlusConfigEntry, get_settings
from .const import CONF_GRID_SERIES, DOMAIN, MANUFACTURER
from .coordinator import PowerData


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
    grid_series = get_settings(entry)[CONF_GRID_SERIES]
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
        signed = s.name == grid_series or (rt.energy.data or {}).get(s.name, 0) < 0
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
        now = time.monotonic()
        changed = (
            value is None
            or self._written is None
            or abs(value - self._written) >= self._deadband
            or (value == 0) != (self._written == 0)
            or now - self._written_at >= self._max_age
            or not self.coordinator.last_update_success
        )
        self._attr_native_value = value
        if changed:
            self._written, self._written_at = value, now
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._attr_native_value is not None


class PlusEnergySensor(CoordinatorEntity, SensorEntity):
    """Accumulated energy (Wh) computed by the IoTaWatt itself."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, device: DeviceInfo, uid: str, name: str, signed: bool) -> None:
        super().__init__(coordinator)
        self._series = name
        self._attr_translation_key = "channel_energy"
        self._attr_translation_placeholders = {"channel": name}
        self._attr_device_info = device
        self._attr_unique_id = f"{uid}_energy_{name}"
        self._attr_state_class = SensorStateClass.TOTAL if signed else SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get(self._series)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


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
