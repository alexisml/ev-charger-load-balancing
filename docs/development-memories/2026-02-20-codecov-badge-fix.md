Title: Codecov badge fix — replace deprecated test-results-action
Date: 2026-02-20
Author: copilot
Status: in-review
Summary: Documents why the Codecov workflow was updated and what was changed to fix the "unknown" badge.

---

## Context

The Codecov coverage badge displayed "unknown" because the CI workflow was using the deprecated `codecov/test-results-action@v1` action to upload JUnit test results. The Codecov docs now recommend using `codecov/codecov-action@v5` for all uploads (coverage and test results).

## What changed

- **`.github/workflows/tests.yml`**: Removed the deprecated `codecov/test-results-action@v1` step and the duplicate `codecov/codecov-action@v5` step that uploaded `junit.xml`.
- Consolidated both into a single `codecov/codecov-action@v5` step that uploads `results/coverage.xml` with the full set of recommended parameters:
  - `flags: unittests`
  - `name: codecov-umbrella`
  - `fail_ci_if_error: false` (avoids CI failures when Codecov is unavailable)
  - `verbose: true`
  - `if: ${{ !cancelled() }}` guard so coverage is still uploaded even if a prior step fails

JUnit test results (`results/junit.xml`) continue to be published via `EnricoMi/publish-unit-test-result-action` and saved as a workflow artifact — they do not need to be sent to Codecov separately.

## Design decisions

1. **`fail_ci_if_error: false`** — Codecov outages should not block merges; coverage upload is best-effort.
2. **Single upload step** — Uploading coverage and test results as two separate steps with the same action was redundant. Coverage XML is the primary input Codecov needs to compute the badge.
3. **`if: ${{ !cancelled() }}`** — Ensures the upload step still runs even when a test assertion fails mid-run, so partial coverage data is captured.

## What's next

- Verify the badge updates to a percentage after the next CI run on `main`.
- If Codecov still shows "unknown", confirm the `CODECOV_TOKEN` secret is set in the repository settings.
