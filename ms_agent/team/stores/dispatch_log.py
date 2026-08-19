# Copyright (c) ModelScope Contributors. All rights reserved.
"""Per-dispatch event log for private-stream replay (C-05)."""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from ms_agent.team.events import TeamEvent


class DispatchLogStore(Protocol):

    def append(self, event: TeamEvent) -> None:
        ...

    def list(self, dispatch_id: str) -> list[TeamEvent]:
        ...


class MemoryDispatchLogStore:
    """In-process log. Lost on restart; enough for tests and persist=0."""

    def __init__(self) -> None:
        self._by_id: dict[str, list[TeamEvent]] = defaultdict(list)
        self._lock = threading.RLock()

    def append(self, event: TeamEvent) -> None:
        did = getattr(event, 'dispatch_id', None)
        if not did:
            return
        with self._lock:
            self._by_id[str(did)].append(event)

    def list(self, dispatch_id: str) -> list[TeamEvent]:
        with self._lock:
            return list(self._by_id.get(dispatch_id) or [])


class FileDispatchLogStore:
    """JSONL under ``{root}/{dispatch_id}.jsonl``, with an in-memory cache."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._mem = MemoryDispatchLogStore()
        self._loaded: set[str] = set()
        self._lock = threading.RLock()

    def _path(self, dispatch_id: str) -> Path:
        safe = ''.join(c for c in dispatch_id if c.isalnum() or c in '-_')
        return self._root / f'{safe or "unknown"}.jsonl'

    def _ensure_loaded(self, dispatch_id: str) -> None:
        if dispatch_id in self._loaded:
            return
        path = self._path(dispatch_id)
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    self._mem.append(TeamEvent.from_dict(json.loads(line)))
                except Exception:  # noqa: BLE001
                    continue
        self._loaded.add(dispatch_id)

    def append(self, event: TeamEvent) -> None:
        did = getattr(event, 'dispatch_id', None)
        if not did:
            return
        with self._lock:
            self._ensure_loaded(str(did))
            self._mem.append(event)
            self._root.mkdir(parents=True, exist_ok=True)
            with open(self._path(str(did)), 'a', encoding='utf-8') as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + '\n')

    def list(self, dispatch_id: str) -> list[TeamEvent]:
        with self._lock:
            self._ensure_loaded(dispatch_id)
            return self._mem.list(dispatch_id)
