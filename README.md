# EV Charger Load Balancing (HACS)

A custom Home Assistant integration (HACS) that provides dynamic load balancing for EV chargers, using a power meter and the lbbrhzn/ocpp integration.

This project gives Home Assistant users a native, no-helper-required solution to limit charging current to an EV charger based on a whole-home power meter, household limits, and user preferences.

---

> ⚠️ **DISCLAIMER — Use at your own risk.**
>
> This integration is provided **as-is**, without any warranty of any kind, express or implied. Installing and running this software may affect your EV charger, home electrical circuit, and connected devices. You are solely responsible for any consequences that result from its use.
>
> You are free to **review, test, and audit** the source code before using it. Contributions, bug reports, and security disclosures are welcome.

---

> ⚠️ **Current limitation (PR-1):** This integration currently supports **one charger per instance**. Multiple-charger support (water-filling fair distribution) is planned for a future PR. Multiple instances of this integration are not supported — only one config entry can be created. See the [roadmap](docs/development/2026-02-19-research-plan.md) for details.

Status: In development — custom integration (PR-2: core entities + device linking complete)

## Why a custom integration?

During prototyping we evaluated AppDaemon apps and automation blueprints as potential delivery mechanisms. Both were rejected for the same reason: **every runtime-configurable parameter (service current, voltage, charger limits, enable toggle) must be a manually created `input_number` / `input_boolean` helper** because neither AppDaemon nor blueprints can create Config Entries in HA.

A **custom HACS integration with Config Flow** solves this:

- **No manual helper creation** — the user configures everything through Settings → Integrations → Add (a guided UI).
- **Native HA entities** — the integration registers `number`, `switch`, `sensor`, and `binary_sensor` entities linked to a proper device in Settings → Devices.
- **Persistent state** — config entries and entity states survive HA restarts.
- **Multi-charger support** *(planned)* — future options flow will handle adding/removing chargers at runtime; currently one charger per instance only.
- **HACS distribution** — install via HACS, configure in the UI, no YAML required.

See [`docs/development/2026-02-19-lessons-learned.md`](docs/development/2026-02-19-lessons-learned.md) for the full evaluation.

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

### Multi-charger fairness — planned feature

> ⚠️ **Not yet implemented.** Multi-charger support is planned for a future PR (PR-5/PR-6). The water-filling algorithm is already unit-tested in `tests/test_load_balancer.py` and will be ported into the integration runtime. Possible future approaches include multiple config entries (one per power meter / site) or a single entry with an options flow to add/remove chargers.

When multiple chargers are active the available current will be distributed fairly using a water-filling algorithm:

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
- See the lessons learned (AppDaemon/blueprint evaluation): [`docs/development/2026-02-19-lessons-learned.md`](docs/development/2026-02-19-lessons-learned.md)
- See development docs README: [`docs/development/README.md`](docs/development/README.md)

## Quick start / Next actions

1. ~~Scaffold `custom_components/ev_lb/` with `manifest.json`, `__init__.py`, `config_flow.py`.~~ ✅ Done (PR-1)
2. ~~Add `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py`.~~ ✅ Done (PR-2)
3. Port the computation core from `tests/` into the integration.
4. Write HA integration tests using `pytest-homeassistant-custom-component`.
5. Publish via HACS.

## Contributing (short tip)

- When adding plans or design docs, follow the docs rule above.
- For code contributions, open PRs against the repository default branch and reference the relevant docs under `docs/development/`.

For the full research plan, design decisions, and lessons learned, see:
- [`docs/development/2026-02-19-research-plan.md`](docs/development/2026-02-19-research-plan.md)
- [`docs/development/2026-02-19-lessons-learned.md`](docs/development/2026-02-19-lessons-learned.md)

---

> 🤖 **AI Disclosure**
>
> A significant portion of this project — including code, documentation, and design — was developed with the assistance of AI tools (GitHub Copilot / large-language models). All AI-generated output has been reviewed, but users and contributors should audit the code independently before relying on it in production environments.
