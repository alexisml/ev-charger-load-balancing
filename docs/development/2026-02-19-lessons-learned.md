Title: Lessons learned — AppDaemon prototype & blueprint evaluation
Date: 2026-02-19
Author: alexisml
Status: approved
Summary: Records what was learned from prototyping the AppDaemon app and automation blueprint, and documents the decision to build a custom HACS integration instead.

---

## Context

As part of the research plan (`2026-02-19-research-plan.md`) two prototype delivery mechanisms were built and evaluated:

1. **AppDaemon app** (`apps/ev_lb/ev_lb.py`) — a Python app that listens to a power-meter sensor and calls user-supplied scripts to adjust charger current.
2. **HA automation blueprint** (`blueprints/automation/ev_lb/ev_charger_load_balancing.yaml`) — a single-charger automation driven by Jinja2 template math.

Both were subsequently deleted in favour of a custom HACS integration. This document records the reasons.

---

## What we learned

### AppDaemon app

**Worked well:**
- Pure Python makes the computation logic easy to write, test, and iterate on.
- State tracking (last set current, last reduction timestamp) is straightforward in a Python object.
- The water-filling algorithm and ramp-up cooldown were prototyped and fully unit-tested here; this code transfers directly to the integration.
- Service call pattern (`self.call_service()`) is clean and matches the agreed payload (`charger_id`, `current_a`).

**Problems identified:**
- **Requires helpers for all runtime-configurable inputs.** AppDaemon runs outside HA's entity registry and cannot create Config Entries. Every user-facing parameter (service current, voltage, charger max, ramp-up time, enable toggle) must be a manually created `input_number` / `input_boolean` helper. Users told us they do not want to create helpers manually.
- **Requires AppDaemon as an extra runtime dependency.** Not every HA user runs AppDaemon. It adds installation and maintenance overhead.
- **No native HA device entry.** AppDaemon can call `set_state` to synthesise sensors, but those entities are orphaned — they are not linked to any HA device and do not appear cleanly in Settings → Devices.

### Automation blueprint

**Worked well:**
- No extra runtime beyond HA core.
- Easy to share via HACS or a URL import.

**Problems identified:**
- **Same helper problem.** Blueprint inputs are resolved once at automation creation and become hardcoded values. To make them runtime-adjustable they still need `input_number` / `input_boolean` helpers.
- **No persistent state.** Template sensors computed by a blueprint are not persisted across HA restarts.
- **Limited expressiveness.** The ramp-up cooldown and multi-charger water-filling algorithm cannot be expressed cleanly in Jinja2 / YAML. The blueprint supported only a single charger with no cooldown.

---

## Decision: build a custom HACS integration

A **custom integration** (`custom_components/ev_lb/`) with a **Config Flow** is the correct delivery mechanism because:

1. **No helpers required.** Config Flow presents a guided UI in Settings → Integrations → Add. All parameters (service current, voltage, charger caps, ramp-up time, enable toggle) are stored in the HA config entry. No manual helper creation by the user.
2. **Native HA entities.** The integration can register `number`, `switch`, `sensor`, and `binary_sensor` entities directly, linked to a proper device entry visible in Settings → Devices.
3. **Persistent state.** Config entries survive HA restarts; entity states are restored by HA's state machine.
4. **Multi-charger support.** The Config Flow options flow can handle adding and removing chargers at runtime.
5. **HACS distribution.** Custom integrations are the standard HACS distribution unit. Users install via HACS, then configure via the HA UI — no YAML required.
6. **Reuses prototype logic.** The pure computation functions (`compute_available_current`, `distribute_current`, `apply_ramp_up_limit`) developed during the AppDaemon prototype transfer directly into the integration with no changes. The 39-test unit test suite continues to cover them.

---

## What was preserved

- `tests/test_load_balancer.py` and the pure computation functions — moved to `custom_components/ev_lb/` (or kept in `tests/` until the integration scaffold is in place). All 39 tests continue to pass.
- The documented design decisions (available current formula, instant-down/delayed-up asymmetry) — recorded in `2026-02-19-research-plan.md` and this file.
- The testing guide (`2026-02-19-testing-guide.md`) — updated to reflect the integration approach.

## What was deleted

- `apps/ev_lb/` — AppDaemon app and config (no longer needed).
- `blueprints/automation/ev_lb/` — HA automation blueprint (no longer needed).

---

## Next steps

See the updated next steps in `2026-02-19-research-plan.md`:

1. Scaffold `custom_components/ev_lb/` with `manifest.json`, `__init__.py`, `config_flow.py`.
2. Add `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py` with the Config Flow options.
3. Port the computation core (`compute_available_current`, `distribute_current`, `apply_ramp_up_limit`) into the integration.
4. Wire up the power-meter listener and service calls.
5. Write HA integration tests using `pytest-homeassistant-custom-component`.
6. Publish via HACS.
