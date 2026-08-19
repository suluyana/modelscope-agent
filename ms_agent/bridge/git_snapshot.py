# Copyright (c) ModelScope Contributors. All rights reserved.
"""Collect a lightweight git snapshot for context_bundle."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def collect_git_snapshot(cwd: str | Path | None = None) -> dict[str, Any]:
    root = Path(cwd) if cwd else Path.cwd()
    if not (root / '.git').exists() and not _find_git_root(root):
        return {'available': False, 'reason': 'not a git repository'}

    def _run(args: list[str]) -> str:
        try:
            out = subprocess.run(
                args,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return (out.stdout or '').strip()
        except (OSError, subprocess.TimeoutExpired):
            return ''

    branch = _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    head = _run(['git', 'rev-parse', '--short', 'HEAD'])
    status = _run(['git', 'status', '--porcelain'])
    diff_stat = _run(['git', 'diff', '--stat', 'HEAD'])
    recent = _run(['git', 'log', '-1', '--oneline'])
    return {
        'available': True,
        'branch': branch,
        'head': head,
        'dirty': bool(status),
        'status_porcelain': status[:4000],
        'diff_stat': diff_stat[:4000],
        'recent_commit': recent,
    }


def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / '.git').exists():
            return p
    return None
