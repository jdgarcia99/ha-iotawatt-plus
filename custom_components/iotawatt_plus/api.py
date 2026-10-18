"""Minimal HTTP client for the IoTaWatt local API (no authentication)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

from . import const
from .const import DEFAULT_QUERY_TIMEOUT, DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class IoTaWattError(Exception):
    """Communication error with the IoTaWatt."""


@dataclass
class Series:
    """Series defined in the IoTaWatt (input or output)."""

    name: str
    unit: str
    kind: str  # "input" | "output"
    channel: int | None = None


@dataclass
class LiveData:
    """Instantaneous reading."""

    voltage: float | None = None
    frequency: float | None = None
    watts: dict[str, float] = field(default_factory=dict)
    power_factor: dict[str, float] = field(default_factory=dict)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class IoTaWattClient:
    """Access to the IoTaWatt /status and /query endpoints.

    The IoTaWatt (an ESP8266) copes badly with parallel requests, so every
    request of a client goes through one lock: the power, energy and
    diagnostics coordinators share the client and never query in parallel.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        timeout: float = DEFAULT_TIMEOUT,
        query_timeout: float = DEFAULT_QUERY_TIMEOUT,
    ) -> None:
        self._session = session
        self._timeout = timeout
        self._query_timeout = query_timeout
        self._lock = asyncio.Lock()
        self.host = host.strip().rstrip("/")
        if not self.host.startswith("http"):
            self.host = f"http://{self.host}"

    async def _request(self, path: str, timeout: float) -> Any:
        try:
            async with asyncio.timeout(timeout):
                async with self._session.get(f"{self.host}{path}") as resp:
                    if resp.status != 200:
                        raise IoTaWattError(f"HTTP {resp.status} on {path}")
                    # The IoTaWatt does not always send a JSON content type
                    return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise IoTaWattError(f"{type(err).__name__} on {path}: {err}") from err

    async def _get_json(self, path: str) -> Any:
        """GET with one retry; the datalog (/query) gets the longer timeout."""
        timeout = self._query_timeout if path.startswith("/query") else self._timeout
        async with self._lock:
            try:
                return await self._request(path, timeout)
            except IoTaWattError as err:
                _LOGGER.debug("Retrying after %s", err)
            await asyncio.sleep(const.RETRY_DELAY)
            return await self._request(path, timeout)

    async def get_series(self) -> list[Series]:
        """Input and output names, in IoTaWatt order."""
        data = await self._get_json("/query?show=series")
        status = await self._get_json("/status?inputs&outputs")
        inputs = status.get("inputs", [])
        outputs = {o.get("name") for o in status.get("outputs", [])}
        result: list[Series] = []
        for idx, item in enumerate(data.get("series", [])):
            name, unit = item.get("name"), item.get("unit", "")
            if not name:
                continue
            if name in outputs:
                result.append(Series(name, unit, "output"))
            else:
                channel = inputs[idx].get("channel", idx) if idx < len(inputs) else idx
                result.append(Series(name, unit, "input", channel))
        return result

    async def get_device(self) -> dict[str, Any]:
        """Device data (firmware version, Wi-Fi, statistics)."""
        data = await self._get_json("/status?device&wifi&stats")
        return {
            "version": (data.get("device") or {}).get("version")
            or (data.get("stats") or {}).get("version"),
            "name": (data.get("device") or {}).get("name"),
            "mac": (data.get("wifi") or {}).get("mac"),
            "rssi": _num((data.get("wifi") or {}).get("RSSI")),
            "uptime": _num((data.get("stats") or {}).get("runseconds")),
        }

    async def get_live(self, series: list[Series]) -> LiveData:
        """Instantaneous power (inputs matched by channel number, outputs by name)."""
        data = await self._get_json("/status?inputs&outputs")
        by_channel = {s.channel: s.name for s in series if s.kind == "input"}
        live = LiveData()
        for item in data.get("inputs", []):
            ch = item.get("channel")
            if "Vrms" in item:
                live.voltage = _num(item.get("Vrms"))
                live.frequency = _num(item.get("Hz"))
                continue
            name = by_channel.get(ch)
            watts = _num(item.get("Watts"))
            if name is not None and watts is not None:
                live.watts[name] = watts
                pf = _num(item.get("Pf"))
                if pf is not None:
                    live.power_factor[name] = pf
        for item in data.get("outputs", []):
            value = _num(item.get("value"))
            if item.get("name") and value is not None and item.get("units") == "Watts":
                live.watts[item["name"]] = value
        return live

    async def get_time(self) -> int:
        """Current time of the IoTaWatt clock (Unix seconds), which may differ from ours."""
        data = await self._get_json("/status?stats")
        now = _num((data.get("stats") or {}).get("currenttime"))
        if now is None:
            raise IoTaWattError("No currenttime in /status?stats")
        return int(now)

    async def get_energy(self, names: list[str], begin: int, end: int) -> dict[str, float]:
        """Wh of each series between two times of the IoTaWatt clock (Unix seconds)."""
        if not names:
            return {}
        result: dict[str, float] = {}
        # Query at most 8 series at a time so the IoTaWatt is not overloaded
        for i in range(0, len(names), 8):
            chunk = names[i : i + 8]
            select = ",".join(f"{n}.wh.d3" for n in chunk)
            data = await self._get_json(
                f"/query?select=[{select}]&begin={begin}&end={end}&group=all&format=json"
            )
            row = data[0] if isinstance(data, list) and data else []
            for name, value in zip(chunk, row):
                num = _num(value)
                if num is not None:
                    result[name] = num
        return result
