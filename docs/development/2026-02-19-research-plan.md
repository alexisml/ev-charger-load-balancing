# Research Plan — Decide: Integration / Template / AppDaemon

Title: Research Plan — Decide: Integration / Template / AppDaemon
Date: 2026-02-19
Author: alexisml
Status: draft
Summary: Plan and design notes to determine the best delivery mechanism for EV charger load balancing (integration, AppDaemon app, or automation blueprint).

---

This document collects the research plan, proposed README content, the blueprint discussion, the requested entity/input model, and next steps. It follows the repository rule that development docs live under `docs/development/<file>.md`.

## Contents

- Goal
- Discovery plan
- Power meter compatibility
- Prototyping options & plan
- Evaluation criteria and recommendation
- Proposed entities, inputs, and service contract
- Blueprint summary
- Implementation roadmaps (integration, AppDaemon, blueprint)
- Testing & QA
- Next steps, timeline, deliverables

---

## Goal

- Provide dynamic load balancing for EV chargers in Home Assistant using the lbbrhzn/ocpp integration and a power meter (household or solar).
- Support core features requested:
  - Persistent sensor: real charging amps set
  - Binary sensor: whether dynamic load balancing is active
  - Runtime inputs: max service current, per-charger max current (dynamic), min current before shutdown
  - Configurable scripts/actions for set_current, stop_charging, start_charging

---

## Discovery — map capabilities (0.5 day)

Note: Deep inspection of lbbrhzn/ocpp internals is lower priority since the user will provide the start/stop/set_current actions as configurable scripts. The integration only needs to call those user-supplied services.

Tasks:
- Confirm the service/script interface expected by the user (set_current, stop_charging, start_charging) and document the agreed data payload.
- Identify how chargers are referenced (entity_id or device_id) so our entities can be linked to the correct device in the HA device registry.
- Optionally skim lbbrhzn/ocpp to understand the device model it creates, to ensure our device registry entries can reference the same device.

Deliverables:
- Short note with agreed service payload format and device-linking approach.

---

## Power Meter compatibility (0.5 day)

- Define canonical sensors and units we support:
  - Required: instantaneous power (W) — `sensor.house_power_w`
  - Optional: solar production (W), grid import/export (W)
- Plan conversions: W ↔ A using configured voltage per charger/site.

---

## Prototype (2–4 days)

Two prototype routes:

### A) AppDaemon prototype (recommended initial prototype)

- Pros: Python, quick to iterate, easier to manage state.
- Cons: Requires user to run AppDaemon.

### B) Automation blueprint & scripts

- Pros: No extra runtime, easy for users without AppDaemon.
- Cons: Limited persistent state and scaling.

Prototype tasks:
- Read power sensors, compute available current, clamp to charger min/max/step.
- Call OCPP service (via lbbrhzn/ocpp service or via user-provided scripts) to set charging profile or target current.
- Validate for one charger; log latency and failures.

---

## Evaluation (1 day)

Criteria:
- Reliability and latency of service calls.
- Maintainability (YAML vs Python).
- UX: how easy to configure, ability to create ConfigFlow and persistent sensors.
- Distribution via HACS and runtime requirements.

---

## Recommendation (summary)

**Decision (2026-02-19): build a custom HACS integration with Config Flow.**

Both the AppDaemon app and the automation blueprint were prototyped and evaluated. Both were rejected because they require users to manually create `input_number` / `input_boolean` helpers for every runtime-configurable parameter. A custom integration with Config Flow is the only mechanism that eliminates manual helper creation and provides native HA device entries, persistent state, and a guided setup UI.

See [`2026-02-19-lessons-learned.md`](2026-02-19-lessons-learned.md) for the full evaluation and rationale.

---

## Proposed entity & input model

### Entities (persistent, per charger where appropriate)

All per-charger entities MUST be registered under the charger's device in the HA device registry. This allows them to appear grouped under the charger device in the HA UI (Settings → Devices) rather than as standalone orphan entities.

- Device registry: register a `DeviceEntry` per charger using a stable unique identifier (e.g., charger serial or config-entry-scoped ID). Associate all per-charger entities with `device_id` or `via_device` pointing to the charger device.
- `sensor.ev_lb_<charger_id>_current_set` (float, A) — last requested/attempted current; linked to charger device
- `binary_sensor.ev_lb_<charger_id>_active` — on when LB actively controlling the charger; linked to charger device
- `sensor.ev_lb_available_current_a` (float) — computed available current (global, not per-charger)
- `sensor.ev_lb_house_power_w` (float) — mirror/derived (global)
- `sensor.ev_<charger_id>_actual_current_a` (optional) — if charger reports measured current; linked to charger device

### Inputs (either created by integration or external input_* helpers)

- `input_number.ev_lb_max_service_current_a` — whole-house breaker rating
- `input_number.ev_lb_max_charging_current_a_<charger_id>` — per-charger max current (dynamic)
- `input_number.ev_lb_min_current_before_shutdown_a` — default 6 A; if set lower than charger min, consider shutdown behavior
- `input_boolean.ev_lb_enabled` — global enable/disable for dynamic LB
- `input_number.ev_lb_user_limit_w` — optional overall power limit to respect
- `input_number.ev_lb_voltage_v` — supply voltage in Volts (default 230 V); allows runtime changes without restarting AppDaemon

### Configurable actions (provided as service strings or script entity IDs in the integration config)

- `set_current`: service to call to set the charger current
- `stop_charging`: optional service to stop charging
- `start_charging`: optional service to start charging

---

## Service contract examples

### set_current

- Example: `script.ev_lb_set_current`
- Data: `{ charger_id: "<charger_entity_id>", current_a: <float> }`

### stop_charging

- Example: `script.ev_lb_stop_charging`
- Data: `{ charger_id: "<charger_entity_id>" }`

### start_charging

- Example: `script.ev_lb_start_charging`
- Data: `{ charger_id: "<charger_entity_id>" }`

---

## Blueprint summary

A blueprint approach uses a single automation with input selectors for:
- Power meter sensor
- OCPP charger entity (or script for set_current)
- Max service current, per-charger max, min current
- Enable/disable toggle

Limitations of the blueprint approach:
- No persistent sensors across restarts (state is in template sensors only).
- Complex multi-charger fairness logic is hard to express in pure YAML.
- Best for single-charger setups with simple load-shedding.

---

## Implementation roadmap — Custom HACS integration

The implementation is split into PR-sized milestones so each step can be delivered, reviewed, and merged independently.
Each milestone is independently testable and must ship with unit tests for the behavior introduced in that PR.

| PR milestone | Scope | Exit criteria |
|---|---|---|
| PR-1: Integration scaffold + Config Flow | Create `custom_components/ev_lb/` with `manifest.json`, `__init__.py`, `config_flow.py`, constants, and validation for required inputs (power meter, voltage, service current); add baseline HACS metadata (`hacs.json`) and a GitHub Actions unit-test workflow. | Integration loads in HA; config entry can be created/removed; basic tests for config flow pass; CI runs unit tests on PR/push. |
| PR-2: Core entities and device linking | Add `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py`; register a charger device entry and attach per-charger entities. | Entities appear under the charger device; unique IDs stable; entity setup tests pass. |
| PR-3: Single-charger balancing engine | Port `compute_available_current` and `apply_ramp_up_limit` from `tests/` into integration runtime and subscribe to power-meter updates. | On meter change, target current updates correctly with instant down / delayed up behavior; unit tests cover core logic. |
| PR-4: Action execution contract | Implement configured `set_current` / `stop_charging` / `start_charging` service calls with payload validation and error handling. | Correct service calls are fired for increase/reduce/stop/resume transitions; integration tests assert payloads and unit tests cover payload validation/error paths. |
| PR-5: Multi-charger fairness | Port `distribute_current` logic for multi-charger allocation. | Current is allocated fairly across active chargers and respects per-charger min/max constraints; fairness tests pass. |
| PR-6: Runtime charger management | Add options flow for adding/removing chargers at runtime. | Chargers can be added/removed without restart and entities/device links remain consistent; options-flow unit tests pass. |
| PR-7: Manual override + observability | Expose `ev_lb.set_limit` service and add/verify diagnostic state updates needed for troubleshooting. | Manual override changes runtime limits safely and state reflects changes without restart; unit tests cover limit and state transitions. |
| PR-8: Test stabilization + HACS release readiness | Finalize HACS requirements (`manifest.json`, `hacs.json`, repository structure/docs), complete integration tests (`pytest-homeassistant-custom-component`), and prepare first release. | CI is green on 3 consecutive runs, installation via HACS works, and docs cover configuration + troubleshooting. |

### Global quality gates (apply to every milestone PR)

- Add/update unit tests for every behavior introduced in that milestone.
- Keep the unit-test CI workflow green on every PR before merge.
- Include a short "how to test" section in each PR description (local `pytest` command + HA smoke check for the changed behavior).

### Review-and-update loop (required after every milestone)

After each PR milestone is merged:
1. Review implementation results vs the milestone exit criteria.
2. Record gaps, risks, and discovered edge cases.
3. Update this roadmap (scope/order/acceptance criteria) before starting the next PR.
4. Confirm test strategy updates needed for the next milestone.

---

## Design decisions

### Available current formula

The headroom available for EV charging is computed as:

```
available_a = service_current_a - house_power_w / voltage_v
```

`house_power_w` is the **total** metered household power, including any active EV charging.  `available_a` is therefore the headroom above the current total draw.  The EV target is derived as:

```
ev_target_a = current_ev_a + available_a   (then clamped to [min_ev_a, max_charger_a])
```

The stop condition is checked against the final `target_a`, not against `available_a` directly, because `max_charger_a` may cap the target below `min_ev_a` even when there is sufficient headroom.

### Current adjustment asymmetry (instant down, delayed up)

**Current reductions are always applied immediately.** When the balancer computes a lower (or zero) target for a charger, the `set_current` or `stop_charging` service is called on the very next power-meter event with no delay.

**Current increases are subject to a configurable ramp-up cooldown** (`ramp_up_time_s`, default 30 s). After any dynamic reduction the balancer records the timestamp and holds increases until the cooldown has fully elapsed. This asymmetry deliberately prioritises grid safety: overloads must be resolved instantly, but premature ramp-back would cause rapid oscillation if the household load is fluctuating near the service limit.

The cooldown is implemented in the pure function `apply_ramp_up_limit()` (`tests/test_load_balancer.py`) and is fully covered by unit tests. It will be ported into the custom integration.

### Supply voltage as a Config Flow input

The supply voltage (used to convert Watts ↔ Amps) is configured via the integration's Config Flow and stored in the config entry. It can be changed via the options flow without restarting HA. No `input_number` helper is required.

### Ramp-up time as a Config Flow option

`ramp_up_time_s` (default 30 s) is an options-flow setting stored in the config entry. Changing it triggers a coordinator reload but does not require a full HA restart. No `input_number` helper is required.

### Blueprint

> **Note:** The automation blueprint was evaluated and rejected. See `2026-02-19-lessons-learned.md`.

---

## Testing & QA

Unit tests are **required** for any implementation (integration, AppDaemon app, or blueprint-supporting scripts).

- Unit tests (mandatory):
  - Current computation logic: available current calculation, clamping to min/max/step, fairness distribution across multiple chargers.
  - Edge cases: min current boundary, disabled state (`ev_lb_enabled = off`), power sensor unavailable/unknown, charger at zero load.
  - Use `pytest` with `pytest-homeassistant-custom-component` for HA integration tests; plain `pytest` for pure-Python AppDaemon logic.
- Integration tests:
  - HA test harness: verify entities are created, linked to the correct device, and update state correctly on power meter changes.
  - Verify service calls (set_current, stop_charging, start_charging) are invoked with the correct payload.
- Manual / end-to-end tests:
  - Test with a real or simulated OCPP charger.
- Regression tests:
  - Cover each edge case identified during prototyping; add a test before fixing any bug.

---

## Next steps, timeline, deliverables

| Step | PR | Owner | ETA | Deliverable | Status |
|------|-----|-------|-----|-------------|--------|
| Scaffold custom integration | PR-1 | alexisml | +2 days | `custom_components/ev_lb/` with Config Flow, `hacs.json`, CI workflow | ✅ Done |
| Core entities and device linking | PR-2 | alexisml | +4 days | `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py`; charger device entry | ✅ Done |
| Single-charger balancing engine | PR-3 | alexisml | +6 days | Port computation core + power-meter listener | |
| Action execution contract | PR-4 | alexisml | +7 days | `set_current` / `stop_charging` / `start_charging` service calls | |
| Multi-charger fairness | PR-5 | alexisml | +9 days | `distribute_current` water-filling logic | |
| Runtime charger management | PR-6 | alexisml | +10 days | Options flow for add/remove chargers | |
| Manual override + observability | PR-7 | alexisml | +11 days | `ev_lb.set_limit` service + diagnostic state | |
| Test stabilization + HACS release | PR-8 | alexisml | +14 days | Full integration tests, HACS-ready release | |
