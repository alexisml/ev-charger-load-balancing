Title: Multi-phase electrical support documentation
Date: 2026-03-01
Author: copilot
Status: in-review
Summary: Documents the single-phase assumption, multi-phase limitations, and workarounds for three-phase installations — no code changes.

---

## Context

Issue requested multi-phase electrical support and documentation. The integration's core formula (`current_a = power_w / voltage_v`) assumes single-phase operation. Three-phase installations use `P = 3 × V_phase × I`, which means the single-phase formula gives incorrect results for three-phase chargers.

After analysis, no code changes are needed at this time:

- **Single-phase charger on single-phase service**: formula is correct.
- **Single-phase charger on one phase of a three-phase service**: the formula is overly conservative (safe side) when using total three-phase power.
- **Three-phase charger on three-phase service**: not directly supported, but a template sensor workaround (`total_w / 3`) provides a reasonable approximation for balanced loads.

The voltage config range (100–480 V) does not cover effective three-phase voltages (e.g., 3 × 230 = 690 V), but expanding it would be misleading since the core formula is single-phase. A proper multi-phase implementation would require changes to the balancing algorithm, config flow, and tests.

## What was changed

### Documentation only — no code changes

1. **`docs/documentation/how-it-works.md`** — Added a new section "Single-phase assumption and multi-phase installations" before the "Next steps" section. Covers:
   - Explanation of the single-phase formula vs. three-phase power relationships
   - Impact table for common installation scenarios
   - Template sensor workaround for three-phase chargers
   - Safety caveats and disclaimers
   - Rationale for not adding a multi-phase config setting in the MVP

2. **`docs/documentation/installation-and-setup.md`** — Added a note in the Supply voltage field description linking to the multi-phase guidance section.

3. **`docs/documentation/troubleshooting-and-debugging.md`** — Added a new FAQ entry "Does it support three-phase (multi-phase) installations?" with a summary and link to the detailed guidance.

## Design decisions

1. **Documentation-only change.** The agent instructions explicitly stated not to modify logic. The workaround (template sensor dividing by 3) is practical and does not require integration changes.
2. **No voltage range expansion.** Expanding `MAX_VOLTAGE` to 700+ would let users enter `690` (3 × 230), but this would be a confusing workaround that papers over the real issue. A proper fix would add a `phases` config option and update the formula.
3. **Conservative guidance.** The documentation emphasizes that the workaround is an approximation and not a substitute for proper electrical protection, consistent with the project's safety-first approach.

## What's next

- Multi-phase support with per-phase configuration could be added in a future release, potentially as part of Phase 2 or as a standalone improvement.
- If demand is high, a `number_of_phases` config option with corresponding formula changes could be scoped as a dedicated PR.

---

## Changelog

- 2026-03-01: Initial version (documentation-only PR).
