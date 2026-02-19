# EV Charger Load Balancing (HACS)

Add-on / integration to provide dynamic load balancing for EV chargers in Home Assistant, using the lbbrhzn/ocpp integration and a power meter.

This project aims to give Home Assistant users a flexible solution to limit and distribute charging current to one or more EV chargers based on a whole-home power meter (or solar production), household limits, and user preferences.

Status: Research / Prototype

## How it works

### Inputs

| Input | Description |
|---|---|
| **Service voltage** (V) | Nominal supply voltage — used to convert power (W) ↔ current (A) |
| **Service current** (A) | Maximum whole-house breaker rating; the system never exceeds this |
| **Power meter** (W) | Real-time total household consumption, including active EV charging |
| **Max charger current** (A) | Per-charger upper limit; can be changed at runtime |
| **Min EV current** (A) | Lowest current at which the charger can operate (IEC 61851: 6 A); below this charging must stop |
| **Ramp-up time** (s) | Cooldown before allowing current to increase after a dynamic reduction (default 30 s) |
| **Actions** | User-supplied scripts: `set_current`, `stop_charging`, `start_charging` |

---

### Decision loop

Every time the power meter reports a new value, the balancer runs the following logic:

```
Power meter changes
        │
        ▼
┌──────────────────────────────────────────┐
│  Compute available headroom              │
│                                          │
│  available_a = service_current_a         │
│                - house_power_w / voltage_v│
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  target_a = min(current_ev_a + available_a,              │
│                 max_charger_a)                           │
│  (floor to 1 A step)                                     │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────┐
│  target_a < min_ev_a ?       │──── YES ──▶  stop_charging()  ◀── instant
└──────────────────────────────┘              (charger OFF)
        │ NO
        ▼
┌─────────────────────────────────┐
│  target_a < current_a ?         │
│  (load increased → must reduce) │
└─────────────────────────────────┘
        │ YES                         │ NO (load decreased → may increase)
        ▼                             ▼
  set_current(target_a)   ┌──────────────────────────────────┐
  ◀── instant             │  ramp-up cooldown elapsed?       │
                          │  (time since last reduction       │
                          │   ≥ ramp_up_time_s)               │
                          └──────────────────────────────────┘
                                │ YES                  │ NO
                                ▼                      ▼
                          set_current(target_a)   hold current
                          ◀── allowed              (wait and retry
                                                    next cycle)
```

---

### Charger state transitions

```
         ┌─────────────────────────────────────────────────────────────────┐
         │                                                                 │
         │  target_a ≥ min_ev_a  AND  ramp-up elapsed (or first start)    │
         ▼                                                                 │
  ┌─────────────┐    target_a < min_ev_a       ┌──────────────────┐       │
  │   CHARGING  │ ──────────────────────────▶  │   STOPPED        │       │
  │  (current   │  ◀── instant stop            │  (charger off)   │       │
  │   = target) │                              └──────────────────┘       │
  └─────────────┘                                       │                 │
         ▲                                              │ target_a         │
         │                                              │ ≥ min_ev_a       │
         │                                              │ AND              │
         └──────────────────────────────────────────────┘ ramp-up elapsed ┘
              start_charging()  then  set_current(target_a)
```

Key rules:
- **Reductions are always instant** — the moment household load rises above the limit, the charger current is reduced on the very next power-meter event.
- **Increases are held for `ramp_up_time_s`** after any reduction — this prevents rapid oscillation when load hovers near the service limit.
- **Stopping charging** happens when even the minimum current would exceed the service limit.
- **Resuming charging** happens when the available current rises back above the minimum threshold and the ramp-up cooldown has elapsed.

---

### Multi-charger fairness (water-filling)

When multiple chargers are active the available current is distributed fairly using a water-filling algorithm:

```
Available current pool
────────────────────────────────────────────
   Charger A (max 10 A)  │  Charger B (max 32 A)
   ───────────────────── │  ──────────────────────
   fair share = pool / N │  fair share = pool / N
                         │
   if share ≥ max A      │  gets share
   → cap at 10 A         │
   → unused headroom     │
     returned to pool    │
────────────────────────────────────────────
   Remaining pool re-divided across uncapped chargers
```

1. Divide the pool equally among all active chargers.
2. Chargers that reach their per-charger maximum are capped; the surplus is returned to the pool.
3. Chargers whose share would fall below `min_ev_a` are stopped; they leave the pool.
4. Repeat until all remaining chargers have a valid fair share.

---

## Development docs

- All research, plans and design docs for development MUST be placed under `docs/development/` following the filename convention described in [`docs/development/README.md`](docs/development/README.md).
- See the current research plan: [`docs/development/2026-02-19-research-plan.md`](docs/development/2026-02-19-research-plan.md)
- See the testing guide: [`docs/development/2026-02-19-testing-guide.md`](docs/development/2026-02-19-testing-guide.md)
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
