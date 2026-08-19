# Copyright (c) ModelScope Contributors. All rights reserved.
"""Codex ACP adapter via ``codex-acp`` / ``@agentclientprotocol/codex-acp``.

ACP transport needs **no** API key (local stdio JSON-RPC). Team never loads
``.env`` for this path.

Auth is the Codex CLI login state on this machine:
- ChatGPT login → tokens in ``~/.codex``; Bridge inherits the launching shell.
- ``apikey`` mode → ``codex-acp`` advertises auth method ``api-key`` and reads
  ``CODEX_API_KEY`` / ``OPENAI_API_KEY`` from the *child* env (it does not read
  ``auth.json``). We only copy those names from ``auth.json`` into the child
  for that handshake — we do **not** invent provider keys or read Team ``.env``.

If ``~/.codex/config.toml`` sets ``model_provider`` with an ``env_key`` (e.g.
DashScope → ``DASHSCOPE_API_KEY``), Bridge first uses that variable from the
launching shell; if unset, it reuses the CLI api-key already materialized from
``auth.json`` / ``OPENAI_API_KEY``. It still does **not** load Team ``.env``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from ms_agent.bridge.adapters.acp_client import AuthRequired
from ms_agent.bridge.adapters.acp_runtime import (
    cancel_acp_session,
    list_acp_sessions,
    run_acp_turn,
)
from ms_agent.bridge.adapters.base import BridgeEvent
from ms_agent.bridge.session_label import (
    build_session_label,
    extract_preview,
    parse_updated_at,
    suggest_at_name,
)

logger = logging.getLogger(__name__)


def _codex_home() -> Path:
    override = os.environ.get('CODEX_HOME', '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.codex'


def _codex_bin() -> str:
    env = os.environ.get('CODEX_PATH') or os.environ.get('MS_AGENT_CODEX')
    if env:
        return env
    found = shutil.which('codex')
    if found:
        return found
    nvm = Path.home() / '.nvm' / 'versions' / 'node'
    if nvm.is_dir():
        matches = sorted(nvm.glob('*/bin/codex'), reverse=True)
        if matches:
            return str(matches[0])
    return 'codex'


def resolve_codex_acp_command() -> list[str] | None:
    """Return argv for the Codex ACP stdio server, or None if unavailable."""
    override = os.environ.get('MS_AGENT_CODEX_ACP', '').strip()
    if override:
        return override.split()
    if shutil.which('codex-acp'):
        return ['codex-acp']
    npx = shutil.which('npx')
    if npx:
        return ['npx', '-y', '@agentclientprotocol/codex-acp']
    return None


def _read_codex_auth_json() -> dict[str, Any]:
    path = _codex_home() / 'auth.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        logger.debug('failed to read %s', path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _provider_env_keys_from_config() -> list[str]:
    """Parse ``env_key = "FOO"`` entries from Codex config.toml."""
    path = _codex_home() / 'config.toml'
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:  # noqa: BLE001
        return []
    keys: list[str] = []
    for match in re.finditer(
            r'^\s*env_key\s*=\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']',
            text,
            re.M,
    ):
        keys.append(match.group(1))
    return keys


def materialize_codex_cli_login_env(
    env: dict[str, str],
    *,
    cwd: str | None = None,
) -> dict[str, str]:
    """Forward CLI login into the ACP child — never load Team ``.env``.

    - Inherit the Bridge process environment as-is (same shell as connect).
    - If ACP ``api-key`` handshake vars are missing, copy from
      ``~/.codex/auth.json`` **only** into ``CODEX_API_KEY`` / ``OPENAI_API_KEY``.
    - If ``config.toml`` declares a provider ``env_key`` (e.g. DashScope →
      ``DASHSCOPE_API_KEY``) that is still unset, reuse the CLI api-key already
      in the child env / ``auth.json``. Users often store the DashScope key in
      ``auth.json`` as ``OPENAI_API_KEY``; we still never read Team ``.env``.
    """
    del cwd  # reserved for callers; we do not scan checkout .env
    out = dict(env)
    auth = _read_codex_auth_json()

    if not (out.get('CODEX_API_KEY') or out.get('OPENAI_API_KEY')):
        for name in ('CODEX_API_KEY', 'OPENAI_API_KEY'):
            val = auth.get(name)
            if isinstance(val, str) and val.strip():
                out.setdefault('CODEX_API_KEY', val.strip())
                out.setdefault('OPENAI_API_KEY', val.strip())
                break

    cli_key = (
        (out.get('CODEX_API_KEY') or '').strip()
        or (out.get('OPENAI_API_KEY') or '').strip()
    )
    for provider_key in _provider_env_keys_from_config():
        if out.get(provider_key):
            continue
        auth_val = auth.get(provider_key)
        if isinstance(auth_val, str) and auth_val.strip():
            out[provider_key] = auth_val.strip()
            continue
        if cli_key:
            # Same credential Codex CLI already uses for apikey mode.
            out[provider_key] = cli_key
            continue
        logger.warning(
            'Codex config expects %s in the Host Bridge process env '
            '(same as your interactive Codex TUI shell). ACP itself needs '
            'no API key; this is the model provider declared in '
            '~/.codex/config.toml. Export %s before starting the Bridge — '
            'Team does not load .env.',
            provider_key,
            provider_key,
        )
    return out


# Back-compat alias.
inject_codex_cli_credentials = materialize_codex_cli_login_env


class AcpCodexAdapter:
    """Drive Codex through a long-lived ACP agent (codex-acp)."""

    name = 'codex'

    def __init__(
        self,
        *,
        dry_run: bool = False,
        auto_allow_permissions: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self.auto_allow_permissions = auto_allow_permissions

    def _command(self) -> list[str] | None:
        return resolve_codex_acp_command()

    def _env(self, cwd: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault('CODEX_PATH', _codex_bin())
        env.setdefault('NO_BROWSER', '1')
        return materialize_codex_cli_login_env(env, cwd=cwd)

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
                env=self._env(cwd),
            )
        except AuthRequired:
            return []
        out: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            sid = (
                row.get('sessionId') or row.get('id')
                or row.get('runtime_session_id') or '')
            if not sid:
                continue
            title = (
                row.get('title') or row.get('name') or row.get('cwd')
                or str(sid)[:8])
            sess_cwd = row.get('cwd') if isinstance(row.get('cwd'), str) else None
            updated = parse_updated_at(row.get('updatedAt') or row.get('updated_at'))
            preview = extract_preview(str(title))
            suggested = suggest_at_name(str(title), str(sid), runtime=self.name)
            out.append({
                'runtime_session_id': sid,
                'label': build_session_label(
                    runtime=self.name,
                    suggested=suggested,
                    preview=preview,
                    cwd=sess_cwd,
                    updated_at=updated,
                ),
                'title': title,
                'preview': preview,
                'cwd': sess_cwd,
                'updated_at': updated,
                'suggested_at_name': suggested,
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
                    f'[codex-acp dry-run] mode={session_mode} '
                    f'session={session_id} prompt_chars={len(prompt)}'),
            )
            yield BridgeEvent(type='done', content='ok')
            return
        cmd = self._command()
        assert cmd is not None
        async for ev in run_acp_turn(
                runtime=self.name,
                command=cmd,
                prompt=prompt,
                session_id=session_id,
                cwd=cwd,
                session_mode=session_mode,
                env=self._env(cwd),
                auto_allow=self.auto_allow_permissions,
        ):
            yield ev

    async def cancel(self, session_id: str) -> None:
        await cancel_acp_session(runtime=self.name, session_id=session_id)


def _suggest_name(title: str, sid: str) -> str:
    """Back-compat wrapper for tests / callers."""
    return suggest_at_name(title, sid, runtime='codex')
