"""End-to-end test against a simulated IoTaWatt."""
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from custom_components.iotawatt_plus.const import DOMAIN

async def test_full(hass, fake_iotawatt):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: fake_iotawatt.host})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "settings", result
    base = {"scan_interval":5,"deadband":10,"max_age":60,"grid_series":"Grid","energy_interval":60,"device_interval":5,"request_timeout":4}
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {**base, "surplus_windows":"abc", "channels":[]})
    assert bad["errors"] == {"surplus_windows": "bad_windows"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**base, "surplus_windows":"30, 60; 300", "channels":[], "bidirectional_channels": "grid; PV1,PV2 , Solar"})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["options"]["surplus_windows"] == "30, 60, 300"
    assert result["options"]["bidirectional_channels"] == "grid, PV1, PV2, Solar"
    assert result["options"]["query_timeout"] == 10 and result["options"]["energy_exclude"] == []
    await hass.async_block_till_done()
    states = {s.entity_id: s.state for s in hass.states.async_all("sensor")}
    for k in sorted(states): print(k, states[k])
    assert states["sensor.iotawatt_plus_grid"] == "1291.0"
    assert states["sensor.iotawatt_plus_grid_import"] == "1291.0"
    assert states["sensor.iotawatt_plus_surplus"] == "0.0"
    assert states["sensor.iotawatt_plus_grid_energy"] == "0.0"  # counts from setup
    assert states["sensor.iotawatt_plus_voltage"] == "228.31"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    fake_iotawatt.now += 60
    await entry.runtime_data.energy.async_refresh(); await hass.async_block_till_done()
    assert hass.states.get("sensor.iotawatt_plus_grid_energy").state == "0.015"  # 60 s x 0.25 Wh/s
    # Surplus: the grid starts exporting
    fake_iotawatt.grid = -800
    await entry.runtime_data.power.async_refresh(); await hass.async_block_till_done()
    fake_iotawatt.grid = -805  # change < 10 W: not written
    await entry.runtime_data.power.async_refresh(); await hass.async_block_till_done()
    s = {x.entity_id: x.state for x in hass.states.async_all("sensor")}
    print("after exporting:", {k:v for k,v in s.items() if "surplus" in k or k.endswith("_grid")})
    assert s["sensor.iotawatt_plus_grid"] == "-800.0"   # -805 is not written (10 W deadband)
    assert s["sensor.iotawatt_plus_surplus"] == "800.0"
    assert s["sensor.iotawatt_plus_grid_import"] == "0.0"
    assert float(s["sensor.iotawatt_plus_surplus_average_30_s"]) > 0
    # Options
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**base, "scan_interval":3, "surplus_windows":"120", "channels":["Grid","PV1"]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.runtime_data.power.update_interval.total_seconds() == 3
    assert entry.runtime_data.channels == ["Grid", "PV1"]
    s2 = {x.entity_id: x.state for x in hass.states.async_all("sensor") if x.state != "unavailable"}
    assert "sensor.iotawatt_plus_surplus_average_2_min" in s2, sorted(s2)
    assert "sensor.iotawatt_plus_induction" not in s2
    assert hass.states.get("sensor.iotawatt_plus_induction") is None  # removed, not left unavailable
    assert await hass.config_entries.async_unload(entry.entry_id)
