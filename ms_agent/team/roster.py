# Copyright (c) ModelScope Contributors. All rights reserved.
"""Per-agent skill ledger for humans / Lead. Never used to auto-dispatch."""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Outcome = Literal['completed', 'failed', 'wait_human']
_KEEP = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RosterStore:
    """In-memory events; optional file persist."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        self._events = list(data.get('events') or [])

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({'events': self._events[-500:]}, ensure_ascii=False),
            encoding='utf-8')
        tmp.rename(self._path)

    def record(
        self,
        *,
        at_name: str,
        outcome: Outcome,
        task_id: str = '',
        tags: list[str] | None = None,
        project_id: str = '',
    ) -> None:
        name = (at_name or '').lstrip('@').strip()
        if not name:
            return
        tag = (tags or ['general'])[0] if (tags or ['general']) else 'general'
        tag = str(tag).strip().lower() or 'general'
        with self._lock:
            self._events.append({
                'at_name': name,
                'tag': tag,
                'outcome': outcome,
                'task_id': task_id,
                'project_id': project_id,
                'created_at': _now_iso(),
            })
            self._events = self._events[-500:]
            self._save()

    def summarize(self) -> list[dict[str, Any]]:
        """Accuracy is display-only. Coordinator must not read this to route."""
        buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
        with self._lock:
            events = list(self._events)
        for ev in events:
            key = (ev.get('at_name') or '', ev.get('tag') or 'general')
            buckets[key].append(str(ev.get('outcome') or ''))
        rows = []
        for (at_name, tag), outcomes in sorted(buckets.items()):
            recent = outcomes[-_KEEP:]
            done = sum(1 for o in recent if o == 'completed')
            failed = sum(1 for o in recent if o == 'failed')
            asked = sum(1 for o in recent if o == 'wait_human')
            judged = done + failed
            accuracy = (done / judged) if judged else None
            rows.append({
                'at_name': at_name,
                'tag': tag,
                'completed': done,
                'failed': failed,
                'wait_human': asked,
                'accuracy': round(accuracy, 3) if accuracy is not None else None,
                'n': len(recent),
            })
        return rows

    def preview_lines(self, limit: int = 12) -> list[str]:
        lines = []
        for row in self.summarize()[:limit]:
            acc = row['accuracy']
            acc_s = f'{acc:.0%}' if acc is not None else 'n/a'
            lines.append(
                f"@{row['at_name']} tag={row['tag']} "
                f"ok={row['completed']} fail={row['failed']} "
                f"ask={row['wait_human']} acc={acc_s} (display only)")
        return lines
