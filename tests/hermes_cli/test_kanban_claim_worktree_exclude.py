"""A kanban claim must not leave the repo dirty with an unignored ``.worktrees/``.

``hermes kanban claim --workspace worktree:<repo>`` materializes
``<repo>/.worktrees/<task-id>`` (``_anchored_worktree``). Nothing excluded that
directory, so a live claim made ``git status`` read ``?? .worktrees/`` and any
gate keyed on a clean tree refused — ``deploy/upgrade.py`` in the
hermes-kanban-mcp repo being the case that found it, where the tree the deploy
was refusing was dirty *because of the claim that produced the work*.

The fix writes the entry to the repo-local ``info/exclude`` rather than the
tracked ``.gitignore``: ``worktree_ops._ensure_worktrees_gitignored`` appends to
``.gitignore``, which merely swaps ``?? .worktrees/`` for `` M .gitignore`` —
still dirty, so it would not have fixed the gate.

These tests assert the OBSERVABLE property (an empty ``git status``), not that
the helper was called, so they keep working if the mechanism changes again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db_workspace as kbw


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git", "-C", str(cwd),
            "-c", "user.name=Test User",
            "-c", "user.email=test@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout


def _exclude_path(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real repo with a COMMITTED .gitignore (the case that breaks a naive fix)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def test_claim_worktree_leaves_a_committed_gitignore_clean(repo: Path) -> None:
    kbw._anchored_worktree(repo, "t_demo0001", "wt/t_demo0001")

    assert (repo / ".worktrees" / "t_demo0001").is_dir()
    assert _status(repo) == "", "a claimed worktree left the repo dirty"
    # The tracked file must not be rewritten: that is the naive fix's failure.
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n"


def test_the_exclusion_is_idempotent_across_claims(repo: Path) -> None:
    kbw._anchored_worktree(repo, "t_demo0001", "wt/t_demo0001")
    # Second call for the SAME task hits the "already materialized" early
    # return — the exclusion must still be in place, and must not duplicate.
    kbw._anchored_worktree(repo, "t_demo0001", "wt/t_demo0001")
    kbw._anchored_worktree(repo, "t_demo0002", "wt/t_demo0002")

    assert _status(repo) == ""
    lines = _exclude_path(repo).read_text(encoding="utf-8").splitlines()
    assert lines.count(".worktrees/") == 1


def test_a_repo_without_a_gitignore_still_reads_clean(tmp_path: Path) -> None:
    root = tmp_path / "bare-ignore"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")

    kbw._anchored_worktree(root, "t_demo0003", "wt/t_demo0003")

    assert _status(root) == ""
    assert not (root / ".gitignore").exists(), "the fix must not create a tracked file"
