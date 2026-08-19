# Copyright (c) ModelScope Contributors. All rights reserved.
"""Per-endpoint serial dispatch queue on the bridge side."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Deque

from ms_agent.team.models import DispatchEnvelope

logger = logging.getLogger(__name__)

DispatchWorker = Callable[[DispatchEnvelope], Awaitable[None]]


class BridgeDispatchQueue:
    """Ensure a single local agent processes one task at a time."""

    def __init__(self) -> None:
        self._q: Deque[DispatchEnvelope] = deque()
        self._running = False
        self._lock = asyncio.Lock()

    async def enqueue(self, envelope: DispatchEnvelope,
                      worker: DispatchWorker) -> None:
        async with self._lock:
            self._q.append(envelope)
            if self._running:
                return
            self._running = True
        asyncio.create_task(self._drain(worker))

    async def _drain(self, worker: DispatchWorker) -> None:
        while True:
            async with self._lock:
                if not self._q:
                    self._running = False
                    return
                env = self._q.popleft()
            try:
                await worker(env)
            except Exception:  # noqa: BLE001
                logger.exception('Bridge dispatch failed: %s', env.dispatch_id)
