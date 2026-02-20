Title: PR-6 — Manual override + observability
Date: 2026-02-20
Author: copilot
Status: in-review
Summary: Records design decisions, implementation details, and lessons learned from the PR-6 milestone — adding the ev_lb.set_limit service and last_action_reason diagnostic sensor.

---

## Context

PR-6 implements the manual override and observability features as described in the MVP plan (`docs/documentation/milestones/01-2026-02-19-mvp-plan.md`). The goal was to expose an `ev_lb.set_limit` service for manual charger current overrides and add diagnostic state updates to help troubleshoot the balancing algorithm's behavior.

## What was built

### `ev_lb.set_limit` service

A new HA service that allows users or automations to manually set the charger current, bypassing the automatic balancing algorithm.

- **Input:** `current_a` (float) — the desired charging current in Amps.
- **Clamping:** The value is clamped to the charger's configured min/max limits. If it falls below `min_ev_current`, charging is stopped (target set to 0 A).
- **One-shot behavior:** The override applies immediately but is temporary. The next power-meter state change event resumes normal automatic balancing.
- **Action execution:** The service triggers the same charger action scripts (set_current, stop_charging, start_charging) as normal balancing transitions.

The service is registered once per domain (not per entry) and routes to every loaded coordinator. In the current single-charger architecture there is exactly one.

### `services.yaml`

A standard HA service description file was added to document the `set_limit` service in the HA developer tools UI. It describes the `current_a` field with a number selector (0–80 A, step 1).

### `last_action_reason` diagnostic sensor

A new diagnostic sensor (entity category: `diagnostic`) that shows why the charger current was last changed. Possible values:

| Reason | When set |
|---|---|
| `power_meter_update` | Normal power meter state change → balancing computation |
| `manual_override` | User called `ev_lb.set_limit` |
| `fallback_unavailable` | Power meter became unavailable/unknown → fallback applied |
| `parameter_change` | Runtime parameter changed (max charger current, min EV current, switch re-enabled) |

This sensor helps users and automators answer "why is my charger at X amps?" without inspecting logs.

### Constants

Four new reason constants added to `const.py`: `REASON_POWER_METER_UPDATE`, `REASON_MANUAL_OVERRIDE`, `REASON_FALLBACK_UNAVAILABLE`, `REASON_PARAMETER_CHANGE`. The service name `SERVICE_SET_LIMIT = "set_limit"` is also defined there.

### Coordinator changes

- The coordinator now tracks `last_action_reason` as a string attribute.
- A new `manual_set_limit(current_a)` method handles the set_limit service call by clamping the value and calling `_update_and_notify` with the `REASON_MANUAL_OVERRIDE` reason.
- `_recompute`, `_apply_fallback_current`, and `async_recompute_from_current_state` now pass the appropriate reason to `_update_and_notify`.
- `_update_and_notify` accepts an optional `reason` parameter and stores it in `self.last_action_reason`.

## Design decisions

1. **One-shot override.** The `set_limit` service applies a single override that is replaced on the next power-meter event. This avoids introducing "override mode" state management (which would need a separate mechanism to clear the override). Users who want persistent overrides can disable the balancer switch first.
2. **Service registered once per domain.** The service handler iterates all loaded coordinators, which currently is always one. This scales naturally to Phase 2 multi-charger support without needing per-entry services.
3. **Diagnostic entity category.** The `last_action_reason` sensor uses `EntityCategory.DIAGNOSTIC` so it does not clutter the main entity list in the HA UI — users who need it can find it under diagnostic entities.
4. **Reason tracking in coordinator, not entities.** The reason is stored as coordinator state, consistent with the existing pattern where sensors read from the coordinator. This avoids entity-level state management for diagnostic data.
5. **Service unregistration on last unload.** The service is removed when the last config entry is unloaded to avoid dangling service registrations.

## Test coverage

12 integration tests in `test_set_limit_service.py` covering:
- `set_limit` sets charger current to the requested value
- `set_limit` clamps at the charger maximum
- `set_limit` stops charging when below minimum EV current
- `set_limit` fires set_current action script (resume transition)
- `set_limit` fires stop_charging when below minimum
- One-shot: next power meter event resumes automatic balancing
- `last_action_reason` is `power_meter_update` after meter event
- `last_action_reason` is `manual_override` after set_limit
- `last_action_reason` is `fallback_unavailable` on unavailable meter
- `last_action_reason` is `parameter_change` after number entity update
- Service is registered on setup
- Service is removed on unload

All 104 tests pass (92 existing + 12 new). Existing entity count tests updated from 6 to 7 entities.

## Lessons learned

- **One-shot is simpler than persistent override.** A persistent override mode would require additional state management (override flag, clear mechanism, interaction with disable switch). The one-shot approach covers the primary use case (temporary adjustment) with minimal complexity.
- **Diagnostic sensor is lightweight.** Adding a string sensor with `EntityCategory.DIAGNOSTIC` provides useful troubleshooting data without adding complexity to the main entity list or the coordinator's computation logic.
- **Service registration pattern.** HA services are domain-scoped, not entry-scoped. Registering once on first entry load and unregistering when the last entry unloads is the standard pattern.

## What's next

- **PR-7-MVP: Test stabilization + HACS release readiness** — Finalize HACS requirements, complete integration tests, and prepare the first release.

---

## Changelog

- 2026-02-20: Initial version (PR-6 implementation complete).
