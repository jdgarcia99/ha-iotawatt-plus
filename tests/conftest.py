"""Shared fixtures: a simulated IoTaWatt served over HTTP."""

from typing import Any

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iotawatt_plus import const
from custom_components.iotawatt_plus.const import DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

NAMES = ["Voltage", "Grid", "PV1", "PV2", "Induction", "AlumbradoPiso", "AirCondition",
         "TomasCocina", "TomasExterior", "RadiadoresPB", "Piscina", "Bomba",
         "RadiadoresPiso", "Tomas_PB", "Alumbrado_PB"]
OUTPUTS = ["House", "Solar"]


class FakeIoTaWatt:
    """Live readings, a datalog with a constant Wh rate per series, and a clock of its own."""

    def __init__(self) -> None:
        self.host = ""
        self.now = 1_800_000_000  # IoTaWatt clock, deliberately unrelated to ours
        self.grid = 1291
        self.rates: dict[str, float] = {"Grid": 0.25, "PV1": -0.1, "Solar": -0.05}  # Wh/s
        self.fail = 0  # the next N requests answer HTTP 500
        self.queries: list[tuple[list[str], int, int]] = []

    def wh(self, name: str, begin: int, end: int) -> float:
        return round((end - begin) * self.rates.get(name, 0.02), 3)

    def status(self) -> dict[str, Any]:
        inputs = [{"channel": 0, "Vrms": 228.31, "Hz": 50.003, "phase": 2}]
        for ch in range(1, 15):
            w = self.grid if ch == 1 else (-476 if ch == 2 else ch * 10)
            inputs.append({"channel": ch, "Watts": f"{w:4d}", "Pf": 0.5})
        return {"inputs": inputs, "outputs": [
            {"name": "House", "units": "Watts", "value": 1536.47},
            {"name": "Solar", "units": "Watts", "value": 245.1},
        ]}

    async def handler(self, request: web.Request) -> web.Response:
        if self.fail > 0:
            self.fail -= 1
            return web.Response(status=500)
        q = request.query_string
        if request.path == "/query" and "show=series" in q:
            return web.json_response({"series": [
                {"name": n, "unit": "Volts" if n == "Voltage" else "Watts"} for n in NAMES + OUTPUTS
            ]})
        if request.path == "/query":
            names = [s.split(".")[0] for s in request.query["select"].strip("[]").split(",")]
            begin, end = int(request.query["begin"]), int(request.query["end"])
            assert begin % 5 == 0 and end % 5 == 0 and begin < end
            self.queries.append((names, begin, end))
            return web.json_response([[self.wh(n, begin, end) for n in names]])
        if request.path == "/status" and "device" in q:
            return web.json_response({
                "device": {"name": "IotaWatt"},
                "wifi": {"mac": "AA:BB:CC:DD:EE:FF", "RSSI": -60},
                "stats": {"runseconds": 12345, "version": "02_08_03"},
            })
        if request.path == "/status" and "stats" in q:
            return web.json_response({"stats": {"currenttime": self.now, "runseconds": 12345}})
        if request.path == "/status":
            return web.json_response(self.status())
        return web.Response(status=404)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    # The recorder must be prepared before hass, which enable_custom_integrations creates
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(const, "RETRY_DELAY", 0)


@pytest.fixture
async def fake_iotawatt(socket_enabled, aiohttp_server) -> FakeIoTaWatt:
    fake = FakeIoTaWatt()
    app = web.Application()
    app.router.add_get("/{tail:.*}", fake.handler)
    server = await aiohttp_server(app)
    fake.host = f"127.0.0.1:{server.port}"
    return fake


async def setup_entry(hass, fake: FakeIoTaWatt, **options: Any) -> MockConfigEntry:
    """Add a config entry for the fake IoTaWatt with the given options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IoTaWatt Plus",
        unique_id="AA:BB:CC:DD:EE:FF",
        data={"host": fake.host},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
