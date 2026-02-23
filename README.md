[![Unit Tests](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/tests.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/alexisml/ha-ev-charger-balancer/graph/badge.svg?token=GOO252H72J)](https://codecov.io/gh/alexisml/ha-ev-charger-balancer)
[![CodeQL](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/codeql.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/codeql.yml)
[![Ruff](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/ruff.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/ruff.yml)
[![Type Check](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/type-check.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/type-check.yml)
[![Spell Check](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/spell-check.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/spell-check.yml)
[![Gitleaks](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/gitleaks.yml)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-brightgreen?logo=dependabot)](https://github.com/alexisml/ha-ev-charger-balancer/blob/main/.github/dependabot.yml)
<!-- Dynamic badges: replace BADGE_GIST_ID below after one-time setup (see .github/workflows/tests.yml) -->
[![Tests](https://img.shields.io/endpoint?url=https%3A%2F%2Fgist.githubusercontent.com%2Falexisml%2FBADGE_GIST_ID%2Fraw%2Fev_lb_test_count.json)](https://github.com/alexisml/ha-ev-charger-balancer/actions/workflows/tests.yml)
[![Lines of Code](https://img.shields.io/endpoint?url=https%3A%2F%2Fgist.githubusercontent.com%2Falexisml%2FBADGE_GIST_ID%2Fraw%2Fev_lb_loc.json)](https://github.com/alexisml/ha-ev-charger-balancer)

# EV Charger Load Balancing (HACS)

A custom Home Assistant integration (HACS) that dynamically adjusts your EV charger's charging current based on real-time household power consumption, ensuring you never exceed your electrical service limit.

---

> ⚠️ **DISCLAIMER — Use at your own risk.**
>
> This integration is provided **as-is**, without any warranty of any kind, express or implied. It is a software load balancer, not a replacement for proper electrical protection (breakers, fuses, RCDs). You are solely responsible for any consequences that result from its use.
>
> You are free to **review, test, and audit** the source code before using it. Contributions, bug reports, and security disclosures are welcome.

---

## What it does

The integration watches your home's power meter. When total household consumption changes, it instantly recalculates how much current your EV charger can safely use without tripping your main breaker. If load goes up, charger current goes down — **immediately**. If load goes down, charger current goes back up — after a short cooldown to prevent oscillation.

**Key features:**
- **Automatic load balancing** — adjusts charger current in real time based on your power meter
- **Safety-first** — reductions are instant; default behavior stops charging if the meter goes offline
- **No YAML required** — configure entirely through the Home Assistant UI
- **Hardware-agnostic** — works with any charger controllable via HA scripts (OCPP, Modbus, REST, etc.)
- **Full observability** — sensors, events, and persistent notifications for monitoring and automations

**Current limitation:** Supports **one charger** per instance. Multi-charger support is planned for [Phase 2](docs/documentation/milestones/02-2026-02-19-multi-charger-plan.md).

---

## 📖 Documentation

| Guide | Description |
|---|---|
| [**Installation & Setup**](docs/documentation/installation-and-setup.md) | Install via HACS, configure step-by-step, verify your setup |
| [**How It Works**](docs/documentation/how-it-works.md) | What to expect, what NOT to expect, entities reference, algorithm details |
| [**Troubleshooting & Debugging**](docs/documentation/troubleshooting-and-debugging.md) | Common problems, log interpretation, diagnostic sensors, FAQ |
| [**Development Guide**](docs/documentation/development-guide.md) | Architecture, running tests/CI locally, contributing, roadmap |

### Reference guides

| Guide | Description |
|---|---|
| [Action Scripts Guide](docs/documentation/action-scripts-guide.md) | Charger control scripts — OCPP, REST, Modbus, switch examples |
| [Event Notifications Guide](docs/documentation/event-notifications-guide.md) | Event types, payloads, automation examples for mobile alerts |
| [Logging Guide](docs/documentation/logging-guide.md) | Debug logs, log levels, diagnostic sensors |

---

## Quick start

1. **Install** via [HACS](https://hacs.xyz/) — see [Installation & Setup](docs/documentation/installation-and-setup.md)
2. **Configure** in Settings → Devices & Services → Add Integration → "EV Charger Load Balancing"
3. **Create action scripts** to control your charger — see [Action Scripts Guide](docs/documentation/action-scripts-guide.md)
4. **Monitor** via dashboard sensors and [event notifications](docs/documentation/event-notifications-guide.md)

---

## Contributing

See the [Development Guide](docs/documentation/development-guide.md) for architecture, testing, CI checks, and contribution guidelines.

Development artifacts (research, design decisions, PR retrospectives) are under [`docs/development-memories/`](docs/development-memories/README.md).

---

> 🤖 **AI Disclosure**
>
> A significant portion of this project — including code, documentation, and design — was developed with the assistance of AI tools (GitHub Copilot / large-language models). All AI-generated output has been reviewed, but users and contributors should audit the code independently before relying on it in production environments.
