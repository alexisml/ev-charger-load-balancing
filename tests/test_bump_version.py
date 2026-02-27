"""Unit tests for scripts/bump_version.py — version computation logic.

Covers:
- branch_slug: sanitises arbitrary git branch names into URL-safe slugs
- prerelease_version: produces YYYY.M.branch-slug[.N] for a given branch, incrementing N when the base tag already exists
- next_version: computes the next YYYY.M.N counter from existing tags
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Import the script module directly (it lives outside the package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py"
_spec = importlib.util.spec_from_file_location("bump_version", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

branch_slug = _mod.branch_slug
prerelease_version = _mod.prerelease_version
next_version = _mod.next_version


# ---------------------------------------------------------------------------
# branch_slug
# ---------------------------------------------------------------------------


class TestBranchSlug:
    """Verify that branch names are sanitised into safe, lowercase slugs."""

    def test_simple_branch_unchanged(self):
        """A plain lowercase branch name with no special characters passes through unchanged."""
        assert branch_slug("main") == "main"

    def test_slash_replaced_with_dash(self):
        """Slashes in feature branch names (e.g. feature/my-work) are converted to dashes."""
        assert branch_slug("feature/my-work") == "feature-my-work"

    def test_underscore_replaced_with_dash(self):
        """Underscores are normalised to dashes so the slug stays consistent."""
        assert branch_slug("fix/some_bug") == "fix-some-bug"

    def test_uppercase_lowercased(self):
        """Branch names containing uppercase letters are lowercased in the slug."""
        assert branch_slug("Feature/MyWork") == "feature-mywork"

    def test_consecutive_separators_collapsed(self):
        """Multiple adjacent non-alphanumeric characters collapse to a single dash."""
        assert branch_slug("fix//double--slash") == "fix-double-slash"

    def test_leading_and_trailing_dashes_stripped(self):
        """Slugs never start or end with a dash, even if the raw branch name would produce one."""
        assert branch_slug("/leading-slash") == "leading-slash"

    def test_numeric_branch_name_preserved(self):
        """Branch names that are purely numeric (e.g. issue numbers) are preserved."""
        assert branch_slug("123") == "123"


# ---------------------------------------------------------------------------
# prerelease_version
# ---------------------------------------------------------------------------


class TestPrereleaseVersion:
    """Verify that pre-release versions follow the YYYY.M.branch-slug[.N] format."""

    def test_no_existing_tag_returns_base_version(self):
        """The first pre-release for a branch has no counter suffix."""
        with patch.object(_mod, "get_existing_tags", return_value=[]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            result = prerelease_version("my-feature")
        assert result == "2026.2.my-feature"

    def test_feature_slash_branch_produces_slug(self):
        """A feature/branch-name is slugified so the slash does not appear in the version."""
        with patch.object(_mod, "get_existing_tags", return_value=[]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            result = prerelease_version("feature/add-prerelease")
        assert result == "2026.2.feature-add-prerelease"

    def test_month_with_no_leading_zero(self):
        """Month numbers below 10 appear without a leading zero in the version string."""
        with patch.object(_mod, "get_existing_tags", return_value=[]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 1, tzinfo=timezone.utc)
            result = prerelease_version("fix/something")
        assert result == "2026.3.fix-something"

    def test_year_and_month_reflect_current_date(self):
        """The year and month in the pre-release version always come from the current UTC date."""
        with patch.object(_mod, "get_existing_tags", return_value=[]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2027, 11, 5, tzinfo=timezone.utc)
            result = prerelease_version("hotfix")
        assert result.startswith("2027.11.")

    def test_base_tag_exists_appends_counter_one(self):
        """When the base pre-release tag already exists, the counter suffix .1 is appended."""
        with patch.object(_mod, "get_existing_tags", return_value=["v2026.2.my-feature"]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            result = prerelease_version("my-feature")
        assert result == "2026.2.my-feature.1"

    def test_counter_increments_beyond_one(self):
        """Each subsequent pre-release for the same branch increments the counter."""
        tags = ["v2026.2.my-feature", "v2026.2.my-feature.1", "v2026.2.my-feature.2"]
        with patch.object(_mod, "get_existing_tags", return_value=tags), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            result = prerelease_version("my-feature")
        assert result == "2026.2.my-feature.3"

    def test_other_branch_tags_do_not_affect_counter(self):
        """Pre-release tags for a different branch slug do not influence the counter for the current branch."""
        tags = ["v2026.2.other-branch", "v2026.2.other-branch.1"]
        with patch.object(_mod, "get_existing_tags", return_value=tags), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            result = prerelease_version("my-feature")
        assert result == "2026.2.my-feature"


# ---------------------------------------------------------------------------
# next_version (existing behaviour — regression guard)
# ---------------------------------------------------------------------------


class TestNextVersion:
    """Verify that the regular release counter is unaffected by pre-release tags."""

    def test_no_tags_starts_counter_at_zero(self):
        """When no release tags exist, the first release is numbered 0."""
        with patch.object(_mod, "get_existing_tags", return_value=[]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            assert next_version() == "2026.2.0"

    def test_existing_release_increments_counter(self):
        """A pre-existing release tag for the same month increments the counter."""
        with patch.object(_mod, "get_existing_tags", return_value=["v2026.2.0"]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            assert next_version() == "2026.2.1"

    def test_prerelease_tags_ignored_by_counter(self):
        """Pre-release tags (non-numeric third component) are ignored when computing the next release."""
        with patch.object(_mod, "get_existing_tags", return_value=["v2026.2.0", "v2026.2.feature-abc"]), \
             patch.object(_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, tzinfo=timezone.utc)
            assert next_version() == "2026.2.1"
