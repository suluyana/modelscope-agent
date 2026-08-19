# Copyright (c) ModelScope Contributors. All rights reserved.
"""Agent Team error codes and exceptions."""
from __future__ import annotations

from typing import Any


class TeamError(Exception):
    """Typed error for Agent Team routing / dispatch / policy failures."""

    def __init__(
        self,
        code: str,
        message: str = '',
        *,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.http_status = http_status
        self.details = details or {}
        super().__init__(f'[{code}] {self.message}')

    def to_dict(self) -> dict[str, Any]:
        return {
            'error': self.code,
            'message': self.message,
            'details': self.details,
        }


# Stable error codes (API contract)
AGENT_OWNER_ONLY = 'AGENT_OWNER_ONLY'
ENDPOINT_OFFLINE = 'ENDPOINT_OFFLINE'
ENDPOINT_RECONNECTING = 'ENDPOINT_RECONNECTING'
ENDPOINT_NOT_FOUND = 'ENDPOINT_NOT_FOUND'
ENDPOINT_BUSY = 'ENDPOINT_BUSY'
PROJECT_REQUIRED = 'PROJECT_REQUIRED'
NEEDS_DISAMBIGUATION = 'NEEDS_DISAMBIGUATION'
NEEDS_PROJECT_CARD = 'NEEDS_PROJECT_CARD'
AT_NAME_CONFLICT = 'AT_NAME_CONFLICT'
DISPATCH_REJECTED = 'DISPATCH_REJECTED'
FEATURE_DISABLED = 'FEATURE_DISABLED'
ARTIFACT_NOT_FOUND = 'ARTIFACT_NOT_FOUND'
INVALID_ENVELOPE = 'INVALID_ENVELOPE'
SESSION_ATTACH_FAILED = 'SESSION_ATTACH_FAILED'
NEED_REAUTH = 'NEED_REAUTH'
CIRCUIT_OPEN = 'CIRCUIT_OPEN'
BRIDGE_UNREACHABLE = 'BRIDGE_UNREACHABLE'
CANCELLED = 'CANCELLED'
ENDPOINT_DEGRADED = 'ENDPOINT_DEGRADED'
