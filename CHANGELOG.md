# Changelog

## 0.1.2

### Fixed
- Energy totals no longer go back to 0 when Home Assistant restarts or the integration reloads. They are restored, the energy logged while Home Assistant was down is added, and from then on new energy is added window by window.
- Energy of bidirectional channels (Grid, PV1, PV2, Solar by default, and always the grid channel) uses `state_class: total` instead of `total_increasing`, which Home Assistant warned about. Their totals keep the sign and are never reset. Consumption channels ignore negative energy.
- Energy no longer depends on the IoTaWatt clock being in sync with Home Assistant's: windows are timed with the IoTaWatt's own clock.
  If that clock is corrected backwards (for example when NTP is enabled), energy pauses until it catches up instead of being counted twice.
- Fewer timeouts: requests to the IoTaWatt are never sent in parallel, and a failed request is retried once.
- An isolated failed reading no longer makes the sensors unavailable: they keep their last value until it is older than *Store at least every*. Failures are logged as debug, then as a warning after 3 in a row, and recovery as info.
- A power sensor whose value did not change while the IoTaWatt was unreachable now becomes available again when it answers.

### Added
- Options *Bidirectional channels*, *Channels without energy sensor* and *Datalog request timeout* (10 s by default, for `/query`).

## 0.1.1

### Added
- Icon and logos for Home Assistant and HACS.
- `scripts/smoke_test.py` to check the API client against a real IoTaWatt.

### Removed
- The unused `free_heap` diagnostic field.

## 0.1.0

First public release.
