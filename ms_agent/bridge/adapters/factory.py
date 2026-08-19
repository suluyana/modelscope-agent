# Copyright (c) ModelScope Contributors. All rights reserved.
"""Factory for local runtime adapters."""
from __future__ import annotations

from typing import Any


def make_adapter(runtime: str, *, dry_run: bool = False) -> Any:
    """Return an adapter instance for ``runtime`` name."""
    name = (runtime or '').strip().lower()
    if name in ('cursor', 'cursor_cli', 'cursor-agent'):
        from ms_agent.bridge.adapters.acp_cursor import AcpCursorAdapter
        return AcpCursorAdapter(dry_run=dry_run)
    if name in ('codex', 'codex_cli'):
        from ms_agent.bridge.adapters.acp_codex import AcpCodexAdapter
        return AcpCodexAdapter(dry_run=dry_run)
    if name in ('hermes',):
        from ms_agent.bridge.adapters.stubs import HermesAdapter
        return HermesAdapter()
    if name in ('openclaw',):
        from ms_agent.bridge.adapters.stubs import OpenClawAdapter
        return OpenClawAdapter()
    # Default / claude_code / ms_agent → Claude ACP.
    from ms_agent.bridge.adapters.acp_claude import AcpClaudeAdapter
    return AcpClaudeAdapter(dry_run=dry_run)
