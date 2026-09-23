"""Workspace-scoped read/grep for permission verifiers and agent hooks.

These tools never go through PermissionEnforcer, so they cannot recurse.
Writes, shell, network, and spawn are not available.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ms_agent.llm.utils import Message, Tool, collect_response

from .provider import run_sync_provider

_SKIP_DIRS = frozenset({
    '.git',
    'node_modules',
    '__pycache__',
    '.venv',
    'venv',
    '.tox',
    '.mypy_cache',
    '.pytest_cache',
})
_MAX_FILE_BYTES = 256 * 1024
_MAX_READ_LINES = 200
_MAX_GREP_HITS = 30
_MAX_GREP_FILES = 200
AGENT_MAX_TURNS = 50

INSPECT_TOOLS: list[Tool] = [
    {
        'tool_name': 'read_file',
        'description': (
            'Read a text file under the project workspace. Path is relative '
            'to the project root unless absolute and still inside it.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'offset': {
                    'type': 'integer',
                    'description': '1-based start line',
                },
                'limit': {'type': 'integer'},
            },
            'required': ['path'],
        },
    },
    {
        'tool_name': 'grep',
        'description': (
            'Search files under the project workspace with a Python regex.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'pattern': {'type': 'string'},
                'path': {
                    'type': 'string',
                    'description': 'Subdirectory or file under the workspace',
                },
                'glob': {'type': 'string'},
            },
            'required': ['pattern'],
        },
    },
]


def resolve_under_workspace(workspace: str, path: str) -> Path | None:
    root = Path(workspace).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def execute_inspect_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    workspace_root: str,
) -> str:
    if tool_name == 'read_file':
        return _read_file(workspace_root, tool_args)
    if tool_name == 'grep':
        return _grep(workspace_root, tool_args)
    return f'Unknown inspect tool: {tool_name}'


def _read_file(workspace: str, args: dict[str, Any]) -> str:
    target = resolve_under_workspace(workspace, str(args.get('path') or ''))
    if target is None:
        return 'Path is outside the workspace'
    if not target.is_file():
        return f'Not a file: {args.get("path")}'
    try:
        if target.stat().st_size > _MAX_FILE_BYTES:
            return f'File too large to read (>{_MAX_FILE_BYTES} bytes)'
        text = target.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return f'Read failed: {exc}'
    lines = text.splitlines()
    offset = max(int(args.get('offset') or 1), 1)
    limit = int(args.get('limit') or _MAX_READ_LINES)
    limit = max(1, min(limit, _MAX_READ_LINES))
    slice_ = lines[offset - 1:offset - 1 + limit]
    numbered = [
        f'{offset + i}|{line}' for i, line in enumerate(slice_)
    ]
    return '\n'.join(numbered) or '(empty)'


def _grep(workspace: str, args: dict[str, Any]) -> str:
    pattern = str(args.get('pattern') or '')
    if not pattern:
        return 'pattern is required'
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f'Invalid regex: {exc}'
    root = Path(workspace).resolve()
    start = resolve_under_workspace(
        workspace, str(args.get('path') or '.')) or root
    glob = str(args.get('glob') or '*')
    hits: list[str] = []
    files_seen = 0
    paths = [start] if start.is_file() else start.rglob(glob)
    for path in paths:
        if len(hits) >= _MAX_GREP_HITS or files_seen >= _MAX_GREP_FILES:
            break
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files_seen += 1
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b'\0' in raw[:1024]:
            continue
        text = raw.decode('utf-8', errors='replace')
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f'{rel}:{i}:{line[:200]}')
                if len(hits) >= _MAX_GREP_HITS:
                    break
    return '\n'.join(hits) or '(no matches)'


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    while isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


async def run_inspect_loop(
    llm: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    workspace_root: str,
    max_turns: int,
    model: str | None = None,
) -> str:
    """Short tool loop. Returns the last assistant text (or empty)."""
    turns = max(1, min(int(max_turns), AGENT_MAX_TURNS))
    messages: list[Message] = [
        Message(role='system', content=system_prompt),
        Message(role='user', content=user_prompt),
    ]
    last_text = ''
    for _ in range(turns):
        snapshot = list(messages)

        def generate(current=snapshot) -> Any:
            kwargs: dict[str, Any] = {'tools': INSPECT_TOOLS}
            if model:
                kwargs['model'] = model
            return collect_response(llm.generate(current, **kwargs))

        response = await run_sync_provider(generate)
        if response is None:
            return last_text
        messages.append(response)
        content = getattr(response, 'content', '') or ''
        if isinstance(content, str) and content.strip():
            last_text = content
        calls = list(getattr(response, 'tool_calls', None) or [])
        if not calls:
            return last_text
        for call in calls:
            name = call.get('tool_name') or ''
            args = _parse_tool_args(call.get('arguments'))
            result = execute_inspect_tool(name, args, workspace_root)
            messages.append(
                Message(
                    role='tool',
                    content=result,
                    tool_call_id=call.get('id') or '',
                    name=name,
                ))
    return last_text
