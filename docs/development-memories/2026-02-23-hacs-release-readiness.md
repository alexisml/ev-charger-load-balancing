Title: HACS release readiness — versioning, validation, and release workflow
Date: 2026-02-23
Author: copilot
Status: in-review
Summary: Added CI workflows and documentation for HACS publishing: a HACS validation workflow, an automated release workflow, and a release guide.

---

## Context

The MVP (Phase 1) is complete, but the repository lacked the CI automation needed to publish releases via HACS. The user asked how versioning and releases work for HACS distribution.

## What HACS requires for publishing

1. **`manifest.json` with a `version` field** — already present (`0.1.0`).
2. **`hacs.json`** — already present with `render_readme`, `homeassistant`, and `hacs` minimum versions.
3. **GitHub releases** — HACS uses GitHub releases (tags) to track available versions. Users see the latest release version in the HACS UI.
4. **Repository structure** — `custom_components/<domain>/` at the repo root. Already correct.

## What was added

### HACS validation workflow (`.github/workflows/hacs-validate.yml`)

Runs the official `hacs/action@main` on every push to `main` and every PR. This validates:
- Repository structure (`custom_components/` layout)
- `manifest.json` fields (domain, version, codeowners, etc.)
- `hacs.json` configuration
- Required files (e.g., `__init__.py`)

This catches HACS compliance issues before they reach users.

### Release workflow (`.github/workflows/release.yml`)

Triggered when a version tag (`v*`) is pushed. It:
1. Extracts the version from the tag (e.g., `v0.1.0` → `0.1.0`).
2. Verifies `manifest.json` version matches the tag — fails if they diverge.
3. Zips the `custom_components/ev_lb/` directory as a release asset.
4. Creates a GitHub release with auto-generated release notes.

### README badge

Added a HACS Validation badge to the README alongside existing CI badges.

## How to create a release

1. Update `version` in `custom_components/ev_lb/manifest.json` to the new version (e.g., `0.1.0`).
2. Commit and push to `main`.
3. Create and push a matching git tag: `git tag v0.1.0 && git push origin v0.1.0`.
4. The release workflow automatically creates a GitHub release with release notes and a zip asset.
5. HACS users see the new version and can update.

## How to get into the HACS default repository list

The repository can already be added as a **custom repository** in HACS (HACS → ⋮ → Custom repositories → paste the GitHub URL → Integration). This works immediately once a GitHub release exists.

To appear in the **default** HACS repository list (so users find it by searching in HACS without adding a custom URL), a PR must be submitted to [hacs/default](https://github.com/hacs/default). Requirements:
- At least one GitHub release
- Passing HACS validation
- A descriptive README
- All standard HACS metadata files

## Decision record

- **Tag format:** `v0.1.0` (with `v` prefix) — this is the most common convention for GitHub releases and avoids ambiguity with branch names.
- **Version verification:** The release workflow verifies that `manifest.json` version matches the tag to prevent mismatches.
- **Release asset:** A zip of `custom_components/ev_lb/` is attached for users who prefer manual installation.
