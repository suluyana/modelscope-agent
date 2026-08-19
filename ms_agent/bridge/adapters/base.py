# Copyright (c) ModelScope Contributors. All rights reserved.
"""Runtime adapter protocol for local Agent CLIs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BridgeEvent:
    type: str  # text | tool_call | tool_result | status | error | done
    content: str = ''
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeAdapter(Protocol):
    name: str

    async def discover(self) -> bool:
        """Return True if this runtime is installed locally."""
        ...

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str,
        permission_tier: str,
        cwd: str | None = None,
        attachments: list[dict] | None = None,
        session_mode: str = 'fresh',
    ) -> AsyncIterator[BridgeEvent]:
        ...

    async def cancel(self, session_id: str) -> None:
        ...
