# Development docs — repository rule

This directory stores development-focused documents: research plans, design notes, prototypes, architecture choices, and meeting outcomes.

## Rule (required)

- Every new plan, research note, design doc or similar development artifact MUST be added under this directory with a filename that begins with the ISO date of creation in the format:
  - `docs/development/<YYYY-MM-DD>-<short-descriptive-file-name>.md`

## Why

- Date-prefixing keeps materials chronologically ordered and helps with traceability.
- Centralizing development artifacts reduces duplication and helps contributors find the latest working plan.

## Filename convention

- Use lowercase, hyphen-separated descriptive names.
- Examples:
  - `docs/development/2026-02-19-research-plan.md`
  - `docs/development/2026-02-19-prototype-notes.md`
  - `docs/development/2026-03-01-integration-design.md`

## Template header for new docs

Copy this header into each new file:

```text
Title: Short descriptive title
Date: YYYY-MM-DD
Author: <your-github-username>
Status: draft | in-review | approved
Summary: One-line summary of this document
---
<document body>
```

## When to use docs/prd vs docs/development

- `docs/development/` is the default place for research and development artifacts.
- If you create formal product requirement documents (PRDs) that should be distinguished from development notes, consider placing them under `docs/prd/`.
- This repository defaults to `docs/development/`. If maintainers prefer a different policy (e.g., `docs/prd/` for PRDs), open an issue to formalize the naming and update this README.

## How to add a document

1. Create a new file with the ISO date prefix and descriptive name.
2. Add the template header.
3. Add the document content (status, context, deliverables, next steps).
4. Open a PR referencing related issues or discussions.

## Notes

- Keep documents focused and link to other documents rather than duplicating content.
- Include related images and assets in `docs/assets/` if needed.
- Consider adding a changelog section at the bottom of a doc if it will be iterated upon frequently.
