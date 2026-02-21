# Logging Improvements

- **Date:** 2026-02-21
- **Author:** Copilot
- **Status:** Complete
- **Summary:** Structured logging across the integration to support runtime debugging without overloading Home Assistant logs. Added a logging wrapper, balancer state sensor, and user documentation.

## Problem

The integration had only 8 log calls, all in `coordinator.py` (3 debug, 1 info, 4 warning). There was no logging in `__init__.py` or `config_flow.py`, and the computation pipeline was invisible at debug level. The "ignore" unavailable mode logged at info on every meter event, which was too chatty for production use.

## Design Decisions

### Log level policy

| Level   | Purpose                                        | Cadence           |
|---------|------------------------------------------------|-------------------|
| DEBUG   | Full computation pipeline, state transitions, skips, cooldown holds | Every meter event |
| INFO    | Charging started / stopped transitions only    | Rare (state flip) |
| WARNING | Real problems: unparsable values, action failures, unavailable meter in stop/fallback modes | Occasional faults |

### Logging wrapper (`_log.py`)

All modules obtain their logger via `_log.get_logger(__name__)` instead of `logging.getLogger(__name__)`. This gives us a single place to add structured output, rate-limiting, or other enhancements without touching every module.

### Balancer state sensor

A new diagnostic sensor (`sensor.*_balancer_state`) shows the operational state of the coordinator, mapping to the charger state machine in the README:

| State | Meaning |
|-------|---------|
| `stopped` | Target current is 0 A (overload or initial) |
| `active` | Target current > 0 and unchanged (steady state) |
| `adjusting` | Target current changed this cycle |
| `ramp_up_hold` | Increase blocked by cooldown |
| `disabled` | Load balancing switch off |

### Meter health and fallback sensors

Meter status and fallback info are separate from the balancer state, tracked by dedicated sensors:

| Entity | Type | Purpose |
|--------|------|---------|
| `binary_sensor.*_meter_status` | Connectivity | On = meter healthy, Off = unavailable |
| `binary_sensor.*_fallback_active` | Problem | On = fallback in effect, Off = normal |
| `sensor.*_configured_fallback` | Diagnostic | Shows configured behavior: stop/ignore/set_current |

### Key changes

1. **`_log.py`**: New logging wrapper module. Thin wrapper around `logging.getLogger()` for future extensibility.

2. **coordinator.py — `_recompute`**: Added a single debug log that emits the full pipeline values (`house_power`, `available`, `raw_target`, `clamped`, `final`) plus the `reason` on every recompute. This is the most valuable log for runtime debugging.

3. **coordinator.py — ramp-up cooldown**: Added a debug log when the cooldown holds the current at the previous value instead of allowing an increase.

4. **coordinator.py — `_resolve_fallback`**: Demoted the "ignore" mode log from `info` to `debug`. In ignore mode, this fires on every unavailable meter event and was unnecessarily noisy.

5. **coordinator.py — `_update_and_notify`**: Added info-level logs for charging start/stop transitions only. These are the only info logs in the coordinator — they fire only on state flips (active ↔ stopped), keeping the info-level log cadence very low. Also computes and stores the balancer operational state.

6. **coordinator.py — `_resolve_balancer_state`**: New method that determines the balancer state for the diagnostic sensor.

7. **coordinator.py — `manual_set_limit`**: Added debug log with requested and clamped values.

8. **coordinator.py — `_handle_power_change` and `async_recompute_from_current_state`**: Added debug logs for disabled-balancer skips and parameter-change recomputes.

9. **__init__.py**: Added debug logs for entry setup/unload and service calls.

10. **config_flow.py**: Added debug logs for config flow validation and entry creation.

11. **sensor.py**: New `EvLbBalancerStateSensor` diagnostic sensor.

12. **const.py**: New `STATE_*` constants for balancer operational states.

### Principle: avoid log overload

- **No info logs on steady-state meter events.** The debug-level `_recompute` log covers this.
- **No info logs for "ignore" unavailable mode.** This was the main noise source; demoted to debug.
- **Info only on state transitions.** Charging start/stop are the only user-visible events worth info-level logging.
- **Warnings only for actionable problems.** Unparsable values, failed actions, and unavailable meter in stop/fallback modes are the only warning triggers.

## Complete log rundown

### `coordinator.py` (14 log calls)

| Level | Location | Message | Cadence |
|-------|----------|---------|---------|
| DEBUG | `async_start` | Coordinator started — config params | Once at startup |
| DEBUG | `async_stop` | Coordinator stopped | Once at shutdown |
| DEBUG | `_handle_power_change` | Disabled — skipping | When disabled |
| WARNING | `_handle_power_change` | Could not parse meter value | Bad data |
| DEBUG | `async_recompute_from_current_state` | Parameter changed, disabled | When disabled |
| DEBUG | `async_recompute_from_current_state` | Power meter missing | On unavailable |
| DEBUG | `async_recompute_from_current_state` | Recomputing with last value | On param change |
| DEBUG | `manual_set_limit` | Manual override requested/clamped | On service call |
| DEBUG | `_resolve_fallback` | Ignoring (keeping last value) | On unavailable (ignore) |
| WARNING | `_resolve_fallback` | Applying fallback current | On unavailable (fallback) |
| WARNING | `_resolve_fallback` | Stopping charging (0 A) | On unavailable (stop) |
| DEBUG | `_recompute` | Full pipeline values | Every recompute |
| DEBUG | `_recompute` | Ramp-up cooldown holding | When held |
| INFO | `_update_and_notify` | Charging started/stopped | State flips |
| DEBUG | `_call_action` | Action executed | On action success |
| WARNING | `_call_action` | Action failed | On action failure |

### `__init__.py` (3 log calls)

| Level | Location | Message | Cadence |
|-------|----------|---------|---------|
| DEBUG | `async_setup_entry` | Entry set up | Once at startup |
| DEBUG | `handle_set_limit` | Service called | On service call |
| DEBUG | `async_unload_entry` | Entry unloaded | Once at shutdown |

### `config_flow.py` (2 log calls)

| Level | Location | Message | Cadence |
|-------|----------|---------|---------|
| DEBUG | `async_step_user` | Entity not found | On validation error |
| DEBUG | `async_step_user` | Creating entry | Once on setup |

## Documentation

- User guide: `docs/documentation/logging-guide.md`

## Tests

- 12 tests in `tests/test_logging.py` covering log levels
- 9 tests in `tests/test_balancer_state.py` covering balancer state sensor
