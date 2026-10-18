"""Energy sensors: state class per channel, persistence and delta accumulation."""

import logging

from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from .conftest import setup_entry

GRID = "sensor.iotawatt_plus_grid_energy"
INDUCTION = "sensor.iotawatt_plus_induction_energy"
PV1 = "sensor.iotawatt_plus_pv1_energy"


def _end(fake) -> int:
    """End of the window the coordinator asks for at the fake's current time."""
    return (fake.now - 5) // 5 * 5


async def _refresh(hass, entry) -> None:
    await entry.runtime_data.energy.async_refresh()
    await hass.async_block_till_done()


def _state_class(hass, entity_id: str) -> str | None:
    return hass.states.get(entity_id).attributes.get("state_class")


async def test_state_class_per_channel(hass, fake_iotawatt) -> None:
    await setup_entry(hass, fake_iotawatt)
    for name in ("grid", "pv1", "pv2", "solar"):
        assert _state_class(hass, f"sensor.iotawatt_plus_{name}_energy") == "total"
    for name in ("induction", "house", "piscina"):
        assert _state_class(hass, f"sensor.iotawatt_plus_{name}_energy") == "total_increasing"


async def test_bidirectional_and_excluded_from_options(hass, fake_iotawatt) -> None:
    await setup_entry(
        hass, fake_iotawatt, bidirectional_channels="induction", energy_exclude=["Solar"]
    )
    assert _state_class(hass, INDUCTION) == "total"
    assert _state_class(hass, GRID) == "total"  # the grid channel always is
    assert _state_class(hass, PV1) == "total_increasing"  # no longer listed
    assert hass.states.get("sensor.iotawatt_plus_solar_energy") is None
    assert hass.states.get("sensor.iotawatt_plus_solar") is not None  # power kept
    assert all("Solar" not in names for names, _, _ in fake_iotawatt.queries)


async def test_accumulates_chained_windows(hass, fake_iotawatt) -> None:
    entry = await setup_entry(hass, fake_iotawatt)
    start = _end(fake_iotawatt)
    assert hass.states.get(GRID).state == "0.0"
    for _ in range(3):
        fake_iotawatt.now += 60
        await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.045"  # 180 s x 0.25 Wh/s
    assert hass.states.get(PV1).state == "-0.018"  # signed channel keeps the sign
    windows = sorted({(b, e) for _, b, e in fake_iotawatt.queries})
    assert windows == [(start, start + 60), (start + 60, start + 120), (start + 120, start + 180)]


async def test_restore_and_backfill_downtime(hass, fake_iotawatt) -> None:
    stopped_at = _end(fake_iotawatt) - 600  # Home Assistant was down for 10 minutes
    mock_restore_cache_with_extra_data(hass, (
        (State(GRID, "5.0"), {
            "native_value": 5000.0, "native_unit_of_measurement": "Wh", "device_time": stopped_at,
        }),
    ))
    entry = await setup_entry(hass, fake_iotawatt)
    assert hass.states.get(GRID).state == "5.0"
    fake_iotawatt.now += 60
    await _refresh(hass, entry)
    # 660 s x 0.25 Wh/s since the restored time, not since this start
    assert hass.states.get(GRID).state == "5.165"
    assert (["Grid"], stopped_at, _end(fake_iotawatt)) in [
        (n[:1], b, e) for n, b, e in fake_iotawatt.queries if "Grid" in n
    ]


async def test_restore_state_of_previous_version(hass, fake_iotawatt) -> None:
    # v0.1.1 kept no sensor data, only the state in kWh
    mock_restore_cache(hass, (State(INDUCTION, "2.5", {"unit_of_measurement": "kWh"}),))
    entry = await setup_entry(hass, fake_iotawatt)
    assert hass.states.get(INDUCTION).state == "2.5"
    fake_iotawatt.now += 100
    await _refresh(hass, entry)
    assert hass.states.get(INDUCTION).state == "2.502"  # + 100 s x 0.02 Wh/s


async def test_negative_delta_ignored_on_consumption_channel(hass, fake_iotawatt, caplog) -> None:
    entry = await setup_entry(hass, fake_iotawatt)
    fake_iotawatt.now += 60
    await _refresh(hass, entry)
    assert hass.states.get(INDUCTION).state == "0.0012"
    fake_iotawatt.rates["Induction"] = -0.01
    caplog.set_level(logging.DEBUG, logger="custom_components.iotawatt_plus")
    fake_iotawatt.now += 60
    await _refresh(hass, entry)
    assert hass.states.get(INDUCTION).state == "0.0012"  # unchanged, never decreases
    assert "Ignoring negative energy delta of Induction: -0.600 Wh" in caplog.text


async def test_energy_failure_keeps_value_and_next_window_covers_gap(hass, fake_iotawatt) -> None:
    entry = await setup_entry(hass, fake_iotawatt)
    fake_iotawatt.now += 60
    fake_iotawatt.fail = 2  # the time request and its retry fail
    await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.0"  # still available
    fake_iotawatt.now += 60
    await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.03"  # both minutes counted once


async def test_clock_going_back_never_counts_twice(hass, fake_iotawatt) -> None:
    entry = await setup_entry(hass, fake_iotawatt)
    fake_iotawatt.now += 60
    await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.015"
    fake_iotawatt.now -= 23 * 60  # the IoTaWatt clock is corrected
    await _refresh(hass, entry)
    fake_iotawatt.now += 22 * 60
    await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.015"  # still behind the last window
    fake_iotawatt.now += 2 * 60
    await _refresh(hass, entry)
    assert hass.states.get(GRID).state == "0.03"  # counts again past it, from it
