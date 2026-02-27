#!/usr/bin/env python3
"""Generate the next calendar version (YYYY.M.N) based on git tags and the current date.

Versioning follows the Home Assistant convention:
  - YYYY = four-digit year
  - M    = month number (no leading zero)
  - N    = zero-based release counter within that month

Examples: 2026.2.0, 2026.2.1, 2026.3.0

Pre-release versions use the branch name as the third component:
  YYYY.M.branch-slug

Examples: 2026.2.feature-my-work, 2026.2.fix-some-bug

Usage:
  python scripts/bump_version.py                            # print the next release version
  python scripts/bump_version.py --apply                    # also update manifest.json
  python scripts/bump_version.py --prerelease <branch>      # print a pre-release version
  python scripts/bump_version.py --prerelease <branch> --apply  # also update manifest.json
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
_BRANCH_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_BRANCH_SLUG_TRIM = re.compile(r"^-+|-+$")


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


def branch_slug(branch: str) -> str:
    """Return a URL-safe, lowercase slug derived from a git branch name.

    Slashes (e.g. ``feature/my-work``) are replaced with dashes, all
    non-alphanumeric characters are collapsed to a single dash, and
    leading/trailing dashes are stripped.

    Examples::

        branch_slug("feature/my-work")  -> "feature-my-work"
        branch_slug("fix/some_bug")     -> "fix-some-bug"
        branch_slug("main")             -> "main"
    """
    slug = branch.lower().replace("/", "-")
    slug = _BRANCH_SLUG_STRIP.sub("-", slug)
    return _BRANCH_SLUG_TRIM.sub("", slug)


def prerelease_version(branch: str) -> str:
    """Compute a pre-release calendar version for the given branch.

    The format is ``YYYY.M.branch-slug`` where ``branch-slug`` is a
    sanitised, lowercase representation of *branch* (slashes and
    non-alphanumeric characters become dashes).

    Examples::

        prerelease_version("feature/my-work")  -> "2026.2.feature-my-work"
        prerelease_version("main")             -> "2026.2.main"
    """
    now = datetime.now(tz=timezone.utc)
    slug = branch_slug(branch)
    return f"{now.year}.{now.month}.{slug}"


def main() -> None:
    """Entry point."""
    apply = "--apply" in sys.argv

    if "--prerelease" in sys.argv:
        idx = sys.argv.index("--prerelease")
        if idx + 1 >= len(sys.argv) or sys.argv[idx + 1].startswith("-"):
            print("Usage: bump_version.py --prerelease <branch> [--apply]", file=sys.stderr)
            sys.exit(1)
        branch = sys.argv[idx + 1]
        version = prerelease_version(branch)
    else:
        version = next_version()

    if apply:
        update_manifest(version)
        print(f"Updated {MANIFEST_PATH} and {TOP_MANIFEST_PATH} to {version}")
    else:
        print(version)


if __name__ == "__main__":
    main()
