# Copyright (c) ModelScope Contributors. All rights reserved.
"""Rich Textual dashboard for live trajectory JSONL.

Install: ``pip install textual``
Run: ``python -m ms_agent.trajectory tui <output_dir_or_jsonl>``
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static


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


def _row_from_obj(obj: Dict[str, Any]) -> tuple[str, str, str, str]:
    if obj.get('type') == 'header':
        return (
            '—',
            'header',
            '—',
            f"run {obj.get('run_id', '')}",
        )
    if obj.get('type') == 'footer':
        return (
            '—',
            'footer',
            '—',
            f"n={obj.get('total_events')}",
        )
    if 'kind' in obj:
        ts = str(obj.get('ts', ''))[:19]
        kind = str(obj.get('kind', ''))
        tool = str(obj.get('tool_name') or '—')
        data = obj.get('data') or {}
        hook = data.get('hook_event_name', '')
        fw = data.get('framework', '')
        preview = hook or fw or json.dumps(data, ensure_ascii=False)[:52]
        if len(preview) > 52:
            preview = preview[:49] + '…'
        return (ts, kind, tool, preview)
    return ('?', '?', '?', json.dumps(obj, ensure_ascii=False)[:48])


class TrajectoryLiveApp(App):
    """Live table; tails JSONL by file offset."""

    CSS = """
    Screen { background: #0d1117; }
    #title { text-align: center; text-style: bold; color: #58a6ff; padding: 1; }
    DataTable { height: 1fr; border: heavy #238636; background: #010409; }
    DataTable > .datatable--header { background: #161b22; color: #79c0ff; }
    Footer { background: #161b22; color: #8b949e; }
    """

    BINDINGS = [('q', 'quit', 'Quit')]

    def __init__(self, jsonl_path: Path) -> None:
        super().__init__()
        self._path = jsonl_path
        self._offset = 0
        self._row_id = 0
        self._keys: deque[str] = deque()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f'Trajectory live  {self._path}',
            id='title',
        )
        yield Vertical(
            DataTable(
                show_header=True,
                zebra_stripes=True,
                cursor_type='row',
                id='grid',
            ),
            id='main',
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns('time', 'kind', 'tool', 'detail')
        self._read_new_lines()
        self.set_interval(0.35, self._tick)

    def _trim_table(self, table: DataTable) -> None:
        while table.row_count >= 450 and self._keys:
            oldest = self._keys.popleft()
            table.remove_row(oldest)

    def _read_new_lines(self) -> None:
        table = self.query_one(DataTable)
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                f.seek(self._offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    row = _row_from_obj(obj)
                    self._row_id += 1
                    key = str(self._row_id)
                    self._trim_table(table)
                    table.add_row(*row, key=key)
                    self._keys.append(key)
                self._offset = f.tell()
        except OSError:
            pass

    def _tick(self) -> None:
        table = self.query_one(DataTable)
        before = table.row_count
        self._read_new_lines()
        if table.row_count > before:
            table.scroll_end(animate=False)

    def action_quit(self) -> None:
        self.exit()


def run_tui(argv: List[str] | None = None) -> None:
    p = argparse.ArgumentParser(description='Trajectory Textual dashboard')
    p.add_argument(
        'path',
        nargs='?',
        default='.',
        help='output directory or .jsonl file',
    )
    args = p.parse_args(argv)
    target = _pick_jsonl(Path(args.path))
    TrajectoryLiveApp(target).run()
