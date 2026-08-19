# Copyright (c) ModelScope Contributors. All rights reserved.
"""WebSocket client for platform ↔ bridge."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class BridgeWSClient:
    """Minimal WS client with auto-reconnect (15–30s)."""

    def __init__(
        self,
        url: str,
        *,
        on_message: MessageHandler,
        reconnect_delay: float = 15.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.on_message = on_message
        self.reconnect_delay = reconnect_delay
        self.headers = headers or {}
        self._ws = None
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except Exception as exc:  # noqa: BLE001
                logger.warning('Bridge WS disconnected: %s', exc)
            if self._stop.is_set():
                break
            await asyncio.sleep(self.reconnect_delay)

    def stop(self) -> None:
        self._stop.set()

    async def send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError('WebSocket not connected')
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def _session(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                'websockets package required for agent-bridge') from exc

        extra = {}
        if self.headers:
            # websockets>=14 asyncio API: additional_headers
            # websockets legacy connect: extra_headers
            ver = getattr(websockets, '__version__', '0')
            try:
                major = int(str(ver).split('.', 1)[0])
            except ValueError:
                major = 0
            if major >= 14:
                extra['additional_headers'] = self.headers
            else:
                extra['extra_headers'] = self.headers
        async with websockets.connect(self.url, **extra) as ws:
            self._ws = ws
            logger.info('Bridge connected to %s', self.url)
            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning('Invalid WS frame: %s', raw)
                    continue
                await self.on_message(msg)
        self._ws = None
