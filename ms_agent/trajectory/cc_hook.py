# Copyright (c) ModelScope Contributors. All rights reserved.
"""Claude Code hook bridge: read hook JSON from stdin, append trajectory lines.

Configure in ``.claude/settings.json``::

    {
      "hooks": {
        "PreToolUse": [{"matcher": "", "hooks": [{
          "type": "command",
          "command": "MS_AGENT_TRAJECTORY_DIR=/path/to/out python -m ms_agent.trajectory cc-hook"
        }]}],
        "PostToolUse": [{"matcher": "", "hooks": [{
          "type": "command",
          "command": "MS_AGENT_TRAJECTORY_DIR=/path/to/out python -m ms_agent.trajectory cc-hook"
        }]}],
        "Stop": [{"matcher": "", "hooks": [{
          "type": "command",
          "command": "MS_AGENT_TRAJECTORY_DIR=/path/to/out python -m ms_agent.trajectory cc-hook"
        }]}]
      }
    }

Or use one script path with env set in a wrapper shell script.

Requires ``MS_AGENT_TRAJECTORY_DIR``. Optional: ``MS_AGENT_TRAJECTORY_RUN_ID``
(default: ``session_id`` from payload), ``MS_AGENT_TRAJECTORY_AGENT_TAG``.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

from ms_agent.trajectory.models import EventKind
from ms_agent.trajectory.persistent_append import append_trajectory_event


def _truncate(obj: Any, limit: int = 12000) -> Any:
    if obj is None:
        return None
    s = json.dumps(obj, ensure_ascii=False)
    if len(s) <= limit:
        return obj
    return s[:limit] + '…[truncated]'


def process_cc_payload(payload: Dict[str, Any]) -> None:
    out_dir = os.environ.get('MS_AGENT_TRAJECTORY_DIR', '').strip()
    if not out_dir:
        print(
            'ms_agent trajectory cc-hook: set MS_AGENT_TRAJECTORY_DIR',
            file=sys.stderr,
        )
        sys.exit(0)

    session_id = str(payload.get('session_id') or 'default-session')
    run_id = os.environ.get('MS_AGENT_TRAJECTORY_RUN_ID', '').strip() or session_id
    agent_tag = os.environ.get('MS_AGENT_TRAJECTORY_AGENT_TAG', '').strip() or None

    hook_name = str(payload.get('hook_event_name') or '')
    tool_name = payload.get('tool_name')
    if tool_name is not None:
        tool_name = str(tool_name)

    base_data: Dict[str, Any] = {
        'framework': 'claude_code',
        'hook_event_name': hook_name,
    }

    if hook_name == 'PreToolUse':
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.TOOL_CALL.value,
            tool_name=tool_name,
            call_id=None,
            agent_tag=agent_tag,
            data={
                **base_data,
                'tool_input': _truncate(payload.get('tool_input'), 8000),
            },
            header_agent_tag=agent_tag,
        )
    elif hook_name == 'PostToolUse':
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.TOOL_RESULT.value,
            tool_name=tool_name,
            call_id=None,
            agent_tag=agent_tag,
            data={
                **base_data,
                'tool_response': _truncate(payload.get('tool_response'), 8000),
            },
            header_agent_tag=agent_tag,
        )
    elif hook_name == 'PostToolUseFailure':
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.TOOL_RESULT.value,
            tool_name=tool_name,
            call_id=None,
            agent_tag=agent_tag,
            data={
                **base_data,
                'error': str(payload.get('error') or payload.get('message') or ''),
            },
            header_agent_tag=agent_tag,
        )
    elif hook_name == 'Stop':
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.LLM_TURN.value,
            tool_name=None,
            call_id=None,
            agent_tag=agent_tag,
            data={
                **base_data,
                'phase': 'stop',
                'transcript_path': payload.get('transcript_path'),
            },
            header_agent_tag=agent_tag,
        )
    elif hook_name == 'SubagentStop':
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.AGENT_END.value,
            tool_name=tool_name,
            call_id=None,
            agent_tag=agent_tag,
            data={
                **base_data,
                'phase': 'subagent_stop',
                'stop_hook_active': payload.get('stop_hook_active'),
            },
            header_agent_tag=agent_tag,
        )
    else:
        append_trajectory_event(
            out_dir,
            run_id,
            kind=EventKind.LLM_TURN.value,
            tool_name=tool_name,
            call_id=None,
            agent_tag=agent_tag,
            data={**base_data, 'raw_keys': list(payload.keys())[:40]},
            header_agent_tag=agent_tag,
        )


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print('ms_agent trajectory cc-hook: invalid stdin JSON', file=sys.stderr)
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)
    process_cc_payload(payload)


if __name__ == '__main__':
    main()
