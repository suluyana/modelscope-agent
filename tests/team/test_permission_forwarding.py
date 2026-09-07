# Copyright (c) ModelScope Contributors. All rights reserved.
"""Permission forwarding contracts across Team, Host Bridge, and ACP."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from ms_agent.bridge.adapters.acp_client import AcpSession
from ms_agent.bridge.daemon import AgentSlot, BridgeDaemon
from ms_agent.bridge.permission import permission_mode_for_tier
from ms_agent.team.events import TeamEvent, task_status_for_event
from ms_agent.team.models import ContextBundle, DispatchEnvelope, TeamProjectMeta, TeamTask


class _Writer:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode('utf-8')))

    async def drain(self) -> None:
        return None


def _envelope() -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id='d_perm',
        prompt='change a file',
        project_id='proj_1',
        target_endpoint_id='ep_1',
        target_at_name='coder',
        sender_user_id='owner_1',
        channel='web',
        thread_id='thread_1',
        context_bundle=ContextBundle(),
        permission_tier='owner',
        caller_is_owner=True,
        runtime_session_id='runtime_1',
    )


def _daemon() -> BridgeDaemon:
    return BridgeDaemon(
        api_base='http://localhost:8000',
        ws_url='ws://localhost:8000/api/v1/team/bridge',
        bridge_id='bridge_1',
        owner_user_id='owner_1',
        agents=[
            AgentSlot(
                endpoint_id='ep_1',
                at_name='coder',
                runtime='codex',
                cwd='/tmp',
                adapter=SimpleNamespace(),
            )
        ],
    )


def test_team_permission_events_and_waiting_task_round_trip():
    event_types = (
        'team.permission_requested',
        'team.permission_resolved',
        'team.dispatch_waiting_approval',
        'team.dispatch_resumed',
    )
    for event_type in event_types:
        event = TeamEvent.from_dict({
            'type': event_type,
            'dispatch_id': 'd_perm',
            'payload': {'permission_request_id': 'perm_1'},
        })
        assert event.to_dict()['type'] == event_type

    task = TeamTask(
        task_id='task_1',
        project_id='proj_1',
        status='waiting_approval',
        prompt='change a file',
        trigger_user_id='owner_1',
    )
    assert TeamTask.from_dict(task.to_dict()).status == 'waiting_approval'
    waiting = TeamEvent(type='team.dispatch_waiting_approval')
    resumed = TeamEvent(type='team.dispatch_resumed')
    assert task_status_for_event(waiting, 'in_progress') == 'waiting_approval'
    assert task_status_for_event(resumed, 'waiting_approval') == 'in_progress'


def test_owner_tier_is_never_bypass_permissions():
    assert permission_mode_for_tier('owner') == 'interactive'
    assert permission_mode_for_tier('restricted') == 'restricted'
    assert permission_mode_for_tier('unexpected') == 'restricted'


def test_default_permission_timeout_precedes_acp_prompt_timeout():
    assert _daemon().permission_timeout < 300


def test_unsafe_permission_timeout_override_is_clamped():
    daemon = BridgeDaemon(
        api_base='http://localhost:8000',
        ws_url='ws://localhost:8000/api/v1/team/bridge',
        bridge_id='bridge_1',
        owner_user_id='owner_1',
        permission_timeout=999,
    )
    assert daemon.permission_timeout < 300


@pytest.mark.asyncio
async def test_acp_permission_request_stays_pending_until_callback_resolves():
    writer = _Writer()
    session = AcpSession(['fake-acp'])
    session.proc = SimpleNamespace(
        returncode=None,
        stdin=writer,
    )
    callback_started = asyncio.Event()
    decision = asyncio.Event()

    async def permission_callback(request: dict[str, Any]) -> dict[str, Any]:
        callback_started.set()
        assert request['request_id'] == 41
        assert request['runtime_session_id'] == 'runtime_1'
        assert request['call_id'] == 'call_1'
        assert request['tool'] == 'Write file'
        assert request['args'] == {'path': 'a.py'}
        await decision.wait()
        return {
            'outcome': {
                'outcome': 'selected',
                'optionId': 'allow-once',
            }
        }

    session.on_permission_request = permission_callback
    incoming = {
        'jsonrpc': '2.0',
        'id': 41,
        'method': 'session/request_permission',
        'params': {
            'sessionId': 'runtime_1',
            'toolCall': {
                'toolCallId': 'call_1',
                'title': 'Write file',
                'rawInput': {'path': 'a.py'},
            },
            'options': [{
                'optionId': 'allow-once',
                'kind': 'allow_once',
            }],
        },
    }
    dispatch = asyncio.create_task(session._dispatch(incoming))  # noqa: SLF001
    await callback_started.wait()
    await dispatch
    assert writer.lines == []

    decision.set()
    await asyncio.gather(*session._request_tasks)  # noqa: SLF001
    assert writer.lines == [{
        'jsonrpc': '2.0',
        'id': 41,
        'result': {
            'outcome': {
                'outcome': 'selected',
                'optionId': 'allow-once',
            }
        },
    }]


@pytest.mark.asyncio
async def test_acp_accepts_future_returned_by_permission_callback():
    writer = _Writer()
    session = AcpSession(['fake-acp'])
    session.proc = SimpleNamespace(returncode=None, stdin=writer)
    decision = asyncio.get_running_loop().create_future()
    session.on_permission_request = lambda request: decision

    await session._dispatch({  # noqa: SLF001
        'jsonrpc': '2.0',
        'id': 42,
        'method': 'session/request_permission',
        'params': {'options': []},
    })
    decision.set_result({'outcome': {'outcome': 'cancelled'}})
    await asyncio.gather(*session._request_tasks)  # noqa: SLF001

    assert writer.lines[-1]['result']['outcome']['outcome'] == 'cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('decision', 'expected_outcome'),
    [('allow', 'selected'), ('deny', 'cancelled')],
)
async def test_bridge_forwards_and_resolves_permission_once(
    decision: str,
    expected_outcome: str,
):
    daemon = _daemon()
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    request = {
        'request_id': 41,
        'runtime_session_id': 'runtime_1',
        'call_id': 'call_1',
        'tool': 'Write file',
        'args': {'path': 'a.py'},
        'options': [
            {'optionId': 'allow-always', 'kind': 'allow_always'},
            {'optionId': 'allow-once', 'kind': 'allow_once'},
            {'optionId': 'deny-once', 'kind': 'reject_once'},
        ],
    }

    pending = asyncio.create_task(
        daemon._forward_permission_request(  # noqa: SLF001
            _envelope(),
            daemon._agents['ep_1'],  # noqa: SLF001
            request,
        ))
    await asyncio.sleep(0)
    permission_frame = next(
        frame for frame in sent if frame['type'] == 'permission_request')
    assert permission_frame['bridge_id'] == 'bridge_1'
    assert permission_frame['endpoint_id'] == 'ep_1'
    assert permission_frame['dispatch_id'] == 'd_perm'
    assert permission_frame['runtime_session_id'] == 'runtime_1'
    assert permission_frame['call_id'] == 'call_1'
    assert permission_frame['tool'] == 'Write file'
    assert permission_frame['args'] == {'path': 'a.py'}
    assert permission_frame['options'] == request['options']
    assert len(permission_frame['fingerprint']) == 64
    permission_request_id = permission_frame['permission_request_id']

    resolve = {
        'type': 'permission_resolve',
        'permission_request_id': permission_request_id,
        'fingerprint': permission_frame['fingerprint'],
        'decision': decision,
    }
    await daemon._on_message(resolve)  # noqa: SLF001
    await daemon._on_message(resolve)  # duplicate delivery is idempotent
    result = await pending

    assert result['outcome']['outcome'] == expected_outcome
    assert sum(f['type'] == 'permission_resolved' for f in sent) == 1
    assert sum(f['type'] == 'dispatch_waiting_approval' for f in sent) == 1
    assert sum(f['type'] == 'dispatch_resumed' for f in sent) == 1


@pytest.mark.asyncio
async def test_permission_timeout_denies_instead_of_wedging_acp():
    daemon = _daemon()
    daemon.permission_timeout = 0.01
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    result = await daemon._forward_permission_request(  # noqa: SLF001
        _envelope(),
        daemon._agents['ep_1'],  # noqa: SLF001
        {
            'request_id': 41,
            'runtime_session_id': 'runtime_1',
            'call_id': 'call_1',
            'tool': 'Write file',
            'args': {'path': 'a.py'},
            'options': [{
                'optionId': 'allow-once',
                'kind': 'allow_once',
            }],
        },
    )

    assert result == {'outcome': {'outcome': 'cancelled'}}
    resolved = next(f for f in sent if f['type'] == 'permission_resolved')
    assert resolved['decision'] == 'deny'
    assert resolved['reason'] == 'timeout'


@pytest.mark.asyncio
async def test_cancel_denies_pending_permission_without_resuming_dispatch():
    daemon = _daemon()
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    async def cancel(session_id: str) -> None:
        return None

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    daemon._agents['ep_1'].adapter.cancel = cancel  # noqa: SLF001
    pending = asyncio.create_task(
        daemon._forward_permission_request(  # noqa: SLF001
            _envelope(),
            daemon._agents['ep_1'],  # noqa: SLF001
            {
                'request_id': 41,
                'runtime_session_id': 'runtime_1',
                'call_id': 'call_1',
                'tool': 'Write file',
                'args': {'path': 'a.py'},
                'options': [{
                    'optionId': 'allow-once',
                    'kind': 'allow_once',
                }],
            },
        ))
    await asyncio.sleep(0)

    await daemon._on_message({  # noqa: SLF001
        'type': 'cancel',
        'dispatch_id': 'd_perm',
        'runtime_session_id': 'runtime_1',
    })
    assert await pending == {'outcome': {'outcome': 'cancelled'}}
    assert not any(f['type'] == 'dispatch_resumed' for f in sent)


@pytest.mark.asyncio
async def test_cancel_overrides_resolve_before_callback_can_resume():
    daemon = _daemon()
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    async def cancel(session_id: str) -> None:
        return None

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    daemon._agents['ep_1'].adapter.cancel = cancel  # noqa: SLF001
    pending = asyncio.create_task(
        daemon._forward_permission_request(  # noqa: SLF001
            _envelope(),
            daemon._agents['ep_1'],  # noqa: SLF001
            {
                'runtime_session_id': 'runtime_1',
                'call_id': 'call_1',
                'tool': 'Write file',
                'args': {'path': 'a.py'},
                'options': [{
                    'optionId': 'allow-once',
                    'kind': 'allow_once',
                }],
            },
        ))
    await asyncio.sleep(0)
    frame = next(f for f in sent if f['type'] == 'permission_request')
    await daemon._on_message({  # noqa: SLF001
        'type': 'permission_resolve',
        'permission_request_id': frame['permission_request_id'],
        'fingerprint': frame['fingerprint'],
        'decision': 'allow',
    })
    await daemon._on_message({  # noqa: SLF001
        'type': 'cancel',
        'dispatch_id': 'd_perm',
        'runtime_session_id': 'runtime_1',
    })

    assert await pending == {'outcome': {'outcome': 'cancelled'}}
    assert not any(f['type'] == 'dispatch_resumed' for f in sent)


@pytest.mark.asyncio
async def test_disconnect_marks_waiting_dispatch_continuation_required():
    daemon = _daemon()
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    pending = asyncio.create_task(
        daemon._forward_permission_request(  # noqa: SLF001
            _envelope(),
            daemon._agents['ep_1'],  # noqa: SLF001
            {
                'request_id': 41,
                'runtime_session_id': 'runtime_1',
                'call_id': 'call_1',
                'tool': 'Write file',
                'args': {'path': 'a.py'},
                'options': [],
            },
        ))
    await asyncio.sleep(0)

    await daemon._on_ws_disconnect()
    with pytest.raises(RuntimeError, match='continuation_required'):
        await pending

    await daemon._on_ws_connect()
    interrupted = next(
        frame for frame in sent
        if frame['type'] == 'dispatch_continuation_required')
    assert interrupted['dispatch_id'] == 'd_perm'
    assert interrupted['status'] == 'needs_manual_restart'
    assert interrupted['continuation_required'] is True
    assert 'args' not in interrupted
    assert 'options' not in interrupted


@pytest.mark.asyncio
async def test_resolve_wins_disconnect_race_without_false_restart_marker():
    daemon = _daemon()
    sent: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    daemon._send = capture  # type: ignore[method-assign]  # noqa: SLF001
    pending = asyncio.create_task(
        daemon._forward_permission_request(  # noqa: SLF001
            _envelope(),
            daemon._agents['ep_1'],  # noqa: SLF001
            {
                'runtime_session_id': 'runtime_1',
                'call_id': 'call_1',
                'tool': 'Write file',
                'args': {'path': 'a.py'},
                'options': [{
                    'optionId': 'allow-once',
                    'kind': 'allow_once',
                }],
            },
        ))
    await asyncio.sleep(0)
    frame = next(f for f in sent if f['type'] == 'permission_request')
    await daemon._on_message({  # noqa: SLF001
        'type': 'permission_resolve',
        'permission_request_id': frame['permission_request_id'],
        'fingerprint': frame['fingerprint'],
        'decision': 'allow',
    })
    await daemon._on_ws_disconnect()

    result = await pending
    assert result['outcome']['outcome'] == 'selected'
    await daemon._on_ws_connect()
    assert not any(
        f['type'] == 'dispatch_continuation_required' for f in sent)


def test_control_plane_maps_permission_frames_and_task_status(tmp_path):
    import os
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    http = os.path.join(root, 'webui', 'backend')
    for path in (root, http):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ['MS_AGENT_TEAM_PERSIST'] = '0'

    from team.state import get_team_state, reset_team_state_for_tests
    from team.ws_bridge_hub import (
        apply_permission_event_to_tasks,
        permission_frame_to_event,
    )

    reset_team_state_for_tests()
    state = get_team_state()
    task = TeamTask(
        task_id='task_1',
        project_id='proj_1',
        status='in_progress',
        prompt='change a file',
        trigger_user_id='owner_1',
        last_dispatch_id='d_perm',
    )
    state.projects.upsert(
        TeamProjectMeta(project_id='proj_1', name='p'))
    state.tasks.upsert(task)

    waiting = permission_frame_to_event({
        'type': 'permission_request',
        'permission_request_id': 'perm_1',
        'dispatch_id': 'd_perm',
        'endpoint_id': 'ep_1',
        'fingerprint': 'abc',
        'args': {'path': 'a.py'},
    })
    assert waiting is not None
    assert waiting.type == 'team.permission_requested'
    apply_permission_event_to_tasks(state, waiting)
    assert state.tasks.get('task_1').status == 'waiting_approval'
    assert state.pending_permissions['perm_1']['fingerprint'] == 'abc'

    continuation = permission_frame_to_event({
        'type': 'dispatch_continuation_required',
        'permission_request_id': 'perm_1',
        'dispatch_id': 'd_perm',
        'endpoint_id': 'ep_1',
        'status': 'needs_manual_restart',
    })
    apply_permission_event_to_tasks(state, continuation)
    assert continuation.payload['resume_status'] == 'needs_manual_restart'
    assert 'perm_1' in state.pending_permissions
