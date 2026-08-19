# Copyright (c) ModelScope Contributors. All rights reserved.
"""DingTalk channel adapter (Phase 3) + stubs for Feishu / WeCom."""
from __future__ import annotations

import logging
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ms_agent.team.errors import FEATURE_DISABLED, TeamError
from ms_agent.team.models import InboundMessage, new_id
from ms_agent.team.project_resolve import ProjectResolver
from ms_agent.team.router import AtMentionParser
from team.state import get_team_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/channels', tags=['team-channels'])


class ChannelAdapter(Protocol):
    name: str

    def to_inbound(self, payload: dict[str, Any]) -> InboundMessage:
        ...

    def format_card(self, title: str, body: str,
                    buttons: list[dict[str, str]] | None = None) -> dict:
        ...


class DingTalkChannel:
    """DingTalk bot channel — group is transport only, not Team-bound."""

    name = 'dingtalk'

    def to_inbound(self, payload: dict[str, Any]) -> InboundMessage:
        # Stream / webhook payload shapes vary; normalize common fields.
        text = ''
        if isinstance(payload.get('text'), dict):
            text = payload['text'].get('content') or ''
        else:
            text = payload.get('text') or payload.get('content') or ''
        sender = (
            payload.get('senderStaffId')
            or payload.get('sender_user_id')
            or payload.get('senderId')
            or 'unknown')
        # Map dingtalk user → platform user (stub: identity pass-through).
        platform_user = map_dingtalk_user(sender)
        chat_id = str(payload.get('conversationId')
                      or payload.get('chat_id') or '')
        thread_id = str(payload.get('processQueryKey')
                        or payload.get('thread_id')
                        or payload.get('msgId') or '')
        mentions = AtMentionParser.parse(text)
        return InboundMessage(
            message_id=new_id('msg_'),
            sender_user_id=platform_user,
            content=text.strip(),
            channel='dingtalk',
            project_id=payload.get('project_id'),
            thread_id=thread_id or None,
            chat_id=chat_id or None,
            mentions=mentions,
            operation_kind=ProjectResolver.infer_operation_kind(text),
        )

    def format_card(self, title: str, body: str,
                    buttons: list[dict[str, str]] | None = None) -> dict:
        btn_list = []
        for b in buttons or []:
            btn_list.append({
                'title': b.get('title', 'OK'),
                'actionURL': b.get('url', ''),
            })
        return {
            'msgtype': 'actionCard',
            'actionCard': {
                'title': title,
                'text': body,
                'btns': btn_list,
            },
        }


class FeishuChannelStub:
    name = 'feishu'

    def to_inbound(self, payload: dict[str, Any]) -> InboundMessage:
        raise TeamError(
            FEATURE_DISABLED,
            'Feishu channel is not enabled (NF-15 reserved).',
            http_status=501,
        )

    def format_card(self, title: str, body: str,
                    buttons: list[dict[str, str]] | None = None) -> dict:
        return {'stub': True, 'title': title, 'body': body}


class WeComChannelStub:
    name = 'wecom'

    def to_inbound(self, payload: dict[str, Any]) -> InboundMessage:
        raise TeamError(
            FEATURE_DISABLED,
            'WeCom channel is not enabled (NF-15 reserved).',
            http_status=501,
        )

    def format_card(self, title: str, body: str,
                    buttons: list[dict[str, str]] | None = None) -> dict:
        return {'stub': True, 'title': title, 'body': body}


_DINGTALK_USER_MAP: dict[str, str] = {}


def map_dingtalk_user(dingtalk_user_id: str) -> str:
    """DingTalk userId ↔ platform userId. Extend via env / admin API later."""
    return _DINGTALK_USER_MAP.get(dingtalk_user_id, dingtalk_user_id)


def register_dingtalk_user_mapping(dingtalk_user_id: str,
                                   platform_user_id: str) -> None:
    _DINGTALK_USER_MAP[dingtalk_user_id] = platform_user_id


class DingTalkCallback(BaseModel):
    """Loose model — accept raw DingTalk webhook body."""
    text: Any = None
    content: str | None = None
    senderStaffId: str | None = None
    senderId: str | None = None
    conversationId: str | None = None
    processQueryKey: str | None = None
    msgId: str | None = None
    project_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = 'allow'


@router.post('/dingtalk/callback')
async def dingtalk_callback(body: dict[str, Any]):
    state = get_team_state()
    if not state.flags.dingtalk_enabled:
        raise HTTPException(501, detail={'error': FEATURE_DISABLED})

    channel = DingTalkChannel()
    inbound = channel.to_inbound(body)

    # Phase-1/3: only owner may @ own agents — enforced in InvokeGate.
    from team.api_dispatch import _ensure_handlers
    await _ensure_handlers(state)
    result = await state.ingress.handle(inbound)

    if result.needs_card:
        card = channel.format_card(
            '选择项目',
            '请选择要执行的项目：',
            buttons=[{
                'title': c['name'],
                'url': f'#project={c["project_id"]}'
            } for c in result.candidates],
        )
        return {'ok': True, 'needs_card': True, 'reply': card}

    if result.error:
        code = result.error.get('error')
        if code == 'AGENT_OWNER_ONLY':
            return {
                'ok': False,
                'reply': {
                    'msgtype': 'text',
                    'text': {
                        'content': '尚未开放他人调用本地 Agent（仅本人可用）。'
                    },
                },
            }
        raise HTTPException(400, detail=result.error)

    summaries = []
    for d in result.dispatches:
        summaries.append(f'已派发 @{d.target_at_name} ({d.dispatch_id})')
    return {
        'ok': True,
        'reply': {
            'msgtype': 'text',
            'text': {
                'content': '\n'.join(summaries) or '已接收'
            },
        },
        'dispatches': [d.to_dict() for d in result.dispatches],
    }


@router.post('/feishu/callback')
async def feishu_callback(body: dict[str, Any]):
    raise HTTPException(
        501,
        detail={
            'error': FEATURE_DISABLED,
            'message': 'Feishu channel stub — not enabled',
        },
    )


@router.post('/wecom/callback')
async def wecom_callback(body: dict[str, Any]):
    raise HTTPException(
        501,
        detail={
            'error': FEATURE_DISABLED,
            'message': 'WeCom channel stub — not enabled',
        },
    )
