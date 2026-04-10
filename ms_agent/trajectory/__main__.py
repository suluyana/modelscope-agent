# Copyright (c) ModelScope Contributors. All rights reserved.
"""CLI: ``watch`` | ``cc-hook`` | ``tui`` | ``serve`` | ``append-json`` | ``openclaw-hook``.

Examples::

  python -m ms_agent.trajectory watch ./output
  python -m ms_agent.trajectory tui ./output
  python -m ms_agent.trajectory serve 8765 ./output
  MS_AGENT_TRAJECTORY_DIR=/tmp/out python -m ms_agent.trajectory cc-hook < payload.json
  echo '{"run_id":"s1","kind":"llm_turn","data":{}}' | python -m ms_agent.trajectory append-json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ms_agent.trajectory.models import EventKind
from ms_agent.trajectory.persistent_append import append_trajectory_event


def _pick_jsonl(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        traj = path / 'trajectories'
        if traj.is_dir():
            files = sorted(
                traj.glob('*.jsonl'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                return files[0]
        files = sorted(
            path.glob('*.jsonl'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            return files[0]
    raise SystemExit(f'No trajectory jsonl found under {path}')


def _summarize(obj: Dict[str, Any], raw_json: bool) -> str:
    if raw_json:
        return json.dumps(obj, ensure_ascii=False)
    t = obj.get('type')
    if t == 'header':
        return (
            f"[header] run_id={obj.get('run_id')} "
            f"started_at={obj.get('started_at')}"
        )
    if t == 'footer':
        return (
            f"[footer] total_events={obj.get('total_events')} "
            f"ended_at={obj.get('ended_at')}"
        )
    if 'kind' in obj:
        return (
            f"{obj.get('ts')} kind={obj.get('kind')} "
            f"tool={obj.get('tool_name')} call_id={obj.get('call_id')}"
        )
    return json.dumps(obj, ensure_ascii=False)


def cmd_watch(argv: Optional[list] = None) -> None:
    p = argparse.ArgumentParser(description='Tail trajectory JSONL (stdlib).')
    p.add_argument('path', help='jsonl file or output directory')
    p.add_argument('--json', action='store_true', help='print raw JSON lines')
    args = p.parse_args(argv)

    target = _pick_jsonl(Path(args.path))
    print(f'Watching {target}', file=sys.stderr)

    with open(target, 'r', encoding='utf-8') as f:
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    print(line)
                    continue
                print(_summarize(obj, args.json))
                continue
            time.sleep(0.2)


def cmd_append_json(argv: Optional[list] = None) -> None:
    """Stdin: one JSON object with keys run_id, kind, data, tool_name?, call_id?.

    Uses env MS_AGENT_TRAJECTORY_DIR (required).
    """
    p = argparse.ArgumentParser(description='Append one trajectory event from JSON stdin.')
    p.parse_args(argv)
    out_dir = os.environ.get('MS_AGENT_TRAJECTORY_DIR', '').strip()
    if not out_dir:
        print('append-json: set MS_AGENT_TRAJECTORY_DIR', file=sys.stderr)
        sys.exit(1)
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    obj = json.loads(raw)
    run_id = str(obj.get('run_id') or 'default-session')
    kind = str(obj.get('kind') or EventKind.LLM_TURN.value)
    append_trajectory_event(
        out_dir,
        run_id,
        kind=kind,
        tool_name=obj.get('tool_name'),
        call_id=obj.get('call_id'),
        agent_tag=obj.get('agent_tag'),
        data=dict(obj.get('data') or {}),
        header_agent_tag=obj.get('header_agent_tag'),
    )


def cmd_openclaw_hook(argv: Optional[list] = None) -> None:
    """Stdin JSON from OpenClaw handler (see contrib template)."""
    p = argparse.ArgumentParser(description='Append from OpenClaw hook JSON stdin.')
    p.parse_args(argv)
    out_dir = os.environ.get('MS_AGENT_TRAJECTORY_DIR', '').strip()
    if not out_dir:
        print('openclaw-hook: set MS_AGENT_TRAJECTORY_DIR', file=sys.stderr)
        sys.exit(0)
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    ev = json.loads(raw)
    run_id = str(ev.get('session_id') or ev.get('run_id') or 'openclaw')
    kind = str(ev.get('kind') or EventKind.LLM_TURN.value)
    data = dict(ev.get('data') or {})
    data.setdefault('framework', 'openclaw')
    append_trajectory_event(
        out_dir,
        run_id,
        kind=kind,
        tool_name=ev.get('tool_name'),
        call_id=ev.get('call_id'),
        agent_tag=ev.get('agent_tag'),
        data=data,
        header_agent_tag=ev.get('header_agent_tag'),
    )


def cmd_tui(argv: Optional[list] = None) -> None:
    try:
        from ms_agent.trajectory.tui_app import run_tui
    except ImportError as e:
        print(
            'TUI requires textual: pip install textual',
            file=sys.stderr,
        )
        raise SystemExit(1) from e
    run_tui(argv)


def main(argv: Optional[list] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            'Usage:\n'
            '  python -m ms_agent.trajectory watch <path>\n'
            '  python -m ms_agent.trajectory tui <path>\n'
            '  python -m ms_agent.trajectory serve [PORT] <path>\n'
            '  python -m ms_agent.trajectory cc-hook   # stdin JSON\n'
            '  python -m ms_agent.trajectory append-json   # stdin JSON\n'
            '  python -m ms_agent.trajectory openclaw-hook   # stdin JSON\n'
            'Legacy: python -m ms_agent.trajectory <path>  (same as watch)\n',
            file=sys.stderr,
        )
        sys.exit(1)
    if argv[0] in ('-h', '--help'):
        print(
            'Usage:\n'
            '  python -m ms_agent.trajectory watch <path>\n'
            '  python -m ms_agent.trajectory tui <path>\n'
            '  python -m ms_agent.trajectory serve [PORT] <path>\n'
            '  python -m ms_agent.trajectory cc-hook\n'
            '  python -m ms_agent.trajectory append-json\n'
            '  python -m ms_agent.trajectory openclaw-hook\n',
        )
        return

    subcommands = (
        'watch', 'cc-hook', 'tui', 'serve', 'append-json', 'openclaw-hook',
    )
    if argv[0] in subcommands:
        cmd, rest = argv[0], argv[1:]
    else:
        cmd, rest = 'watch', argv

    if cmd == 'watch':
        cmd_watch(rest)
    elif cmd == 'cc-hook':
        from ms_agent.trajectory.cc_hook import main as cc_main

        cc_main()
    elif cmd == 'tui':
        cmd_tui(rest)
    elif cmd == 'serve':
        from ms_agent.trajectory.web_app import main as web_main

        web_main(rest)
    elif cmd == 'append-json':
        cmd_append_json(rest)
    elif cmd == 'openclaw-hook':
        cmd_openclaw_hook(rest)


if __name__ == '__main__':
    main()
