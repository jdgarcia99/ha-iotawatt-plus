"""Config flow for IoTaWatt Plus."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IoTaWattClient, IoTaWattError
from .const import (
    CONF_CHANNELS,
    CONF_DEADBAND,
    CONF_DEVICE_INTERVAL,
    CONF_ENERGY_INTERVAL,
    CONF_ENERGY_START,
    CONF_GRID_SERIES,
    CONF_MAX_AGE,
    CONF_SCAN_INTERVAL,
    CONF_SURPLUS_WINDOWS,
    CONF_TIMEOUT,
    DEFAULT_GRID_SERIES,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    parse_windows,
)


def _number(minimum: float, maximum: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def settings_schema(values: dict[str, Any], channels: list[str]) -> vol.Schema:
    """Form shared by the initial setup and the options flow."""
    from . import DEFAULTS  # local import avoids a circular import at load time

    v = {**DEFAULTS, **values}
    grid = v[CONF_GRID_SERIES] if v[CONF_GRID_SERIES] in channels else (
        DEFAULT_GRID_SERIES if DEFAULT_GRID_SERIES in channels else channels[0]
    )
    return vol.Schema(
        {
            vol.Required(CONF_SCAN_INTERVAL, default=v[CONF_SCAN_INTERVAL]): _number(
                MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, 1, "s"
            ),
            vol.Required(CONF_DEADBAND, default=v[CONF_DEADBAND]): _number(0, 1000, 1, "W"),
            vol.Required(CONF_MAX_AGE, default=v[CONF_MAX_AGE]): _number(10, 3600, 5, "s"),
            vol.Required(CONF_GRID_SERIES, default=grid): selector.SelectSelector(
                selector.SelectSelectorConfig(options=channels)
            ),
            vol.Required(CONF_SURPLUS_WINDOWS, default=v[CONF_SURPLUS_WINDOWS]): str,
            vol.Optional(CONF_CHANNELS, default=v[CONF_CHANNELS] or []): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=channels, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_ENERGY_INTERVAL, default=v[CONF_ENERGY_INTERVAL]): _number(
                30, 3600, 10, "s"
            ),
            vol.Required(CONF_DEVICE_INTERVAL, default=v[CONF_DEVICE_INTERVAL]): _number(
                1, 60, 1, "min"
            ),
            vol.Required(CONF_TIMEOUT, default=v[CONF_TIMEOUT]): _number(1, 15, 1, "s"),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise the form values; None when the surplus windows are invalid."""
    windows = parse_windows(user_input.get(CONF_SURPLUS_WINDOWS, ""))
    if not windows:
        return None
    data = dict(user_input)
    data[CONF_SURPLUS_WINDOWS] = ", ".join(str(w) for w in windows)
    data[CONF_CHANNELS] = list(user_input.get(CONF_CHANNELS) or [])
    return data


class IoTaWattPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an IoTaWatt: address first, then settings."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._channels: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            client = IoTaWattClient(async_get_clientsession(self.hass), host)
            try:
                series = await client.get_series()
                try:
                    device = await client.get_device()
                except IoTaWattError:
                    device = {}
            except IoTaWattError:
                errors["base"] = "cannot_connect"
            else:
                self._channels = [s.name for s in series if s.unit == "Watts"]
                if not self._channels:
                    errors["base"] = "no_series"
                else:
                    await self.async_set_unique_id(device.get("mac") or host)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    self._host = host
                    return await self.async_step_settings()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean(user_input)
            if options is None:
                errors[CONF_SURPLUS_WINDOWS] = "bad_windows"
            else:
                return self.async_create_entry(
                    title="IoTaWatt Plus",
                    data={CONF_HOST: self._host, CONF_ENERGY_START: int(time.time())},
                    options=options,
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=settings_schema(user_input or {}, self._channels),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IoTaWattPlusOptionsFlow()


class IoTaWattPlusOptionsFlow(OptionsFlow):
    """Change any setting after installation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean(user_input)
            if options is not None:
                return self.async_create_entry(data=options)
            errors[CONF_SURPLUS_WINDOWS] = "bad_windows"
        runtime = getattr(self.config_entry, "runtime_data", None)
        channels = [s.name for s in runtime.series if s.unit == "Watts"] if runtime else []
        current = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        if not channels:
            channels = [current.get(CONF_GRID_SERIES, DEFAULT_GRID_SERIES)]
        return self.async_show_form(
            step_id="init", data_schema=settings_schema(current, channels), errors=errors
        )
