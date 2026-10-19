"""A REVIEW run may not create tickets (Ryan's rule, 2026-09-18).

A reviewer returns a verdict and may suggest work for someone else to ticket. Before this gate
`kanban_create` was mounted on a review run like every other lifecycle tool (the registry gates
on ``HERMES_KANBAN_TASK``, not on the lane), and on 2026-09-14 a review run created ``t_ff1915b0``
parented on the card under review; a worker spawned for it in ~90 seconds.

The signal is the one ``request_changes`` already gates on: the run's ``claimed`` event carries
``source_status=review``. These tests pin all three surfaces (native tool, CLI, kernel) and the
negative direction: a worker run claimed from ``ready`` still creates.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    (home / "profiles" / "reviewer").mkdir(parents=True)
    (home / "profiles" / "reviewer" / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK",
                "HERMES_KANBAN_RUN_ID", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(var, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _claim(tmp_path: Path, *, review: bool) -> tuple[str, int]:
    """Create a card, claim it, and (optionally) hand it to review and claim THAT run.

    Returns ``(task_id, our_run_id)``: the implementation run when ``review=False``, the
    reviewer run when ``review=True``.
    """
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Review create gate", assignee="builder")
        impl = kb.claim_task(conn, tid, claimer="builder:1")
        assert impl is not None
        if not review:
            assert impl.current_run_id is not None
            return tid, impl.current_run_id
        assert kb.request_review(conn, tid, summary="ready for review",
                                 expected_run_id=impl.current_run_id)
        reviewer = kb.claim_review_task(conn, tid, claimer="reviewer:1")
        assert reviewer is not None
        assert reviewer.current_run_id is not None
    return tid, reviewer.current_run_id


@pytest.fixture
def review_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A card claimed FROM review, with the worker env a dispatcher pins at spawn."""
    tid, run_id = _claim(tmp_path, review=True)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    # The dispatcher pins the DB as well; without it a worker's explicit board= would
    # resolve to a different board dir, which is NOT the real spawn shape.
    monkeypatch.setenv("HERMES_KANBAN_DB", str(kb.kanban_db_path()))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    return tid


@pytest.fixture
def worker_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The same, for an ordinary implementation run claimed from ``ready``."""
    tid, run_id = _claim(tmp_path, review=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(kb.kanban_db_path()))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    return tid


def _created_titles() -> list[str]:
    with kbc.connect() as conn:
        return [task.title for task in kb.list_tasks(conn)]


def _verdict(task_id: str, kind: str) -> None:
    """Deliver a review verdict from THIS run (the env carries the run id)."""
    run_id = int(os.environ["HERMES_KANBAN_RUN_ID"])
    with kbc.connect() as conn:
        if kind == "complete":
            assert kb.complete_task(conn, task_id, summary="approved",
                                    expected_run_id=run_id)
        else:
            ok, status = kb.request_changes(conn, task_id, reason="needs work",
                                            expected_run_id=run_id)
            assert ok, status
        # The verdict ENDS the run: the task's current run is now gone, which is exactly the
        # state a lane gate that looked the task up instead of the run would read as "ready".
        after = kb.get_task(conn, task_id)
        assert after is not None and after.current_run_id is None


# --- native tool -------------------------------------------------------------------------

def test_tool_create_is_refused_from_a_review_run(review_run: str) -> None:
    from tools import kanban_tools as tools

    payload = json.loads(tools._handle_create({"title": "Fix what I found", "assignee": "builder"}))
    assert "error" in payload
    assert "review run cannot create tickets" in payload["error"]
    # The refusal names the sanctioned exit so the model does not retry or go looking.
    assert "kanban_request_changes" in payload["error"]
    assert "Fix what I found" not in _created_titles()


def test_tool_create_is_still_allowed_from_a_worker_run(worker_run: str) -> None:
    from tools import kanban_tools as tools

    payload = json.loads(tools._handle_create({"title": "Follow-up work", "assignee": "builder"}))
    assert payload.get("ok") is True, payload
    assert payload["task_id"]
    assert "Follow-up work" in _created_titles()


def test_tool_create_cannot_dodge_the_gate_with_a_board_flag(review_run: str) -> None:
    """A worker's ``board=`` resolves back through its pinned DB (the fence), so it still refuses."""
    from tools import kanban_tools as tools

    kb.create_board("other")
    payload = json.loads(tools._handle_create(
        {"title": "Somewhere else", "assignee": "builder", "board": "other"}))
    assert "review run cannot create tickets" in payload.get("error", "")
    assert "Somewhere else" not in _created_titles()


def test_tool_create_is_refused_after_the_review_verdict(review_run: str) -> None:
    """The gate is keyed on the RUN, so handing off the verdict does not disarm it.

    ``kanban_complete`` clears the task's ``current_run_id``. A gate that read the task's
    current run instead of the creating run would read "ready" here and allow the create —
    and the refusal message itself tells the reviewer to deliver exactly this verdict, so
    that ordering is the one a live reviewer would hit.
    """
    from tools import kanban_tools as tools

    _verdict(review_run, "complete")
    payload = json.loads(tools._handle_create(
        {"title": "Filed after my approval", "assignee": "builder"}))
    assert "review run cannot create tickets" in payload.get("error", "")
    assert "Filed after my approval" not in _created_titles()


def test_tool_create_is_refused_after_request_changes(review_run: str) -> None:
    from tools import kanban_tools as tools

    _verdict(review_run, "changes")
    payload = json.loads(tools._handle_create(
        {"title": "Filed with my changes", "assignee": "builder"}))
    assert "review run cannot create tickets" in payload.get("error", "")
    assert "Filed with my changes" not in _created_titles()


def test_tool_create_is_allowed_after_a_worker_run_completes(worker_run: str) -> None:
    """Negative control for the run-keyed gate: a run claimed from ``ready`` may still create."""
    from tools import kanban_tools as tools

    _verdict(worker_run, "complete")
    payload = json.loads(tools._handle_create(
        {"title": "Follow-up after done", "assignee": "builder"}))
    assert payload.get("ok") is True, payload
    assert "Follow-up after done" in _created_titles()


# --- kernel backstop ---------------------------------------------------------------------

def test_kernel_refuses_a_create_that_stamps_a_review_creator(review_run: str) -> None:
    """The gate is in ``create_task`` too, so a surface that forgets its own check cannot leak."""
    with kbc.connect() as conn:
        with pytest.raises(ValueError, match="review run cannot create tickets"):
            kb.create_task(conn, title="Direct kernel create", creator_task_id=review_run)
        assert [task.title for task in kb.list_tasks(conn)] == ["Review create gate"]


def test_kernel_allows_a_create_that_stamps_a_worker_creator(worker_run: str) -> None:
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Kernel follow-up", creator_task_id=worker_run)
        created = kb.get_task(conn, tid)
        assert created is not None
        assert created.title == "Kernel follow-up"


def test_kernel_refuses_after_the_verdict_when_the_run_is_passed(review_run: str) -> None:
    """The kernel gate alone holds post-verdict, provided the caller passes its run id."""
    _verdict(review_run, "changes")
    run_id = int(os.environ["HERMES_KANBAN_RUN_ID"])
    with kbc.connect() as conn:
        with pytest.raises(ValueError, match="review run cannot create tickets"):
            kb.create_task(conn, title="Direct after verdict", creator_task_id=review_run,
                           creator_run_id=run_id)
        assert [task.title for task in kb.list_tasks(conn)] == ["Review create gate"]


# --- CLI ---------------------------------------------------------------------------------

def _run(argv: list[str], *, board: str | None = None) -> int:
    root = argparse.ArgumentParser(prog="hermes")
    kc.build_parser(root.add_subparsers())
    scope = ["--board", board] if board else []
    return kc._cmd_create(root.parse_args(["kanban", *scope, "create", *argv]))


def test_cli_create_is_refused_from_a_review_run(review_run: str, capsys) -> None:
    """The worker PROCESS keeps its run env, so the CLI's own lane check refuses here.

    A worker's terminal CHILD is scrubbed instead and meets the delegate fence first — see
    ``test_cli_create_in_a_worker_terminal_is_refused_by_the_delegate_fence``.
    """
    assert _run(["Follow-up", "--assignee", "builder"]) != 0
    assert "review run cannot create tickets" in capsys.readouterr().err
    assert "Follow-up" not in _created_titles()


def test_cli_create_is_refused_after_the_review_verdict(review_run: str, capsys) -> None:
    """The CLI pre-check reads the env run id, so the verdict does not reopen it either."""
    _verdict(review_run, "changes")
    assert _run(["Follow-up", "--assignee", "builder"]) != 0
    assert "review run cannot create tickets" in capsys.readouterr().err
    assert "Follow-up" not in _created_titles()


def test_cli_create_in_a_worker_terminal_is_refused_by_the_delegate_fence(
    review_run: str, capsys, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: in the shell a worker actually has, the FENCE is what refuses, not the lane.

    The measured shape of a worker's terminal child (``delegation_context`` →
    ``scrub_kanban_env``): no ``HERMES_KANBAN_TASK``/``RUN_ID``, and
    ``HERMES_DELEGATED_CHILD_CONTEXT`` carrying the board root. The lane pre-check cannot
    fire there, so the test pins the mechanism that does.
    """
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_RUN_ID", raising=False)
    monkeypatch.setenv("HERMES_DELEGATED_CHILD_CONTEXT", str(kb.kanban_home()))
    root = argparse.ArgumentParser(prog="hermes")
    kc.build_parser(root.add_subparsers())
    rc = kc.kanban_command(root.parse_args(
        ["kanban", "create", "Shelled out", "--assignee", "builder"]))
    assert rc != 0
    err = capsys.readouterr().err
    assert "delegate_task child contexts cannot mutate" in err
    assert "Shelled out" not in _created_titles()


def test_cli_create_cannot_dodge_the_gate_with_a_board_flag(review_run: str, capsys) -> None:
    kb.create_board("other")
    assert _run(["Elsewhere", "--assignee", "builder"], board="other") != 0
    assert "review run cannot create tickets" in capsys.readouterr().err
    assert "Elsewhere" not in _created_titles()


def test_cli_create_is_still_allowed_from_a_worker_run(worker_run: str) -> None:
    assert _run(["Follow-up", "--assignee", "builder"]) == 0
    assert "Follow-up" in _created_titles()


def test_cli_swarm_is_refused_from_a_review_run(review_run: str, capsys) -> None:
    """``swarm`` builds five cards in one call and passes no ``creator_task_id``."""
    root = argparse.ArgumentParser(prog="hermes")
    kc.build_parser(root.add_subparsers())
    args = root.parse_args([
        "kanban", "swarm", "ship it", "--worker", "builder=slice one",
        "--verifier", "builder", "--synthesizer", "builder",
    ])
    assert kc._cmd_swarm(args) != 0
    assert "review run cannot create tickets" in capsys.readouterr().err
    assert _created_titles() == ["Review create gate"]
