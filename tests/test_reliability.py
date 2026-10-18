"""Power coordinator: isolated failures keep the last value, streaks are logged."""

from datetime import timedelta
import logging

from .conftest import setup_entry

GRID = "sensor.iotawatt_plus_grid"


async def _poll(hass, entry, fake, fail: bool = False) -> None:
    if fail:
        fake.fail = 2  # the request and its retry
    await entry.runtime_data.power.async_refresh()
    await hass.async_block_till_done()


async def test_isolated_failures_keep_last_value(hass, fake_iotawatt, freezer, caplog) -> None:
    entry = await setup_entry(hass, fake_iotawatt, max_age=60)
    caplog.set_level(logging.DEBUG, logger="custom_components.iotawatt_plus")

    await _poll(hass, entry, fake_iotawatt, fail=True)
    assert hass.states.get(GRID).state == "1291.0"  # not unavailable
    assert "IoTaWatt power failure 1" in caplog.text
    assert "failed 3 times" not in caplog.text

    freezer.tick(timedelta(seconds=5))
    await _poll(hass, entry, fake_iotawatt, fail=True)
    freezer.tick(timedelta(seconds=5))
    await _poll(hass, entry, fake_iotawatt, fail=True)
    assert hass.states.get(GRID).state == "1291.0"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings if "IoTaWatt" in r.getMessage()] == [
        "IoTaWatt power failed 3 times in a row: HTTP 500 on /status?inputs&outputs"
    ]

    fake_iotawatt.grid = 500
    await _poll(hass, entry, fake_iotawatt)
    assert hass.states.get(GRID).state == "500.0"
    assert "IoTaWatt power answering again after 3 failures" in caplog.text


async def test_unavailable_once_last_value_is_max_age_old(hass, fake_iotawatt, freezer) -> None:
    entry = await setup_entry(hass, fake_iotawatt, max_age=60)
    freezer.tick(timedelta(seconds=30))
    await _poll(hass, entry, fake_iotawatt, fail=True)
    assert hass.states.get(GRID).state == "1291.0"
    freezer.tick(timedelta(seconds=31))  # 61 s since the last good reading
    await _poll(hass, entry, fake_iotawatt, fail=True)
    assert hass.states.get(GRID).state == "unavailable"
    await _poll(hass, entry, fake_iotawatt)
    assert hass.states.get(GRID).state == "1291.0"
