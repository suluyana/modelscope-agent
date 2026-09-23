# Copyright (c) ModelScope Contributors. All rights reserved.
"""T3.1 / T3.3: parent_task_id, task_claim CAS, worker inbox projection."""
from __future__ import annotations

import json
import threading

import pytest
from omegaconf import OmegaConf

from ms_agent.team.context import ContextBundleAssembler
from ms_agent.team.errors import TeamError
from ms_agent.team.models import TeamTask
from ms_agent.team.stores.memory import MemoryTaskBoardStore
from ms_agent.team.tools.endpoint_tools import TeamTaskBoardTools


def _task(**kwargs) -> TeamTask:
    defaults = dict(
        task_id='t1',
        project_id='p1',
        status='pending',
        prompt='write tests',
        trigger_user_id='u1',
    )
    defaults.update(kwargs)
    return TeamTask(**defaults)


def test_parent_task_id_roundtrip_on_board_write():
    store = MemoryTaskBoardStore()
    store.upsert(_task(task_id='root', prompt='ship the feature'))

    async def _run():
        tools = TeamTaskBoardTools(
            OmegaConf.create({'output_dir': '.'}), task_store=store)
        written = json.loads(
            await tools.call_tool(
                'team_taskboard',
                tool_name='task_board_write',
                tool_args={
                    'project_id': 'p1',
                    'status': 'pending',
                    'prompt': 'write tests for auth',
                    'target_at_name': 'codex',
                    'parent_task_id': 'root',
                    'blocked_by': [],
                },
            ))
        assert written['parent_task_id'] == 'root'
        assert written['target_at_name'] == 'codex'
        listed = json.loads(
            await tools.call_tool(
                'team_taskboard',
                tool_name='task_board_read',
                tool_args={'project_id': 'p1'},
            ))
        child = next(r for r in listed if r['task_id'] == written['task_id'])
        assert child['parent_task_id'] == 'root'
        assert child['blocked_by'] == []
        assert 'prompt' not in child

    import asyncio
    asyncio.run(_run())


def test_board_write_preserves_last_dispatch_id():
    store = MemoryTaskBoardStore()
    store.upsert(
        _task(
            task_id='t1',
            status='in_progress',
            last_dispatch_id='d_keep',
            claim_version=2,
        ))

    async def _run():
        tools = TeamTaskBoardTools(
            OmegaConf.create({'output_dir': '.'}), task_store=store)
        out = json.loads(
            await tools.call_tool(
                'team_taskboard',
                tool_name='task_board_write',
                tool_args={
                    'task_id': 't1',
                    'project_id': 'p1',
                    'status': 'completed',
                    'prompt': 'write tests',
                    'result_summary': 'done',
                },
            ))
        assert out['last_dispatch_id'] == 'd_keep'
        assert out['claim_version'] == 2

    import asyncio
    asyncio.run(_run())


def test_claim_succeeds_once_and_bumps_version():
    store = MemoryTaskBoardStore()
    store.upsert(_task(target_at_name='codex', claim_version=0))
    claimed = store.claim(
        't1', claimant_at_name='codex', expected_version=0)
    assert claimed.status == 'in_progress'
    assert claimed.claim_version == 1
    assert claimed.target_at_name == 'codex'
    with pytest.raises(TeamError) as exc:
        store.claim('t1', claimant_at_name='codex', expected_version=0)
    assert exc.value.code == 'CLAIM_CONFLICT'


def test_two_claimants_only_one_wins():
    store = MemoryTaskBoardStore()
    store.upsert(_task(target_at_name=None, claim_version=0, tags=['code']))
    winners: list[str] = []
    errors: list[str] = []

    def _try(name: str) -> None:
        try:
            store.claim(
                't1',
                claimant_at_name=name,
                expected_version=0,
                claimant_capabilities=['code'],
            )
            winners.append(name)
        except TeamError as exc:
            errors.append(exc.code)

    threads = [
        threading.Thread(target=_try, args=('codex',)),
        threading.Thread(target=_try, args=('claude',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1
    assert errors == ['CLAIM_CONFLICT']
    assert store.get('t1').target_at_name == winners[0]


@pytest.mark.asyncio
async def test_task_claim_tool_cas():
    store = MemoryTaskBoardStore()
    store.upsert(_task(target_at_name='codex'))
    tools = TeamTaskBoardTools(
        OmegaConf.create({'output_dir': '.'}), task_store=store)
    ok = json.loads(
        await tools.call_tool(
            'team_taskboard',
            tool_name='task_claim',
            tool_args={
                'task_id': 't1',
                'at_name': 'codex',
                'expected_version': 0,
            },
        ))
    assert ok['status'] == 'in_progress'
    conflict = json.loads(
        await tools.call_tool(
            'team_taskboard',
            tool_name='task_claim',
            tool_args={
                'task_id': 't1',
                'at_name': 'codex',
                'expected_version': 0,
            },
        ))
    assert conflict['error'] == 'CLAIM_CONFLICT'


def test_inbox_only_own_claimable_or_active_rows():
    tasks = [
        _task(task_id='mine_p', target_at_name='codex', status='pending',
              prompt='codex pending'),
        _task(task_id='mine_run', target_at_name='codex', status='in_progress',
              prompt='codex running'),
        _task(task_id='other', target_at_name='bibo', status='pending',
              prompt='bibo secret token stream should not leak'),
        _task(task_id='done', target_at_name='codex', status='completed',
              prompt='already done'),
        _task(task_id='open', target_at_name=None, status='pending',
              prompt='unassigned claimable'),
        _task(
            task_id='tagged',
            target_at_name=None,
            status='pending',
            prompt='tagged unclaimed',
            tags=['code'],
        ),
        _task(
            task_id='blocked',
            target_at_name='codex',
            status='pending',
            prompt='blocked on tests',
            blocked_by=['mine_p'],
        ),
    ]
    inbox = ContextBundleAssembler.inbox_from_tasks(
        tasks, at_name='codex', capabilities=['code'])
    ids = [row['task_id'] for row in inbox]
    assert 'other' not in ids
    assert 'done' not in ids
    assert 'mine_p' in ids
    assert 'mine_run' in ids
    assert 'open' not in ids
    assert 'tagged' in ids
    assert 'blocked' in ids
    blob = json.dumps(inbox)
    assert 'bibo secret' not in blob
    blocked = next(r for r in inbox if r['task_id'] == 'blocked')
    assert blocked['blocked_by'] == ['mine_p']
    assert blocked['claimable'] is False
    tagged = next(r for r in inbox if r['task_id'] == 'tagged')
    assert tagged['claimable'] is True
    assert 'claim_version' in tagged
