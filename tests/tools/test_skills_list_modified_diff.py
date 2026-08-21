"""Tests for discovering and diffing user-modified bundled skills.

`hermes update` keeps (does not overwrite) bundled skills the user edited
locally, but historically only printed a *count* — there was no way to find
which skills, or see what changed. These tests cover the two helpers that close
that gap, exercising the real sync pipeline (no mocks of the comparison logic):

* ``list_user_modified_bundled_skills()`` — the discovery half of the exact
  test the sync loop uses to decide what to skip.
* ``diff_bundled_skill()`` — a unified diff of the user copy vs the stock copy.

Revert already exists (``reset_bundled_skill``); the last test confirms it
clears the modified state so the two stay consistent.
"""

from contextlib import ExitStack
import shutil
from unittest.mock import patch

from tools.skills_sync import sync_skills
from tools.skills_sync_bundled_ops import (
    reset_bundled_skill,
    list_user_modified_bundled_skills,
    diff_bundled_skill,
)


def _make_bundled(tmp_path):
    """A fake bundled skills tree with one skill: category/foo."""
    bundled = tmp_path / "bundled_skills"
    foo = bundled / "category" / "foo"
    foo.mkdir(parents=True)
    (foo / "SKILL.md").write_text("---\nname: foo\n---\n# Foo Skill\n")
    (foo / "helper.py").write_text("print('stock')\n")
    return bundled


def _patches(bundled, skills_dir, manifest_file):
    stack = ExitStack()
    stack.enter_context(
        patch("tools.skills_sync._get_bundled_dir", return_value=bundled)
    )
    stack.enter_context(
        patch(
            "tools.skills_sync._get_optional_dir",
            return_value=bundled.parent / "optional-skills",
        )
    )
    stack.enter_context(patch("tools.skills_sync.SKILLS_DIR", skills_dir))
    stack.enter_context(patch("tools.skills_sync.MANIFEST_FILE", manifest_file))
    return stack


def _env(tmp_path):
    bundled = _make_bundled(tmp_path)
    skills_dir = tmp_path / "user_skills"
    manifest_file = skills_dir / ".bundled_manifest"
    return bundled, skills_dir, manifest_file


def test_pristine_skill_is_not_listed_as_modified(tmp_path):
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        assert list_user_modified_bundled_skills() == []


def test_reset_clears_modified_state(tmp_path):
    """Revert (existing) and discovery (new) must agree: after reset, not modified."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        (skills_dir / "category" / "foo" / "helper.py").write_text("print('mine')\n")
        assert [m["name"] for m in list_user_modified_bundled_skills()] == ["foo"]

        # Restore from the stock source, then it must no longer be flagged.
        result = reset_bundled_skill("foo", restore=True)
        assert result["ok"] is True
        assert list_user_modified_bundled_skills() == []


def test_fork_in_different_category_is_tracked(tmp_path):
    """A fork living in a DIFFERENT category than the bundled original must be
    tracked as a user modification.

    The bundled-category destination is gone (pruned/archived, the sdlc-review
    shape) and the user's fork lives at other-category/foo; the manifest hash
    must be compared against the fork, not silently skipped because the
    bundled-category path is missing.
    """
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        # Prune the bundled-category copy, then fork into another category.
        shutil.rmtree(skills_dir / "category" / "foo")
        fork = skills_dir / "other-category" / "foo"
        fork.mkdir(parents=True)
        (fork / "SKILL.md").write_text("---\nname: foo\n---\n# Foo Skill (fork)\n")
        (fork / "helper.py").write_text("print('forked')\n")

        modified = list_user_modified_bundled_skills()
        assert [m["name"] for m in modified] == ["foo"]
        assert modified[0].get("stale") is None
        assert modified[0]["dest"] == fork


def test_missing_dest_is_flagged_stale(tmp_path):
    """A manifest entry whose destination exists nowhere (pruned and not
    forked) must be surfaced as stale instead of silently skipped."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        shutil.rmtree(skills_dir / "category" / "foo")

        modified = list_user_modified_bundled_skills()
        assert [m["name"] for m in modified] == ["foo"]
        assert modified[0]["stale"] is True
        assert modified[0]["dest"] is None


def test_pristine_fork_elsewhere_is_not_modified(tmp_path):
    """A cross-category fork whose content matches the origin hash (fresh
    sync, untouched) must NOT be reported as modified — only stale-safe."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        shutil.rmtree(skills_dir / "category" / "foo")
        fork = skills_dir / "other-category" / "foo"
        fork.mkdir(parents=True)
        (fork / "SKILL.md").write_text("---\nname: foo\n---\n# Foo Skill\n")
        (fork / "helper.py").write_text("print('stock')\n")

        assert list_user_modified_bundled_skills() == []


def _fork_into_other_category(skills_dir, *, skill_md="# Foo Skill (fork)\n", helper="print('forked')\n"):
    """Prune the bundled-category copy of foo, then re-create it under another category."""
    shutil.rmtree(skills_dir / "category" / "foo")
    fork = skills_dir / "other-category" / "foo"
    fork.mkdir(parents=True)
    (fork / "SKILL.md").write_text(f"---\nname: foo\n---\n{skill_md}")
    (fork / "helper.py").write_text(helper)
    return fork


def test_diff_follows_a_fork_in_another_category(tmp_path):
    """``hermes skills diff`` must diff the fork that lives in a DIFFERENT category than
    the bundled original — the sdlc-review shape (bundled ``devops/sdlc-review``, live
    ``software-development/sdlc-review``).

    The bundled-category path does not exist for such a fork, and ``diff_bundled_skill``
    used to bail with "No local copy ... found" while ``hermes skills list-modified``
    named the skill — leaving skill-upstream-sync's read-both-versions step blind for
    every relocated bundled skill.
    """
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        fork = _fork_into_other_category(skills_dir)

        result = diff_bundled_skill("foo")
        assert result["ok"] is True, result["message"]
        assert result["found"] is True
        assert result["modified"] is True
        by_path = {entry["path"]: entry for entry in result["diffs"]}
        assert set(by_path) == {"SKILL.md", "helper.py"}
        assert by_path["helper.py"]["status"] == "modified"
        assert "-print('stock')" in by_path["helper.py"]["diff"]
        assert "+print('forked')" in by_path["helper.py"]["diff"]
        assert "# Foo Skill (fork)" in by_path["SKILL.md"]["diff"]
        # Same copy the discovery half reports, so the two can never disagree.
        assert [m["dest"] for m in list_user_modified_bundled_skills()] == [fork]


def test_diff_of_pristine_cross_category_copy_matches_stock(tmp_path):
    """A relocated copy that still matches the origin hash diffs as identical (the
    lookup finds the copy; the content comparison decides modified-ness)."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        _fork_into_other_category(skills_dir, skill_md="# Foo Skill\n", helper="print('stock')\n")

        result = diff_bundled_skill("foo")
        assert result["ok"] is True
        assert result["modified"] is False
        assert "matches the stock version" in result["message"]


def test_diff_reports_missing_local_copy(tmp_path):
    """Negative control for the cross-category lookup: a bundled skill with NO copy
    anywhere (pruned, never forked) still reports not-found — the name scan must not
    invent a match."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        shutil.rmtree(skills_dir / "category" / "foo")

        result = diff_bundled_skill("foo")
        assert result["ok"] is False
        assert result["found"] is True  # tracked bundled skill, just no local copy
        assert "No local copy of 'foo' found" in result["message"]
        assert result["diffs"] == []


def test_diff_rejects_untracked_skill(tmp_path):
    """An unknown/unbundled name reports found=False with the hub-install hint, not the
    missing-local-copy message."""
    bundled, skills_dir, manifest_file = _env(tmp_path)
    with _patches(bundled, skills_dir, manifest_file):
        sync_skills(quiet=True)
        result = diff_bundled_skill("nope")
        assert result["ok"] is False
        assert result["found"] is False
        assert "not a tracked bundled skill" in result["message"]
