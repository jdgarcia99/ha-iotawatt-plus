# IoTaWatt Plus

Custom Home Assistant integration (domain `iotawatt_plus`), distributed through HACS from the public repository `jdgarcia99/ha-iotawatt-plus`. It polls an IoTaWatt energy monitor every few seconds and exposes power, energy, grid import/surplus and diagnostics sensors, writing a new state only when the value changes beyond a deadband.

## Layout

- `custom_components/iotawatt_plus/`
  - `api.py`: HTTP client for the IoTaWatt local API (`/status`, `/query`). Inputs are matched by channel number, outputs by name. All requests of a client go through one `asyncio.Lock` (the ESP8266 copes badly with parallel requests), are retried once, and `/query` has its own, longer timeout.
  - `coordinator.py`: three coordinators sharing one client: power (fast, also derives grid import/export and surplus averages), energy and device diagnostics. `FailureTracker` keeps the last data through isolated failures and logs debug / warning after 3 in a row / info on recovery.
  - Energy: the coordinator reads Wh in consecutive windows whose `begin`/`end` are times of the IoTaWatt's own clock (`/status?stats` `currenttime`, multiples of 5 s), never Home Assistant's, because that clock can be off. It publishes only deltas; `PlusEnergySensor` (a `RestoreSensor`) adds them up and stores its total with the device time it was counted up to, so restarts backfill the gap. Restore order: its own sensor data, then the last state, then the recorder's last numeric state (sensors of v0.1.1 and earlier were not `RestoreEntity`, so they have no restore data at all). Bidirectional channels use `total` and signed deltas; others use `total_increasing` and drop negative deltas.
  - `sensor.py`: entities. `PlusPowerSensor` applies the deadband/max-age write filter. Entities of channels or windows that are no longer configured are removed from the registry. Unique IDs (`<uid>_power_<name>`, `<uid>_energy_<name>`, ...) must never change: the Energy dashboard and statistics depend on them.
  - `config_flow.py`: config flow (host, then settings) and options flow. Both share `settings_schema()`.
  - `const.py`: `CONF_*` keys, `DEFAULT_*` values and limits.
  - `strings.json`, `translations/en.json`, `translations/es.json`: UI text.
  - `brand/`: icon and logos (light and `dark_` variants, `@2x`) used by Home Assistant and the HACS brands check.
- `images/`: `icon.svg` (source of the icon) and `banner.png` (README header).
- `tests/`: pytest with `pytest-homeassistant-custom-component`. `conftest.py` serves a fake IoTaWatt over `aiohttp` (own clock, constant Wh rate per series, forced failures). `test_integration.py` runs the config and options flows; `test_energy.py` covers state classes, restore and accumulation; `test_reliability.py` the failure handling; `test_api.py` the client, with payloads captured from real firmware.
- `CHANGELOG.md`: one section per version.
- `scripts/smoke_test.py`: runs `IoTaWattClient` against a real IoTaWatt (`python scripts/smoke_test.py <host>`). Only needs `aiohttp`.
- Only GET requests are ever sent to the IoTaWatt: never change its configuration.
- `.github/workflows/validate.yml`: **Validate** workflow (hassfest, HACS action and tests). It must stay green.

## Conventions

### Language
- Code, comments, docstrings, README, release notes and commit messages are in **English**.
- The user interface is translated: every user-facing string lives in `translations/en.json` and `translations/es.json`. Never hard-code UI text in Python.
- `strings.json` must be **identical** to `translations/en.json`. When a string changes, update `strings.json`, `en.json` and `es.json` together.

### Configuration
- Everything configurable goes in the **config flow and the options flow**. No YAML configuration.
- A new setting needs: a `CONF_*` key and a `DEFAULT_*` value in `const.py`, an entry in `DEFAULTS` in `__init__.py`, a field in `settings_schema()` (shared by both flows), labels and descriptions in both translations (under `config.step.settings` and `options.step.init`), and a row in the README *Settings* table.
- Read settings through `get_settings(entry)` (defaults, then data, then options). Changing options reloads the entry.

### Every change
- Includes **tests** that cover it.
- **Bumps `version` in `custom_components/iotawatt_plus/manifest.json`** (semantic versioning: patch for fixes, minor for features).
- Updates the README when behaviour or settings change, and adds an entry to `CHANGELOG.md`.
- Keeps the style of the surrounding code: type hints, short docstrings, Home Assistant import layout.

### Commits and releases
- Imperative subject in English (`Add …`, `Fix …`), focused commits, linear history on `main`.
- A release is a tag `v<version>` matching `manifest.json`, created with `gh release create`, with brief English notes.
- After pushing, check that the **Validate** workflow passes (`gh run list`, `gh run watch`).

## Tests

On Linux, macOS, WSL or CI:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest tests
```

Home Assistant's test tools do not run on plain Windows (`fcntl`, `resource` and asyncio's TCP socketpair). On the maintainer's Windows machine, which has no WSL or Docker, local shims live in the git-ignored `.dev/winshim/`:

```bash
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements_test.txt
PYTHONPATH=.dev/winshim .venv/Scripts/python -m pytest -p win_socketpair tests -q
```

The shims are only for local runs and are never committed; CI on Linux is the reference.
