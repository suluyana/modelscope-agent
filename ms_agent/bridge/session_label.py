# Copyright (c) ModelScope Contributors. All rights reserved.
"""Human-readable labels for ACP session candidates."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SMOKE_TITLE_RE = re.compile(
    r'(?i)^\s*(reply with exactly|pong)\b|single word\s+pong')
_NAME_HINT_RE = re.compile(
    r'(?:你叫|叫我|我叫|call me)\s*[@]?([A-Za-z][\w\-]{0,31}|'
    r'[\u4e00-\u9fff]{1,16})',
    re.I,
)
_AT_NAME_RE = re.compile(r'@([A-Za-z][\w\-]{0,31})')
_USER_LINE_RE = re.compile(
    r'\[([^\]]+)\]\s+(?:u1|user)\s*:\s*(.+)',
    re.I,
)
_GENERIC_SLUGS = frozenset({
    'codex',
    'session',
    'reply_with_exact',
    'context_assemble',
    '你好',
})


def parse_updated_at(value: Any) -> float | None:
    """Return unix seconds from ACP ``updatedAt`` (ISO or epoch)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if num > 1e12:  # ms
            return num / 1000.0
        return num
    if isinstance(value, str) and value.strip():
        text = value.strip().replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    return None


def format_relative(ts: float | None, *, now: float | None = None) -> str:
    if ts is None:
        return ''
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    delta = max(0, int(now - ts))
    if delta < 60:
        return 'just now'
    if delta < 3600:
        return f'{delta // 60}m ago'
    if delta < 86400:
        return f'{delta // 3600}h ago'
    if delta < 86400 * 14:
        return f'{delta // 86400}d ago'
    return datetime.fromtimestamp(ts).strftime('%m-%d')


def cwd_basename(cwd: str | None) -> str:
    if not cwd:
        return ''
    try:
        return Path(cwd).expanduser().name or cwd
    except Exception:  # noqa: BLE001
        return str(cwd)[-24:]


def is_smoke_session(title: str) -> bool:
    head = ' '.join(str(title or '').split())[:120]
    return bool(_SMOKE_TITLE_RE.search(head))


def extract_preview(title: str, *, max_len: int = 56) -> str:
    """Collapse Codex titles (often full prompts / Team context dumps)."""
    raw = str(title or '').strip()
    if not raw:
        return ''
    if raw.lstrip().startswith('# Context'):
        # Prefer last user utterance inside the dump.
        user_lines = _USER_LINE_RE.findall(raw)
        if user_lines:
            raw = user_lines[-1][1].strip()
        else:
            # Fall back to first non-header line.
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('['):
                    continue
                if line.startswith('system:') or 'session_mode=' in line:
                    continue
                raw = line
                break
    preview = ' '.join(raw.split())
    if len(preview) > max_len:
        preview = preview[: max_len - 1] + '…'
    return preview


def suggest_at_name(title: str, sid: str, *, runtime: str = 'codex') -> str:
    text = str(title or '')

    # Team context dumps: prefer the latest user @mention ("@lily …").
    if text.lstrip().startswith('# Context'):
        user_lines = _USER_LINE_RE.findall(text)
        for _ts, body in reversed(user_lines):
            ats = _AT_NAME_RE.findall(body)
            for name in ats:
                low = name.lower()
                if low not in ('codex', 'me', 'ms_agent', 'claude', 'cursor'):
                    return low

    m = _NAME_HINT_RE.search(text)
    if m:
        name = m.group(1)
        return name.lower() if name.isascii() else name[:32]

    # Any @mention in title (intro prompts, short titles).
    ats = _AT_NAME_RE.findall(text)
    for name in ats:
        low = name.lower()
        if low not in ('codex', 'me', 'ms_agent', 'claude', 'cursor'):
            return low

    # Agent reply prefix in dumps: "lily: Warning…"
    reply = re.search(
        r'(?m)^(?:\[[^\]]+\]\s+)?([A-Za-z][\w\-]{0,31})\s*:\s+\S',
        text,
    )
    if reply:
        low = reply.group(1).lower()
        if low not in ('system', 'user', 'u1', 'codex', 'assistant'):
            return low

    preview = extract_preview(text, max_len=40)
    slug = re.sub(r'[^\w\u4e00-\u9fff]+', '_', preview).strip('_')[:16]
    if slug and slug.lower() not in _GENERIC_SLUGS and not slug.startswith(
            'Reply'):
        return slug
    return f'{runtime}_{str(sid)[:4]}'


def is_named_suggestion(name: str, *, runtime: str = 'codex') -> bool:
    low = (name or '').lower()
    if not low:
        return False
    if low.startswith(f'{runtime}_'):
        return False
    if low in _GENERIC_SLUGS:
        return False
    if low.startswith('reply'):
        return False
    return True


def build_session_label(
    *,
    runtime: str,
    suggested: str,
    preview: str,
    cwd: str | None,
    updated_at: float | None,
) -> str:
    parts = [runtime]
    if is_named_suggestion(suggested, runtime=runtime):
        parts.append(f'@{suggested}')
    elif preview:
        parts.append(preview)
    folder = cwd_basename(cwd)
    if folder:
        parts.append(folder)
    rel = format_relative(updated_at)
    if rel:
        parts.append(rel)
    return ' · '.join(parts)
