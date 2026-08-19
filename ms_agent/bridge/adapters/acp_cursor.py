# Copyright (c) ModelScope Contributors. All rights reserved.
"""Cursor CLI ACP adapter (`agent acp`).

Cursor **IDE chat** is not ACP-attachable. This adapter drives the separate
CLI ACP server and reuses a long-lived process from ``AcpProcessPool``.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import AsyncIterator

from ms_agent.bridge.adapters.acp_client import AuthRequired
from ms_agent.bridge.adapters.acp_runtime import (
    cancel_acp_session,
    list_acp_sessions,
    run_acp_turn,
)
from ms_agent.bridge.adapters.base import BridgeEvent

logger = logging.getLogger(__name__)


def _default_agent_bin() -> str:
    env = os.environ.get('MS_AGENT_CURSOR_AGENT') or os.environ.get(
        'CURSOR_AGENT_BIN')
    if env:
        return env
    home = Path.home()
    for cand in (
            home / '.local' / 'bin' / 'agent',
            home / '.local' / 'bin' / 'cursor-agent',
            Path('/usr/local/bin/agent'),
    ):
        if cand.is_file():
            return str(cand)
    return 'agent'


class AcpCursorAdapter:
    """Drive Cursor CLI via pooled ACP stdio."""

    name = 'cursor'

    def __init__(
        self,
        *,
        agent_command: str | None = None,
        dry_run: bool = False,
        auto_allow_permissions: bool = True,
    ) -> None:
        self.agent_command = agent_command or _default_agent_bin()
        self.dry_run = dry_run
        self.auto_allow_permissions = auto_allow_permissions

    def _command(self) -> list[str]:
        return [self.agent_command, 'acp']

    async def discover(self) -> bool:
        if self.dry_run:
            return True
        return bool(
            shutil.which(self.agent_command)
            or Path(self.agent_command).is_file())

    async def list_sessions(self, *, cwd: str | None = None) -> list[dict]:
        if self.dry_run or not await self.discover():
            return []
        try:
            raw = await list_acp_sessions(
                runtime=self.name,
                command=self._command(),
                cwd=cwd,
                auth_method_id='cursor_login',
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
                'label': f'cursor · {title}',
                'title': title,
                'suggested_at_name': f'cursor_{str(sid)[:4]}',
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
                    f'[cursor dry-run] mode={session_mode} '
                    f'session={session_id} prompt_chars={len(prompt)}'),
            )
            yield BridgeEvent(type='done', content='ok')
            return
        async for ev in run_acp_turn(
                runtime=self.name,
                command=self._command(),
                prompt=prompt,
                session_id=session_id,
                cwd=cwd,
                session_mode=session_mode,
                auth_method_id='cursor_login',
                auto_allow=self.auto_allow_permissions,
        ):
            yield ev

    async def cancel(self, session_id: str) -> None:
        await cancel_acp_session(runtime=self.name, session_id=session_id)
