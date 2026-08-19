# Copyright (c) ModelScope Contributors. All rights reserved.
"""Stable runtime session bindings for attach / fresh dispatch."""
from __future__ import annotations

import os
from dataclasses import dataclass

from ms_agent.team.errors import SESSION_ATTACH_FAILED, TeamError
from ms_agent.team.models import (
    SessionBinding,
    SessionMode,
    SessionModeHint,
    SessionResolutionReason,
    new_id,
)
from ms_agent.team.stores.base import SessionBindingStore


def _attach_fallback() -> str:
    # Default: fail attach loudly (true ACP). Opt into silent fresh with
    # MS_AGENT_SESSION_ATTACH_FALLBACK=fresh.
    return os.environ.get('MS_AGENT_SESSION_ATTACH_FALLBACK', 'error').lower()


def _attach_supported() -> bool:
    """S1 gate: set MS_AGENT_SESSION_ATTACH_SUPPORTED=0 to force fresh."""
    return os.environ.get('MS_AGENT_SESSION_ATTACH_SUPPORTED', '1') != '0'


@dataclass(frozen=True)
class SessionResolution:
    runtime_session_id: str
    session_mode: SessionMode
    session_resolution: SessionResolutionReason
    binding: SessionBinding


class SessionDirectory:
    """Resolve attach|fresh|auto into a stable runtime_session_id."""

    def __init__(self, store: SessionBindingStore) -> None:
        self.store = store

    def resolve(
        self,
        *,
        endpoint_id: str,
        project_id: str,
        thread_id: str | None,
        adapter_kind: str,
        mode_hint: SessionModeHint = 'auto',
        dispatch_id: str | None = None,
    ) -> SessionResolution:
        if not _attach_supported() and mode_hint in ('attach', 'auto'):
            return self._create_fresh(
                endpoint_id=endpoint_id,
                project_id=project_id,
                thread_id=thread_id,
                adapter_kind=adapter_kind,
                dispatch_id=dispatch_id,
                reason='forced_fresh',
            )

        if mode_hint == 'fresh':
            return self._create_fresh(
                endpoint_id=endpoint_id,
                project_id=project_id,
                thread_id=thread_id,
                adapter_kind=adapter_kind,
                dispatch_id=dispatch_id,
                reason='created',
            )

        existing = self.store.get_active(
            endpoint_id, project_id, thread_id)
        if existing is not None:
            if dispatch_id:
                existing.last_dispatch_id = dispatch_id
                existing = self.store.upsert(existing)
            return SessionResolution(
                runtime_session_id=existing.runtime_session_id,
                session_mode='attach',
                session_resolution='bound',
                binding=existing,
            )

        if mode_hint == 'attach':
            if _attach_fallback() == 'error':
                raise TeamError(
                    SESSION_ATTACH_FAILED,
                    'No active session binding to attach.',
                    http_status=409,
                    details={
                        'endpoint_id': endpoint_id,
                        'project_id': project_id,
                        'thread_id': thread_id,
                    },
                )
            return self._create_fresh(
                endpoint_id=endpoint_id,
                project_id=project_id,
                thread_id=thread_id,
                adapter_kind=adapter_kind,
                dispatch_id=dispatch_id,
                reason='attach_fallback_fresh',
            )

        # auto + no binding
        return self._create_fresh(
            endpoint_id=endpoint_id,
            project_id=project_id,
            thread_id=thread_id,
            adapter_kind=adapter_kind,
            dispatch_id=dispatch_id,
            reason='created',
        )

    def invalidate(
        self,
        binding_id: str,
        *,
        status: str = 'invalid',
    ) -> SessionBinding | None:
        binding = self.store.get(binding_id)
        if binding is None:
            return None
        binding.status = status  # type: ignore[assignment]
        return self.store.upsert(binding)

    def mark_need_reauth(self, binding_id: str) -> SessionBinding | None:
        return self.invalidate(binding_id, status='need_reauth')

    def list_for_endpoint(self, endpoint_id: str) -> list[SessionBinding]:
        return self.store.list_for_endpoint(endpoint_id)

    def _create_fresh(
        self,
        *,
        endpoint_id: str,
        project_id: str,
        thread_id: str | None,
        adapter_kind: str,
        dispatch_id: str | None,
        reason: SessionResolutionReason,
    ) -> SessionResolution:
        # One active binding per key: retire prior actives.
        for old in self.store.list_for_endpoint(endpoint_id):
            if (old.project_id == project_id and old.thread_id == thread_id
                    and old.status == 'active'):
                old.status = 'invalid'
                self.store.upsert(old)

        binding = SessionBinding(
            binding_id=new_id('sb_'),
            endpoint_id=endpoint_id,
            project_id=project_id,
            thread_id=thread_id,
            runtime_session_id=new_id('sess_'),
            adapter_kind=adapter_kind,
            last_dispatch_id=dispatch_id,
            status='active',
        )
        binding = self.store.upsert(binding)
        mode: SessionMode = 'fresh'
        return SessionResolution(
            runtime_session_id=binding.runtime_session_id,
            session_mode=mode,
            session_resolution=reason,
            binding=binding,
        )
