"""Constants for IoTaWatt Plus."""

from __future__ import annotations

DOMAIN = "iotawatt_plus"
MANUFACTURER = "IoTaWatt, Inc."

CONF_SCAN_INTERVAL = "scan_interval"
CONF_ENERGY_INTERVAL = "energy_interval"
CONF_DEADBAND = "deadband"
CONF_MAX_AGE = "max_age"
CONF_GRID_SERIES = "grid_series"
CONF_ENERGY_START = "energy_start"
CONF_DEVICE_INTERVAL = "device_interval"
CONF_TIMEOUT = "request_timeout"
CONF_SURPLUS_WINDOWS = "surplus_windows"
CONF_CHANNELS = "channels"

DEFAULT_SCAN_INTERVAL = 5  # s, power polling
DEFAULT_ENERGY_INTERVAL = 60  # s, accumulated energy polling
DEFAULT_DEADBAND = 10  # W, minimum change before a new state is written
DEFAULT_MAX_AGE = 60  # s, a state is written at least this often
DEFAULT_GRID_SERIES = "Grid"
DEFAULT_DEVICE_INTERVAL = 5  # min, diagnostics polling
DEFAULT_TIMEOUT = 4  # s, HTTP request timeout
DEFAULT_SURPLUS_WINDOWS = "30, 60, 300"  # s, surplus averaging windows

MIN_SCAN_INTERVAL = 2
MAX_SCAN_INTERVAL = 60

MAX_SURPLUS_WINDOW = 3600  # s


def parse_windows(text: str) -> list[int]:
    """Parse a list like "30, 60, 300" into sorted unique seconds."""
    result: set[int] = set()
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 < int(part) <= MAX_SURPLUS_WINDOW:
            result.add(int(part))
    return sorted(result)
