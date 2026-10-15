"""Process-wide PermissionLedger (PermissionMemory + ApprovalStore).

Chat cards, settings CRUD, and the Team inbox share these two files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.backends.ms_agent.common import pm

_asks_store = None


def asks_store():
    """Singleton ask ledger (~/.ms_agent/permission_asks.json)."""
    global _asks_store
    if _asks_store is None:
        from ms_agent.permission.approval import FileApprovalStore
        from ms_agent.permission.ledger import default_asks_path

        _asks_store = FileApprovalStore(default_asks_path())
    return _asks_store


def ledger_for(project_path: str | Path | None = None):
    from ms_agent.permission.ledger import PermissionLedger

    return PermissionLedger(
        project_path=project_path,
        asks_store=asks_store(),
    )


def ledger_for_project(project_id: str | None):
    path = None
    if project_id:
        try:
            proj = pm().get(project_id)
        except Exception:  # noqa: BLE001
            proj = None
        if proj is not None:
            path = getattr(proj, "path", None)
    return ledger_for(path)


def serialize_ask(request) -> dict[str, Any]:
    """Public inbox row — no plaintext token."""
    return {
        "request_id": request.id,
        "call_id": request.call_id,
        "tool_name": request.tool_name,
        "tool_args": request.tool_args,
        "state": request.state,
        "project_id": request.project_id,
        "session_id": request.session_id,
        "dispatch_id": request.dispatch_id,
        "created_at": request.created_at,
        "decision": request.decision,
        "pattern": request.pattern,
        "resume_status": request.resume_status,
    }
