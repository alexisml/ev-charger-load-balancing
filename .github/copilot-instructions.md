# Copilot Instructions

## Repository context

This repository contains a custom Home Assistant (HACS) integration for dynamic EV charger load balancing. The integration is under `custom_components/ev_lb/`.

## Rules for the coding agent

### Always read all markdown files first

Before making any changes, **always** read and review all `.md` files in the repository to understand the current project state, plans, and decisions. Key files:

- `README.md` — project overview, status, and quick-start actions
- `docs/development/README.md` — rules for development documentation
- `docs/development/2026-02-19-research-plan.md` — implementation roadmap and PR milestones
- `docs/development/2026-02-19-lessons-learned.md` — design decisions and rationale
- `docs/development/2026-02-19-testing-guide.md` — how to test the integration

When new `.md` files are added under `docs/development/`, read those as well.

### Development documentation rule

All research plans, design notes, and development artifacts MUST be placed under `docs/development/` with an ISO-date prefix: `docs/development/YYYY-MM-DD-short-name.md`.

### Testing

- Run `python -m pytest tests/ -v` to execute all tests.
- Existing pure-logic tests are in `tests/test_load_balancer.py` (39 tests).
- Config flow and integration tests use `pytest-homeassistant-custom-component`.
- All tests must pass before committing changes.

### Integration structure

The integration lives in `custom_components/ev_lb/` and follows the standard Home Assistant custom component layout:
- `manifest.json` — HA/HACS metadata
- `__init__.py` — entry setup/unload
- `config_flow.py` — Config Flow UI
- `const.py` — constants and defaults
- `strings.json` + `translations/` — UI strings (English + Spanish)
