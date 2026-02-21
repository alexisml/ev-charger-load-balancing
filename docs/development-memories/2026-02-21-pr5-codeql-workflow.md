Title: PR-5 — Add GitHub CodeQL code scanning workflow
Date: 2026-02-21
Author: copilot
Status: in-review
Summary: Documents why CodeQL was added, the configuration choices made, and lessons learned.

---

## Context

The repository had no automated static security analysis. Adding CodeQL via GitHub Actions surfaces security findings under **Security → Code scanning** without any external service dependency, and it is free for public repositories.

## What changed

- **`.github/workflows/codeql.yml`** (new file): CodeQL analysis workflow for Python.
- **`.github/workflows/tests.yml`**: Added `timeout-minutes: 10` to the unit-tests job.
- **`README.md`**: Added a CodeQL status badge alongside the existing Unit Tests and Codecov badges.

## Design decisions

### 1. CodeQL action version: v4

The workflow uses `github/codeql-action/init@v4` and `github/codeql-action/analyze@v4`, the current major version as of 2026-02-21 per the [GitHub starter workflow](https://github.com/actions/starter-workflows/blob/main/code-scanning/codeql.yml). v3 was the initial version used and was upgraded immediately based on reviewer feedback.

### 2. Language: `python`, `build-mode: none`

Python is an interpreted language — CodeQL does not need a compilation step to extract facts from the source. `build-mode: none` makes this explicit and avoids any risk of a spurious autobuild attempt.

### 3. Triggers: push + pull_request + weekly schedule

- **`push` to `main`**: Scans each merge so the Security tab always reflects the current state of the default branch.
- **`pull_request` targeting `main`**: Acts as a PR gate — findings are reported inline on the PR diff before merge.
- **`schedule: 30 1 * * 1`** (every Monday at 01:30 UTC): Catches newly-disclosed CVEs or updated CodeQL query packs that may affect code that hasn't changed.

### 4. Permissions (least-privilege)

| Permission | Why |
|---|---|
| `contents: read` | Check out repository source code |
| `security-events: write` | Upload SARIF results to GitHub Security → Code scanning |
| `packages: read` | Fetch internal or private CodeQL query packs |
| `actions: read` | Read workflow run context (required on private repositories) |

`packages: read` and `actions: read` follow the GitHub starter workflow recommendation. They are harmless on public repositories and ensure the workflow works unchanged if the repo is ever made private.

### 5. Timeout: 5 minutes for CodeQL, 10 minutes for unit tests

CodeQL Python analysis on this small codebase (one custom component, ~500 LOC) completes in under 2 minutes in practice. A 5-minute ceiling prevents runaway jobs without being so tight that transient slowness causes false failures. The unit-test job was similarly capped at 10 minutes for consistency.

## Lessons learned

- The GitHub starter workflow (`actions/starter-workflows`) is the authoritative reference for recommended CodeQL settings and is updated as new major versions are released. Always check it before creating a new CodeQL workflow.
- `build-mode: none` should always be set explicitly for Python to document the intent and prevent surprises if CodeQL's defaults change.
- `fail-fast: false` on a matrix is only relevant when there is more than one language. For a single-language repo the matrix can be omitted entirely to reduce YAML noise.

## What's next

- Verify that code scanning alerts appear under **Security → Code scanning** after the first run on `main`.
- If the repository is ever extended with JavaScript/TypeScript tooling (e.g., a dashboard frontend), add `javascript-typescript` to the `languages` list.
- Consider enabling the `security-and-quality` query suite (`queries: security-and-quality` in the `init` step) once the team is ready to triage additional code-quality findings.
