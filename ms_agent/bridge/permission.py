# Copyright (c) ModelScope Contributors. All rights reserved.
"""Permission tier enforcement on the bridge."""
from __future__ import annotations

from ms_agent.team.errors import DISPATCH_REJECTED, TeamError
from ms_agent.team.models import DispatchEnvelope


def assert_dispatch_allowed(envelope: DispatchEnvelope) -> None:
    """Phase-1: only owner dispatches execute on the local bridge."""
    if not envelope.caller_is_owner:
        raise TeamError(
            DISPATCH_REJECTED,
            'Bridge rejects non-owner dispatch (caller_is_owner=false).',
            http_status=403,
            details={'dispatch_id': envelope.dispatch_id},
        )


def permission_mode_for_tier(permission_tier: str) -> str:
    if permission_tier == 'owner':
        return 'bypassPermissions'
    return 'default'
