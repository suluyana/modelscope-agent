# Copyright (c) ModelScope Contributors. All rights reserved.
"""Streaming / UI event schemas for Agent Team."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


EventType = Literal[
    'team.stream',
    'team.message',
    'team.card',
    'team.dispatch_start',
    'team.dispatch_done',
    'team.dispatch_error',
    'team.dispatch_cancelled',
    'team.session',
    'team.circuit_open',
    'team.attribution_mismatch',
    'endpoint.status',
    'task.update',
    'artifact.ready',
]


@dataclass
class TeamEvent:
    type: EventType
    project_id: str | None = None
    dispatch_id: str | None = None
    endpoint_id: str | None = None
    at_name: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'TeamEvent':
        return cls(
            type=data.get('type') or 'team.stream',
            project_id=data.get('project_id'),
            dispatch_id=data.get('dispatch_id'),
            endpoint_id=data.get('endpoint_id'),
            at_name=data.get('at_name'),
            payload=dict(data.get('payload') or {}),
            created_at=data.get('created_at') or _now_iso(),
        )


def _norm_at(name: str | None) -> str:
    return (name or '').lstrip('@').strip().lower()


def reconcile_event_attribution(
    event: TeamEvent,
    card_at_name: str | None,
) -> TeamEvent | None:
    """Stamp the card's @name onto ``event``. Return a mismatch event if needed.

    C-03: a stream token labeled for the wrong agent must not silently land
    on this card. The original event is rewritten to the card's at_name so
    dispatch-keyed subscribers stay consistent; callers also fan out the
    returned mismatch marker for the UI (do not concatenate it as text).
    """
    card = (card_at_name or '').lstrip('@').strip()
    if not card:
        return None
    incoming = (event.at_name or '').lstrip('@').strip()
    if not incoming:
        event.at_name = card
        return None
    if _norm_at(incoming) == _norm_at(card):
        event.at_name = card
        return None
    mismatch = TeamEvent(
        type='team.attribution_mismatch',
        project_id=event.project_id,
        dispatch_id=event.dispatch_id,
        endpoint_id=event.endpoint_id,
        at_name=card,
        payload={
            'event_at_name': incoming,
            'card_at_name': card,
            'event_type': event.type,
        },
    )
    event.at_name = card
    return mismatch
