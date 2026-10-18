"""End-to-end test against a simulated IoTaWatt."""
import pytest
from aiohttp import web
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from homeassistant.util import dt as dt_util
from datetime import timedelta

from custom_components.iotawatt_plus.const import DOMAIN

STATE = {"grid": 1291}
NAMES = ["Voltage","Grid","PV1","PV2","Induction","AlumbradoPiso","AirCondition","TomasCocina","TomasExterior","RadiadoresPB","Piscina","Bomba","RadiadoresPiso","Tomas_PB","Alumbrado_PB"]

def status():
    inputs = [{"channel":0,"Vrms":228.31,"Hz":50.003,"phase":2}]
    for ch in range(1,15):
        w = STATE["grid"] if ch == 1 else (-476 if ch == 2 else ch*10)
        inputs.append({"channel":ch,"Watts":f"{w:4d}","Pf":0.5})
    return {"inputs":inputs,"outputs":[{"name":"House","units":"Watts","value":1536.47},{"name":"Solar","units":"Watts","value":245.1}]}

async def handler(request):
    q = request.query_string
    if request.path == "/query" and "show=series" in q:
        return web.json_response({"series":[{"name":n,"unit":"Volts" if n=="Voltage" else "Watts"} for n in NAMES]+[{"name":"House","unit":"Watts"},{"name":"Solar","unit":"Watts"}]})
    if request.path == "/query":
        sel = request.query["select"].strip("[]").split(",")
        return web.Response(text=str([[ -100.5 if s.startswith("PV1") else 1234.567 for s in sel]]).replace("'", '"'))
    if request.path == "/status" and "device" in q:
        return web.json_response({"device":{"name":"IotaWatt","version":"02_08_02"},"wifi":{"mac":"AA:BB:CC:DD:EE:FF","RSSI":-60},"stats":{"runseconds":12345}})
    if request.path == "/status":
        return web.json_response(status())
    return web.Response(status=404)

@pytest.fixture
async def fake_iotawatt(socket_enabled, aiohttp_server):
    app = web.Application(); app.router.add_get("/{tail:.*}", handler)
    server = await aiohttp_server(app)
    return f"127.0.0.1:{server.port}"

async def test_full(hass, fake_iotawatt, socket_enabled, freezer):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: fake_iotawatt})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "settings", result
    base = {"scan_interval":5,"deadband":10,"max_age":60,"grid_series":"Grid","energy_interval":60,"device_interval":5,"request_timeout":4}
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {**base, "surplus_windows":"abc", "channels":[]})
    assert bad["errors"] == {"surplus_windows": "bad_windows"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**base, "surplus_windows":"30, 60; 300", "channels":[]})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["options"]["surplus_windows"] == "30, 60, 300"
    await hass.async_block_till_done()
    states = {s.entity_id: s.state for s in hass.states.async_all("sensor")}
    for k in sorted(states): print(k, states[k])
    assert states["sensor.iotawatt_plus_grid"] == "1291.0"
    assert states["sensor.iotawatt_plus_grid_import"] == "1291.0"
    assert states["sensor.iotawatt_plus_surplus"] == "0.0"
    assert states["sensor.iotawatt_plus_grid_energy"] == "1.234567"  # kWh
    assert states["sensor.iotawatt_plus_voltage"] == "228.31"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    # Surplus: the grid starts exporting
    STATE["grid"] = -800
    await entry.runtime_data.power.async_refresh(); await hass.async_block_till_done()
    STATE["grid"] = -805  # change < 10 W: not written
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
