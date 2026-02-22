# EV Charger Load Balancing — User Manual

This manual covers everything you need to install, configure, and use the **EV Charger Load Balancing** integration for Home Assistant.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Entities reference](#entities-reference)
5. [How it works](#how-it-works)
6. [Action scripts](#action-scripts)
7. [Event notifications](#event-notifications)
8. [Manual override](#manual-override)
9. [Power meter unavailable](#power-meter-unavailable)
10. [Logging and diagnostics](#logging-and-diagnostics)
11. [Troubleshooting](#troubleshooting)
12. [FAQ](#faq)

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Home Assistant 2023.6 or later | Core, OS, or Container install |
| [HACS](https://hacs.xyz/) 2.0 or later | For installing the integration |
| A power meter sensor | Any `sensor.*` entity reporting total household power in Watts |
| An EV charger integration | e.g. [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp), Modbus, REST API, or any charger controllable via HA scripts |

---

## Installation

### Via HACS (recommended)

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/alexisml/ha-ev-charger-balancer` as an **Integration**.
3. Search for **EV Charger Load Balancing** and click **Download**.
4. Restart Home Assistant.

### Manual installation

1. Copy the `custom_components/ev_lb/` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

After installation, the integration will be available in **Settings → Devices & Services → Add Integration**.

---

## Configuration

### Initial setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **EV Charger Load Balancing**.
3. Fill in the required fields:

| Field | Description | Default |
|---|---|---|
| **Power meter sensor** | A sensor entity reporting total household power consumption in Watts. | *(required)* |
| **Supply voltage** | Nominal supply voltage in Volts, used to convert W ↔ A. | 230 V |
| **Max service current** | Whole-house breaker rating in Amps. Charging will never exceed this limit. | 32 A |
| **When power meter is unavailable** | What to do when the meter stops reporting: **Stop charging** (safest), **Ignore** (keep last value), or **Set a specific current**. | Stop charging |
| **Fallback current** | Current to use when meter is unavailable and mode is "Set a specific current". Capped at the charger maximum. | 6 A |

4. Optionally configure action scripts (see [Action scripts](#action-scripts)):

| Field | Description |
|---|---|
| **Set current action** | Script entity called to set the charging current. |
| **Stop charging action** | Script entity called to stop charging. |
| **Start charging action** | Script entity called to start/resume charging. |

5. Click **Submit**. The integration creates a device and its entities immediately.

### Changing action scripts later

You can add, change, or remove action scripts at any time without recreating the integration:

1. Go to **Settings → Devices & Services → EV Charger Load Balancing**.
2. Click **Configure**.
3. Update the script selections.
4. Click **Submit**. The integration reloads automatically.

---

## Entities reference

All entities are grouped under a single device called **EV Charger Load Balancer** in Settings → Devices.

### Sensors

| Entity | Type | Description |
|---|---|---|
| `sensor.*_charging_current_set` | Measurement (A) | The last charging current sent to the charger (0 A when stopped). |
| `sensor.*_available_current` | Measurement (A) | Computed available headroom for EV charging based on the power meter and service limit. |
| `sensor.*_last_action_reason` | Diagnostic | Why the last recomputation happened: `power_meter_update`, `manual_override`, `fallback_unavailable`, or `parameter_change`. |
| `sensor.*_balancer_state` | Diagnostic | Operational state: `stopped`, `active`, `adjusting`, `ramp_up_hold`, or `disabled`. |
| `sensor.*_configured_fallback` | Diagnostic | Configured unavailable behavior: `stop`, `ignore`, or `set_current`. |

### Binary sensors

| Entity | Type | Description |
|---|---|---|
| `binary_sensor.*_load_balancing_active` | — | **On** when the balancer is actively controlling the charger (current > 0). |
| `binary_sensor.*_power_meter_status` | Connectivity | **On** = meter reporting valid readings. **Off** = unavailable. |
| `binary_sensor.*_meter_fallback_active` | Problem | **On** = meter-unavailable fallback in effect. **Off** = normal operation. |

### Number entities

| Entity | Range | Description |
|---|---|---|
| `number.*_max_charger_current` | 1–80 A | Per-charger upper current limit. Adjustable at runtime. |
| `number.*_min_ev_current` | 1–32 A | Lowest current at which the charger can operate (IEC 61851: 6 A). Below this, charging stops. |

### Switch

| Entity | Description |
|---|---|
| `switch.*_load_balancing_enabled` | Master enable/disable for load balancing. When **off**, power-meter events are ignored and no recomputation occurs. |

### Service

| Service | Description |
|---|---|
| `ev_lb.set_limit` | Manually override the charger current. Accepts `current_a` (float). The value is clamped to min/max range. The override is one-shot — the next power-meter event resumes automatic balancing. |

---

## How it works

### Decision loop

The balancer is **event-driven** — it does not poll on a timer. A recomputation is triggered by:

- **Power meter state change** — the sensor reports a new Watt value.
- **Max charger current changed** — the user or an automation adjusts the number entity.
- **Min EV current changed** — same as above.
- **Load balancing re-enabled** — the switch entity is turned back on.

On each trigger the balancer:

1. Computes available headroom: `available_a = service_current_a − house_power_w / voltage_v`
2. Computes target: `target_a = min(current_ev_a + available_a, max_charger_a)`, floored to 1 A steps.
3. If `target_a < min_ev_a` → **stop charging** (instant).
4. If target is lower than current → **reduce immediately** (instant).
5. If target is higher than current → **increase only if ramp-up cooldown has elapsed**.

### Key rules

- **Reductions are always instant.** The moment household load rises, the charger current is reduced on the very next event.
- **Increases are delayed** by `ramp_up_time_s` (default 30 s) after any reduction, preventing rapid oscillation.
- **Stopping** happens when even the minimum current would exceed the service limit.
- **Resuming** happens when available current rises above the minimum threshold and the cooldown has elapsed.

### Home Assistant restart

All entity states survive a restart via Home Assistant's **RestoreEntity** mechanism. On startup the coordinator waits for the first power-meter event before taking any action — the charger current stays at the last known value until fresh data arrives.

---

## Action scripts

Action scripts are the bridge between this integration and your physical charger hardware. The integration computes the optimal current and calls your scripts to execute the commands.

### Overview

| Action | When it fires | Variables passed |
|---|---|---|
| **Set current** | Target charging current changes | `current_a` (float), `charger_id` (string) |
| **Stop charging** | Headroom drops below minimum | `charger_id` (string) |
| **Start charging** | Charging can resume after being stopped | `charger_id` (string) |

All actions are **optional**. Without scripts, the integration runs in "compute-only" mode — it calculates and displays the target current via sensors but sends no commands.

### Creating scripts

Go to **Settings → Automations & Scenes → Scripts → + Add Script**.

#### Example: Set current (OCPP)

```yaml
alias: EV LB - Set Current
mode: single
fields:
  current_a:
    description: Target charging current in Amps
    selector:
      number:
        min: 0
        max: 80
        step: 1
        unit_of_measurement: A
  charger_id:
    description: Charger identifier
    selector:
      text:
sequence:
  - action: ocpp.set_charge_rate
    data:
      limit_amps: "{{ current_a }}"
```

#### Example: Stop charging (OCPP)

```yaml
alias: EV LB - Stop Charging
mode: single
fields:
  charger_id:
    description: Charger identifier
    selector:
      text:
sequence:
  - action: ocpp.set_charge_rate
    data:
      limit_amps: 0
```

#### Example: Start charging (OCPP)

```yaml
alias: EV LB - Start Charging
mode: single
fields:
  charger_id:
    description: Charger identifier
    selector:
      text:
sequence:
  - action: ocpp.reset
    data: {}
```

> **Tip:** Replace the `ocpp.*` actions with whatever services your charger integration exposes. See [Adapting for different chargers](#adapting-for-different-chargers) for more examples.

### Transition logic

| Previous state | New state | Actions fired |
|---|---|---|
| Stopped (0 A) | Charging (> 0 A) | `start_charging` → `set_current` |
| Charging (X A) | Stopped (0 A) | `stop_charging` |
| Charging (X A) | Charging (Y A, Y ≠ X) | `set_current` |
| No change | No change | *(no action)* |

When resuming, `start_charging` is called **before** `set_current` to ensure the charger is ready to accept a target.

### Error handling

- **Script not configured:** Silently skipped.
- **Script call fails:** A warning is logged and an `ev_lb_action_failed` event is fired. Other actions are not affected.
- **Script entity does not exist:** Treated as a call failure.

### Adapting for different chargers

#### REST API chargers

```yaml
# set_current
- action: rest_command.set_charger_current
  data:
    current: "{{ current_a }}"

# stop_charging
- action: rest_command.stop_charger

# start_charging
- action: rest_command.start_charger
```

#### Modbus chargers

```yaml
# set_current
- action: modbus.write_register
  data:
    hub: charger
    unit: 1
    address: 100
    value: "{{ (current_a * 10) | int }}"
```

#### Switch-based chargers

```yaml
# stop_charging
- action: switch.turn_off
  target:
    entity_id: switch.ev_charger

# start_charging
- action: switch.turn_on
  target:
    entity_id: switch.ev_charger
```

> **Note:** For switch-based chargers, `set_current` may not be applicable if the charger does not support current limiting. Only configure `stop_charging` and `start_charging`.

---

## Event notifications

The integration fires Home Assistant bus events when notable conditions occur. Use these in automations for mobile alerts, dashboard indicators, or any notification platform.

### Event types

| Event type | When it fires | Persistent notification |
|---|---|---|
| `ev_lb_meter_unavailable` | Power meter becomes unavailable (stop mode) | ✅ Created |
| `ev_lb_overload_stop` | Household load exceeds service limit, charging stopped | ✅ Created |
| `ev_lb_fallback_activated` | Meter unavailable, fallback current applied (set-current mode) | ✅ Created |
| `ev_lb_charging_resumed` | Charging resumes after being stopped | ❌ (dismisses overload notification) |
| `ev_lb_action_failed` | A charger action script fails | ❌ |

### Event payloads

#### `ev_lb_meter_unavailable`

| Field | Type | Description |
|---|---|---|
| `entry_id` | string | Config entry ID |
| `power_meter_entity` | string | Entity ID of the power meter |

#### `ev_lb_overload_stop`

| Field | Type | Description |
|---|---|---|
| `entry_id` | string | Config entry ID |
| `previous_current_a` | float | Current before the stop (A) |
| `available_current_a` | float | Computed headroom at the time of stop (A) |

#### `ev_lb_fallback_activated`

| Field | Type | Description |
|---|---|---|
| `entry_id` | string | Config entry ID |
| `power_meter_entity` | string | Entity ID of the power meter |
| `fallback_current_a` | float | Fallback current applied (A) |

#### `ev_lb_charging_resumed`

| Field | Type | Description |
|---|---|---|
| `entry_id` | string | Config entry ID |
| `current_a` | float | New charging current (A) |

#### `ev_lb_action_failed`

| Field | Type | Description |
|---|---|---|
| `entry_id` | string | Config entry ID |
| `action_name` | string | Failed action (`set_current`, `stop_charging`, or `start_charging`) |
| `entity_id` | string | Entity ID of the failed script |
| `error` | string | Error message |

### Persistent notifications

Fault conditions (meter unavailable, overload, fallback) create persistent notifications on the HA dashboard. They are automatically dismissed when the fault resolves — no user action needed.

### Automation examples

#### Mobile notification on overload

```yaml
automation:
  - alias: "EV charger overload alert"
    trigger:
      - platform: event
        event_type: ev_lb_overload_stop
    action:
      - action: notify.mobile_app_my_phone
        data:
          title: "EV Charger — Overload"
          message: >
            Charging stopped. Was {{ trigger.event.data.previous_current_a }} A,
            available headroom: {{ trigger.event.data.available_current_a }} A.
```

#### Mobile notification on meter unavailable

```yaml
automation:
  - alias: "EV charger meter lost"
    trigger:
      - platform: event
        event_type: ev_lb_meter_unavailable
    action:
      - action: notify.mobile_app_my_phone
        data:
          title: "EV Charger — Meter Lost"
          message: >
            Power meter {{ trigger.event.data.power_meter_entity }} is
            unavailable. Charging has been stopped for safety.
```

#### Combined alert for all fault events

```yaml
automation:
  - alias: "EV charger fault alert"
    trigger:
      - platform: event
        event_type: ev_lb_meter_unavailable
      - platform: event
        event_type: ev_lb_overload_stop
      - platform: event
        event_type: ev_lb_fallback_activated
    action:
      - action: notify.mobile_app_my_phone
        data:
          title: "EV Charger Alert"
          message: "Event: {{ trigger.event.event_type }}"
```

#### Notification on action script failure

```yaml
automation:
  - alias: "EV charger action failed"
    trigger:
      - platform: event
        event_type: ev_lb_action_failed
    action:
      - action: notify.mobile_app_my_phone
        data:
          title: "EV Charger — Script Failed"
          message: >
            Action {{ trigger.event.data.action_name }} failed
            ({{ trigger.event.data.entity_id }}):
            {{ trigger.event.data.error }}
```

> **Tip:** Test your automations by firing a test event from **Developer Tools → Events** (e.g., `ev_lb_overload_stop` with a sample payload) before a real fault occurs.

---

## Manual override

The `ev_lb.set_limit` service lets you manually set the charger current from automations, scripts, or Developer Tools:

```yaml
action: ev_lb.set_limit
data:
  current_a: 16
```

**Behavior:**
- The value is clamped to the charger's min/max range.
- If the value falls below the minimum EV current, charging is stopped.
- The override is **one-shot** — the next power-meter event resumes automatic balancing.

**Use case:** Temporarily limit charging during a high-demand period via an automation, then let the balancer resume normal operation automatically.

---

## Power meter unavailable

When the power meter entity transitions to `unavailable` or `unknown`, the balancer can no longer compute headroom. The behavior depends on the configured setting:

| Mode | Behavior |
|---|---|
| **Stop charging** (default) | Charger is immediately set to 0 A — safest option. |
| **Ignore** | Keep the last computed current. Useful if brief meter dropouts are common. |
| **Set a specific current** | Apply the configured fallback current, **capped at the charger maximum**. For example: if max charger current is 32 A and fallback is 50 A, the charger is set to 32 A. |

When the meter recovers, normal computation resumes automatically on the next state change.

> **Tip:** The `binary_sensor.*_power_meter_status` and `binary_sensor.*_meter_fallback_active` sensors let you monitor meter health in dashboards and automations without enabling debug logs.

> **Note:** The EV charger device itself is not monitored by this integration. The integration only controls the *target current* it sends. Charger health monitoring is the responsibility of the charger integration (e.g., OCPP).

---

## Logging and diagnostics

### Enabling debug logs

Add the following to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ev_lb: debug
```

Restart Home Assistant. Debug logs appear in **Settings → System → Logs**.

### Log levels

| Level | What gets logged | Cadence |
|---|---|---|
| **DEBUG** | Full computation pipeline, ramp-up holds, skips, overrides | Every meter event |
| **INFO** | Charging started/stopped transitions | State flips only |
| **WARNING** | Unparsable meter values, action failures, meter unavailable in stop/fallback modes | On faults only |

### Example log output

**INFO** (default) — very quiet:
```
INFO  Charging started at 18.0 A
INFO  Charging stopped (was 18.0 A, reason=power_meter_update)
```

**DEBUG** — full pipeline:
```
DEBUG Recompute (power_meter_update): house=3000 W, available=19.0 A, raw_target=19.0 A, clamped=18.0 A, final=18.0 A
DEBUG Ramp-up cooldown holding current at 17.0 A (target 32.0 A)
DEBUG Power meter changed but load balancing is disabled — skipping
DEBUG Manual override: requested=20.0 A, clamped=20.0 A
```

**WARNING** — problems:
```
WARNING Could not parse power meter value: not_a_number
WARNING Power meter sensor.house_power is unavailable — stopping charging (0 A)
WARNING Action set_current failed via script.ev_set_current: Service not found
```

### Diagnostic sensors

Instead of (or in addition to) debug logs, use the diagnostic sensors for dashboards and automations:

| Entity | Purpose |
|---|---|
| `sensor.*_balancer_state` | Operational state: `stopped`, `active`, `adjusting`, `ramp_up_hold`, `disabled` |
| `sensor.*_configured_fallback` | Configured fallback behavior: `stop`, `ignore`, `set_current` |
| `binary_sensor.*_power_meter_status` | **On** = meter healthy, **Off** = unavailable |
| `binary_sensor.*_meter_fallback_active` | **On** = fallback active, **Off** = normal operation |

---

## Troubleshooting

### The integration does not appear in Add Integration

- Verify the `custom_components/ev_lb/` folder exists in your HA config directory.
- Check that you restarted Home Assistant after installation.
- Look in **Settings → System → Logs** for any errors mentioning `ev_lb`.

### "Already configured" error

The integration supports only one instance. If you see this error, the integration is already set up. Go to **Settings → Devices & Services** to find it.

### Power meter entity not found

During setup, the selected sensor must exist and be a `sensor.*` entity. Check that:
1. The sensor is visible in **Developer Tools → States**.
2. It reports a numeric value in Watts.

### Charger does not respond to commands

1. Verify your action scripts work independently: go to **Developer Tools → Services**, call `script.turn_on` for your script entity, and check the charger.
2. Confirm the scripts are selected in the integration configuration (**Settings → Devices & Services → EV Charger Load Balancing → Configure**).
3. Check **Settings → System → Logs** for warnings about failed actions.

### Current never increases after a reduction

The ramp-up cooldown (default 30 seconds) must elapse before the current is allowed to increase. This is by design to prevent oscillation. Wait for the cooldown to pass and for the next power-meter event.

### Sensors show "unavailable" after restart

Entity states are restored from the last known value. If sensors show "unavailable", it usually means:
- The integration failed to load — check **Settings → System → Logs**.
- The config entry was removed — re-add the integration.

### Actions fire but the wrong current is set

Verify:
1. The `current_a` variable is being used correctly in your script (e.g., `{{ current_a }}` in YAML templates).
2. Your charger integration accepts current in Amps (some use tenths of Amps — multiply accordingly in the script).

---

## FAQ

### Can I use this without a physical charger?

Yes. Without action scripts configured, the integration runs in "compute-only" mode. It calculates and displays the target current via sensor entities, which you can use in your own automations or dashboards.

### Does it work with solar/battery systems?

Yes, as long as you have a sensor reporting total household power in Watts. The integration does not differentiate between grid, solar, or battery power — it uses the total metered value to compute headroom.

### Can I use multiple chargers?

Not yet. The current version supports **one charger per instance**, and only one instance is allowed. Multi-charger support with per-charger prioritization is planned for Phase 2. See the [multi-charger plan](milestones/02-2026-02-19-multi-charger-plan.md).

### What happens during a Home Assistant restart?

All entity states are restored. The charger current stays at its last known value until the first power-meter event triggers a new computation. No commands are sent until fresh meter data arrives.

### How fast does it react to load changes?

The balancer is event-driven and reacts on the same HA event-loop tick as the power-meter state change. Reductions are instant. Increases are subject to the ramp-up cooldown (default 30 s).

### Can I change the ramp-up cooldown time?

The ramp-up cooldown is currently fixed at 30 seconds. A future update may expose this as a configurable option.

### Is it safe?

The integration prioritizes safety:
- **Reductions are always instant** — overloads are resolved immediately.
- **The default meter-unavailable behavior is "stop charging"** — if the meter goes offline, charging stops.
- **Power readings above 200 kW are rejected** as sensor errors.
- **Fallback current is always capped at the charger maximum** — even a misconfigured fallback cannot exceed the physical limit.

However, this integration is provided **as-is** without warranty. Always audit the code and test with your specific hardware before relying on it.

### Where can I report bugs or request features?

Open an issue at [github.com/alexisml/ha-ev-charger-balancer/issues](https://github.com/alexisml/ha-ev-charger-balancer/issues).
