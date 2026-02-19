# EV Charger Load Balancing (HACS)

Add-on / integration to provide dynamic load balancing for EV chargers in Home Assistant, using the lbbrhzn/ocpp integration and a power meter.

This project aims to give Home Assistant users a flexible solution to limit and distribute charging current to one or more EV chargers based on a whole-home power meter (or solar production), household limits, and user preferences.

Status: Research / Prototype

## Development docs

- All research, plans and design docs for development MUST be placed under `docs/development/` following the filename convention described in [`docs/development/README.md`](docs/development/README.md).
- See the current research plan: [`docs/development/2026-02-19-research-plan.md`](docs/development/2026-02-19-research-plan.md)
- See development docs README: [`docs/development/README.md`](docs/development/README.md)

## Repository rule (summary)

- New plans, research notes, and design docs MUST be created under:
  - `docs/development/<YYYY-MM-DD>-<short-descriptive-file-name>.md`
- Use ISO date prefix `YYYY-MM-DD` to make files sortable and clearly timestamped.
- If the document is a product requirements document (PRD) that you prefer to separate, create `docs/prd/` and note it in `docs/development/README.md` (maintainers should discuss and formalize).

## Quick start / Next actions

- To validate functionality quickly, we recommend:
  1. Inspect the `lbbrhzn/ocpp` integration to determine exact OCPP service names and payloads.
  2. Prototype with AppDaemon (fast) or with a blueprint (no extra runtime) for a single-charger setup.
  3. If persistent sensors and better UX are required, migrate to a small custom integration (HACS).

## Contributing (short tip)

- When adding plans or design docs, follow the docs rule above.
- For code contributions, open PRs against the repository default branch and reference the relevant docs under `docs/development/`.

For the full research plan, implementation notes and a proposed entity/input model, see:
- [`docs/development/2026-02-19-research-plan.md`](docs/development/2026-02-19-research-plan.md)
