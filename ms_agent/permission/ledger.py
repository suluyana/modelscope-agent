"""Single ledger for user permission decisions.

Two files, one product — do not add a third JSON:

  * ``PermissionMemory`` — always-allow *rules*
    (``.ms_agent/permission_memory.json`` project + ``~/.ms_agent/permission_memory.json``)
  * ``ApprovalStore`` — pending / decided *asks*
    (``~/.ms_agent/permission_asks.json``)

YAML ``permission.whitelist`` is agent policy (read-only builtins, etc.),
not a user CRUD ledger. Chat cards, settings pages, and the Team inbox
must read/write this facade instead of a parallel ``permissions.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .approval import (
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStore,
    FileApprovalStore,
    MemoryApprovalStore,
)
from .handler import PermissionAction, PermissionResponse
from .memory import MemoryEntry, PermissionMemory
from .pattern_guard import (
    UnsafePersistPattern,
    classify_rule_kind,
    validate_persist_pattern,
)


def default_asks_path() -> Path:
    from ms_agent.project.paths import global_home
    return global_home() / 'permission_asks.json'


def default_global_memory_path() -> Path:
    from ms_agent.project.paths import global_home
    return global_home() / 'permission_memory.json'


class PermissionLedger:
    """Compose the two SDK stores behind one object."""

    def __init__(
        self,
        *,
        project_path: str | Path | None = None,
        global_memory_path: str | Path | None = None,
        asks_store: ApprovalStore | None = None,
        asks_path: str | Path | None = None,
    ) -> None:
        self.memory = PermissionMemory(
            project_path=project_path,
            global_path=global_memory_path,
        )
        if asks_store is not None:
            self.asks = asks_store
        else:
            path = Path(asks_path) if asks_path else default_asks_path()
            self.asks = FileApprovalStore(path)

    # ── rules (always-allow) ──────────────────────────────────────────

    def add_rule(
        self,
        pattern: str,
        scope: Literal['project', 'global'] = 'project',
        source: Literal['user', 'plugin', 'hook'] = 'user',
    ) -> None:
        pattern = validate_persist_pattern(pattern)
        self.memory.add(pattern, scope=scope, source=source)

    def list_rules(
        self,
        scope: Literal['project', 'global'] | None = None,
    ) -> list[MemoryEntry]:
        entries = self.memory.list_all()
        if scope is None:
            return entries
        return [e for e in entries if e.scope == scope]

    def revoke_rule(self, pattern: str) -> int:
        removed = self.memory.revoke(pattern)
        self.memory.reload()
        return removed

    def rule_matches(self, tool_name: str, tool_args: dict[str, Any]) -> bool:
        return self.memory.matches(tool_name, tool_args)

    def preview_match(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Which always-allow rule would fire for this call (settings preview)."""
        hits = [
            e for e in self.memory.list_all()
            if self.memory._matcher.match_with_content(
                e.pattern, tool_name, tool_args)
        ]
        return {
            'matches': bool(hits),
            'rules': [
                {
                    'pattern': e.pattern,
                    'scope': e.scope,
                    'source': e.source,
                    'kind': classify_rule_kind(e.pattern),
                }
                for e in hits
            ],
        }

    def serialize_rule(self, entry: MemoryEntry) -> dict[str, Any]:
        return {
            'pattern': entry.pattern,
            'scope': entry.scope,
            'source': entry.source,
            'created_at': entry.created_at,
            'kind': classify_rule_kind(entry.pattern),
        }

    # ── asks (inbox) ─────────────────────────────────────────────────

    def create_ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        call_id: str = '',
        context: str = '',
        suggestions: list[str] | None = None,
        request_id: str | None = None,
        project_id: str = '',
        session_id: str = '',
        dispatch_id: str = '',
        runtime_id: str = '',
    ) -> ApprovalRequest:
        request = ApprovalRequest.create(
            tool_name,
            tool_args,
            call_id=call_id,
            context=context,
            suggestions=suggestions or [],
            request_id=request_id,
            project_id=project_id,
            session_id=session_id,
            dispatch_id=dispatch_id,
            runtime_id=runtime_id,
        )
        return self.asks.create(request)

    def get_ask(self, request_id: str) -> ApprovalRequest | None:
        return self.asks.get(request_id)

    def list_asks(
        self,
        *,
        state: str | None = None,
        project_id: str | None = None,
        dispatch_id: str | None = None,
    ) -> list[ApprovalRequest]:
        rows = self.asks.list(state) if state else self.asks.list()
        if project_id:
            rows = [r for r in rows if r.project_id == project_id]
        if dispatch_id:
            rows = [r for r in rows if r.dispatch_id == dispatch_id]
        return rows

    def list_pending_asks(
        self,
        *,
        project_id: str | None = None,
        dispatch_id: str | None = None,
    ) -> list[ApprovalRequest]:
        rows = self.asks.list('pending')
        if project_id:
            rows = [r for r in rows if r.project_id == project_id]
        if dispatch_id:
            rows = [r for r in rows if r.dispatch_id == dispatch_id]
        return rows

    def decide_ask(
        self,
        request: ApprovalRequest,
        response: PermissionResponse,
        *,
        decided_by: str = 'human',
    ) -> ApprovalRequest:
        """Apply a human decision using the live request's token.

        The UI only sends ``request_id``. Token/fingerprint stay on the
        server (the object returned by ``create`` / live Future).
        """
        state = (
            'denied' if response.action == PermissionAction.DENY else 'approved')
        return self.asks.transition(
            request.id,
            state,  # type: ignore[arg-type]
            expected_version=request.version,
            token=request.token,
            fingerprint=request.fingerprint,
            decision=response.action.value,
            pattern=response.pattern or '',
            decided_by=decided_by,
        )

    def queue_resume(
        self,
        request_id: str,
        *,
        decision: str = 'allow_once',
        pattern: str = '',
    ):
        """After restart (no live Future): approve + mint continuation token."""
        return self.asks.queue_resume(
            request_id, decision=decision, pattern=pattern)


__all__ = [
    'ApprovalConflictError',
    'PermissionLedger',
    'default_asks_path',
    'default_global_memory_path',
]
