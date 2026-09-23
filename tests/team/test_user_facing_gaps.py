# Copyright (c) ModelScope Contributors. All rights reserved.
"""User-trust gaps: waiting_approval overlay, task PATCH, honest receipts."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HTTP = _ROOT / 'webui' / 'backend'
for _p in (str(_ROOT), str(_HTTP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ms_agent.team.context import format_dispatch_receipt
from ms_agent.team.coordinator import select_ready_tasks
from ms_agent.team.events import TeamEvent
from ms_agent.team.models import TeamProjectMeta, TeamTask
from team import router as team_router
from team.state import get_team_state, reset_team_state_for_tests


def _client() -> TestClient:
    reset_team_state_for_tests()
    os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
    os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'
    app = FastAPI()
    app.include_router(team_router)
    return TestClient(app)


@pytest.fixture
def api():
    client = _client()
    try:
        yield client
    finally:
        reset_team_state_for_tests()


def test_receipt_names_attach_and_fresh_in_chinese():
    assert '续已有会话' in format_dispatch_receipt(
        at_name='codex', session_mode='attach', session_resolution='bound')
    assert '新开会话' in format_dispatch_receipt(
        at_name='codex', session_mode='fresh', session_resolution='created')
    text = format_dispatch_receipt(
        at_name='codex',
        session_mode='fresh',
        session_resolution='attach_fallback_fresh',
    )
    assert '续会话失败' in text
    assert text.startswith('已派 @codex')
    hinted = format_dispatch_receipt(
        at_name='codex', session_mode='attach', session_resolution=None)
    assert hinted == '已派 @codex · 尝试续已有会话'


def test_waiting_approval_counts_as_inflight():
    tasks = [
        TeamTask(
            task_id='running',
            project_id='p',
            status='waiting_approval',
            prompt='x',
            trigger_user_id='u1',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
        ),
        TeamTask(
            task_id='next',
            project_id='p',
            status='pending',
            prompt='y',
            trigger_user_id='u1',
            target_at_name='codex',
            target_endpoint_id='ep-codex',
        ),
    ]
    ready, skipped = select_ready_tasks(tasks)
    assert ready == []
    assert skipped[0][0].task_id == 'next'
    assert skipped[0][1] == 'endpoint_busy'


def test_max_parallel_slots():
    tasks = [
        TeamTask(
            task_id='a',
            project_id='p',
            status='pending',
            prompt='a',
            trigger_user_id='u1',
            target_at_name='a',
            target_endpoint_id='ep-a',
        ),
        TeamTask(
            task_id='b',
            project_id='p',
            status='pending',
            prompt='b',
            trigger_user_id='u1',
            target_at_name='b',
            target_endpoint_id='ep-b',
        ),
    ]
    ready, skipped = select_ready_tasks(tasks, max_global=1)
    assert [t.task_id for t in ready] == ['a']
    assert skipped[0][1] == 'queued_slot'


def test_list_tasks_marks_waiting_approval(api: TestClient):
    state = get_team_state()
    state.projects.upsert(TeamProjectMeta(project_id='proj_w', name='w'))
    state.tasks.upsert(
        TeamTask(
            task_id='t_wait',
            project_id='proj_w',
            status='in_progress',
            prompt='x',
            trigger_user_id='u1',
            last_dispatch_id='d_wait',
            target_at_name='coder',
        ))

    async def _seed():
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_w',
                dispatch_id='d_wait',
                at_name='coder',
                payload={
                    'request_id': 'r1',
                    'call_id': 'c1',
                    'tool_name': 'shell',
                },
            ))

    asyncio.run(_seed())
    listed = api.get('/api/v1/team/projects/proj_w/tasks')
    assert listed.status_code == 200
    row = next(t for t in listed.json()['tasks'] if t['task_id'] == 't_wait')
    assert row['waiting_approval'] is True
    assert row['pending_permission_asks'][0]['call_id'] == 'c1'


def test_patch_task_reassign_and_blocked_by(api: TestClient):
    created = api.post(
        '/api/v1/team/projects',
        json={'name': 'edit-board', 'workspace_path': '/tmp'},
    )
    assert created.status_code == 200
    pid = created.json()['project_id']
    blocker = api.post(
        f'/api/v1/team/projects/{pid}/tasks',
        json={
            'task_id': 't0',
            'status': 'pending',
            'prompt': 'first',
            'target_at_name': 'coder',
        },
    )
    assert blocker.status_code == 200
    child = api.post(
        f'/api/v1/team/projects/{pid}/tasks',
        json={
            'task_id': 't1',
            'status': 'pending',
            'prompt': 'second',
        },
    )
    assert child.status_code == 200
    patched = api.patch(
        f'/api/v1/team/projects/{pid}/tasks/t1',
        json={
            'prompt': 'second, clarified',
            'target_at_name': 'coder',
            'blocked_by': ['t0'],
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body['prompt'] == 'second, clarified'
    assert body['target_at_name'] == 'coder'
    assert body['blocked_by'] == ['t0']


def test_local_file_stays_inside_workspace(api: TestClient, tmp_path):
    root = tmp_path / 'ws'
    root.mkdir()
    (root / 'out.txt').write_text('hello artifact', encoding='utf-8')
    created = api.post(
        '/api/v1/team/projects',
        json={'name': 'ws', 'workspace_path': str(root)},
    )
    assert created.status_code == 200
    pid = created.json()['project_id']
    ok = api.get(
        f'/api/v1/team/projects/{pid}/local-file',
        params={'path': 'out.txt'},
    )
    assert ok.status_code == 200
    assert ok.json()['text'] == 'hello artifact'
    denied = api.get(
        f'/api/v1/team/projects/{pid}/local-file',
        params={'path': '/etc/passwd'},
    )
    assert denied.status_code == 403


def test_waiting_approval_is_persisted_on_task(api: TestClient):
    state = get_team_state()
    state.projects.upsert(TeamProjectMeta(project_id='proj_w', name='w'))
    state.tasks.upsert(
        TeamTask(
            task_id='t_wait',
            project_id='proj_w',
            status='in_progress',
            prompt='x',
            trigger_user_id='u1',
            last_dispatch_id='d_wait',
            target_at_name='coder',
        ))

    async def _seed():
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_w',
                dispatch_id='d_wait',
                at_name='coder',
                payload={
                    'request_id': 'r1',
                    'call_id': 'c1',
                    'tool_name': 'shell',
                },
            ))

    asyncio.run(_seed())
    stored = state.tasks.get('t_wait')
    assert stored is not None
    assert stored.status == 'waiting_approval'


def test_inbox_does_not_leak_asks_across_projects():
    from team.permission_inbox import PermissionInbox

    inbox = PermissionInbox()
    inbox.put({
        'request_id': 'orphan',
        'call_id': 'c0',
        'tool_name': 'shell',
        'dispatch_id': 'd0',
    })
    inbox.put({
        'request_id': 'owned',
        'call_id': 'c1',
        'tool_name': 'shell',
        'project_id': 'proj_a',
        'dispatch_id': 'd1',
    })
    rows = inbox.list_pending(project_id='proj_a')
    assert {r['request_id'] for r in rows} == {'owned'}
    inbox.decide('owned', 'allow_once')
    assert inbox.list_pending(project_id='proj_a') == []
    stored = inbox.store.get('owned')
    assert stored is not None
    assert stored.state == 'approved'
    inbox.pop('orphan')
    cancelled = inbox.store.get('orphan')
    assert cancelled is not None
    assert cancelled.state == 'cancelled'


def test_cloud_allow_always_is_not_downgraded_to_once():
    from ms_agent.permission.handler import PermissionAction, PermissionResponse
    from team.cloud_runner import CloudAgentRuntime

    rt = CloudAgentRuntime(dry_run=True)

    class _Handler:
        def __init__(self):
            self.seen = None

        def resolve(self, request_id, response):
            del request_id
            self.seen = response

    handler = _Handler()
    rt._permission_handlers['d1'] = handler
    assert rt.resolve_permission(
        'd1', 'r1', action='allow_always', pattern='Bash(*)')
    assert isinstance(handler.seen, PermissionResponse)
    assert handler.seen.action == PermissionAction.ALLOW_ALWAYS
    assert handler.seen.pattern == 'Bash(*)'


def test_coordinator_class_defaults_enabled():
    from ms_agent.team.coordinator import Coordinator
    from ms_agent.team.ingress import MessageIngress
    from ms_agent.team.stores.memory import (
        MemoryEndpointStore,
        MemoryProjectMetaStore,
        MemoryTaskBoardStore,
        MemoryTimelineStore,
    )

    ingress = MessageIngress(
        MemoryEndpointStore(),
        MemoryProjectMetaStore(),
        MemoryTimelineStore(),
        task_store=MemoryTaskBoardStore(),
    )
    coord = Coordinator(ingress, task_store=MemoryTaskBoardStore())
    assert coord.enabled is True


def test_file_inbox_decide_survives_token_strip(tmp_path):
    from ms_agent.permission.approval import FileApprovalStore
    from team.permission_inbox import PermissionInbox

    path = tmp_path / 'asks.json'
    inbox = PermissionInbox(store=FileApprovalStore(path))
    inbox.put({
        'request_id': 'r1',
        'tool_name': 'shell',
        'project_id': 'p',
        'dispatch_id': 'd1',
    })
    reloaded = PermissionInbox(store=FileApprovalStore(path))
    row = reloaded.decide('r1', 'allow_always', pattern='Bash(*)')
    assert row is not None
    assert row['state'] == 'approved'
    stored = reloaded.store.get('r1')
    assert stored is not None
    assert stored.state == 'approved'
    assert stored.decision == 'allow_always'


def test_acp_ids_are_namespaced_per_dispatch():
    from team.permission_inbox import PermissionInbox

    inbox = PermissionInbox()
    a = inbox.put({
        'acp_id': 7,
        'call_id': 'call_a',
        'tool_name': 'shell',
        'dispatch_id': 'd1',
        'project_id': 'p1',
    })
    b = inbox.put({
        'acp_id': 7,
        'call_id': 'call_b',
        'tool_name': 'shell',
        'dispatch_id': 'd2',
        'project_id': 'p2',
    })
    assert a['request_id'] != b['request_id']
    assert {row['request_id'] for row in inbox.list_pending(project_id='p1')} == {
        a['request_id']}
    assert {row['request_id'] for row in inbox.list_pending(project_id='p2')} == {
        b['request_id']}


def test_permission_resolved_without_action_does_not_allow(api: TestClient):
    state = get_team_state()
    state.projects.upsert(TeamProjectMeta(project_id='proj_w', name='w'))

    async def _seed():
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_w',
                dispatch_id='d_wait',
                payload={
                    'request_id': 'r1',
                    'call_id': 'c1',
                    'tool_name': 'shell',
                },
            ))
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_resolved',
                project_id='proj_w',
                dispatch_id='d_wait',
                payload={'call_id': 'c1', 'ok': True},
            ))

    asyncio.run(_seed())
    stored = state.permission_inbox.store.get('r1')
    assert stored is not None
    assert stored.state == 'pending'
