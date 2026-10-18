"""Smoke test of IoTaWattClient against a real IoTaWatt.

Read only: it sends GET requests and never changes the device configuration.
Only aiohttp is needed; Home Assistant does not have to be installed.

    python scripts/smoke_test.py 192.168.1.50 [--grid Grid] [--timeout 4]
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable
import importlib.util
from pathlib import Path
import sys
import time
import types
from typing import Any, TypeVar

import aiohttp

T = TypeVar("T")
PACKAGE = "iotawatt_plus"
SOURCE = Path(__file__).resolve().parent.parent / "custom_components" / PACKAGE


def _load_api() -> types.ModuleType:
    """Import api.py and const.py without running the integration's __init__.py."""
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", SOURCE / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{PACKAGE}.api"]


async def _timed(label: str, call: Awaitable[T]) -> T:
    start = time.monotonic()
    result = await call
    print(f"\n## {label}  ({(time.monotonic() - start) * 1000:.0f} ms)")
    return result


def _fmt(value: Any, unit: str = "") -> str:
    return "None" if value is None else f"{value}{unit}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="IoTaWatt IP address, hostname or URL")
    parser.add_argument("--grid", default="Grid", help="series used for the energy check")
    parser.add_argument("--timeout", type=float, default=4.0, help="request timeout in seconds")
    args = parser.parse_args()

    api = _load_api()
    async with aiohttp.ClientSession() as session:
        client = api.IoTaWattClient(session, args.host, args.timeout)
        print(f"IoTaWatt at {client.host}")

        series = await _timed("get_series()", client.get_series())
        print(f"{'name':<16} {'unit':<6} {'kind':<7} channel")
        for s in series:
            print(f"{s.name:<16} {s.unit:<6} {s.kind:<7} {_fmt(s.channel)}")

        live = await _timed("get_live()", client.get_live(series))
        print(f"voltage   {_fmt(live.voltage, ' V')}")
        print(f"frequency {_fmt(live.frequency, ' Hz')}")
        for s in series:
            if s.unit == "Watts":
                print(f"{s.name:<16} {_fmt(live.watts.get(s.name), ' W')}")

        device = await _timed("get_device()", client.get_device())
        for key, value in device.items():
            print(f"{key:<10} {_fmt(value)}")
        missing = [key for key, value in device.items() if value is None]
        if missing:
            raw = await _timed("raw /status?device&wifi&stats", client._get_json("/status?device&wifi&stats"))
            print(f"missing fields: {', '.join(missing)}\n{raw}")

        # Energy windows use the IoTaWatt's own clock, which may differ from ours
        device_now = await _timed("get_time()", client.get_time())
        print(f"device clock {device_now}, offset (device - local): {device_now - int(time.time())} s")
        end = device_now // 5 * 5
        begin, middle = end - 3600, end - 1800
        hour = await _timed(
            f"get_energy([{args.grid}], {begin}, {end})", client.get_energy([args.grid], begin, end)
        )
        first = await client.get_energy([args.grid], begin, middle)
        second = await client.get_energy([args.grid], middle, end)
        raw = await _timed(
            f"raw /query {args.grid}.wh begin=s-1h",
            client._get_json(
                f"/query?select=[{args.grid}.wh.d3]&begin=s-1h&end=s&group=all&format=json"
            ),
        )
        relative = api._num(raw[0][0]) if raw and raw[0] else None
        whole = hour.get(args.grid)
        halves = (first.get(args.grid) or 0) + (second.get(args.grid) or 0)
        print(f"last hour (device epochs) {_fmt(whole, ' Wh')}")
        print(f"two chained half hours    {round(halves, 3)} Wh")
        print(f"relative s-1h             {_fmt(relative, ' Wh')}")

        def close(a: float | None, b: float | None) -> bool:
            return a is not None and b is not None and abs(a - b) <= max(5.0, abs(b) * 0.01)

        ok = close(whole, relative) and close(halves, whole)
        print("energy check: " + ("OK, chained windows match" if ok else "MISMATCH"))
        return 0 if ok and not missing else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
