"""Prompt and agent hook executors."""

from __future__ import annotations

import pytest

from ms_agent.hooks.defaults import DEFAULT_ENABLED_EXECUTORS
from ms_agent.hooks.executor import HookExecutor
from ms_agent.hooks.executors.command import HookExecutionContext
from ms_agent.hooks.registry import HookHandlerConfig, HookRegistry
from ms_agent.llm.utils import Message


class ScriptedLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.replies.pop(0)


def _ctx(tmp_path, llm):
    return HookExecutionContext(
        session_id='s1',
        project_path=str(tmp_path),
        llm=llm,
        default_model='qwen-plus',
    )


@pytest.mark.asyncio
async def test_prompt_ok_true_is_pass_not_allow(tmp_path):
    llm = ScriptedLlm([
        Message(role='assistant', content='{"ok": true}'),
    ])
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'prompt'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='Check $ARGUMENTS'),
        {'event': 'PreToolUse', 'tool_name': 'Bash', 'tool_args': {}},
        _ctx(tmp_path, llm),
    )
    assert result.action == 'pass'
    assert llm.calls[0][1]['tools'] == []
    assert 'Check ' in llm.calls[0][0][-1].content


@pytest.mark.asyncio
async def test_prompt_ok_false_denies(tmp_path):
    llm = ScriptedLlm([
        Message(
            role='assistant',
            content='{"ok": false, "reason": "blocked by policy"}',
        ),
    ])
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'prompt'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='x'),
        {'event': 'PreToolUse'},
        _ctx(tmp_path, llm),
    )
    assert result.action == 'deny'
    assert 'blocked' in result.reason


@pytest.mark.asyncio
async def test_prompt_permission_decision_allow_skips_ask(tmp_path):
    llm = ScriptedLlm([
        Message(
            role='assistant',
            content='{"ok": true, "permissionDecision": "allow"}',
        ),
    ])
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'prompt'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='x'),
        {'event': 'PreToolUse'},
        _ctx(tmp_path, llm),
    )
    assert result.action == 'allow'


@pytest.mark.asyncio
async def test_prompt_invalid_json_is_error_unless_fail_closed(tmp_path):
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'prompt'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='x'),
        {'event': 'PreToolUse'},
        _ctx(tmp_path, ScriptedLlm([
            Message(role='assistant', content='not json'),
        ])),
    )
    assert result.action == 'error'

    closed = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='x', fail_closed=True),
        {'event': 'PreToolUse'},
        _ctx(tmp_path, ScriptedLlm([
            Message(role='assistant', content='not json'),
        ])),
    )
    assert closed.action == 'deny'


@pytest.mark.asyncio
async def test_prompt_missing_llm_errors(tmp_path):
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'prompt'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='prompt', prompt='x'),
        {'event': 'PreToolUse'},
        HookExecutionContext(session_id='s', project_path=str(tmp_path)),
    )
    assert result.action == 'error'


@pytest.mark.asyncio
async def test_agent_hook_reads_workspace_then_returns_ok(tmp_path):
    (tmp_path / 'README.md').write_text('ok to stop', encoding='utf-8')
    llm = ScriptedLlm([
        Message(
            role='assistant',
            content='',
            tool_calls=[{
                'id': '1',
                'tool_name': 'read_file',
                'arguments': '{"path": "README.md"}',
            }],
        ),
        Message(role='assistant', content='{"ok": true}'),
    ])
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'agent'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='agent', prompt='Verify $ARGUMENTS', max_turns=4),
        {'event': 'Stop', 'reason': 'done'},
        _ctx(tmp_path, llm),
    )
    assert result.action == 'pass'
    tool_msgs = [
        m for msgs, _ in llm.calls for m in msgs if getattr(m, 'role', '') == 'tool'
    ]
    assert tool_msgs
    assert 'ok to stop' in tool_msgs[0].content


@pytest.mark.asyncio
async def test_agent_hook_ok_false_denies(tmp_path):
    llm = ScriptedLlm([
        Message(
            role='assistant',
            content='{"ok": false, "reason": "unfinished work"}',
        ),
    ])
    executor = HookExecutor(
        working_dir=str(tmp_path),
        enabled_executors=frozenset({'agent'}),
    )
    result = await executor.execute(
        HookHandlerConfig(type='agent', prompt='x'),
        {'event': 'Stop'},
        _ctx(tmp_path, llm),
    )
    assert result.action == 'deny'
    assert 'unfinished' in result.reason


def test_default_enabled_executors_include_prompt_and_agent():
    assert DEFAULT_ENABLED_EXECUTORS == frozenset(
        {'command', 'prompt', 'agent'})


def test_registry_loads_prompt_when_executor_enabled():
    reg = HookRegistry.from_dict({
        'PreToolUse': [{
            'hooks': [{'type': 'prompt', 'prompt': 'check $ARGUMENTS'}],
        }],
    }, enabled_executors=frozenset({'prompt'}))
    handlers = reg.get_handlers('PreToolUse', 'any')
    assert len(handlers) == 1
    assert handlers[0].type == 'prompt'


def test_agent_hook_default_timeout_is_60():
    reg = HookRegistry.from_dict({
        'Stop': [{
            'hooks': [
                {'type': 'prompt', 'prompt': 'p'},
                {'type': 'agent', 'prompt': 'a'},
                {'type': 'agent', 'prompt': 'b', 'timeout': 90},
            ],
        }],
    }, enabled_executors=frozenset({'prompt', 'agent'}))
    prompt, agent, custom = reg.get_handlers('Stop')
    assert prompt.timeout == 30.0
    assert agent.timeout == 60.0
    assert custom.timeout == 90.0
