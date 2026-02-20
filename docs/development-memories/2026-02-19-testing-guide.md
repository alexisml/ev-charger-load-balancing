Title: Testing guide — Custom integration, AppDaemon app, and blueprint in Home Assistant
Date: 2026-02-19
Author: alexisml
Status: draft
Summary: Step-by-step instructions for testing the EV charger load-balancing custom integration (and legacy AppDaemon/blueprint approaches) in a live Home Assistant instance.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Home Assistant (2023.6+) | Core, OS, or Container install |
| HACS | For installing the custom integration |
| Python 3.12+ and pytest | For running unit tests locally |
| AppDaemon 4.x | (Legacy) Only needed if testing the old AppDaemon prototype |
| lbbrhzn/ocpp integration | Optional — needed for real OCPP charger; for testing you can use helper scripts instead |

---

## 1 — Create the required HA helpers

Go to **Settings → Devices & Services → Helpers** and create the following helpers.  
These correspond to the entities referenced in `apps/ev_lb/ev_lb.yaml`.

| Helper type | Entity ID | Initial value | Notes |
|---|---|---|---|
| `input_number` | `input_number.ev_lb_max_service_current_a` | 32 | Whole-house breaker rating (A) |
| `input_number` | `input_number.ev_lb_min_current_before_shutdown_a` | 6 | Min before charger is stopped (A) |
| `input_number` | `input_number.ev_lb_max_charging_current_a_charger_1` | 16 | Per-charger cap (A) |
| `input_number` | `input_number.ev_lb_voltage_v` | 230 | Supply voltage (V) |
| `input_boolean` | `input_boolean.ev_lb_enabled` | on | Global enable/disable |

> **Tip:** You can also create these helpers in YAML under `configuration.yaml`:
> ```yaml
> input_number:
>   ev_lb_max_service_current_a:
>     min: 1
>     max: 100
>     step: 1
>     initial: 32
>     unit_of_measurement: A
>   ev_lb_min_current_before_shutdown_a:
>     min: 1
>     max: 32
>     step: 1
>     initial: 6
>     unit_of_measurement: A
>   ev_lb_max_charging_current_a_charger_1:
>     min: 1
>     max: 32
>     step: 1
>     initial: 16
>     unit_of_measurement: A
>   ev_lb_voltage_v:
>     min: 100
>     max: 480
>     step: 1
>     initial: 230
>     unit_of_measurement: V
> input_boolean:
>   ev_lb_enabled:
>     initial: on
> ```

---

## 2 — Create stub scripts for set_current / stop / start

Because a real OCPP charger is not required for functional testing, create three HA scripts that simply log the call.

Add to `scripts.yaml` (or create in the Scripts UI):

```yaml
ev_lb_set_current_charger_1:
  alias: "[EV LB] Set current – charger 1 (stub)"
  variables:
    charger_id: ""
    current_a: 0
  sequence:
    - service: system_log.write
      data:
        message: "ev_lb set_current: charger={{ charger_id }} current={{ current_a }} A"
        level: info

ev_lb_stop_charging_charger_1:
  alias: "[EV LB] Stop charging – charger 1 (stub)"
  variables:
    charger_id: ""
  sequence:
    - service: system_log.write
      data:
        message: "ev_lb stop_charging: charger={{ charger_id }}"
        level: info

ev_lb_start_charging_charger_1:
  alias: "[EV LB] Start charging – charger 1 (stub)"
  variables:
    charger_id: ""
  sequence:
    - service: system_log.write
      data:
        message: "ev_lb start_charging: charger={{ charger_id }}"
        level: info
```

Reload scripts (`Developer Tools → YAML → Scripts`).

---

## 3 — Create a simulated power sensor

If you don't have a real power meter, add a `template` sensor that you can control from Developer Tools:

```yaml
# configuration.yaml
template:
  - sensor:
      - name: "House Power W (simulated)"
        unique_id: house_power_w_sim
        state: "{{ states('input_number.sim_house_power_w') | float(0) }}"
        unit_of_measurement: W
        device_class: power
        state_class: measurement

input_number:
  sim_house_power_w:
    min: 0
    max: 20000
    step: 100
    initial: 3000
    unit_of_measurement: W
```

Use `input_number.sim_house_power_w` to drive the simulated load in tests.

---

## 4 — Install the AppDaemon app

1. Copy `apps/ev_lb/ev_lb.py` and `apps/ev_lb/ev_lb.yaml` into your AppDaemon `apps/` directory.  
   If using the HA add-on, this is typically `/config/appdaemon/apps/ev_lb/`.

2. Edit `ev_lb.yaml` to point `power_sensor` at your sensor:
   ```yaml
   power_sensor: sensor.house_power_w_simulated  # or your real sensor
   ```

3. Restart AppDaemon (add-on restart or `appdaemon --config /path/to/config`).

4. Check the AppDaemon log for:
   ```
   INFO AppDaemon: EVChargerLoadBalancer initialised
   ```

---

## 5 — Run the tests

### 5a — Unit tests (no HA required)

```bash
pip install -r tests/requirements.txt
python -m pytest tests/ -v
```

Expected: all 104 tests pass (39 load-balancer logic + 4 config flow + 2 integration setup/unload + 15 entity platform tests + 22 balancing engine tests + 11 action execution tests + 12 set-limit service and observability tests).

### 5b — Config flow tests only

```bash
python -m pytest tests/test_config_flow.py tests/test_init.py -v
```

These tests use `pytest-homeassistant-custom-component` to simulate the HA config flow UI.

### 5c — Functional tests in HA (legacy AppDaemon approach)

Open **Developer Tools → Template** and run the following to verify helpers are readable:

```jinja2
max service: {{ states('input_number.ev_lb_max_service_current_a') }} A
voltage:     {{ states('input_number.ev_lb_voltage_v') }} V
enabled:     {{ states('input_boolean.ev_lb_enabled') }}
```

#### Test 1 — Normal load (current set)

1. Set `input_number.sim_house_power_w` to **3000** W (≈ 13 A at 230 V).
2. Expected available = 32 − 13 ≈ **19 A** → clamped to charger max (16 A).
3. In HA **Logbook** or `appdaemon.log` you should see:  
   `ev_lb set_current: charger=charger_1 current=16.0 A`
4. Check `sensor.ev_lb_charger_1_current_set` = **16**.
5. Check `binary_sensor.ev_lb_charger_1_active` = **on**.

#### Test 2 — High household load (current reduced instantly)

**Reductions are applied immediately** — no ramp-up delay applies when going down.

1. Set `input_number.sim_house_power_w` to **6500** W (≈ 28.3 A at 230 V, including 16 A EV).  
   Non-EV load ≈ 28.3 − 16 = 12.3 A → available ≈ 32 − 12.3 = **19.7 A** → 16 A (capped).
2. Push higher: set to **8000** W.  
   Non-EV load ≈ (8000 − 16×230)/230 ≈ 18.8 A → available ≈ 32 − 18.8 = **13.2 A** → 13 A.
3. Verify `sensor.ev_lb_charger_1_current_set` drops to **13** on the very next power-meter state change (no delay).

#### Test 3 — Overload → stop charging (instant)

1. Set `input_number.sim_house_power_w` to **9500** W.  
   Non-EV load ≈ (9500 − 0)/230 ≈ 41.3 A > 32 A → available < 0 → below min.
2. Log should show immediately: `ev_lb stop_charging: charger=charger_1`
3. `sensor.ev_lb_charger_1_current_set` = **0**, `binary_sensor.ev_lb_charger_1_active` = **off**.

#### Test 4 — Ramp-up cooldown (asymmetric: down is instant, up is delayed)

This test demonstrates the deliberate asymmetry: reductions are instant, increases are held for `ramp_up_time_s` (default 30 s) to prevent oscillation.

1. Start from Test 3's state (charger stopped, load = 9500 W).
2. Immediately set `input_number.sim_house_power_w` back to **3000** W.
3. **Within the first 30 s**: `sensor.ev_lb_charger_1_current_set` should stay at **0** (held, not yet allowed to ramp up).
4. **After 30 s**: the current should increase back toward 16 A on the next power-meter event.  
   Watch `sensor.ev_lb_charger_1_current_set` in **History** — you will see the step-up happen after the cooldown.

> **Tip for faster testing:** Temporarily set `ramp_up_time_s: 5` in `ev_lb.yaml` and restart AppDaemon to reduce the wait to 5 seconds.

#### Test 5 — Disable load balancing

1. Turn `input_boolean.ev_lb_enabled` **off**.
2. Change `input_number.sim_house_power_w` — no service calls should appear in the log.
3. Re-enable and verify normal operation resumes.

#### Test 6 — Runtime voltage change

1. Change `input_number.ev_lb_voltage_v` from 230 to **120** (simulating a North-American setup).
2. At 3000 W and 120 V: non-EV = 25 A, available = 32 − 25 = **7 A** → charger gets 7 A.
3. Verify `sensor.ev_lb_charger_1_current_set` = **7**.

---

## 6 — Testing the blueprint (no AppDaemon)

1. In HA go to **Settings → Automations & Scenes → Blueprints → Import blueprint**.
2. Paste the raw URL of `blueprints/automation/ev_lb/ev_charger_load_balancing.yaml`  
   (or upload the file directly if using a local instance).
3. Create an automation from the blueprint, filling in:
   - Power sensor: `sensor.house_power_w_simulated`
   - Voltage: `230`
   - Max service current: `input_number.ev_lb_max_service_current_a`
   - Charger max current: `input_number.ev_lb_max_charging_current_a_charger_1`
   - Min current: `6`
   - Enabled toggle: `input_boolean.ev_lb_enabled`
   - Set current script: `script.ev_lb_set_current_charger_1`
   - Stop charging script: `script.ev_lb_stop_charging_charger_1`
   - Charger entity: `charger_1`
4. Repeat tests 1–3 from section 5b above; verify the same stub script logs appear.

> **Note:** The blueprint does not support the ramp-up cooldown (Test 4) or runtime voltage change (Test 6) — these require the AppDaemon app.

---

## 7 — Checking logs

| Location | How to open |
|---|---|
| AppDaemon log | Add-on UI → Log tab; or `/config/appdaemon/logs/main.log` |
| HA system log | **Settings → System → Logs** |
| HA Logbook | **Logbook** page filtered by `ev_lb` |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "EVChargerLoadBalancer initialised" not in log | App not loaded | Check YAML syntax in `ev_lb.yaml`; check AppDaemon apps directory path |
| No service calls logged | `input_boolean.ev_lb_enabled` is off or power sensor state is `unavailable` | Check helper states in Developer Tools |
| Current never increases after reduction | `ramp_up_time_s` not yet elapsed | Wait 30 s (default) or lower `ramp_up_time_s` in `ev_lb.yaml` for testing |
| Wrong current values | `voltage_input` or `voltage_v` mismatch | Verify `input_number.ev_lb_voltage_v` matches your actual supply voltage |
