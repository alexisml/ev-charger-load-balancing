Title: PR — Provide watts alongside amps for set_current script action
Date: 2026-02-22
Author: copilot
Status: merged
Summary: Records design decisions and implementation details for adding `current_w` (watts) to the set_current script payload, and confirming voltage was already user-configurable.

---

## Context

Issue asked for two things:

1. Pass watts alongside amps in the `set_current` script action, so chargers that require a watt value can use it directly.
2. Make voltage user-configurable if it isn't already.

On investigation, voltage (`CONF_VOLTAGE`) was **already configurable** in the config flow — a `NumberSelector` field (100–480 V, default 230 V) was present since PR-1. No changes were needed there.

## What was changed

### `coordinator.py`

Both `set_current` calls in `_execute_actions` now pass an additional `current_w` variable:

```python
current_w=round(new_current * self._voltage, 1)
```

`self._voltage` is the user-configured supply voltage loaded from `CONF_VOLTAGE` at coordinator init. The value is rounded to one decimal place to avoid floating-point noise (e.g., `10 A × 230 V = 2300.0 W` rather than `2300.0000000001`).

### Payload format (updated)

- `set_current` receives: `{"entity_id": "...", "variables": {"current_a": <float>, "current_w": <float>, "charger_id": "<entry_id>"}}`
- `stop_charging` and `start_charging` payloads are **unchanged** — they still receive only `charger_id`.

### Documentation

`docs/documentation/action-scripts-guide.md` was updated to:
- Add `current_w` to the variables reference table
- Show `current_w` as a declared `fields` entry in the OCPP script example
- Update the transition table to mention `current_w` in set_current calls
- Add a REST API example showing both `{{ current_a }}` and `{{ current_w }}` usage

### Translation / UI strings

`strings.json`, `translations/en.json`, and `translations/es.json` were updated to document `current_w` in the `action_set_current` field description in both config and options UI.

### Tests

Existing tests in `test_action_execution.py` were extended to assert `current_w` is present and correct:
- Initial charge: 10 A × 230 V = 2300.0 W
- Current adjustment: 15 A × 230 V = 3450.0 W
- Payload type test: `current_w == current_a * 230.0`

## Design decisions

1. **Compute in coordinator, not load_balancer.** The watts conversion (`current_a × voltage`) is a presentation concern specific to the script-calling layer, not core balancing logic. Keeping it in `coordinator.py` avoids polluting the pure `load_balancer.py` module.
2. **Round to 1 decimal.** Floating-point multiplication can produce trailing noise (e.g., `16 × 230 = 3680.0000000004`). Rounding to one decimal gives clean values without losing meaningful precision.
3. **Voltage already configurable — no config changes needed.** `CONF_VOLTAGE` with a `NumberSelector` (100–480 V) was already in the config flow. The only fix was to document it clearly in the action scripts guide.
4. **Backward compatible.** Scripts that don't declare or use `current_w` simply ignore it. Existing scripts continue to work without modification.

## What's next

- Multi-charger support (Phase 2) will need to pass per-charger `current_w` values, using the same voltage setting (single-phase assumption for now).

---

## Changelog

- 2026-02-22: Initial version (PR implementation complete).
