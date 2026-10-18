"""IoTaWattClient parsing, with payloads captured from firmware 02_08_03 (identifiers anonymised)."""

from typing import Any

from custom_components.iotawatt_plus.api import IoTaWattClient

DEVICE_STATUS = {
    "device": {"name": "IotaWatt", "timediff": 60, "allowdst": True, "update": "NONE"},
    "stats": {
        "cyclerate": 772.2328,
        "chanrate": 27.29085,
        "starttime": 1792047456,
        "currenttime": 1792334813,
        "runseconds": 287357,
        "stack": 23136,
        "version": "02_08_03",
        "frequency": 50.02,
        "lowbat": False,
    },
    "wifi": {
        "connecttime": 1792047469,
        "SSID": "Example",
        "IP": "192.0.2.10",
        "channel": 1,
        "RSSI": -56,
        "mac": "AA:BB:CC:DD:EE:FF",
    },
}

SERIES = {
    "series": [
        {"name": "Voltage", "unit": "Volts"},
        {"name": "Grid", "unit": "Watts"},
        {"name": "PV1", "unit": "Watts"},
        {"name": "House", "unit": "Watts"},
    ]
}

LIVE_STATUS = {
    "inputs": [
        {"channel": 0, "Vrms": 229.796, "Hz": 50.02184, "phase": 2},
        {"channel": 1, "Watts": "1169", "Pf": 0.703124, "phase": 3, "lastphase": 1},
        {"channel": 2, "Watts": " 6", "Pf": 0.038409, "reversed": True, "phase": 3.1},
    ],
    "outputs": [{"name": "House", "units": "Watts", "value": 1265.097}],
}


class FakeResponse:
    def __init__(self, data: Any) -> None:
        self.status = 200
        self._data = data

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> Any:
        return self._data


class FakeSession:
    """Answers GET requests from a {path prefix: payload} map and records the URLs."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        path = url.removeprefix("http://iotawatt.local")
        for prefix, data in self.routes.items():
            if path.startswith(prefix):
                return FakeResponse(data)
        raise AssertionError(f"unexpected request {url}")


async def test_get_device_real_keys() -> None:
    client = IoTaWattClient(FakeSession({"/status?device": DEVICE_STATUS}), "iotawatt.local")
    assert await client.get_device() == {
        "version": "02_08_03",  # only reported under stats
        "name": "IotaWatt",
        "mac": "AA:BB:CC:DD:EE:FF",
        "rssi": -56.0,
        "uptime": 287357.0,
    }


async def test_get_series_and_live() -> None:
    session = FakeSession({"/query?show=series": SERIES, "/status?inputs": LIVE_STATUS})
    client = IoTaWattClient(session, "iotawatt.local")
    series = await client.get_series()
    assert [(s.name, s.kind, s.channel) for s in series] == [
        ("Voltage", "input", 0),
        ("Grid", "input", 1),
        ("PV1", "input", 2),
        ("House", "output", None),
    ]
    live = await client.get_live(series)
    assert live.voltage == 229.796
    assert live.frequency == 50.02184
    assert live.watts == {"Grid": 1169.0, "PV1": 6.0, "House": 1265.097}  # padded " 6" parsed


async def test_get_energy_epoch_begin() -> None:
    session = FakeSession({"/query?select=": [[1866.871, -42.5]]})
    client = IoTaWattClient(session, "iotawatt.local")
    assert await client.get_energy(["Grid", "PV1"], 1792331260) == {"Grid": 1866.871, "PV1": -42.5}
    assert session.urls == [
        "http://iotawatt.local/query?select=[Grid.wh.d3,PV1.wh.d3]"
        "&begin=1792331260&end=s&group=all&format=json"
    ]
