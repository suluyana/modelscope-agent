# Copyright (c) ModelScope Contributors. All rights reserved.
"""Web channel adapter — maps UI payloads to InboundMessage."""
from __future__ import annotations

from typing import Any

from ms_agent.team.models import InboundMessage, new_id
from ms_agent.team.project_resolve import ProjectResolver
from ms_agent.team.router import AtMentionParser


class WebChannel:
    """MS-Agent UI channel."""

    name = 'web'

    def to_inbound(self, payload: dict[str, Any]) -> InboundMessage:
        content = payload.get('content') or payload.get('text') or ''
        mentions = payload.get('mentions') or AtMentionParser.parse(content)
        op = payload.get('operation_kind') or ProjectResolver.infer_operation_kind(
            content)
        return InboundMessage(
            message_id=payload.get('message_id') or new_id('msg_'),
            sender_user_id=payload['sender_user_id'],
            content=content,
            channel='web',
            project_id=payload.get('project_id'),
            thread_id=payload.get('thread_id'),
            mentions=mentions,
            operation_kind=op,  # type: ignore[arg-type]
            referenced_task_id=payload.get('referenced_task_id'),
        )

    def format_outbound(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            'channel': 'web',
            'event': event,
        }
