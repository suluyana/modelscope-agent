# Copyright (c) ModelScope Contributors. All rights reserved.
"""Token expiry helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None


def is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    dt = _parse_iso(expires_at)
    if dt is None:
        return True
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return now > dt
