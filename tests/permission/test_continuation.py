"""Restart continuation: fingerprint match consumes a one-shot token."""
from __future__ import annotations

import pytest

from ms_agent.permission.approval import (
    ApprovalRequest,
    FileApprovalStore,
    MemoryApprovalStore,
    approval_fingerprint,
)
from ms_agent.permission.config import PermissionConfig
from ms_agent.permission.enforcer import PermissionEnforcer
from ms_agent.permission.handler import (
    PermissionAction,
    PermissionResponse,
    WebPermissionHandler,
)
from ms_agent.permission.memory import PermissionMemory


class _DenyHandler:
    async def ask(self, tool_name, tool_args, context, suggestions=None, call_id=''):
        return PermissionResponse(
            action=PermissionAction.DENY, feedback='should not ask')


def test_fingerprint_ignores_call_id():
    a = approval_fingerprint('shell', {'cmd': 'ls'}, call_id='c1')
    b = approval_fingerprint('shell', {'cmd': 'ls'}, call_id='c2')
    c = approval_fingerprint('shell', {'cmd': 'rm'}, call_id='c1')
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_restart_resolve_queues_resume_and_matching_call_consumes(
        tmp_path):
    path = tmp_path / 'asks.json'
    store = FileApprovalStore(path)
    request = ApprovalRequest.create(
        'code_executor---shell_executor',
        {'command': 'curl https://example.com'},
        call_id='old-call',
        session_id='sess-1',
        dispatch_id='d1',
    )
    store.create(request)

    class Emitter:
        def emit(self, event):
            pass

    restarted = FileApprovalStore(path)
    handler = WebPermissionHandler(Emitter(), store=restarted)
    assert handler.resolve(
        request.id,
        PermissionResponse(action=PermissionAction.ALLOW_ONCE),
    )
    queued = restarted.get(request.id)
    assert queued is not None
    assert queued.state == 'resume_queued'

    enforcer = PermissionEnforcer(
        config=PermissionConfig.from_dict({'mode': 'interactive'}),
        handler=_DenyHandler(),
        memory=PermissionMemory(project_path=tmp_path / 'proj'),
        approval_store=restarted,
    )
    names = enforcer.attach_continuations(
        session_id='sess-1', dispatch_id='d1')
    assert 'code_executor---shell_executor' in names

    allowed = await enforcer.check(
        'code_executor---shell_executor',
        {'command': 'curl https://example.com'},
        call_id='brand-new-id',
    )
    assert allowed.action == 'allow'
    assert 'continuation' in allowed.reason
    assert restarted.get(request.id).state == 'resumed'

    second = await enforcer.check(
        'code_executor---shell_executor',
        {'command': 'curl https://example.com'},
        call_id='another',
    )
    assert second.action == 'deny'


@pytest.mark.asyncio
async def test_fingerprint_mismatch_asks_again_and_burns_old_token(tmp_path):
    store = MemoryApprovalStore()
    request = ApprovalRequest.create(
        'code_executor---shell_executor',
        {'command': 'curl https://a.example'},
        session_id='s',
    )
    store.create(request)
    store.queue_resume(request.id, decision='allow_once')

    enforcer = PermissionEnforcer(
        config=PermissionConfig.from_dict({'mode': 'interactive'}),
        handler=_DenyHandler(),
        memory=PermissionMemory(project_path=tmp_path / 'p'),
        approval_store=store,
    )
    enforcer.attach_continuations(session_id='s')

    denied = await enforcer.check(
        'code_executor---shell_executor',
        {'command': 'curl https://b.example'},
        call_id='n1',
    )
    assert denied.action == 'deny'
    assert store.get(request.id).state == 'resume_failed'


def test_resume_notice_does_not_include_tool_args():
    from ms_agent.agent.llm_agent import LLMAgent
    from ms_agent.llm.utils import Message

    class _E:
        def attach_continuations(self, **kwargs):
            return ['code_executor---shell_executor']

    class _TM:
        _permission_enforcer = _E()

    agent = LLMAgent.__new__(LLMAgent)
    agent.tool_manager = _TM()
    agent._permission_handler = None
    agent.runtime = type('R', (), {'session_id': 's'})()
    messages: list[Message] = []
    agent._attach_permission_continuations(messages)
    assert messages
    text = messages[0].content
    assert 'code_executor---shell_executor' in text
    assert 'curl' not in text
    assert 'Arguments are not replayed' in text


def test_web_handler_late_approve_after_reload_does_not_need_token(tmp_path):
    path = tmp_path / 'asks.json'
    first = FileApprovalStore(path)
    request = ApprovalRequest.create('tool', {'x': 1})
    first.create(request)

    class Emitter:
        def emit(self, event):
            pass

    handler = WebPermissionHandler(Emitter(), store=FileApprovalStore(path))
    assert handler.resolve(
        request.id,
        PermissionResponse(action=PermissionAction.ALLOW_ONCE),
    )
    assert FileApprovalStore(path).get(request.id).state == 'resume_queued'
