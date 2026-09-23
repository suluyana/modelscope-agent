# Copyright (c) ModelScope Contributors. All rights reserved.
"""Lead vs worker system prompts for in-process cloud agents."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HTTP = _ROOT / 'webui' / 'backend'
for _p in (str(_ROOT), str(_HTTP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('MS_AGENT_TEAM_PERSIST', '0')
os.environ.setdefault('MS_AGENT_TEAM_CLOUD_DRY_RUN', '1')

from ms_agent.team.models import (  # noqa: E402
    AgentEndpoint,
    ContextBundle,
    DispatchEnvelope,
)
from ms_agent.team.stores.memory import MemoryEndpointStore  # noqa: E402
from team.cloud_runner import CloudAgentRuntime  # noqa: E402


def _envelope(audience: str, at_name: str = 'ms_agent') -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id='d1',
        prompt='please split the job',
        project_id='p1',
        target_endpoint_id='ep-lead',
        target_at_name=at_name,
        sender_user_id='u1',
        channel='web',
        thread_id='t1',
        context_bundle=ContextBundle(audience=audience, project_id='p1'),
        permission_tier='owner',
        caller_is_owner=True,
    )


def test_lead_system_prompt_asks_board_write_not_coding():
    store = MemoryEndpointStore()
    store.upsert(
        AgentEndpoint(
            endpoint_id='ep-bibo',
            at_name='bibo',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            status='online',
        ))
    rt = CloudAgentRuntime(endpoint_store=store, dry_run=True)
    text = rt._system_prompt(_envelope('lead'))
    assert 'task_board_write' in text
    assert 'todo_write' in text
    assert 'status=pending' in text
    assert 'bibo' in text
    assert 'coding agent' not in text
    filled = _envelope('lead')
    filled.context_bundle.task_snapshots = [
        {'project_id': 'p1', 'task_id': 't1'}]
    follow = rt._system_prompt(filled)
    assert 'already has rows' in follow
    assert 'second decompose' in follow
    assert 'task_ask' in text
    assert 'wait_human' in follow
    assert 'board is the plan' in text


def test_worker_system_prompt_collects_via_dispatch_result_read():
    rt = CloudAgentRuntime(dry_run=True)
    text = rt._system_prompt(_envelope('worker', at_name='ms_agent'))
    assert 'dispatch_result_read' in text
    assert 'task_board_write' not in text
    assert 'task_ask' in text
    assert 'glob/find/ls' in text


@pytest.mark.asyncio
async def test_maybe_attach_tools_indexes_taskboard():
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    class TM:
        extra_tools: list = []

        def __init__(self):
            self.extra_tools = []
            self.indexed: list[str] = []

        async def index_extra_tool(self, tool):
            self.indexed.append(type(tool).__name__)

    class Agent:
        def __init__(self):
            self.tool_manager = TM()

    rt = CloudAgentRuntime(
        task_store=MemoryTaskBoardStore(), dry_run=True)
    agent = Agent()
    await rt._maybe_attach_tools(agent, _envelope('lead'))
    assert 'TeamTaskBoardTools' in agent.tool_manager.indexed
    assert any(
        type(t).__name__ == 'TeamTaskBoardTools'
        for t in agent.tool_manager.extra_tools)

