"""A review-lane run gets five toolsets out of the assignee profile's nineteen.

The reviewer stays on the assignee profile on purpose: a separate ``reviewer`` profile carries its
own cloned config and model pin, which then rots behind model changes. Only the tool surface
narrows, and only for the review lane — an implementer run keeps the profile's full set.
"""
from contextlib import closing
from types import SimpleNamespace

import pytest

# The composite a default ``hermes-cli`` profile expands to, measured on the live box 2026-10-18.
WIDE = [
    'a2a', 'browser', 'clarify', 'code_execution', 'computer_use', 'context_engine', 'cronjob',
    'delegation', 'file', 'image_gen', 'memory', 'session_search', 'skills', 'terminal', 'todo',
    'tts', 'video_gen', 'vision', 'web',
]
NARROW = {'file', 'skills', 'terminal', 'todo', 'web'}
# Surfaces a headless reviewer must never reach: generation, memory, delegation, browser, clarify.
REVIEWER_MUST_NOT_HAVE = {'a2a', 'browser', 'clarify', 'code_execution', 'computer_use', 'cronjob',
                          'delegation', 'image_gen', 'memory', 'session_search', 'tts', 'video_gen',
                          'vision'}


def _config(**kanban):
    """An owner-profile config whose CLI surface is the wide composite; ``kanban`` keys vary."""
    return {'model': {'default': 'loop-model'}, 'platform_toolsets': {'cli': list(WIDE)},
            'agent': {'disabled_toolsets': []}, 'kanban': dict(kanban)}


def _claim(tmp_path, monkeypatch, *, review):
    """Claim a card the way the dispatcher does, on the review lane or the implementation one."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from gateway.session_contract import Principal

    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_DELEGATED_CHILD', raising=False)
    monkeypatch.setattr('hermes_cli.profiles.resolve_profile_env',
                        lambda name: str(tmp_path) if name == 'default' else str(tmp_path / name))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    kb.create_board('owned')  # explicit creation; connect() never creates a board
    with closing(connect(board='owned')) as conn:
        task_id = kb.create_task(conn, title='Reviewer surface', assignee='default',
                                 workspace_kind='dir', workspace_path=str(workspace),
                                 skills=['owned-skill'], model_override='loop-model',
                                 provider_override='custom')
        kb.recompute_ready(conn)
        implementation = kb.claim_task(conn, task_id)
        assert implementation is not None
        if review:
            assert kb.request_review(conn, task_id, summary='ready for review',
                                     expected_run_id=implementation.current_run_id)
            task = kb.claim_review_task(conn, task_id)
        else:
            task = implementation
        assert task is not None
    actor = Principal('owner', str(tmp_path), frozenset({'session:create'}), 'transport')
    connection = SimpleNamespace(authority=SimpleNamespace(profile_id=str(tmp_path)), actor=actor,
                                 native_owner=True)
    params = dict(board='owned', task_id=task.id, run_id=task.current_run_id,
                  claim_lock=task.claim_lock)
    return connection, params


def _policy(tmp_path, monkeypatch, *, review, **kanban):
    import json

    from gateway.session_kanban import build_kanban_policy

    connection, params = _claim(tmp_path, monkeypatch, review=review)
    policy, _secrets = build_kanban_policy(connection, params, _config(**kanban))
    return policy, json.loads(policy.kanban_json)


def test_review_lane_gets_the_five_tool_default(tmp_path, monkeypatch):
    policy, context = _policy(tmp_path, monkeypatch, review=True)
    # ``kanban`` rides along so the lifecycle tools (comment/complete/block/request_changes) stay
    # mounted: the union is pre-existing behaviour and is not part of the narrowing.
    assert set(policy.toolsets) == NARROW | {'kanban'}
    assert not (set(policy.toolsets) & REVIEWER_MUST_NOT_HAVE)
    # The forced review skill is unaffected by the toolset narrowing.
    assert 'sdlc-review' in context['skills']


def test_implementation_lane_is_unchanged(tmp_path, monkeypatch):
    policy, _context = _policy(tmp_path, monkeypatch, review=False)
    assert set(policy.toolsets) == set(WIDE) | {'kanban'}
    assert REVIEWER_MUST_NOT_HAVE <= set(policy.toolsets)


def test_empty_key_keeps_the_assignee_profile_toolsets(tmp_path, monkeypatch):
    """An explicit ``review_toolsets: []`` is the opt-out: the reviewer runs the profile's own set."""
    policy, _context = _policy(tmp_path, monkeypatch, review=True, review_toolsets=[])
    assert set(policy.toolsets) == set(WIDE) | {'kanban'}


def test_explicit_key_replaces_the_default_list(tmp_path, monkeypatch):
    policy, _context = _policy(tmp_path, monkeypatch, review=True, review_toolsets=['terminal', 'web'])
    assert set(policy.toolsets) == {'terminal', 'web', 'kanban'}


def test_unknown_toolset_fails_the_spawn_instead_of_widening_it(tmp_path, monkeypatch):
    """A typo in the key must surface, not silently grant (or half-grant) a surface."""
    from hermes_state_runtime import RuntimeStoreError

    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        _policy(tmp_path, monkeypatch, review=True, review_toolsets=['terminal', 'nolongeratoolset'])


def test_malformed_key_falls_back_to_the_narrow_default(tmp_path, monkeypatch):
    """A bad value type never restores the wide set: fail closed, toward fewer tools."""
    policy, _context = _policy(tmp_path, monkeypatch, review=True, review_toolsets='terminal,web')
    assert set(policy.toolsets) == NARROW | {'kanban'}


def test_review_toolsets_helper_contract():
    from hermes_cli.config_defaults import DEFAULT_REVIEW_TOOLSETS
    from gateway.session_kanban import review_toolsets

    assert review_toolsets({}) == DEFAULT_REVIEW_TOOLSETS
    assert review_toolsets({'kanban': {}}) == DEFAULT_REVIEW_TOOLSETS
    assert review_toolsets({'kanban': {'review_toolsets': []}}) is None
    assert review_toolsets({'kanban': {'review_toolsets': ['terminal', ' ']}}) == ['terminal']
