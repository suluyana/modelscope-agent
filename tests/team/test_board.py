# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from omegaconf import OmegaConf

from ms_agent.team.board import (
    can_self_claim,
    is_unclaimed,
    is_unclaimed_expired,
    parent_completion_blocked,
)
from ms_agent.team.errors import PARENT_INCOMPLETE, TASK_NOT_CLAIMABLE, TeamError
from ms_agent.team.models import TeamTask
from ms_agent.team.roster import RosterStore
from ms_agent.team.stores.memory import MemoryTaskBoardStore
from ms_agent.team.tools.endpoint_tools import TeamTaskBoardTools


def _task(task_id: str, **kwargs) -> TeamTask:
    defaults = dict(
        task_id=task_id,
        project_id='p1',
        status='pending',
        prompt='work',
        trigger_user_id='u1',
    )
    defaults.update(kwargs)
    return TeamTask(**defaults)


def test_unclaimed_without_tags_not_self_claimable():
    task = _task('t1', target_at_name=None)
    assert is_unclaimed(task)
    assert not can_self_claim(task, ['code'])


def test_unclaimed_tags_intersect_capabilities():
    task = _task('t1', target_at_name=None, tags=['code', 'review'])
    assert can_self_claim(task, ['code'])
    assert not can_self_claim(task, ['design'])


def test_unclaimed_expired_by_created_at():
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    task = _task('t1', target_at_name=None, created_at=old)
    assert is_unclaimed_expired(task, timeout_s=10)
    assert not is_unclaimed_expired(task, timeout_s=3600)


def test_parent_completion_blocked_while_child_open():
    parent = _task('root', status='completed')
    child = _task('c1', parent_task_id='root', status='pending')
    assert parent_completion_blocked([parent, child], parent)
    child.status = 'completed'
    assert parent_completion_blocked([parent, child], parent) is None


@pytest.mark.asyncio
async def test_board_write_rejects_parent_complete_with_open_child():
    store = MemoryTaskBoardStore()
    store.upsert(_task('root', status='pending'))
    store.upsert(_task('c1', parent_task_id='root', status='pending'))
    tools = TeamTaskBoardTools(
        OmegaConf.create({'output_dir': '.'}), task_store=store)
    import json
    out = json.loads(
        await tools.call_tool(
            'team_taskboard',
            tool_name='task_board_write',
            tool_args={
                'task_id': 'root',
                'project_id': 'p1',
                'status': 'completed',
                'prompt': 'all done',
            },
        ))
    assert out['error'] == PARENT_INCOMPLETE


def test_claim_unclaimed_without_matching_caps_fails():
    store = MemoryTaskBoardStore()
    store.upsert(_task('t1', target_at_name=None, tags=['code']))
    with pytest.raises(TeamError) as exc:
        store.claim(
            't1',
            claimant_at_name='bibo',
            expected_version=0,
            claimant_capabilities=['design'],
        )
    assert exc.value.code == TASK_NOT_CLAIMABLE


def test_roster_accuracy_is_display_only():
    store = RosterStore()
    store.record(at_name='bibo', outcome='completed', tags=['code'])
    store.record(at_name='bibo', outcome='failed', tags=['code'])
    rows = store.summarize()
    assert rows[0]['at_name'] == 'bibo'
    assert rows[0]['accuracy'] == 0.5
    lines = store.preview_lines()
    assert 'display only' in lines[0]
