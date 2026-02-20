Title: PR-4 — Action execution contract
Date: 2026-02-20
Author: copilot
Status: in-review
Summary: Records design decisions, implementation details, and lessons learned from the PR-4 milestone — implementing the action execution contract for charger control.

---

## Context

PR-4 implements the action execution contract as described in the MVP plan (`docs/documentation/milestones/01-2026-02-19-mvp-plan.md`). The goal was to wire the coordinator's computed state transitions into actual charger control by calling user-configured HA scripts for `set_current`, `stop_charging`, and `start_charging`.

## What was built

### Config flow additions

Three new optional entity selector fields were added to the config flow:

- **`action_set_current`** — script entity called to set the charging current; receives a `current_a` variable.
- **`action_stop_charging`** — script entity called to stop charging when headroom is insufficient.
- **`action_start_charging`** — script entity called to start/resume charging after a stop.

All three are optional, preserving backward compatibility with existing config entries that predate PR-4.

### Coordinator changes

The coordinator now tracks state transitions and executes the appropriate actions:

| Transition | Actions fired |
|---|---|
| **Resume** (was stopped, now active) | `start_charging`, then `set_current(current_a)` |
| **Stop** (was active, now stopped) | `stop_charging` |
| **Adjust** (active, current changed) | `set_current(current_a)` |
| **No change** | No action |

A new `_update_and_notify` method consolidates state updates, action scheduling, and entity notification. Actions are scheduled via `hass.async_create_task` from the synchronous callback context and execute as an ordered async coroutine.

### Error handling

- When an action script is not configured, the call is silently skipped.
- When a service call fails (e.g., script entity does not exist), a warning is logged but the coordinator continues operating. A single broken script does not prevent other actions from executing.
- The `HomeAssistantError` exception type is caught specifically.

### Payload format

- `set_current` receives: `{"entity_id": "<script_entity>", "variables": {"current_a": <float>, "charger_id": "<entry_id>"}}`
- `stop_charging` receives: `{"entity_id": "<script_entity>", "variables": {"charger_id": "<entry_id>"}}`
- `start_charging` receives: `{"entity_id": "<script_entity>", "variables": {"charger_id": "<entry_id>"}}`

Scripts are called via `hass.services.async_call("script", "turn_on", ...)` with `blocking=True`.

## Design decisions

1. **All actions optional.** Making all three actions optional preserves backward compatibility and allows the integration to work in "compute-only" mode (displaying sensor values without charger control). This avoids the need for config entry migration.
2. **Script entity selector.** Using `EntitySelector(domain="script")` gives the user a dropdown of existing scripts in the HA UI, which is more discoverable than a free-text field.
3. **Ordered execution via single coroutine.** The `_execute_actions` method is a single async coroutine that awaits each action in order. This ensures `start_charging` completes before `set_current` during resume transitions.
4. **`charger_id` in all payloads.** Every action receives a `charger_id` variable (the config entry ID) so scripts can address the correct charger. This prepares for Phase 2 multi-charger support.
5. **Action scheduling via `async_create_task`.** Since the coordinator's `_update_and_notify` runs from a synchronous `@callback` context, async service calls are scheduled as a task. After `await hass.async_block_till_done()` in tests, the task completes.
6. **Options flow for post-setup changes.** Users can add, change, or remove action scripts at any time via Settings → Integrations → Configure. The integration reloads automatically when options change.
7. **Script entities over inline actions.** HA's `ActionSelector` could allow inline action sequences, but script entities were chosen because: they provide a familiar UI-driven creation flow, support multi-step sequences, and are reusable across automations. Direct inline action support may be added in a future version.

## Test coverage

11 integration tests in `test_action_execution.py` covering:
- `set_current` fires with correct payload (current_a + charger_id) on initial charge (resume)
- `set_current` fires on current adjustment while already active
- Payload contains `current_a` as a float and `charger_id` as a string
- `stop_charging` fires with charger_id when headroom drops below minimum (overload)
- `stop_charging` fires when meter becomes unavailable in stop mode
- `start_charging` + `set_current` fire in correct order on resume
- No actions fire when current is unchanged
- No actions fire when charger is already stopped and stays stopped
- No actions fire when action scripts are not configured (backward compat)
- Failed action script logs warning but integration continues operating
- Options flow allows updating action scripts after initial setup

All 92 tests pass (81 existing + 11 new).

## Lessons learned

- **Power calculations include EV draw.** The power meter reads total household power including active EV charging. This means that a reduction from 18 A while meter reads 9000 W does not necessarily cause a stop — the formula `raw_target = current_set + available` accounts for the EV's own contribution. Tests must use sufficiently high power values (e.g., 12000 W) to trigger a genuine stop from an active charging state.
- **Ramp-up cooldown affects resume tests.** After any reduction (including a stop), the ramp-up cooldown blocks increases for 30 seconds. Resume tests must either inject a fake clock or set the cooldown to 0 to avoid false negatives.
- **DRY state updates via `_update_and_notify`.** Consolidating the state update, action scheduling, and dispatcher send into a single method eliminated duplication between `_recompute` and `_apply_fallback_current`.

## What's next

- **PR-5-MVP: Event notifications** — Fire HA events and persistent notifications when notable conditions occur (meter unavailable, overload/stop, charging resumed, fallback activated).
- **PR-6-MVP: Manual override + observability** — Expose `ev_lb.set_limit` service and add diagnostic state updates.

---

## Changelog

- 2026-02-20: Initial version (PR-4 implementation complete).
- 2026-02-20: Added `charger_id` to all action payloads, options flow for post-setup action changes, comprehensive usage documentation (`docs/documentation/action-scripts-guide.md`).
