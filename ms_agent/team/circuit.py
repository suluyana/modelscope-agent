# Copyright (c) ModelScope Contributors. All rights reserved.
"""Dispatch circuit breaker — prevent fingerprint thrash loops."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def fingerprint(project_id: str, endpoint_id: str, prompt: str) -> str:
    normalized = ' '.join((prompt or '').split()).strip().lower()
    raw = f'{project_id}|{endpoint_id}|{normalized}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


@dataclass
class _Bucket:
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None
    half_open_probe: bool = False


class CircuitBreaker:
    """Windowed failure counter with open / half-open / closed states."""

    def __init__(
        self,
        *,
        failure_threshold: int | None = None,
        window_s: float | None = None,
        cooldown_s: float | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold if failure_threshold is not None \
            else _env_int('MS_AGENT_CIRCUIT_N', 3)
        self.window_s = window_s if window_s is not None else float(
            _env_int('MS_AGENT_CIRCUIT_WINDOW_S', 600))
        self.cooldown_s = cooldown_s if cooldown_s is not None else self.window_s
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.RLock()

    def allow(self, fp: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(fp)
            if bucket is None or bucket.opened_at is None:
                return True
            elapsed = time.monotonic() - bucket.opened_at
            if elapsed < self.cooldown_s:
                return False
            # Half-open: allow a single probe.
            if not bucket.half_open_probe:
                bucket.half_open_probe = True
                return True
            return False

    def record_success(self, fp: str) -> None:
        with self._lock:
            self._buckets.pop(fp, None)

    def record_failure(self, fp: str) -> bool:
        """Record failure. Returns True if circuit just opened."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(fp, _Bucket())
            bucket.failures = [
                t for t in bucket.failures if now - t <= self.window_s
            ]
            bucket.failures.append(now)
            if bucket.opened_at is not None:
                # Probe failed — stay open.
                bucket.opened_at = now
                bucket.half_open_probe = False
                return False
            if len(bucket.failures) >= self.failure_threshold:
                bucket.opened_at = now
                bucket.half_open_probe = False
                return True
            return False

    def reset(self, fp: str | None = None) -> None:
        with self._lock:
            if fp is None:
                self._buckets.clear()
            else:
                self._buckets.pop(fp, None)

    def is_open(self, fp: str) -> bool:
        return not self.allow(fp)
