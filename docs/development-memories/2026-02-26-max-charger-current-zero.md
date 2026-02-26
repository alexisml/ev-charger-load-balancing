Title: Allow max charger current to be set to 0 A (stop charging)
Date: 2026-02-26
Author: copilot
Status: merged
Summary: Records design decisions and implementation details for the PR that allows `max_charger_current` to be set to 0 A as a way to stop charging and bypass the load-balancing algorithm entirely.

---

## Context

The `max_charger_current` number entity had a minimum of 1 A. This prevented users from using it as a simple stop-charging toggle — for example, an automation that sets max to 0 when solar surplus disappears, or a time-of-use automation that wants to halt charging without disabling the entire integration.

The request was: setting max charger current to 0 should stop charging immediately. Load balancing should be bypassed, and both output current (A) and output power (W) should be 0.

---

## Design decision: bypass vs. treat 0 as "below min"

Two approaches were considered:

1. **Treat 0 A like any other max below `min_ev_current`.** The existing `clamp_current` function already returns `None` (stop) when `available_a < min_charger_a`. Setting `max_charger_a = 0` would result in a target of `None` → 0 A. This requires no new logic — just lowering `MIN_CHARGER_CURRENT` from 1 to 0.

2. **Explicit early exit before the balancing algorithm.** Add an `if max_charger_current == 0.0` guard at the top of `_recompute()` that skips the entire computation and calls `_update_and_notify(0.0, 0.0)` directly.

**Chosen: explicit early exit (option 2).**

Rationale:
- The problem statement says "no load balancing is needed when set to 0" — so the intent is not just "clamp to 0" but "skip the algorithm entirely".
- The early exit makes the intent clear in code without relying on implicit interactions between `max_charger_a = 0` and `clamp_current`.
- It avoids dividing by or subtracting zero in any intermediate step.
- Both approaches were combined: lower `MIN_CHARGER_CURRENT` to 0 (to open the UI range), **and** add the early exit (to make the behavior explicit and correct).

---

## What was changed

### `const.py`

- `MIN_CHARGER_CURRENT` changed from `1.0` → `0.0`.
- This directly controls `_attr_native_min_value` on `EvLbMaxChargerCurrentNumber`, so the UI input range becomes `0–80 A`.

### `coordinator.py` — `_recompute()`

Early exit added at the very top:

```python
def _recompute(self, service_power_w: float, reason: str = REASON_POWER_METER_UPDATE) -> None:
    if self.max_charger_current == 0.0:
        _LOGGER.debug("Max charger current is 0 A — skipping load balancing, outputting 0 A")
        self._update_and_notify(0.0, 0.0, reason)
        return
    # ... normal balancing continues
```

The early exit applies to all callers of `_recompute()`:
- Power meter state change events (`_handle_power_change`)
- Parameter-change recomputes (`async_recompute_from_current_state`)
- Overload correction loop callbacks (`_on_overload_triggered`, `_overload_loop_callback`, `_force_recompute_from_meter`)

This means subsequent power meter events also output 0 A while max is 0.

### `tests/integration/test_integration_input_boundaries.py`

- `TestMaxChargerCurrentBoundaries` class docstring updated: range is now `0–80 A`.
- Old `test_set_exactly_at_minimum_limit` (testing 1 A) replaced with:
  - `test_set_exactly_at_minimum_limit_stops_charging`: tests 0 A → charging stops immediately (early exit path).
  - `test_set_to_one_amp_still_stops_below_min_ev`: tests 1 A → load balancer runs but stops because 1 A < min_ev (6 A).
- New `test_max_zero_bypasses_load_balancing_on_meter_update`: verifies that subsequent power meter updates also output 0 A when max is 0 (not just on parameter change).

### `docs/documentation/how-it-works.md`

- Entity reference table: range updated to `0–80 A`, description updated to note that 0 A stops charging.
- Event triggers table: `Max charger current changed` row updated to explain the 0 A bypass.
- Balancer states table: `stopped` row updated to mention max = 0 as a cause.
- Solar surplus example: note added that surplus reaching 0 stops charging.
- Time-of-use example: updated to show `max = 0` as the preferred "stop during peak" pattern instead of toggling the enabled switch.

---

## Lessons learned

- The early exit guard on `max_charger_current == 0.0` is a cleaner design than allowing zero to propagate through `clamp_current`. It makes the 0 = "off" semantics explicit and prevents any accidental computation with a zero max value.
- Lowering `MIN_CHARGER_CURRENT` to 0 without adding the early exit would have worked correctly via `clamp_current`, but would have required understanding that interaction — harder to reason about.
