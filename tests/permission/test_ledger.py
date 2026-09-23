"""PermissionLedger: rules = PermissionMemory, asks = ApprovalStore."""
from __future__ import annotations

import asyncio

from ms_agent.permission.approval import MemoryApprovalStore
from ms_agent.permission.handler import (
    PermissionAction,
    PermissionResponse,
    WebPermissionHandler,
)
from ms_agent.permission.ledger import PermissionLedger
from ms_agent.permission.memory import PermissionMemory


def test_always_allow_lands_in_memory_not_a_second_json(tmp_path):
    ledger = PermissionLedger(
        project_path=tmp_path / 'proj',
        global_memory_path=tmp_path / 'global' / 'permission_memory.json',
        asks_store=MemoryApprovalStore(),
    )
    ledger.add_rule('code_executor---shell_executor:git *', scope='project')

    reloaded = PermissionMemory(
        project_path=tmp_path / 'proj',
        global_path=tmp_path / 'global' / 'permission_memory.json',
    )
    assert reloaded.matches(
        'code_executor---shell_executor', {'command': 'git status'})
    assert ledger.list_pending_asks() == []
    assert (tmp_path / 'proj' / '.ms_agent' / 'permission_memory.json').exists()


def test_pending_ask_and_settings_inbox_share_one_store(tmp_path):
    store = MemoryApprovalStore()
    ledger = PermissionLedger(
        project_path=tmp_path / 'proj',
        asks_store=store,
    )
    created = ledger.create_ask(
        'code_executor---shell_executor',
        {'command': 'curl https://example.com'},
        call_id='c1',
        dispatch_id='d1',
        project_id='p1',
    )
    pending = ledger.list_pending_asks(project_id='p1')
    assert [a.id for a in pending] == [created.id]
    assert pending[0].call_id == 'c1'

    decided = ledger.decide_ask(
        created,
        PermissionResponse(action=PermissionAction.ALLOW_ONCE),
    )
    assert decided.state == 'approved'
    assert ledger.list_pending_asks(project_id='p1') == []


def test_web_handler_writes_asks_to_the_shared_store():
    class _Emitter:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    store = MemoryApprovalStore()
    handler = WebPermissionHandler(_Emitter(), store=store)

    async def run():
        task = asyncio.create_task(
            handler.ask('shell', {'cmd': 'ls'}, '', call_id='c9'))
        await asyncio.sleep(0.01)
        request_id = handler._event_emitter.events[0]['request_id']
        assert store.get(request_id).state == 'pending'
        handler.resolve(
            request_id,
            PermissionResponse(action=PermissionAction.ALLOW_ONCE))
        await task
        assert store.get(request_id).state == 'approved'

    asyncio.run(run())
