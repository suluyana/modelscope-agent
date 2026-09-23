# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

import json

from ms_agent.bridge.adapters.acp_runtime import team_ask_mcp_servers
from ms_agent.team.mcp_ask import handle_rpc


def test_handle_rpc_initialize_and_tools_list():
    init = handle_rpc({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {'protocolVersion': '2025-03-26'},
    })
    assert init['protocolVersion'] == '2025-03-26'
    assert init['serverInfo']['name'] == 'team-ask'
    listed = handle_rpc({
        'jsonrpc': '2.0',
        'id': 2,
        'method': 'tools/list',
    })
    names = [t['name'] for t in listed['tools']]
    assert names == ['task_ask']


def test_handle_rpc_task_ask_defaults_to_lead(monkeypatch):
    monkeypatch.setenv('MS_AGENT_TEAM_API', 'http://127.0.0.1:8000')
    monkeypatch.setenv('MS_AGENT_TEAM_PROJECT_ID', 'p1')
    monkeypatch.setenv('MS_AGENT_TEAM_TASK_ID', 't1')
    captured = []

    def fake_post(api_base, project_id, task_id, question, audience='lead'):
        captured.append((api_base, project_id, task_id, question, audience))
        return {'ok': True, 'task': {'status': 'wait_human'}}

    result = handle_rpc(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {
                'name': 'task_ask',
                'arguments': {'question': 'need the path'},
            },
        },
        post_fn=fake_post,
    )
    assert result['isError'] is False
    body = json.loads(result['content'][0]['text'])
    assert body['ok'] is True
    assert captured == [
        ('http://127.0.0.1:8000', 'p1', 't1', 'need the path', 'lead'),
    ]


def test_team_ask_mcp_servers_requires_ids():
    assert team_ask_mcp_servers(
        api_base='http://127.0.0.1:8000',
        project_id='p1',
        task_id=None,
    ) == []
    servers = team_ask_mcp_servers(
        api_base='http://127.0.0.1:8000',
        project_id='p1',
        task_id='t1',
    )
    assert len(servers) == 1
    assert servers[0]['args'] == ['-m', 'ms_agent.team.mcp_ask']
    assert servers[0]['env']['MS_AGENT_TEAM_TASK_ID'] == 't1'
