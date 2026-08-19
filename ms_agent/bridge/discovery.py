# Copyright (c) ModelScope Contributors. All rights reserved.
"""Discover installed local ACP agent runtimes and listable sessions."""
from __future__ import annotations

import shutil
from typing import Any

from ms_agent.bridge.adapters.acp_claude import AcpClaudeAdapter
from ms_agent.bridge.adapters.acp_codex import AcpCodexAdapter
from ms_agent.bridge.adapters.acp_cursor import AcpCursorAdapter
from ms_agent.bridge.adapters.stubs import HermesAdapter, OpenClawAdapter
from ms_agent.bridge.session_label import (
    build_session_label,
    extract_preview,
    is_named_suggestion,
    is_smoke_session,
)


def _cursor_ide_running() -> bool:
    """True if Cursor desktop appears to be running (not ACP-attachable)."""
    try:
        import subprocess
        for pattern in (
                'Cursor.app/Contents/MacOS/Cursor',
                'Cursor Helper \\(Renderer\\)',
                'Cursor Helper (Renderer)',
        ):
            try:
                out = subprocess.check_output(
                    ['pgrep', '-f', pattern],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return True
            except subprocess.CalledProcessError:
                continue
        return False
    except Exception:  # noqa: BLE001
        return False


def _select_session_candidates(
    sessions: list[dict[str, Any]],
    *,
    runtime: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Filter smoke/noise, prefer named sessions, dedupe by @name."""
    usable: list[dict[str, Any]] = []
    for sess in sessions or []:
        title = str(sess.get('title') or sess.get('label') or '')
        if is_smoke_session(title):
            continue
        preview = str(sess.get('preview') or extract_preview(title))
        if is_smoke_session(preview):
            continue
        usable.append(sess)

    def _rank_key(sess: dict[str, Any]) -> tuple:
        sug = str(sess.get('suggested_at_name') or '')
        title = str(sess.get('title') or '')
        named = 0 if is_named_suggestion(sug, runtime=runtime) else 1
        context_dump = 1 if title.lstrip().startswith('# Context') else 0
        # Newer first within the same rank bucket.
        updated = sess.get('updated_at')
        try:
            age = -float(updated) if updated is not None else 0.0
        except (TypeError, ValueError):
            age = 0.0
        return (named, context_dump, age)

    usable.sort(key=_rank_key)

    picked: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_generic: set[str] = set()
    for sess in usable:
        sug = str(sess.get('suggested_at_name') or '').lower()
        if is_named_suggestion(sug, runtime=runtime):
            if sug in seen_names:
                continue
            seen_names.add(sug)
        else:
            preview = str(sess.get('preview') or extract_preview(
                str(sess.get('title') or '')))
            folder = str(sess.get('cwd') or '')
            generic_key = f'{preview.lower()}::{folder}'
            if generic_key in seen_generic:
                continue
            seen_generic.add(generic_key)
        picked.append(sess)
        if len(picked) >= limit:
            break
    return picked


async def discover_runtimes(*, dry_run: bool = False) -> list[dict[str, Any]]:
    adapters = [
        AcpCodexAdapter(dry_run=dry_run),
        AcpCursorAdapter(dry_run=dry_run),
        AcpClaudeAdapter(dry_run=dry_run),
        HermesAdapter(),
        OpenClawAdapter(),
    ]
    found: list[dict[str, Any]] = []
    for adapter in adapters:
        ok = await adapter.discover()
        attachable = ok and adapter.name in ('claude_code', 'cursor', 'codex')
        row: dict[str, Any] = {
            'runtime': adapter.name,
            'available': ok,
            'adapter_kind': _kind(adapter.name),
            'attachable': attachable,
            'label': adapter.name,
        }
        if adapter.name == 'cursor' and ok:
            row['label'] = 'Cursor CLI (agent acp)'
            row['meta'] = {
                'note':
                'Requires `agent login`. IDE Composer is not ACP-attachable.',
            }
        if adapter.name == 'codex' and ok:
            row['label'] = 'Codex ACP (codex-acp)'
            row['meta'] = {
                'note':
                'Long-lived ACP agent via codex-acp. Uses CLI login — not Team .env keys. '
                'Interactive Codex TUI is a different process.',
            }
        if adapter.name == 'claude_code' and ok:
            row['label'] = 'Claude ACP'
            row['meta'] = {
                'note':
                'Requires claude-agent-acp or `claude acp`. Print mode is not attachable.',
            }
        found.append(row)

        if ok and attachable and hasattr(adapter, 'list_sessions'):
            try:
                import asyncio
                sessions = await asyncio.wait_for(
                    adapter.list_sessions(), timeout=20.0)
            except Exception:  # noqa: BLE001
                sessions = []
            for sess in _select_session_candidates(
                    list(sessions or []), runtime=adapter.name):
                sid = sess.get('runtime_session_id') or ''
                if not sid:
                    continue
                title = sess.get('title') or sess.get('label') or sid[:8]
                preview = str(
                    sess.get('preview') or extract_preview(str(title)))
                suggested = (
                    sess.get('suggested_at_name')
                    or f'{adapter.name}_{str(sid)[:4]}')
                sess_cwd = sess.get('cwd') if isinstance(
                    sess.get('cwd'), str) else None
                updated = sess.get('updated_at')
                label = sess.get('label') or build_session_label(
                    runtime=adapter.name,
                    suggested=str(suggested),
                    preview=preview,
                    cwd=sess_cwd,
                    updated_at=float(updated) if updated is not None else None,
                )
                meta: dict[str, Any] = {
                    'preview': preview,
                    'suggested_at_name': suggested,
                    'note': 'ACP session (session/load).',
                }
                if sess_cwd:
                    meta['cwd'] = sess_cwd
                if updated is not None:
                    try:
                        meta['mtime'] = float(updated)
                    except (TypeError, ValueError):
                        pass
                found.append({
                    'runtime': adapter.name,
                    'available': True,
                    'adapter_kind': 'acp',
                    'attachable': True,
                    'label': label,
                    'cwd': sess_cwd,
                    'runtime_session_id': sid,
                    'candidate_id': (
                        f'cand_{adapter.name}_{sid.replace("-", "")[:16]}'),
                    'meta': meta,
                })

    if _cursor_ide_running():
        found.append({
            'runtime': 'cursor_ide',
            'available': True,
            'adapter_kind': 'ide',
            'attachable': False,
            'label': 'Cursor IDE (running)',
            'meta': {
                'note':
                'In-app Agent chat is not ACP-attachable. Use Cursor CLI '
                '(`agent acp`) after login.',
            },
        })

    if shutil.which('ms-agent') or shutil.which('ms_agent'):
        found.append({
            'runtime': 'ms_agent',
            'available': True,
            'adapter_kind': 'ms_agent',
            'attachable': False,
            'label': 'ms_agent',
        })
    return found


def _kind(name: str) -> str:
    if name in ('claude_code', 'codex', 'cursor'):
        return 'acp'
    if name == 'hermes':
        return 'hermes'
    if name == 'openclaw':
        return 'openclaw'
    if name == 'cursor_ide':
        return 'ide'
    return 'acp'
