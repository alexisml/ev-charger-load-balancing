Title: 7-step timelapse integration test + extended ramp-up cooldown reset
Date: 2026-02-27
Author: copilot
Status: in-review
Summary: Adds a full stop→hold→dip→resume integration test exercising the charger status sensor at every step, and extends the ramp-up cooldown reset to trigger on any drop in available headroom from a previously usable level.

---

## Context

Two gaps were identified after the charger status sensor was merged (#79):

1. **No integration test covered the sensor's impact across a full session arc.**  
   `TestChargerStatusSensorMidSession` (in `test_integration_charger_sensor.py`) tested isolated scenarios (sensor flip mid-session, sensor preventing overshoot, charge/stop/resume) but no test walked through the complete sequence: charge at max → EV pauses while load spikes → charger stops → hold through cooldown with a mid-hold dip → resume at partial speed once cooldown expires → sensor transitions back to Charging → coordinator increases to max.

2. **The ramp-up cooldown did not restart when headroom worsened while the charger was already stopped.**  
   If the charger was at 0 A and the available headroom dropped from above `min_ev_current` to below it (a second "spike" scenario), the cooldown timer was unchanged. The balancer could attempt a restart before conditions had been stable for a full cooldown period.

---

## What was built

### 1. `TestChargingTimelapseWithIsChargingSensor` (7-step test)

Added to `tests/integration/test_integration_timelapse.py`.

| Step | Scenario | What it verifies |
|------|----------|-----------------|
| 1 | House-only meter → coordinator commands 16 A (full headroom, at max) | Cold-start with `sensor=Charging`; charger output = `max_charger_current` |
| 2 | `sensor→Available` + house spikes to 28 A → **stop** | With sensor: `ev_estimate=0`, available=4 A < 6 A → stops. Without sensor: `ev_estimate=16`, available=20 A → would **not** stop. |
| 3 | Charger stopped; headroom still below min | `sensor=Available`, `ev_estimate=0` throughout |
| 4 | Three meter updates, all below min | Loop confirms charger stays stopped |
| 5 | Headroom rises above min; cooldown (60 s) still active | Non-decreasing available values to avoid unintentional cooldown reset |
| 6 | Dips below min again (available dropped from 11 A → 3 A) | Cooldown timer **resets** to T=1055 |
| 7a | Back above min; only 10 s elapsed since reset | Still blocked |
| 7b | 61 s elapsed since reset → resumes at **9 A** (partial speed) | `current_set_a = 9`, `active = on`, `balancer_state = adjusting`, `current_set_a < max_charger_current` |
| 7c | `sensor→Charging`; house=2 A + EV=9 A → full headroom | `ev_estimate=9 A`, `coordinator.ev_charging=True`, target = 16 A (max) |

**Key setup detail:** The charger status entity must be pre-seeded (`hass.states.async_set`) before coordinator startup so the coordinator reads it on the first trigger. This is why the test uses inline setup rather than `setup_integration()`, consistent with `TestChargerStatusSensorMidSession`.

---

### 2. Extended ramp-up cooldown reset (`coordinator.py`)

**Before:**
```python
# Track reductions for ramp-up cooldown
if final_a < self.current_set_a:
    self._last_reduction_time = now
```

**After:**
```python
headroom_worsened = (
    available_a < self.available_current_a
    and self.available_current_a >= self.min_ev_current
)
if final_a < self.current_set_a or headroom_worsened:
    self._last_reduction_time = now
```

The `headroom_worsened` condition catches the case where the charger is already stopped (current = 0) but the available headroom shrinks further — a sign that conditions are still deteriorating. By resetting the cooldown timer, the coordinator ensures it must wait a full stable period before attempting a restart.

**Safety on first call:** `self.available_current_a` is always a `float` (initialised to `0.0`). On the very first `_recompute` call, `available_a ≥ 0 ≥ self.available_current_a`, so `headroom_worsened` is `False` by design — no spurious reset at startup.

---

### 3. Documentation updates (`docs/documentation/how-it-works.md`)

Three places updated to reflect the extended cooldown reset condition:

- **"What NOT to expect"** list item for "Increases are delayed" — added "or any drop in available headroom from a previously usable level".
- **"Simple version"** safety rules bullet point — added a fourth point explaining the cooldown timer reset condition.
- **"Why instant down, delayed up?"** section — expanded to enumerate both reset conditions (current drop, headroom worsening).
- **Number entity table** for `ramp_up_time` — updated description to include the headroom-drop trigger.

---

## Test impacts

- **`test_integration_spike_recovery.py`** — the second spike at T=1028 (available drops from 20 A to −3 A) now resets the cooldown timer. Phase 5 resume timestamp moved from T=1041 → T=1059 (31 s after the second spike). Class docstring updated.

- **`test_integration_timelapse.py` step 5 loop** — values changed to non-decreasing (10.5 A, 11.0 A) to avoid an unintentional cooldown reset mid-loop that would shift the step 7b resume time.

All other existing tests that use non-zero `ramp_up_time_s` were unaffected because their headroom never decreases from above min while the charger is already stopped.

---

## Design decisions

### Why `self.available_current_a >= self.min_ev_current` (not just `> 0`)?

The threshold for "previously usable headroom" is `min_ev_current` rather than `0` because headroom in the range `(0, min_ev_current)` already causes the charger to stop — it's not a valid charging window. Resetting the cooldown for decreases within that range would add noise without meaningful safety value (the charger is already stopped and would stay stopped regardless). The `min_ev_current` boundary is the natural semantic threshold.

### Why not reset on every headroom decrease?

If the cooldown reset on every decrease — including decreases from below min — it would reset perpetually in oscillating-load scenarios where the available current bounces between negative and small positive values. Using `self.available_current_a >= self.min_ev_current` as the guard ensures only meaningful (was-usable → now-worse) transitions trigger the reset.

---

## Lessons learned

- **Non-decreasing loop values in tests:** When asserting that the cooldown holds over multiple meter updates, the test values must be non-decreasing if any decrease from above `min_ev_current` would reset the timer. Discovered when step 5 loop's `9.0 A` (after `10.5 A`) would have reset the cooldown, breaking step 7b's timing.

- **Spike recovery test cascade:** A logic change in the cooldown reset condition affected an existing test (`test_integration_spike_recovery.py`) whose second spike scenario now resets the cooldown. Always grep for tests that depend on `_last_reduction_time` timing when changing that logic.

---

## Changelog

- 2026-02-27: Initial version (PR implementation complete, 351 tests passing).
