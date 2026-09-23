# Copyright (c) ModelScope Contributors. All rights reserved.
"""Team inbox is a view over the SDK ApprovalStore — not a third ledger."""
from __future__ import annotations

from typing import Any

from ms_agent.permission.approval import (
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStore,
    MemoryApprovalStore,
)


def _row(request: ApprovalRequest, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = dict(meta or {})
    return {
        'request_id': request.id,
        'call_id': request.call_id,
        'tool_name': request.tool_name,
        'tool_args': request.tool_args,
        'context': request.context,
        'options': extra.get('options') or [],
        'suggestions': list(request.suggestions or extra.get('suggestions') or []),
        'status': request.state,
        'state': request.state,
        'dispatch_id': request.dispatch_id or extra.get('dispatch_id'),
        'project_id': request.project_id or extra.get('project_id'),
        'endpoint_id': extra.get('endpoint_id'),
        'at_name': extra.get('at_name'),
        'source': extra.get('source') or request.runtime_id or 'cloud',
        'acp_id': extra.get('acp_id'),
        'created_at': request.created_at,
        'fingerprint': request.fingerprint,
        'version': request.version,
    }


def _canonical_request_id(record: dict[str, Any]) -> str:
    request_id = str(record.get('request_id') or '').strip()
    acp_id = record.get('acp_id')
    dispatch_id = str(record.get('dispatch_id') or '')
    if not request_id:
        request_id = str(record.get('call_id') or '')
        if not request_id and acp_id is not None:
            request_id = str(acp_id)
    if acp_id is not None and dispatch_id:
        acp_s = str(acp_id)
        if request_id in ('', acp_s):
            return f'{dispatch_id}:{acp_s}'
    return request_id


def _cas_kwargs(stored: ApprovalRequest) -> dict[str, Any]:
    """File stores wipe plaintext tokens; resume with hash + host_resume."""
    return {
        'expected_version': stored.version,
        'token': stored.token or None,
        'fingerprint': stored.fingerprint or None,
        'host_resume': not bool(stored.token),
    }


class PermissionInbox:
    """Pending asks keyed by request_id, backed by ``ApprovalStore``.

    Cloud asks are woken via ``WebPermissionHandler.resolve`` (same store).
    ACP asks are answered over the Bridge WS, then ``decide`` here.
    ``call_id`` distinguishes parallel Workers that share a tool name.
    """

    def __init__(self, store: ApprovalStore | None = None) -> None:
        self._store = store or MemoryApprovalStore()
        self._meta: dict[str, dict[str, Any]] = {}

    @property
    def store(self) -> ApprovalStore:
        return self._store

    def put(self, record: dict[str, Any]) -> dict[str, Any]:
        request_id = _canonical_request_id(record)
        if not request_id:
            return record
        meta = {
            'endpoint_id': record.get('endpoint_id'),
            'at_name': record.get('at_name'),
            'source': record.get('source'),
            'acp_id': record.get('acp_id'),
            'options': record.get('options') or [],
            'suggestions': record.get('suggestions') or [],
            'project_id': record.get('project_id'),
            'dispatch_id': record.get('dispatch_id'),
        }
        existing = self._store.get(request_id)
        if existing is None:
            request = ApprovalRequest.create(
                str(record.get('tool_name') or ''),
                dict(record.get('tool_args') or {}),
                call_id=str(record.get('call_id') or ''),
                context=str(record.get('context') or ''),
                suggestions=list(record.get('suggestions') or []),
                request_id=request_id,
                project_id=str(record.get('project_id') or ''),
                dispatch_id=str(record.get('dispatch_id') or ''),
                runtime_id=str(record.get('source') or ''),
            )
            try:
                existing = self._store.create(request)
            except ApprovalConflictError:
                existing = self._store.get(request_id)
        self._meta[request_id] = meta
        if existing is None:
            return {**record, 'request_id': request_id}
        return _row(existing, meta)

    def get(self, request_id: str) -> dict[str, Any] | None:
        stored = self._store.get(str(request_id))
        if stored is None:
            return None
        return _row(stored, self._meta.get(str(request_id)))

    def get_by_call(
        self,
        dispatch_id: str,
        call_id: str,
    ) -> dict[str, Any] | None:
        if not call_id:
            return None
        for row in self.list_pending(dispatch_id=dispatch_id):
            if str(row.get('call_id') or '') == str(call_id):
                return row
        return None

    def decide(
        self,
        request_id: str,
        action: str,
        *,
        pattern: str = '',
        decided_by: str = 'human',
    ) -> dict[str, Any] | None:
        """Record a human decision on the SDK store (approved / denied)."""
        rid = str(request_id)
        stored = self._store.get(rid)
        if stored is None:
            return None
        if stored.state == 'pending':
            next_state = (
                'denied' if action in ('deny', 'reject') else 'approved')
            try:
                stored = self._store.transition(
                    stored.id,
                    next_state,  # type: ignore[arg-type]
                    decision=action,
                    pattern=pattern or '',
                    decided_by=decided_by,
                    **_cas_kwargs(stored),
                )
            except ApprovalConflictError:
                stored = self._store.get(rid)
                if stored is None or stored.state == 'pending':
                    return None
        if stored is None:
            return None
        return _row(stored, self._meta.get(rid))

    def pop(self, request_id: str) -> dict[str, Any] | None:
        """Drop inbox meta. Pending store rows are cancelled, not forgotten."""
        rid = str(request_id)
        stored = self._store.get(rid)
        if stored is not None and stored.state == 'pending':
            try:
                stored = self._store.transition(
                    stored.id,
                    'cancelled',
                    decision='cancelled',
                    decided_by='system',
                    **_cas_kwargs(stored),
                )
            except ApprovalConflictError:
                stored = self._store.get(rid)
                if stored is not None and stored.state == 'pending':
                    return _row(stored, self._meta.get(rid))
        row = _row(stored, self._meta.get(rid)) if stored is not None else None
        if stored is None or stored.state != 'pending':
            self._meta.pop(rid, None)
        return row

    def list_pending(
        self,
        *,
        project_id: str | None = None,
        dispatch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for request in self._store.list('pending'):
            meta = self._meta.get(request.id) or {}
            if project_id:
                pid = request.project_id or meta.get('project_id') or ''
                if str(pid) != str(project_id):
                    continue
            if dispatch_id:
                did = request.dispatch_id or meta.get('dispatch_id') or ''
                if str(did) != str(dispatch_id):
                    continue
            rows.append(_row(request, meta))
        rows.sort(key=lambda r: r.get('created_at') or '')
        return rows
