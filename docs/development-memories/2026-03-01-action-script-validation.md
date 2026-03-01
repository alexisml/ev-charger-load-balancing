Title: Action script validation and best-practice documentation
Date: 2026-03-01
Author: copilot
Status: in-review
Summary: Added starter script templates, validation checklist, and best practices to the action-scripts-guide for OCPP, REST, Modbus, and switch chargers.

---

## Context

Issue #104 requested example/action script validation and best-practice documentation. Users lacked canonical starter scripts, a pre-flight validation checklist, and guidance on error handling patterns within their scripts.

The existing `action-scripts-guide.md` had good OCPP examples (expanded in PR #40) but the REST and Modbus sections were minimal snippets without configuration prerequisites or best-practice guidance. There was no formal validation checklist or best-practices section.

## What was changed

### New: `docs/examples/` directory

Created 11 ready-to-use YAML starter script templates, one for each action/charger-type combination:

- **OCPP:** `ocpp-set-current.yaml`, `ocpp-stop-charging.yaml`, `ocpp-start-charging.yaml`
- **REST:** `rest-set-current.yaml`, `rest-stop-charging.yaml`, `rest-start-charging.yaml`
- **Modbus:** `modbus-set-current.yaml`, `modbus-stop-charging.yaml`, `modbus-start-charging.yaml`
- **Switch:** `switch-stop-charging.yaml`, `switch-start-charging.yaml`

Each template includes:
- Inline comments explaining what the script does and what variables it receives
- Prerequisites (e.g. `rest_command` or `modbus` hub configuration)
- Step-by-step "How to use" instructions
- Manual test command for Developer Tools → Actions

A `README.md` index links all templates with a summary table.

### Updated: `docs/documentation/action-scripts-guide.md`

1. **Expanded REST API section** — added `configuration.yaml` prerequisites for `rest_command` definitions, tips on content type, authentication, and payload format.

2. **Expanded Modbus section** — added `configuration.yaml` prerequisites for Modbus hub, complete stop/start examples (previously only set_current existed), tips on register maps, coils vs registers, and slave addresses.

3. **New "Validation checklist" section** — two-phase checklist (before and after connecting scripts) with specific steps and example service call YAML for manual testing.

4. **New "Best practices" section** covering:
   - Script mode selection (`mode: single` recommended, `mode: restart` as alternative)
   - Error handling patterns inside scripts (confirmation step, notification on unexpected state)
   - Testing and debugging workflow (manual testing, diagnostic sensors, debug logs, event monitoring)
   - Compatibility notes per charger type

5. **New "Starter script templates" link** pointing to `docs/examples/`.

### Updated: `README.md` and `user-manual.md`

Added a row linking to the starter script templates in the reference guides table.

## Design decisions

- **Standalone YAML files, not just inline examples.** Users can copy a complete file rather than extracting snippets from markdown. Each file is self-contained with instructions in comments.
- **`mode: single` as default.** This prevents overlapping calls and matches how the integration sends actions (`blocking: true`). Documented `mode: restart` as an alternative for chargers with slow responses.
- **Validation checklist uses checkboxes.** Users can copy the checklist into their own notes and track progress. The "before" and "after" structure ensures scripts are tested independently before being connected to the integration.
- **Best practices section is advisory, not prescriptive.** The confirmation-step and notification examples are optional patterns, not requirements. Users can adopt what fits their setup.
- **REST and Modbus prerequisites shown explicitly.** Previous snippets assumed users had already configured `rest_command` or `modbus` hub. Now the required `configuration.yaml` entries are shown alongside the script examples.

## Lessons learned

- Modbus chargers have the most variation in register maps and value scaling. The documentation emphasizes consulting the charger's register map rather than providing a "universal" example.
- REST chargers vary in authentication and content type. Providing the `rest_command` prerequisite inline prevents a common "service not found" error.
- Switch-based chargers cannot use `set_current` — this was already documented but is now reinforced in both the guide and the template README.
