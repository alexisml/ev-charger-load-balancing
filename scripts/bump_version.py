#!/usr/bin/env python3
"""Generate the next calendar version (YYYY.M.N) based on git tags and the current date.

Versioning follows the Home Assistant convention:
  - YYYY = four-digit year
  - M    = month number (no leading zero)
  - N    = zero-based release counter within that month

Examples: 2026.2.0, 2026.2.1, 2026.3.0

Usage:
  python scripts/bump_version.py            # print the next version
  python scripts/bump_version.py --apply    # also update manifest.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "ev_lb" / "manifest.json"
TOP_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"
TAG_PATTERN = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d+)$")


def get_existing_tags() -> list[str]:
    """Return all git tags in the repository."""
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip().splitlines() if result.returncode == 0 else []


def next_version() -> str:
    """Compute the next calendar version based on existing tags and the current UTC date."""
    now = datetime.now(tz=timezone.utc)
    year, month = now.year, now.month

    max_release = -1
    for tag in get_existing_tags():
        m = TAG_PATTERN.match(tag)
        if m and int(m.group(1)) == year and int(m.group(2)) == month:
            max_release = max(max_release, int(m.group(3)))

    return f"{year}.{month}.{max_release + 1}"


def update_manifest(version: str) -> None:
    """Write the new version into both manifest.json files."""
    for path in (MANIFEST_PATH, TOP_MANIFEST_PATH):
        data = json.loads(path.read_text())
        data["version"] = version
        path.write_text(json.dumps(data, indent=2) + "\n")


def main() -> None:
    """Entry point."""
    version = next_version()

    if "--apply" in sys.argv:
        update_manifest(version)
        print(f"Updated {MANIFEST_PATH} and {TOP_MANIFEST_PATH} to {version}")
    else:
        print(version)


if __name__ == "__main__":
    main()
