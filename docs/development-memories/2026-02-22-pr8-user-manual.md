Title: PR-8-MVP — User manual
Date: 2026-02-22
Author: copilot
Status: draft
Summary: Created comprehensive multi-file user documentation, slimmed down the README, and added a development guide.

---

## Context

PR-8-MVP is the final milestone of Phase 1 (MVP). All previous milestones (PR-1 through PR-7) delivered the working integration — scaffold, entities, balancing engine, action execution, event notifications, manual override, and test stabilization.

This PR creates the user documentation covering all user-facing features.

## What was done

1. **Split user manual into four focused guides:**
   - `docs/documentation/installation-and-setup.md` — prerequisites, HACS/manual install, step-by-step config, verification, removal
   - `docs/documentation/how-it-works.md` — what to expect, what NOT to expect, entities reference, algorithm (layered: simple first, then advanced), state machines, safety guardrails
   - `docs/documentation/troubleshooting-and-debugging.md` — quick checklist, common problems, log interpretation, diagnostic sensors, FAQ, issue reporting guide
   - `docs/documentation/development-guide.md` — architecture overview, repo structure, running tests, CI checks, contributing guidelines, project roadmap

2. **Converted `docs/documentation/user-manual.md` into a documentation index page** linking all four guides plus the existing reference guides.

3. **Slimmed down `README.md`** — moved detailed technical content (mermaid diagrams, state machines, diagnostic sensor tables, power meter unavailable details, restart behavior, multi-charger fairness, CI check instructions, "Why a custom integration") into the user docs. README is now a concise project overview with links.

4. **Made sections layered** — each guide starts with a simple explanation accessible to all users, then provides advanced details for those who want to dig deeper. E.g., "How It Works" starts with "The short version" (3 sentences), then has the full algorithm with mermaid diagrams below.

5. **Added comprehensive "What NOT to expect" section** — clarifies that the integration is not a charger driver, does not monitor charger health, is not circuit-level protection, doesn't manage tariffs/solar, and only supports one charger.

## Design decisions

- **Multi-file structure:** Splitting by concern (install, usage, debugging, development) makes each guide focused and scannable. Users don't need to read 600 lines to find their answer.
- **Existing individual guides preserved:** `action-scripts-guide.md`, `event-notifications-guide.md`, and `logging-guide.md` remain as deep-dive reference material, linked from the relevant section guides.
- **README trimmed significantly:** Technical details belong in the docs, not the README. The README is the first thing users see — it should be a clear, concise overview that helps them decide if this integration is for them.

## Exit criteria check

| Criterion | Status |
|---|---|
| User manual published in `docs/documentation/` | ✅ (4 files + index) |
| README links to manual | ✅ |
| Manual covers all user-facing features from PR-1 through PR-7 | ✅ |
| Sections are layered (simple → advanced) | ✅ |
| Development guide included | ✅ |
| "What NOT to expect" section | ✅ |
