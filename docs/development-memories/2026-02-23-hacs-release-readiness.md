Title: HACS release readiness — versioning, validation, and release workflow
Date: 2026-02-23
Author: copilot
Status: in-review
Summary: Added CI workflows and documentation for HACS publishing: a HACS validation workflow, an automated release workflow with calendar versioning, and a release guide.

---

## Context

The MVP (Phase 1) is complete, but the repository lacked the CI automation needed to publish releases via HACS. The user asked how versioning and releases work for HACS distribution.

## Versioning scheme

Uses **calendar versioning** matching the Home Assistant convention: `YYYY.M.N`

- `YYYY` — four-digit year
- `M` — month number (no leading zero)
- `N` — zero-based release counter within that month

Examples: `2026.2.0` (first release in Feb 2026), `2026.2.1` (second), `2026.3.0` (first release in Mar).

The script `scripts/bump_version.py` automatically computes the next version by inspecting existing git tags and the current UTC date.

## What HACS requires for publishing

1. **`manifest.json` with a `version` field** — set to `2026.2.0` (CalVer).
2. **`hacs.json`** — already present with `render_readme`, `homeassistant`, and `hacs` minimum versions.
3. **GitHub releases** — HACS uses GitHub releases (tags) to track available versions. Users see the latest release version in the HACS UI.
4. **Repository structure** — `custom_components/<domain>/` at the repo root. Already correct.

## What was added

### Version bump script (`scripts/bump_version.py`)

Computes the next calendar version automatically:
- Reads existing `v*` git tags
- Finds the highest release number for the current year+month
- Increments the release counter (or starts at 0 for a new month)
- With `--apply`, updates `manifest.json` in place

### HACS validation workflow (`.github/workflows/hacs-validate.yml`)

Runs the official `hacs/action@main` on every push to `main` and every PR. This validates:
- Repository structure (`custom_components/` layout)
- `manifest.json` fields (domain, version, codeowners, etc.)
- `hacs.json` configuration
- Required files (e.g., `__init__.py`)

This catches HACS compliance issues before they reach users.

### Release workflow (`.github/workflows/release.yml`)

Triggered via `workflow_dispatch` — click "Run workflow" in the Actions tab. The workflow:
- Runs `scripts/bump_version.py` to compute the next version
- Creates a `release/vYYYY.M.N` branch, applies the version bump, and pushes it
- Opens a PR targeting `main` titled "Bump version to YYYY.M.N"

### Publish workflow (`.github/workflows/publish.yml`)

Triggered automatically when `custom_components/ev_lb/manifest.json` is pushed to `main` (i.e., when the release PR is merged). The workflow:
- Reads the version from `manifest.json`
- Creates and pushes the `vYYYY.M.N` tag
- Creates a GitHub release with auto-generated notes and a zip asset

### README badge

Added a HACS Validation badge to the README alongside existing CI badges.

## How to create a release

1. Go to **Actions → Release → Run workflow**.
2. The workflow computes the next version, creates a `release/vX.Y.Z` branch, applies the version bump, and opens a PR.
3. Review and merge the PR into `main`.
4. Merging triggers the **Publish Release** workflow automatically, which creates the tag and GitHub release.

## How to get into the HACS default repository list

The repository can already be added as a **custom repository** in HACS (HACS → ⋮ → Custom repositories → paste the GitHub URL → Integration). This works immediately once a GitHub release exists.

To appear in the **default** HACS repository list (so users find it by searching in HACS without adding a custom URL), a PR must be submitted to [hacs/default](https://github.com/hacs/default). Requirements:
- At least one GitHub release
- Passing HACS validation
- A descriptive README
- All standard HACS metadata files

## Decision record

- **Versioning scheme:** Calendar versioning `YYYY.M.N` matching Home Assistant convention.
- **Tag format:** `v2026.2.0` (with `v` prefix) — this is the most common convention for GitHub releases and avoids ambiguity with branch names.
- **Version generation:** Automated via `scripts/bump_version.py` — inspects git tags + current date.
- **Release asset:** A zip of `custom_components/ev_lb/` is attached for users who prefer manual installation.
