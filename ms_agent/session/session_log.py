"""SessionLog — append-only JSONL session log.

The source of truth for message history.  Every message is appended with
a monotonic ``seq`` number; nothing is ever overwritten or deleted.
Compaction events are recorded as special markers so that the full timeline
(including *when* and *why* context was compressed) is preserved.

``last_consolidated`` stores a **seq** value (not an array index).  The
visible window consists of all messages whose ``seq >= last_consolidated``.
Because compaction_events are filtered out of ``get_all_messages()``, using
seq avoids the fragile mapping between array positions and JSONL line
numbers.

**Mutable metadata lives in a sidecar file**, not in the main log.  Values
that change during a session (``last_consolidated``, ``round``, ``status``,
``title``) are stored in ``{session_key}.meta.json`` and updated with a small
atomic write.  This keeps the main ``.jsonl`` strictly append-only: it is
never rewritten, so a crash can never corrupt the message history.  The main
log still carries an immutable header (``session_key`` / ``created_at``) on
its first line so it remains self-describing.

JSONL format::

    {"_type": "metadata", "session_key": "abc", "created_at": "..."}
    {"role": "system", "content": "...", "seq": 0, "timestamp": "..."}
    {"role": "user",   "content": "...", "seq": 1, "timestamp": "...", "tokens": 42}
    {"_type": "compaction_event", "seq": 4, "strategy": "summary_compactor", ...}

Sidecar (``{session_key}.meta.json``)::

    {"session_key": "abc", "created_at": "...", "last_consolidated": 0,
     "round": 0, "title": "", "status": "idle"}
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionLog:
    """Append-only JSONL session log — the source of truth for message history."""

    def __init__(
        self,
        session_dir: str | Path,
        session_key: str | None = None,
    ) -> None:
        self._dir = Path(session_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.session_key = session_key or f'session_{uuid.uuid4().hex[:8]}'
        self._path = self._dir / f'{self.session_key}.jsonl'
        self._meta_path = self._dir / f'{self.session_key}.meta.json'

        self._metadata: Optional[Dict[str, Any]] = None
        self._messages: Optional[List[Dict[str, Any]]] = None
        self._seq: int = 0

        self._ensure_metadata()

    # ------------------------------------------------------------------
    # Write path (append-only)
    # ------------------------------------------------------------------

    def append(self, message: Dict[str, Any]) -> int:
        """Append a message record.  Returns its ``seq`` number.

        The write is crash-safe: each line is flushed individually.
        """
        seq = self._next_seq()
        record: Dict[str, Any] = {**message, 'seq': seq}
        if 'timestamp' not in record:
            record['timestamp'] = datetime.now(timezone.utc).isoformat()
        self._append_line(record)
        if self._messages is not None:
            self._messages.append(record)
        return seq

    def append_messages(self, messages: List[Dict[str, Any]]) -> List[int]:
        """Append multiple messages.  Returns list of seq numbers."""
        return [self.append(m) for m in messages]

    def record_compaction(self, event: Dict[str, Any]) -> None:
        """Record a compaction event (non-destructive marker)."""
        seq = self._next_seq()
        record = {
            '_type': 'compaction_event',
            'seq': seq,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_error(self, event: Dict[str, Any]) -> None:
        """Record an error event — a non-message, display-only marker.

        Like ``record_compaction``, this appends a ``_type``-tagged record that
        ``get_all_messages`` filters out, so the error is preserved for history
        replay but never re-enters the LLM context on resume.  Use it for
        turn-level / API errors that must NOT go back to the model (tool-call
        errors stay as ordinary ``role="tool"`` messages instead).  ``event``
        typically carries ``message``, ``error_type``, ``recoverable``, ``round``.
        """
        seq = self._next_seq()
        record = {
            "_type": "error",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_loop_end(self, event: Dict[str, Any]) -> None:
        """Record a tool-call-loop boundary — a non-message, display-only marker.

        Written when a turn's tool-call loop finishes (the final assistant
        message with no further tool calls). Like the other ``_type``-tagged
        markers it is filtered out of ``get_all_messages`` (never re-enters the
        LLM context), but lets history replay reproduce the live "loop done"
        summary — most importantly the wall-clock ``duration_ms``, which is not
        otherwise derivable from the log. ``event`` typically carries
        ``duration_ms`` and ``changed_files``.
        """
        seq = self._next_seq()
        record = {
            "_type": "loop_end",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_permission(self, event: Dict[str, Any]) -> None:
        """Record a permission decision — a non-message, display-only marker.

        Like ``record_error``, this appends a ``_type``-tagged record that
        ``get_all_messages`` filters out, so a restricted-mode authorization
        (asked and resolved to approve/deny) is preserved for history replay
        but never re-enters the LLM context on resume.  ``event`` typically
        carries ``tool_name``, ``arguments``, ``state`` (approved|rejected).
        """
        seq = self._next_seq()
        record = {
            "_type": "permission",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_skill_invocation(self, event: Dict[str, Any]) -> None:
        """Record a slash-skill invocation — a display-only marker.

        A consumer that expands ``/skill`` into the full skill prompt persists
        the ENRICHED text as the user row (that is what the model must see on
        resume). This marker preserves what the user actually typed so history
        replay can show the original message instead of the expanded wrapper.
        ``event`` typically carries ``original_text`` and ``skill_ids``.
        """
        seq = self._next_seq()
        record = {
            "_type": "skill_invocation",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_team_reply(self, event: Dict[str, Any]) -> None:
        """Record an Agent Team (@mention) reply — display-only.

        Written when a Host Bridge / ACP agent finishes a chat @dispatch.
        Filtered out of ``get_all_messages`` so it never re-enters the lead
        LLM context; history replay merges it back by ``seq`` as its own
        assistant bubble (``at_name`` + ``content``).
        """
        seq = self._next_seq()
        record = {
            "_type": "team_reply",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_team_receipt(self, event: Dict[str, Any]) -> None:
        """Idle-style Team receipt (已派 / 已结束执行) — display-only.

        Same contract as ``record_team_reply``: never re-enters the lead LLM
        context. History replay shows a compact receipt bar, not a teammate
        bubble (C-04).
        """
        seq = self._next_seq()
        record = {
            "_type": "team_receipt",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    def record_team_user(self, event: Dict[str, Any]) -> None:
        """Human @mention that was routed to a teammate — display-only.

        The main-chat timeline still shows the user's ``@bibo …`` bubble.
        Filtered out of ``get_all_messages`` so Lead does not treat a
        teammate's assignment as its own user turn (C-06).
        """
        seq = self._next_seq()
        record = {
            "_type": "team_user",
            "role": "user",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        self._append_line(record)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    @property
    def last_consolidated(self) -> int:
        return self._read_meta().get('last_consolidated', 0)

    @last_consolidated.setter
    def last_consolidated(self, value: int) -> None:
        self._update_meta('last_consolidated', value)

    @property
    def round(self) -> int:
        """The last persisted agent-loop round (for resume)."""
        return self._read_meta().get('round', 0)

    @round.setter
    def round(self, value: int) -> None:
        self._update_meta('round', value)

    def get_all_messages(self) -> List[Dict[str, Any]]:
        """LLM-visible messages (no metadata, receipts, or teammate @prompts)."""
        if self._messages is not None:
            return self._messages
        msgs: List[Dict[str, Any]] = []
        records = self._read_jsonl_records()
        skip_user = self._legacy_team_user_seqs(records)
        for record in records:
            if record.get("_type") in (
                    "metadata", "compaction_event", "error", "permission",
                    "skill_invocation", "loop_end", "team_reply",
                    "team_receipt", "team_user"):
                continue
            if record.get("seq") in skip_user:
                continue
            msgs.append(record)
        self._messages = msgs
        return msgs

    def get_visible_messages(self) -> List[Dict[str, Any]]:
        """Messages whose ``seq >= last_consolidated`` (the LLM window).

        Because ``last_consolidated`` is a seq value, this correctly skips
        compaction_events (which are filtered by ``get_all_messages``) without
        relying on fragile array-index arithmetic.
        """
        all_msgs = self.get_all_messages()
        lc_seq = self.last_consolidated
        for i, m in enumerate(all_msgs):
            if m.get('seq', 0) >= lc_seq:
                return all_msgs[i:]
        return []

    def get_compaction_events(self) -> List[Dict[str, Any]]:
        """All compaction events in chronological order."""
        events: List[Dict[str, Any]] = []
        if not self._path.exists():
            return events
        for line in self._path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get('_type') == 'compaction_event':
                events.append(record)
        return events

    def get_errors(self) -> List[Dict[str, Any]]:
        """All error records in chronological order (each keeps its ``seq``).

        These are excluded from ``get_all_messages`` (and thus the LLM context);
        a UI can merge them back by ``seq`` to replay *when* errors occurred.
        """
        errors: List[Dict[str, Any]] = []
        if not self._path.exists():
            return errors
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("_type") == "error":
                errors.append(record)
        return errors

    def get_permissions(self) -> List[Dict[str, Any]]:
        """All permission-decision records in chronological order (each keeps
        its ``seq``).  Excluded from ``get_all_messages`` (and the LLM context);
        a UI merges them back by ``seq`` to replay authorization cards.
        """
        perms: List[Dict[str, Any]] = []
        if not self._path.exists():
            return perms
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("_type") == "permission":
                perms.append(record)
        return perms

    def get_loop_ends(self) -> List[Dict[str, Any]]:
        """All tool-call-loop boundary markers in chronological order (each
        keeps its ``seq``).  Excluded from ``get_all_messages`` (and the LLM
        context); a UI merges them back by ``seq`` to reproduce the per-loop
        "done" summary (duration + changed files) on history replay.
        """
        out: List[Dict[str, Any]] = []
        if not self._path.exists():
            return out
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("_type") == "loop_end":
                out.append(record)
        return out

    def get_skill_invocations(self) -> List[Dict[str, Any]]:
        """All slash-skill invocation markers in chronological order (each
        keeps its ``seq``).  Excluded from ``get_all_messages`` (and the LLM
        context); a UI matches each to the user row that follows it by ``seq``
        to display the original typed text instead of the expanded prompt.
        """
        out: List[Dict[str, Any]] = []
        if not self._path.exists():
            return out
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("_type") == "skill_invocation":
                out.append(record)
        return out

    def get_team_replies(self) -> List[Dict[str, Any]]:
        """All Agent Team reply markers in chronological order (each keeps
        its ``seq``).  Excluded from ``get_all_messages``; a UI merges them
        back by ``seq`` as ``@at_name`` assistant bubbles.
        """
        out: List[Dict[str, Any]] = []
        if not self._path.exists():
            return out
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("_type") == "team_reply":
                out.append(record)
        return out

    def get_team_receipts(self) -> List[Dict[str, Any]]:
        """Idle Team receipts (已派 / 已结束执行) in chronological order.

        Excluded from ``get_all_messages``; a UI merges them back by ``seq``
        as compact receipt bars (C-04).
        """
        out: List[Dict[str, Any]] = []
        for record in self._read_jsonl_records():
            if record.get("_type") == "team_receipt":
                out.append(record)
        return out

    def get_team_users(self) -> List[Dict[str, Any]]:
        """Human @mention rows routed to teammates (main-chat timeline only).

        Tagged ``team_user`` records plus legacy untagged user rows that sit
        immediately before a ``dispatch_start`` receipt.
        """
        records = self._read_jsonl_records()
        legacy = self._legacy_team_user_seqs(records)
        out: List[Dict[str, Any]] = []
        for record in records:
            if record.get("_type") == "team_user":
                out.append(record)
            elif record.get("seq") in legacy:
                out.append({**record, "_type": "team_user"})
        return out

    def get_metadata(self) -> Dict[str, Any]:
        """Session metadata (title, created_at, status, counts, etc.)."""
        meta = self._read_meta()
        all_msgs = self.get_all_messages()
        return {
            'session_key': self.session_key,
            'created_at': meta.get('created_at', ''),
            'title': meta.get('title', ''),
            'status': meta.get('status', 'idle'),
            'last_consolidated': meta.get('last_consolidated', 0),
            'round': meta.get('round', 0),
            'message_count': len(all_msgs),
            'total_tokens': sum(m.get('tokens', 0) for m in all_msgs),
        }

    def set_metadata_field(self, key: str, value: Any) -> None:
        """Update a single metadata field (e.g. title, status)."""
        self._update_meta(key, value)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self) -> None:
        """Force re-read from disk on next access."""
        self._metadata = None
        self._messages = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        # Re-scan disk so a second SessionLog (Team persist vs LLMAgent)
        # cannot reuse seq numbers already written by the other writer.
        self._load_all_to_set_seq()
        seq = self._seq
        self._seq += 1
        return seq

    def _default_meta(self, created_at: str) -> Dict[str, Any]:
        return {
            'session_key': self.session_key,
            'created_at': created_at,
            'last_consolidated': 0,
            'round': 0,
            'title': '',
            'status': 'idle',
        }

    def _ensure_metadata(self) -> None:
        """Create the immutable log header and the mutable sidecar if missing."""
        if not self._path.exists():
            created_at = datetime.now(timezone.utc).isoformat()
            header = {
                '_type': 'metadata',
                'session_key': self.session_key,
                'created_at': created_at,
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, 'w', encoding='utf-8') as f:
                f.write(json.dumps(header, ensure_ascii=False) + '\n')
            self._write_meta(self._default_meta(created_at))
        else:
            # Scan existing file to set seq counter
            self._load_all_to_set_seq()
            # Make sure a sidecar exists (migrates legacy header-based metadata)
            self._read_meta()

    def _load_all_to_set_seq(self) -> None:
        """Scan the file to find the highest seq number."""
        if not self._path.exists():
            return
        max_seq = -1
        for line in self._path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                s = record.get('seq', -1)
                if s > max_seq:
                    max_seq = s
            except json.JSONDecodeError:
                continue
        self._seq = max_seq + 1

    def _read_jsonl_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if not self._path.exists():
            return records
        for line in self._path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    @staticmethod
    def _legacy_team_user_seqs(records: List[Dict[str, Any]]) -> set:
        """Untagged user rows immediately followed by a 已派 receipt.

        Older builds persisted ``@bibo …`` via ``append`` (LLM-visible). Pair
        them with the following ``dispatch_start`` so Lead no longer inherits
        the teammate assignment.
        """
        by_seq = {
            r['seq']: r
            for r in records if isinstance(r.get('seq'), int)
        }
        skip: set = set()
        for record in records:
            if (record.get('_type') != 'team_receipt'
                    or record.get('kind') != 'dispatch_start'):
                continue
            prev = by_seq.get(int(record.get('seq', 0)) - 1)
            if not prev or prev.get('role') != 'user':
                continue
            if prev.get('_type') in (
                    'team_user', 'team_receipt', 'team_reply', 'error',
                    'permission', 'skill_invocation', 'loop_end'):
                continue
            skip.add(prev.get('seq'))
        return skip

    def _read_meta(self) -> Dict[str, Any]:
        """Load mutable metadata from the sidecar (cached).

        Falls back to migrating a legacy in-log metadata header the first time
        a pre-sidecar session is opened.
        """
        if self._metadata is not None:
            return self._metadata

        # 1. Preferred: the sidecar file.
        if self._meta_path.exists():
            try:
                self._metadata = json.loads(
                    self._meta_path.read_text(encoding='utf-8'))
                return self._metadata
            except (json.JSONDecodeError, OSError):
                pass

        # 2. Migration: read a legacy header from the main log, persist sidecar.
        legacy = self._read_legacy_header()
        if legacy is not None:
            meta = self._default_meta(legacy.get('created_at', ''))
            for key in ('last_consolidated', 'round', 'title', 'status'):
                if key in legacy:
                    meta[key] = legacy[key]
            self._write_meta(meta)
            return self._metadata  # set by _write_meta

        # 3. Brand new / unreadable: defaults.
        self._metadata = self._default_meta('')
        return self._metadata

    def _update_meta(self, key: str, value: Any) -> None:
        meta = dict(self._read_meta())
        meta[key] = value
        self._write_meta(meta)

    def _write_meta(self, meta: Dict[str, Any]) -> None:
        """Atomically persist the sidecar (write temp + os.replace)."""
        tmp = self._meta_path.parent / (self._meta_path.name + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._meta_path)
        self._metadata = meta

    def _read_legacy_header(self) -> Optional[Dict[str, Any]]:
        """Return the first-line metadata record of the main log, if any."""
        if not self._path.exists():
            return None
        with open(self._path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
        if first_line:
            try:
                record = json.loads(first_line)
                if record.get('_type') == 'metadata':
                    return record
            except json.JSONDecodeError:
                pass
        return None

    def _append_line(self, record: Dict[str, Any]) -> None:
        """Append a single JSON line and flush."""
        with open(self._path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
