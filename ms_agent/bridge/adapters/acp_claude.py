# Copyright (c) ModelScope Contributors. All rights reserved.
"""Claude Code ACP adapter (true ACP via claude-agent-acp / ``claude acp``).

Print-mode ``claude -p`` is **not** attachable and is not used for Team dispatch.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import AsyncIterator

from ms_agent.bridge.adapters.acp_client import AuthRequired
from ms_agent.bridge.adapters.acp_runtime import (
    cancel_acp_session,
    list_acp_sessions,
    run_acp_turn,
)
from ms_agent.bridge.adapters.base import BridgeEvent

logger = logging.getLogger(__name__)


def resolve_claude_acp_command() -> list[str] | None:
    acp = os.environ.get('MS_AGENT_ACP_CLAUDE', 'claude-agent-acp')
    if shutil.which(acp):
        return [acp]
    # Only advertise Claude when an explicit ACP binary exists. A bare
    # `claude` CLI often lacks a working `acp` subcommand and blocks discover.
    return None


class AcpClaudeAdapter:
    """Drive Claude through a long-lived ACP agent process."""

    name = 'claude_code'

    def __init__(
        self,
        *,
        dry_run: bool = False,
        auto_allow_permissions: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self.auto_allow_permissions = auto_allow_permissions

    def _command(self) -> list[str] | None:
        return resolve_claude_acp_command()

    async def discover(self) -> bool:
        if self.dry_run:
            return True
        return self._command() is not None

    async def list_sessions(self, *, cwd: str | None = None) -> list[dict]:
        if self.dry_run or not await self.discover():
            return []
        cmd = self._command()
        assert cmd is not None
        try:
            raw = await list_acp_sessions(
                runtime=self.name,
                command=cmd,
                cwd=cwd,
            )
        except AuthRequired:
            return []
        out = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            sid = row.get('sessionId') or row.get('id') or ''
            if not sid:
                continue
            title = row.get('title') or row.get('cwd') or str(sid)[:8]
            out.append({
                'runtime_session_id': sid,
                'label': f'claude · {title}',
                'title': title,
                'suggested_at_name': f'claude_{str(sid)[:4]}',
                'raw': row,
            })
        return out

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
        del permission_tier, attachments
        if self.dry_run or not await self.discover():
            yield BridgeEvent(type='status', content='dry_run')
            yield BridgeEvent(
                type='text',
                content=(
                    f'[claude-acp dry-run] mode={session_mode} '
                    f'session={session_id} prompt_chars={len(prompt)}'),
            )
            yield BridgeEvent(type='done', content='ok')
            return
        cmd = self._command()
        if cmd is None:
            yield BridgeEvent(
                type='error',
                content=(
                    'Claude ACP server not found. Install claude-agent-acp '
                    'or a Claude CLI with `acp` subcommand.'),
                payload={'code': 'endpoint_offline'},
            )
            yield BridgeEvent(type='done', content='error')
            return
        async for ev in run_acp_turn(
                runtime=self.name,
                command=cmd,
                prompt=prompt,
                session_id=session_id,
                cwd=cwd,
                session_mode=session_mode,
                auto_allow=self.auto_allow_permissions,
        ):
            yield ev

    async def cancel(self, session_id: str) -> None:
        await cancel_acp_session(runtime=self.name, session_id=session_id)
