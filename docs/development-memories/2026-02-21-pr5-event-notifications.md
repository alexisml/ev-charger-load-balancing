Title: PR-5-MVP — Event notifications
Date: 2026-02-21
Author: copilot
Status: in-review
Summary: Documents the implementation of HA event notifications and persistent notifications for fault conditions in the EV charger load-balancing integration.

---

## Context

The integration could compute and apply charger current changes but had no mechanism to proactively notify users or automations when notable conditions occurred. PR-5-MVP adds event notifications (consumable by automations) and persistent notifications (visible on the dashboard) for fault conditions.

## What changed

- **`custom_components/ev_lb/const.py`**: Added event type constants (`EVENT_METER_UNAVAILABLE`, `EVENT_OVERLOAD_STOP`, `EVENT_CHARGING_RESUMED`, `EVENT_FALLBACK_ACTIVATED`, `EVENT_ACTION_FAILED`) and persistent notification ID templates (`NOTIFICATION_METER_UNAVAILABLE_FMT`, `NOTIFICATION_OVERLOAD_STOP_FMT`, `NOTIFICATION_FALLBACK_ACTIVATED_FMT`).
- **`custom_components/ev_lb/coordinator.py`**: Added `_fire_events` method that fires HA bus events and creates/dismisses persistent notifications on state transitions. Called from `_update_and_notify`. Action failure event fired from `_call_action`.
- **`tests/test_event_notifications.py`** (new file): 15 integration tests verifying event payloads, persistent notification creation, notification dismissal on recovery, ignore-mode silence, absence of spurious events, and action failure events.
- **`docs/development-memories/2026-02-19-testing-guide.md`**: Updated test instructions.
- **`docs/documentation/milestones/01-2026-02-19-mvp-plan.md`**: Marked PR-5-MVP as done.
- **`README.md`**: Updated status line.

## Design decisions

### 1. Five event types covering the notable conditions

| Event type | Condition | Persistent notification |
|---|---|---|
| `ev_lb_meter_unavailable` | Power meter becomes unavailable in stop mode | Yes — fault |
| `ev_lb_fallback_activated` | Power meter becomes unavailable in set_current mode | Yes — fault |
| `ev_lb_overload_stop` | Household load exceeds service limit, charging stops | Yes — fault |
| `ev_lb_charging_resumed` | Charging resumes from a stopped state | No — resolution |
| `ev_lb_action_failed` | Charger action script raises an error | No — diagnostic |

Events are fired via `hass.bus.async_fire` and carry structured payloads including `entry_id` so automations can identify which charger is affected (preparing for Phase 2 multi-charger support).

### 2. Persistent notifications for fault conditions only

Persistent notifications are created for the three fault conditions (meter unavailable, overload stop, fallback activated) to ensure the user sees them on the dashboard. Charging resumed is a positive resolution event and does not create a notification.

### 3. Automatic notification dismissal

- **Meter recovery**: When a valid power meter event arrives (`REASON_POWER_METER_UPDATE`), the `meter_unavailable` and `fallback_activated` notifications are dismissed.
- **Charging resumed**: When charging transitions from stopped to active, the `overload_stop` notification is dismissed.

This avoids stale notifications lingering on the dashboard after the fault has been resolved.

### 4. Ignore mode produces no events

When the user configures `unavailable_behavior: ignore`, the meter going unavailable does not produce any event or notification because the integration takes no action (the current is held at the last value). Users who want to detect meter unavailability in ignore mode can set up an automation on the meter entity's state directly.

### 5. Event firing location: `_fire_events` called from `_update_and_notify`

All events are fired in a single `_fire_events` method called from `_update_and_notify`, which is the single point where all state transitions flow through. This ensures consistent event firing regardless of the trigger (power meter change, parameter change, manual override, fallback).

### 6. Persistent notification testing via mock patches

The `persistent_notification` component is not loaded in the `pytest-homeassistant-custom-component` test environment by default. Rather than adding test-only component setup, the persistent notification functions (`pn_async_create`, `pn_async_dismiss`) are patched at the coordinator module level and verified via mock assertions. Event tests use real `hass.bus` listeners.

## Test coverage

15 integration tests in `test_event_notifications.py` covering:
- Meter unavailable event fires with correct payload (entry_id, power_meter_entity)
- Meter unavailable creates persistent notification
- Meter unavailable notification dismissed on meter recovery
- Overload stop event fires with correct payload (entry_id, previous_current_a, available_current_a)
- Overload stop creates persistent notification
- Charging resumed event fires with correct payload (entry_id, current_a)
- Overload notification dismissed when charging resumes
- Initial charge (0→active) fires a resumed event
- Fallback activated event fires with correct payload (entry_id, power_meter_entity, fallback_current_a)
- Fallback creates persistent notification
- Fallback notification dismissed on meter recovery
- No event fires in ignore mode
- No events on steady-state (same power meter value)
- No overload event when charger is already stopped and stays stopped
- Action failed event fires with correct payload (entry_id, action_name, entity_id, error)

## Lessons learned

- **Persistent notification testing**: The `persistent_notification` component needs explicit setup in HA test fixtures to create state entities. Mocking the create/dismiss functions at the module level is simpler and more robust across HA versions.
- **Event deduplication**: The `_fire_events` method uses `elif` chains for fault conditions so only one fault event fires per update cycle. The resolution conditions (resumed, meter recovery) use separate `if` blocks so they can fire alongside fault conditions in edge cases.
- **Notification ID templates**: Using entry_id-scoped notification IDs (`ev_lb_overload_stop_{entry_id}`) ensures notifications are correctly managed per config entry, which will scale to Phase 2 multi-charger support.

## What's next

- **PR-7-MVP: Test stabilization + HACS release readiness** — Finalize HACS requirements, complete integration tests, and prepare the first release.

---

## Changelog

- 2026-02-21: Initial version (PR-5-MVP implementation complete).
