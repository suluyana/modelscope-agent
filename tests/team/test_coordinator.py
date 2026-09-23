# Copyright (c) ModelScope Contributors. All rights reserved.
"""T4.1: deterministic Coordinator — DAG dispatch, never via Lead prompt."""
from __future__ import annotations

import asyncio

import pytest

from ms_agent.team.coordinator import Coordinator, select_ready_tasks
from ms_agent.team.ingress import MessageIngress
from ms_agent.team.models import AgentEndpoint, TeamProjectMeta, TeamTask
from ms_agent.team.stores.memory import (
    MemoryEndpointStore,
    MemoryProjectMetaStore,
    MemoryTaskBoardStore,
    MemoryTimelineStore,
)


def _task(task_id: str, **kwargs) -> TeamTask:
    defaults = dict(
        task_id=task_id,
        project_id='p1',
        status='pending',
        prompt=f'work {task_id}',
        trigger_user_id='u1',
    )
    defaults.update(kwargs)
    return TeamTask(**defaults)


def _endpoint(eid: str, at_name: str) -> AgentEndpoint:
    return AgentEndpoint(
        endpoint_id=eid,
        at_name=at_name,
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='ms_agent',
        adapter_kind='cloud',
        status='online',
    )


def test_select_ready_unblocked_parallel_across_endpoints():
    tasks = [
        _task('t1', target_at_name='bibo', target_endpoint_id='ep-bibo'),
        _task('t3', target_at_name='lily', target_endpoint_id='ep-lily'),
        _task(
            't4',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
            blocked_by=['t1', 't3'],
        ),
    ]
    ready, skipped = select_ready_tasks(tasks)
    ready_ids = {t.task_id for t in ready}
    assert ready_ids == {'t1', 't3'}
    reasons = {t.task_id: reason for t, reason in skipped}
    assert reasons['t4'] == 'blocked'


def test_select_ready_same_endpoint_serial():
    tasks = [
        _task(
            'running',
            status='in_progress',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
        ),
        _task(
            'next',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
        ),
    ]
    ready, skipped = select_ready_tasks(tasks)
    assert ready == []
    assert skipped[0][0].task_id == 'next'
    assert skipped[0][1] == 'endpoint_busy'


def test_select_ready_no_target_is_unclaimed():
    tasks = [_task('orphan', target_at_name=None)]
    ready, skipped = select_ready_tasks(tasks)
    assert ready == []
    assert skipped[0][1] == 'unclaimed'


def test_select_ready_skips_assigned_wait_human():
    tasks = [
        _task(
            'gate',
            status='wait_human',
            target_at_name='bibo',
            target_endpoint_id='ep-bibo',
        ),
    ]
    ready, skipped = select_ready_tasks(tasks)
    assert ready == []
    assert skipped[0][1] == 'wait_human'


def test_failed_blocker_does_not_release_or_rerun_neighbor():
    tasks = [
        _task('t1', status='failed', target_at_name='bibo'),
        _task('t3', status='completed', target_at_name='lily'),
        _task(
            't4',
            target_at_name='codex',
            blocked_by=['t1', 't3'],
        ),
    ]
    ready, skipped = select_ready_tasks(tasks)
    assert [t.task_id for t in ready] == []
    assert skipped[0][1] == 'blocked'


@pytest.mark.asyncio
async def test_coordinator_run_dispatches_task_prompt_not_lead_text():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    for name, eid in (('planner', 'ep-lead'), ('bibo', 'ep-bibo'),
                      ('lily', 'ep-lily')):
        endpoints.upsert(_endpoint(eid, name))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    captured = []

    async def handler(env):
        captured.append(env)

    ingress.set_dispatch_handler(handler)
    tasks.upsert(
        _task(
            't1',
            prompt='找北京美食',
            target_at_name='bibo',
            target_endpoint_id='ep-bibo',
        ))
    tasks.upsert(
        _task(
            't3',
            prompt='找北京美景',
            target_at_name='lily',
            target_endpoint_id='ep-lily',
        ))
    tasks.upsert(
        _task(
            't4',
            prompt='汇总',
            target_at_name='planner',
            target_endpoint_id='ep-lead',
            blocked_by=['t1', 't3'],
        ))
    coord = Coordinator(ingress, task_store=tasks, enabled=True)
    result = await coord.run('p1')
    await asyncio.sleep(0.05)
    dispatched_ats = {e.target_at_name for e in captured}
    assert dispatched_ats == {'bibo', 'lily'}
    assert {d['task_id'] for d in result['dispatched']} == {'t1', 't3'}
    for env in captured:
        assert env.referenced_task_id in ('t1', 't3')
        assert '帮我做整个项目' not in env.prompt
        assert env.prompt in ('找北京美食', '找北京美景')
        assert env.sender_user_id == 'coordinator'
    lead_msgs = [
        m for m in timeline.list('p1')
        if getattr(m, 'sender_type', None) == 'human'
    ]
    assert lead_msgs == []


@pytest.mark.asyncio
async def test_coordinator_releases_after_blockers_complete():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(TeamProjectMeta(project_id='p1', name='demo'))
    endpoints.upsert(_endpoint('ep-codex', 'codex'))
    endpoints.upsert(_endpoint('ep-bibo', 'bibo'))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    captured = []

    async def handler(env):
        captured.append(env)

    ingress.set_dispatch_handler(handler)
    tasks.upsert(
        _task(
            't1',
            prompt='A',
            target_at_name='bibo',
            target_endpoint_id='ep-bibo',
        ))
    tasks.upsert(
        _task(
            't4',
            prompt='B after A',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
            blocked_by=['t1'],
        ))
    coord = Coordinator(ingress, task_store=tasks, enabled=True)
    await coord.run('p1')
    await asyncio.sleep(0.05)
    assert [e.target_at_name for e in captured] == ['bibo']
    ingress.complete_dispatch(captured[0], ok=True)
    captured.clear()
    await coord.run('p1')
    await asyncio.sleep(0.05)
    assert [e.target_at_name for e in captured] == ['codex']
    assert captured[0].prompt == 'B after A'


@pytest.mark.asyncio
async def test_coordinator_floats_expired_unclaimed_to_lead():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(_endpoint('ep-lead', 'planner'))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)

    async def _noop(_env):
        return None

    ingress.set_dispatch_handler(_noop)
    old = '2000-01-01T00:00:00+00:00'
    tasks.upsert(_task('orphan', target_at_name=None, created_at=old))
    coord = Coordinator(
        ingress, task_store=tasks, enabled=True, claim_timeout_s=1)
    result = await coord.run('p1')
    assert 'orphan' in result['unclaimed_escalated']
    assert (tasks.get('orphan').deployment_context or {}).get(
        'unclaimed_escalated') is True
    assert tasks.get('orphan').status == 'pending'
    assert tasks.get('orphan').target_at_name is None


@pytest.mark.asyncio
async def test_coordinator_disabled_skips_auto_but_manual_run_works():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(TeamProjectMeta(project_id='p1', name='demo'))
    endpoints.upsert(_endpoint('ep-bibo', 'bibo'))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    seen = []

    async def handler(env):
        seen.append(env.target_at_name)

    ingress.set_dispatch_handler(handler)
    tasks.upsert(
        _task(
            't1',
            prompt='A',
            target_at_name='bibo',
            target_endpoint_id='ep-bibo',
        ))
    coord = Coordinator(ingress, task_store=tasks, enabled=False)
    auto = await coord.on_dispatch_done('p1')
    assert auto['skipped_reason'] == 'disabled'
    assert seen == []
    manual = await coord.run('p1', force=True)
    await asyncio.sleep(0.05)
    assert {d['task_id'] for d in manual['dispatched']} == {'t1'}
    assert seen == ['bibo']


@pytest.mark.asyncio
async def test_coordinator_keeps_at_tokens_in_board_prompt():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(_endpoint('ep-lead', 'planner'))
    endpoints.upsert(_endpoint('ep-bibo', 'bibo'))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    captured = []

    async def handler(env):
        captured.append(env)

    ingress.set_dispatch_handler(handler)
    prompt = '用 dispatch_result_read 收集 @bibo 和 @lily 的终态，不要 glob'
    tasks.upsert(
        _task(
            't_merge',
            prompt=prompt,
            target_at_name='planner',
            target_endpoint_id='ep-lead',
        ))
    coord = Coordinator(ingress, task_store=tasks, enabled=True)
    await coord.run('p1')
    await asyncio.sleep(0.05)
    assert captured
    assert captured[0].prompt == prompt
    assert captured[0].context_bundle.audience == 'worker'
