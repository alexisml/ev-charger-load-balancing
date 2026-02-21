# Logging Guide

This guide explains how to use and interpret logs from the EV Charger Load Balancing integration.

## Enabling debug logs

Add the following to your Home Assistant `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ev_lb: debug
```

Restart Home Assistant. Debug-level logs for this integration will appear in **Settings → System → Logs**.

To go back to normal (info-level) logging, change `debug` to `info` or remove the entry.

## Log level policy

| Level     | What gets logged                                            | Cadence            |
|-----------|-------------------------------------------------------------|--------------------|
| **DEBUG** | Full computation pipeline, ramp-up holds, skips, overrides  | Every meter event  |
| **INFO**  | Charging started / stopped transitions                      | State flips only   |
| **WARNING** | Unparsable meter values, action script failures, meter unavailable in stop/fallback modes | On faults only |

### What you'll see at each level

**INFO** (default) — very quiet, only state transitions:
```
INFO  Charging started at 18.0 A
INFO  Charging stopped (was 18.0 A, reason=power_meter_update)
```

**DEBUG** — full pipeline on every recompute cycle:
```
DEBUG Recompute (power_meter_update): house=3000 W, available=19.0 A, raw_target=19.0 A, clamped=18.0 A, final=18.0 A
```

When ramp-up cooldown blocks an increase:
```
DEBUG Ramp-up cooldown holding current at 17.0 A (target 32.0 A)
```

When load balancing is disabled:
```
DEBUG Power meter changed but load balancing is disabled — skipping
```

Manual override via `ev_lb.set_limit`:
```
DEBUG Manual override: requested=20.0 A, clamped=20.0 A
```

**WARNING** — problems that need attention:
```
WARNING Could not parse power meter value: not_a_number
WARNING Power meter sensor.house_power is unavailable — stopping charging (0 A)
WARNING Action set_current failed via script.ev_set_current: Service not found
```

## Balancer state sensor

The integration exposes a **Balancer state** diagnostic sensor (`sensor.*_balancer_state`) that shows the current operational state. This maps to the charger state machine described in the README:

| State                        | Meaning                                                    |
|------------------------------|------------------------------------------------------------|
| `stopped`                    | Target current is 0 A (overload or initial state)          |
| `active`                     | Target current > 0 and unchanged this cycle (steady state) |
| `adjusting`                  | Target current changed this cycle                          |
| `ramp_up_hold`               | Increase blocked by ramp-up cooldown                       |
| `meter_unavailable_stopped`  | Power meter unavailable — charging stopped (0 A)           |
| `meter_unavailable_fallback` | Power meter unavailable — fallback current applied         |
| `meter_unavailable_ignored`  | Power meter unavailable — keeping last value               |
| `disabled`                   | Load balancing switch is off                               |

Use this sensor in automations or dashboards to monitor the integration's behavior without enabling debug logs.

## Logging wrapper

All modules in this integration obtain their logger via `custom_components.ev_lb._log.get_logger()` instead of calling `logging.getLogger()` directly. This gives us a single place to change logging behavior in the future (structured output, rate-limiting, etc.) without modifying every module.
