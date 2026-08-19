# Copyright (c) ModelScope Contributors. All rights reserved.
"""Stub adapters for future runtimes (Phase 3)."""
from __future__ import annotations

from typing import AsyncIterator

from ms_agent.bridge.adapters.base import BridgeEvent

# Re-export real Codex ACP adapter for older imports.
from ms_agent.bridge.adapters.acp_codex import AcpCodexAdapter  # noqa: F401


class HermesAdapter:
    name = 'hermes'

    async def discover(self) -> bool:
        return False

    async def execute(self, **kwargs) -> AsyncIterator[BridgeEvent]:
        yield BridgeEvent(
            type='error',
            content='Hermes adapter not implemented yet (Phase 3).',
        )
        yield BridgeEvent(type='done', content='error')
        return
        yield  # pragma: no cover

    async def cancel(self, session_id: str) -> None:
        return None


class OpenClawAdapter:
    name = 'openclaw'

    async def discover(self) -> bool:
        return False

    async def execute(self, **kwargs) -> AsyncIterator[BridgeEvent]:
        yield BridgeEvent(
            type='error',
            content='OpenClaw adapter not implemented yet (Phase 3).',
        )
        yield BridgeEvent(type='done', content='error')
        return
        yield  # pragma: no cover

    async def cancel(self, session_id: str) -> None:
        return None
