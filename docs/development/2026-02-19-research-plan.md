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

- Start with a fast AppDaemon prototype to validate logic and service interactions.
- Provide a blueprint for users who want no extra runtime for simple single-charger setups.
- If persistent sensors, multi-charger fairness, and better UX are desired (as requested), build a small custom integration (HACS) with ConfigFlow.

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

## Implementation roadmaps

### Integration (HACS custom component)

1. Scaffold integration with `config_flow.py`, `sensor.py`, `binary_sensor.py`.
2. Register entities at setup.
3. Subscribe to power meter sensor state changes.
4. Compute available current, call configured set_current service.
5. Expose service `ev_lb.set_limit` for manual override.

### AppDaemon app

1. Create `apps/ev_lb/ev_lb.py`.
2. Listen to power meter entity; compute and dispatch current adjustments.
3. Use `self.call_service()` to invoke OCPP or user-provided scripts.
4. Persist state in AppDaemon's entity helper or HA input helpers.

### Blueprint

1. Define inputs (selectors) for all configurable parameters.
2. Use `trigger: state` on power meter sensor.
3. Use template conditions to compute available current and clamp.
4. Call service dynamically using template service call.

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

| Step | Owner | ETA | Deliverable |
|------|-------|-----|-------------|
| Discovery: confirm service payload & device-linking approach | alexisml | +0.5 days | Short note in docs/development/ |
| AppDaemon prototype (single charger) | alexisml | +3 days | Working app + notes |
| Evaluate prototype, choose delivery mechanism | alexisml | +4 days | Decision doc in docs/development/ |
| Implement chosen approach (MVP) with unit tests | alexisml | +12 days | Code + tests + blueprint/integration |
| HACS manifest, README, release | alexisml | +14 days | Publishable HACS repo |
