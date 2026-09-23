"""accept_edits / plan modes and persist-pattern guard."""
from __future__ import annotations

import pytest

from ms_agent.permission.config import PermissionConfig
from ms_agent.permission.enforcer import PermissionEnforcer
from ms_agent.permission.handler import PermissionAction, PermissionResponse
from ms_agent.permission.ledger import PermissionLedger
from ms_agent.permission.pattern_guard import (
    UnsafePersistPattern,
    validate_persist_pattern,
)
from ms_agent.permission.approval import MemoryApprovalStore


class _AskOnce:
    async def ask(self, tool_name, tool_args, context, suggestions=None,
                  call_id=''):
        return PermissionResponse(action=PermissionAction.ALLOW_ONCE)


class _AskAlways:
    def __init__(self, pattern: str):
        self.pattern = pattern

    async def ask(self, tool_name, tool_args, context, suggestions=None,
                  call_id=''):
        return PermissionResponse(
            action=PermissionAction.ALLOW_ALWAYS, pattern=self.pattern)


@pytest.mark.asyncio
async def test_accept_edits_allows_write_asks_shell():
    enf = PermissionEnforcer(
        PermissionConfig.from_dict({'mode': 'acceptEdits'}),
        handler=_AskOnce(),
    )
    write = await enf.check('file_system---write_file', {'path': 'a.py'})
    assert write.action == 'allow'
    assert 'Accept edits' in write.reason
    shell = await enf.check(
        'code_executor---shell_executor', {'command': 'ls'})
    assert shell.action == 'allow'
    assert 'allowed once' in shell.reason.lower()


@pytest.mark.asyncio
async def test_plan_mode_denies_writes_allows_reads():
    enf = PermissionEnforcer(
        PermissionConfig.from_dict({'mode': 'plan'}),
        handler=_AskOnce(),
    )
    read = await enf.check('file_system---read_file', {'path': 'a.py'})
    assert read.action == 'allow'
    write = await enf.check('file_system---write_file', {'path': 'a.py'})
    assert write.action == 'deny'
    shell = await enf.check(
        'code_executor---shell_executor', {'command': 'rm a.py'})
    assert shell.action == 'deny'
    board = await enf.check('team_taskboard---task_board_write', {})
    assert board.action == 'allow'


def test_validate_persist_rejects_bare_and_pipe():
    with pytest.raises(UnsafePersistPattern):
        validate_persist_pattern('*')
    with pytest.raises(UnsafePersistPattern):
        validate_persist_pattern('a|b')
    with pytest.raises(UnsafePersistPattern):
        validate_persist_pattern('code_executor---shell_executor:ls && rm')
    assert validate_persist_pattern(
        'code_executor---shell_executor:git *') == (
            'code_executor---shell_executor:git *')


def test_ledger_add_rule_rejects_unsafe(tmp_path):
    ledger = PermissionLedger(
        project_path=tmp_path / 'p',
        asks_store=MemoryApprovalStore(),
    )
    with pytest.raises(UnsafePersistPattern):
        ledger.add_rule('*')
    ledger.add_rule('file_system---read_file', scope='project')
    preview = ledger.preview_match('file_system---read_file', {'path': 'x'})
    assert preview['matches'] is True
    assert preview['rules'][0]['kind'] == 'file'


@pytest.mark.asyncio
async def test_allow_always_unsafe_pattern_does_not_persist(tmp_path):
    from ms_agent.permission.memory import PermissionMemory

    enf = PermissionEnforcer(
        PermissionConfig.from_dict({'mode': 'interactive'}),
        handler=_AskAlways('*'),
        memory=PermissionMemory(project_path=tmp_path),
    )
    r = await enf.check('file_system---write_file', {'path': 'a.py'})
    assert r.action == 'deny'
    assert not (tmp_path / '.ms_agent' / 'permission_memory.json').exists()
