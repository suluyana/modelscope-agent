# Copyright (c) ModelScope Contributors. All rights reserved.
"""Stdio MCP so ACP workers can file an explicit ``task_ask`` ticket.

A question in the assistant reply is treated as a finished answer. Call
this tool instead. Default audience is Lead.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

PROTOCOL_VERSION = '2024-11-05'

TOOL_ASK = {
    'name': 'task_ask',
    'description': (
        'Park this task as wait_human and ask for a missing fact. '
        'Default audience is lead: the Lead patches the prompt (your '
        'transcript is not dumped). audience=user surfaces the question '
        'for the human on this rail. Call this and stop. A question in '
        'the reply is treated as a finished answer.'
    ),
    'inputSchema': {
        'type': 'object',
        'properties': {
            'question': {'type': 'string'},
            'audience': {
                'type': 'string',
                'enum': ['user', 'lead'],
            },
            'task_id': {'type': 'string'},
            'project_id': {'type': 'string'},
        },
        'required': ['question'],
    },
}


def _env(name: str, default: str = '') -> str:
    return (os.environ.get(name) or default).strip()


def post_ask(
    api_base: str,
    project_id: str,
    task_id: str,
    question: str,
    audience: str = 'lead',
) -> dict[str, Any]:
    """POST /api/v1/team/projects/{id}/tasks/{tid}/ask."""
    base = (api_base or '').rstrip('/')
    if not base or not project_id or not task_id:
        raise ValueError('api_base, project_id, and task_id are required')
    url = f'{base}/api/v1/team/projects/{project_id}/tasks/{task_id}/ask'
    payload = json.dumps({
        'question': question,
        'audience': audience or 'lead',
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ''
        raise RuntimeError(f'ask HTTP {exc.code}: {body}') from exc


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        'content': [{'type': 'text', 'text': text}],
        'isError': is_error,
    }


def handle_rpc(
    msg: dict[str, Any],
    *,
    post_fn: Callable[..., dict[str, Any]] = post_ask,
) -> dict[str, Any] | None:
    """Return a JSON-RPC result payload, or None for notifications."""
    method = msg.get('method') or ''
    if 'id' not in msg:
        return None
    if method == 'initialize':
        client_pv = (
            (msg.get('params') or {}).get('protocolVersion')
            or PROTOCOL_VERSION)
        return {
            'protocolVersion': client_pv,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'team-ask', 'version': '1'},
        }
    if method == 'ping':
        return {}
    if method in ('tools/list', 'tools/listChanged'):
        return {'tools': [TOOL_ASK]}
    if method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name') or ''
        args = params.get('arguments') or {}
        if name != 'task_ask':
            return _tool_result(f'unknown tool {name}', is_error=True)
        question = str(args.get('question') or '').strip()
        if not question:
            return _tool_result('question_required', is_error=True)
        audience = str(args.get('audience') or 'lead')
        if audience not in ('user', 'lead'):
            audience = 'lead'
        project_id = str(
            args.get('project_id') or _env('MS_AGENT_TEAM_PROJECT_ID'))
        task_id = str(args.get('task_id') or _env('MS_AGENT_TEAM_TASK_ID'))
        api_base = _env('MS_AGENT_TEAM_API')
        try:
            result = post_fn(
                api_base, project_id, task_id, question, audience)
        except Exception as exc:  # noqa: BLE001
            return _tool_result(str(exc), is_error=True)
        return _tool_result(json.dumps(result, ensure_ascii=False))
    return _tool_result(f'unknown method {method}', is_error=True)


def main() -> None:
    """JSON-RPC stdio loop. Never write non-JSON to stdout."""
    orig_stdout = sys.stdout
    sys.stdout = sys.stderr
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        result = handle_rpc(msg)
        if result is None or 'id' not in msg:
            continue
        orig_stdout.write(
            json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': result,
            }, ensure_ascii=False)
            + '\n')
        orig_stdout.flush()


if __name__ == '__main__':
    main()
