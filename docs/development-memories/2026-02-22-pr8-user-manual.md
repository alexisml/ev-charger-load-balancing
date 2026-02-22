Title: PR-8-MVP — User manual
Date: 2026-02-22
Author: copilot
Status: draft
Summary: Created a comprehensive end-user manual consolidating all existing guides into a single document, and linked it from the README.

---

## Context

PR-8-MVP is the final milestone of Phase 1 (MVP). All previous milestones (PR-1 through PR-7) delivered the working integration — scaffold, entities, balancing engine, action execution, event notifications, manual override, and test stabilization.

This PR creates the user manual that covers all user-facing features.

## What was done

1. **Created `docs/documentation/user-manual.md`** — a comprehensive end-user manual covering:
   - Prerequisites and installation (HACS and manual)
   - Configuration (Config Flow initial setup and options flow)
   - Full entities reference (sensors, binary sensors, numbers, switch, service)
   - How the balancing algorithm works (decision loop, key rules, restart behavior)
   - Action scripts (creation, variables, transition logic, error handling, charger-specific examples)
   - Event notifications (event types, payloads, persistent notifications, automation examples)
   - Manual override (`ev_lb.set_limit` service)
   - Power meter unavailable behavior (all three modes)
   - Logging and diagnostics (debug logs, log levels, diagnostic sensors)
   - Troubleshooting (common issues and fixes)
   - FAQ (solar, multi-charger, safety, restart behavior)

2. **Updated `README.md`** — added a link to the user manual and updated the status line to reflect PR-8-MVP completion.

## Design decisions

- **Single document, not multiple pages:** The user manual consolidates content from `action-scripts-guide.md`, `event-notifications-guide.md`, and `logging-guide.md` into one document. The existing individual guides are preserved for backward compatibility and for users who want focused reference material.
- **Markdown format:** Consistent with the repository's documentation convention. The user preferred `.md` files.
- **Placed in `docs/documentation/`:** Following the MVP plan exit criteria that specified the manual should be published in `docs/documentation/`.

## Exit criteria check

| Criterion | Status |
|---|---|
| User manual published in `docs/documentation/` | ✅ |
| README links to manual | ✅ |
| Manual covers all user-facing features from PR-1 through PR-7 | ✅ |
