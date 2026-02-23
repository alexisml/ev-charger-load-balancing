Title: OCPP script examples — fix and expand action scripts guide
Date: 2026-02-23
Author: copilot
Status: approved
Summary: Fixed incorrect start_charging OCPP example, added conn_id parameter, and expanded the OCPP section of the action scripts guide with comprehensive guidance.

---

## Context

Issue #40 requested script examples for users of the [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp) integration. The existing `action-scripts-guide.md` already had OCPP examples, but they contained a critical error and were missing key parameters.

## Problems found

1. **Incorrect `start_charging` example** — The guide called `ocpp.reset`, which sends an OCPP `Reset.req` message (a hardware reboot), not a "resume charging" command. This would restart the charger's firmware rather than allowing charging to resume.

2. **Missing `conn_id` parameter** — All `ocpp.set_charge_rate` calls require a `conn_id` specifying the connector number. The examples omitted it, which would cause the calls to fail or target the wrong connector.

3. **Sparse "Adapting" section** — The OCPP section in "Adapting for different charger integrations" was three bare code snippets with no explanation of how OCPP charging control works, why there is no dedicated "start" command, or how to find the connector ID.

## What was changed

### `docs/documentation/action-scripts-guide.md`

1. **Fixed `start_charging`** — replaced `ocpp.reset` with `ocpp.set_charge_rate` setting `limit_amps: 6` (the IEC 61851 minimum). OCPP has no dedicated resume command; setting a positive limit signals the charger to start accepting current. The integration immediately calls `set_current` with the actual target after `start_charging` completes.

2. **Added `conn_id: 1`** to all OCPP examples in the step-by-step "Creating scripts" section, with a note explaining connector numbering.

3. **Expanded the OCPP "Adapting for different charger integrations" section** from three bare snippets to a comprehensive block covering:
   - How `ocpp.set_charge_rate` maps to start/stop/adjust semantics
   - Why there is no dedicated OCPP "start" command
   - `limit_watts` alternative for chargers that prefer power-based control
   - How to find `conn_id` from the HA device page
   - Why `limit_amps: 6` is used for `start_charging`
   - Note that scripts can contain any HA actions (enable/disable toggle, `ocpp.reset` for dead-stop chargers, etc.)
   - Manual testing tip using Developer Tools → Actions

4. **Clarified blocking behavior** in the "Resume sequence" section — each script call is `blocking=True`, meaning the integration waits for the entire script (including any delays or multi-step sequences) to finish before firing the next action.

## Design decisions

- **`limit_amps: 6` for start_charging, not `ocpp.reset`**: Setting the minimum allowed current is the correct OCPP-level signal to resume charging. `ocpp.reset` was wrong because it reboots the charger hardware. Since the integration calls `set_current` immediately after `start_charging`, the 6 A is transient and quickly replaced by the actual computed target.

- **`conn_id: 1` as the default example**: Most home chargers have a single connector. The guide notes how to find the correct value for multi-connector chargers.

- **Scripts are customisable**: The examples are minimal starting points. The note explicitly tells users they can add any HA actions, including charger-specific steps like toggling an enable/disable switch or calling `ocpp.reset` if their hardware requires a restart from a complete dead-stop. All three action scripts remain optional.

## Lessons learned

- `ocpp.reset` is a hardware reset command, not a "resume" command. OCPP chargers resume charging automatically when a positive `limit_amps` is set while a vehicle is connected.
- The `conn_id` parameter is required by `lbbrhzn/ocpp`'s `set_charge_rate` service. Its absence would silently target the wrong connector or cause an error.
