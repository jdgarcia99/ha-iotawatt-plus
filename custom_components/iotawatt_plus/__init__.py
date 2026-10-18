"""IoTaWatt Plus: fast IoTaWatt readings for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IoTaWattClient, IoTaWattError, Series
from .const import (
    CONF_BIDIRECTIONAL,
    CONF_CHANNELS,
    CONF_DEADBAND,
    CONF_DEVICE_INTERVAL,
    CONF_ENERGY_EXCLUDE,
    CONF_ENERGY_INTERVAL,
    CONF_GRID_SERIES,
    CONF_MAX_AGE,
    CONF_QUERY_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_SURPLUS_WINDOWS,
    CONF_TIMEOUT,
    DEFAULT_BIDIRECTIONAL,
    DEFAULT_DEADBAND,
    DEFAULT_DEVICE_INTERVAL,
    DEFAULT_ENERGY_INTERVAL,
    DEFAULT_GRID_SERIES,
    DEFAULT_MAX_AGE,
    DEFAULT_QUERY_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SURPLUS_WINDOWS,
    DEFAULT_TIMEOUT,
    parse_names,
    parse_windows,
)
from .coordinator import DeviceCoordinator, EnergyCoordinator, PowerCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

DEFAULTS: dict[str, Any] = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_ENERGY_INTERVAL: DEFAULT_ENERGY_INTERVAL,
    CONF_DEVICE_INTERVAL: DEFAULT_DEVICE_INTERVAL,
    CONF_TIMEOUT: DEFAULT_TIMEOUT,
    CONF_QUERY_TIMEOUT: DEFAULT_QUERY_TIMEOUT,
    CONF_DEADBAND: DEFAULT_DEADBAND,
    CONF_MAX_AGE: DEFAULT_MAX_AGE,
    CONF_GRID_SERIES: DEFAULT_GRID_SERIES,
    CONF_SURPLUS_WINDOWS: DEFAULT_SURPLUS_WINDOWS,
    CONF_CHANNELS: None,  # None = all channels
    CONF_BIDIRECTIONAL: DEFAULT_BIDIRECTIONAL,
    CONF_ENERGY_EXCLUDE: [],
}


def get_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Effective settings: defaults, then entry data, then options."""
    return {**DEFAULTS, **entry.data, **entry.options}


@dataclass
class RuntimeData:
    """Objects shared by a config entry."""

    client: IoTaWattClient
    series: list[Series]
    channels: list[str]
    energy_channels: list[str]
    bidirectional: set[str]  # casefolded names
    windows: list[int]
    power: PowerCoordinator
    energy: EnergyCoordinator
    device: DeviceCoordinator
    deadband: float
    max_age: int


type IoTaWattPlusConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: IoTaWattPlusConfigEntry) -> bool:
    """Set up IoTaWatt Plus from a config entry."""
    cfg = get_settings(entry)
    client = IoTaWattClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        float(cfg[CONF_TIMEOUT]),
        float(cfg[CONF_QUERY_TIMEOUT]),
    )
    try:
        series = await client.get_series()
    except IoTaWattError as err:
        raise ConfigEntryNotReady(f"Cannot connect to the IoTaWatt: {err}") from err

    watts = [s.name for s in series if s.unit == "Watts"]
    selected = cfg[CONF_CHANNELS]
    channels = [n for n in watts if not selected or n in selected]
    excluded = {n.casefold() for n in cfg[CONF_ENERGY_EXCLUDE] or []}
    energy_channels = [n for n in channels if n.casefold() not in excluded]
    bidirectional = {n.casefold() for n in parse_names(cfg[CONF_BIDIRECTIONAL])}
    bidirectional.add(str(cfg[CONF_GRID_SERIES]).casefold())  # signed by definition
    windows = parse_windows(cfg[CONF_SURPLUS_WINDOWS])
    max_age = int(cfg[CONF_MAX_AGE])

    power = PowerCoordinator(
        hass, entry, client, series,
        int(cfg[CONF_SCAN_INTERVAL]), cfg[CONF_GRID_SERIES], windows, max_age,
    )
    energy = EnergyCoordinator(
        hass, entry, client, energy_channels, int(cfg[CONF_ENERGY_INTERVAL]), max_age
    )
    device = DeviceCoordinator(hass, entry, client, int(cfg[CONF_DEVICE_INTERVAL]))

    await power.async_config_entry_first_refresh()
    # Energy and diagnostics must not block setup if they fail
    await energy.async_refresh()
    await device.async_refresh()

    entry.runtime_data = RuntimeData(
        client=client,
        series=series,
        channels=channels,
        energy_channels=energy_channels,
        bidirectional=bidirectional,
        windows=windows,
        power=power,
        energy=energy,
        device=device,
        deadband=float(cfg[CONF_DEADBAND]),
        max_age=max_age,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: IoTaWattPlusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IoTaWattPlusConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
