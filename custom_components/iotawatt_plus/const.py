"""Constants for IoTaWatt Plus."""

from __future__ import annotations

DOMAIN = "iotawatt_plus"
MANUFACTURER = "IoTaWatt, Inc."

CONF_SCAN_INTERVAL = "scan_interval"
CONF_ENERGY_INTERVAL = "energy_interval"
CONF_DEADBAND = "deadband"
CONF_MAX_AGE = "max_age"
CONF_GRID_SERIES = "grid_series"
CONF_DEVICE_INTERVAL = "device_interval"
CONF_TIMEOUT = "request_timeout"
CONF_QUERY_TIMEOUT = "query_timeout"
CONF_SURPLUS_WINDOWS = "surplus_windows"
CONF_CHANNELS = "channels"
CONF_BIDIRECTIONAL = "bidirectional_channels"
CONF_ENERGY_EXCLUDE = "energy_exclude"

DEFAULT_SCAN_INTERVAL = 5  # s, power polling
DEFAULT_ENERGY_INTERVAL = 60  # s, accumulated energy polling
DEFAULT_DEADBAND = 10  # W, minimum change before a new state is written
DEFAULT_MAX_AGE = 60  # s, a state is written at least this often
DEFAULT_GRID_SERIES = "Grid"
DEFAULT_DEVICE_INTERVAL = 5  # min, diagnostics polling
DEFAULT_TIMEOUT = 4  # s, timeout of /status requests
DEFAULT_QUERY_TIMEOUT = 10  # s, timeout of /query requests (the datalog is slower)
DEFAULT_SURPLUS_WINDOWS = "30, 60, 300"  # s, surplus averaging windows
DEFAULT_BIDIRECTIONAL = "Grid, PV1, PV2, Solar"

MIN_SCAN_INTERVAL = 2
MAX_SCAN_INTERVAL = 60

MAX_SURPLUS_WINDOW = 3600  # s

RETRY_DELAY = 0.5  # s, wait before the single retry of a failed request
FAILURES_BEFORE_WARNING = 3  # consecutive failures before logging a warning
DATALOG_STEP = 5  # s, the IoTaWatt datalog resolution; query times are multiples of it
ENERGY_END_MARGIN = 5  # s, energy windows end this far behind the device clock
MAX_ENERGY_GAP = 31 * 24 * 3600  # s, longer gaps are not backfilled


def parse_windows(text: str) -> list[int]:
    """Parse a list like "30, 60, 300" into sorted unique seconds."""
    result: set[int] = set()
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 < int(part) <= MAX_SURPLUS_WINDOW:
            result.add(int(part))
    return sorted(result)


def parse_names(text: str | None) -> list[str]:
    """Parse a list like "Grid, PV1; PV2" into unique names, keeping their order."""
    result: list[str] = []
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        if part and part.casefold() not in (n.casefold() for n in result):
            result.append(part)
    return result
