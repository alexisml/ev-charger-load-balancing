Title: PR-3 — Single-charger balancing engine
Date: 2026-02-20
Author: copilot
Status: in-review
Summary: Records design decisions, implementation details, and lessons learned from the PR-3 milestone — porting the balancing engine into the integration runtime.

---

## Context

PR-3 implements the single-charger balancing engine as described in the MVP plan (`docs/documentation/milestones/01-2026-02-19-mvp-plan.md`). The goal was to port the pure computation functions from `tests/load_balancer_core.py` into `custom_components/ev_lb/load_balancer.py` and wire them to HA state changes via a coordinator.

## What was built

### New modules

- **`load_balancer.py`** — `compute_available_current`, `clamp_current`, `apply_ramp_up_limit` ported into the integration package. The `distribute_current` water-filling algorithm was decomposed into three focused helpers (`_classify_chargers`, `_assign_final_shares`, `_settle_capped_and_below_min`) after review feedback.
- **`coordinator.py`** — `EvLoadBalancerCoordinator`: subscribes to power-meter entity via `async_track_state_change_event`, runs the balancing loop, tracks ramp-up cooldown, and publishes updates via `async_dispatcher_send`. Exposes `async_recompute_from_current_state()` for on-demand recomputation when runtime parameters change.

### Entity wiring

- **Sensors / binary sensor** subscribe to the coordinator's dispatcher signal and read computed state on update.
- **Number entities** push runtime values (`max_charger_current`, `min_ev_current`) into the coordinator and trigger immediate recomputation — no need to wait for the next meter event.
- **Switch** syncs the `enabled` flag; coordinator skips recomputation when disabled. Re-enabling triggers an immediate recomputation from the current meter state.
- **`__init__.py`** creates the coordinator before platform setup, starts the listener after entities load, and stops on unload.

### Event-driven triggers

Recomputation is triggered by any of the following events (no polling):

| Trigger | Effect |
|---|---|
| Power meter state change | Coordinator reads the new Watt value and runs the full balancing algorithm |
| Power meter unavailable/unknown | Coordinator applies the configured unavailable behavior (stop, ignore, or set current) |
| Max charger current changed | Number entity pushes new value and triggers immediate recomputation |
| Min EV current changed | Same — if the new minimum exceeds the current target, charging stops instantly |
| Load balancing re-enabled | Switch triggers immediate recomputation to catch up to current meter state |

### Power meter unavailable — three-mode behavior

A config selector `unavailable_behavior` controls what happens when the meter transitions to `unavailable` or `unknown`:

| Mode | Behavior |
|---|---|
| **Stop charging** (default) | Set to 0 A — safest when meter data is unreliable |
| **Ignore** | Keep last computed value unchanged |
| **Set a specific current** | Apply `min(configured_fallback, max_charger_current)` — capped at the physical charger limit |

When the meter recovers, normal computation resumes automatically.

### Home Assistant restart

All entities use `RestoreEntity` so their state survives restarts. The `current_set` sensor syncs its restored value back into the coordinator on startup. The next meter event triggers a normal recomputation.

## Design decisions

1. **Coordinator as authoritative state holder.** Entities are readers/writers, not owners. This avoids split-brain between entity state and coordinator state.
2. **`_time_fn` for deterministic testing.** The coordinator accepts a clock function that can be injected in tests, enabling precise control over ramp-up cooldown timing without real delays.
3. **Instant-down, delayed-up asymmetry.** Reductions are always applied immediately; increases are blocked during the ramp-up cooldown period. This prevents oscillation when household load fluctuates near the service limit.
4. **Immediate recomputation on input changes.** Runtime parameter changes (number entities, switch re-enable) call `async_recompute_from_current_state()` rather than waiting for the next power meter event. This gives the user instant feedback.
5. **Fallback current caps at `max_charger_current`, not `current_set_a`.** During meter unavailability, the "set current" mode caps at the physical charger limit — not the last balanced target — to allow users to set a meaningful fallback value.
6. **EV charger health not monitored.** The integration only controls target current; charger availability is the charger integration's responsibility (e.g., OCPP).

## Test coverage

22 integration tests in `test_balancing_engine.py` covering:
- Target computation from power meter events
- Charger maximum capping
- Overload → stop charging
- Instant current reduction
- Ramp-up cooldown hold and release
- Enabled/disabled switch behavior
- All three unavailable behavior modes (stop, ignore, set_current)
- Non-numeric meter values ignored
- Immediate recomputation on number entity changes
- Switch re-enable triggering recomputation
- Fallback current capping at charger maximum
- Meter recovery after unavailable

All 81 tests pass (59 existing + 22 new).

## Lessons learned

- **Break long functions early.** The `distribute_current` while-loop was flagged in review as too long. Extracting helper functions (`_classify_chargers`, `_assign_final_shares`, `_settle_capped_and_below_min`) improved readability and testability.
- **Config flow changes need test updates.** Adding new config parameters (unavailable behavior, fallback current) required updating `test_config_flow.py` expected data — easy to miss.
- **Three-mode behavior is better than a single fallback number.** The original design used a single `unavailable_fallback_current` number, but the dropdown selector with stop/ignore/set_current modes is more intuitive and covers more use cases.

## What's next

- **PR-4: Action execution contract** — Implement `set_current` / `stop_charging` / `start_charging` service calls with payload validation and error handling.
- **PR-5-MVP: Event notifications** — Fire HA events and persistent notifications for fault conditions (meter unavailable, overload/stop, charging resumed, fallback activated).

---

## Changelog

- 2026-02-20: Initial version (PR-3 implementation complete).
