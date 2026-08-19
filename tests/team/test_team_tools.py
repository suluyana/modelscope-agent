# Copyright (c) ModelScope Contributors. All rights reserved.
"""Tests for team platform tools."""
from __future__ import annotations

import json

import pytest
from omegaconf import OmegaConf

from ms_agent.team.models import AgentEndpoint
from ms_agent.team.stores.memory import (
    MemoryArtifactStore,
    MemoryEndpointStore,
    MemoryEndpointTokenStore,
    MemoryTaskBoardStore,
)
from ms_agent.team.tools.endpoint_tools import (
    TeamArtifactTools,
    TeamEndpointTools,
    TeamTaskBoardTools,
)


@pytest.mark.asyncio
async def test_endpoint_tools_status_and_token():
    store = MemoryEndpointStore()
    tokens = MemoryEndpointTokenStore()
    store.upsert(
        AgentEndpoint(
            endpoint_id='ep1',
            at_name='me-gpu',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            status='online',
        ))
    cfg = OmegaConf.create({'output_dir': '.'})
    tools = TeamEndpointTools(
        cfg, endpoint_store=store, endpoint_token_store=tokens)
    await tools.connect()
    listed = await tools.get_tools()
    names = [t['tool_name'] for t in listed['team_endpoint']]
    assert 'delegate_to_endpoint' in names
    assert 'wait_for_endpoint_online' in names

    status = json.loads(
        await tools.call_tool(
            'team_endpoint',
            tool_name='get_endpoint_status',
            tool_args={'at_name': 'me-gpu'},
        ))
    assert status['status'] == 'online'

    tok = json.loads(
        await tools.call_tool(
            'team_endpoint',
            tool_name='issue_endpoint_token',
            tool_args={'endpoint_id': 'ep1'},
        ))
    assert tok['token'].startswith('etok_')


@pytest.mark.asyncio
async def test_artifact_and_taskboard_tools():
    arts = MemoryArtifactStore()
    tasks = MemoryTaskBoardStore()
    cfg = OmegaConf.create({'output_dir': '.'})
    atools = TeamArtifactTools(cfg, artifact_store=arts)
    ttools = TeamTaskBoardTools(cfg, task_store=tasks)
    await atools.connect()
    await ttools.connect()

    import base64
    up = json.loads(
        await atools.call_tool(
            'team_artifact',
            tool_name='upload_artifact',
            tool_args={
                'project_id': 'p1',
                'filename': 'a.bin',
                'content_base64': base64.b64encode(b'hello').decode(),
            },
        ))
    assert up['sha256']
    down = json.loads(
        await atools.call_tool(
            'team_artifact',
            tool_name='download_artifact',
            tool_args={
                'artifact_id': up['artifact_id'],
                'include_content_base64': True,
            },
        ))
    assert base64.b64decode(down['content_base64']) == b'hello'

    written = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='task_board_write',
            tool_args={
                'project_id': 'p1',
                'status': 'in_progress',
                'prompt': 'build',
                'blocked_by': [],
            },
        ))
    listed = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='task_board_read',
            tool_args={'project_id': 'p1'},
        ))
    assert any(t['task_id'] == written['task_id'] for t in listed)
    assert 'prompt' not in listed[0]


@pytest.mark.asyncio
async def test_dispatch_result_read_returns_final_text_not_tools():
    from ms_agent.team.events import TeamEvent
    from ms_agent.team.models import TeamTask
    from ms_agent.team.stores.dispatch_log import MemoryDispatchLogStore

    log = MemoryDispatchLogStore()
    log.append(
        TeamEvent(
            type='team.stream',
            dispatch_id='d1',
            at_name='bibo',
            payload={
                'type': 'tool_call',
                'name': 'find',
                'content': '/workspace',
            },
        ))
    reply = (
        'Warning: Model metadata for `qwen3.7-plus` not found. '
        'Defaulting to fallback metadata; this can degrade performance '
        'and cause issues.\n\n北京烤鸭'
    )
    log.append(
        TeamEvent(
            type='team.stream',
            dispatch_id='d1',
            at_name='bibo',
            payload={'type': 'text', 'content': reply},
        ))
    log.append(
        TeamEvent(
            type='team.dispatch_done',
            dispatch_id='d1',
            at_name='bibo',
            payload={'summary': reply, 'ok': True},
        ))
    tasks = MemoryTaskBoardStore()
    tasks.upsert(
        TeamTask(
            task_id='task_1',
            project_id='p1',
            status='completed',
            prompt='美食',
            trigger_user_id='u1',
            target_at_name='bibo',
            last_dispatch_id='d1',
        ))
    cfg = OmegaConf.create({'output_dir': '.'})
    ttools = TeamTaskBoardTools(
        cfg, task_store=tasks, dispatch_log=log)
    await ttools.connect()
    listed = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='task_board_read',
            tool_args={'project_id': 'p1'},
        ))
    assert listed[0]['last_dispatch_id'] == 'd1'
    assert listed[0]['read'] == 'dispatch_result_read'
    out = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='dispatch_result_read',
            tool_args={'dispatch_id': 'd1'},
        ))
    assert out['result_text'] == '北京烤鸭'
    assert 'find' not in json.dumps(out)
    by_task = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='dispatch_result_read',
            tool_args={'task_id': 'task_1'},
        ))
    assert by_task['result_text'] == '北京烤鸭'
