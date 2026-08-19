# Copyright (c) ModelScope Contributors. All rights reserved.
"""@ mention parsing and endpoint routing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ms_agent.team.errors import (
    ENDPOINT_NOT_FOUND,
    ENDPOINT_OFFLINE,
    ENDPOINT_RECONNECTING,
    TeamError,
)
from ms_agent.team.models import AgentEndpoint, InboundMessage
from ms_agent.team.policies import InvokeGate, RemoteProfileEnforcer
from ms_agent.team.models import TeamFeatureFlags

# @张三 / @me-gpu / @zhangsan-claude
_AT_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_])@([\w\u4e00-\u9fff][\w\u4e00-\u9fff\-.]*)')


@dataclass
class RouteTarget:
    endpoint: AgentEndpoint
    caller_is_owner: bool
    permission_tier: str


class AtMentionParser:
    """Extract @at_name tokens from message content."""

    @staticmethod
    def parse(content: str) -> list[str]:
        if not content:
            return []
        seen: set[str] = set()
        names: list[str] = []
        for match in _AT_PATTERN.finditer(content):
            name = match.group(1)
            if name.lower() == 'all':
                # @all is broadcast-only (P2); skip as execution target.
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

    @staticmethod
    def strip_mentions(content: str) -> str:
        return _AT_PATTERN.sub('', content).strip()

    @staticmethod
    def clause_for(content: str, at_name: str) -> str:
        """Text addressed to one @target: from this mention until the next @.

        Dual-@ turns must not share one stripped prompt (C-02). If this
        mention has no clause of its own, fall back to the fully stripped
        remainder so ``@codex @lily do both`` still delivers ``do both``.
        """
        text = content or ''
        want = (at_name or '').lower().lstrip('@').strip()
        if not text or not want:
            return AtMentionParser.strip_mentions(text)
        matches = list(_AT_PATTERN.finditer(text))
        if not matches:
            return text.strip()
        for i, match in enumerate(matches):
            if match.group(1).lower() != want:
                continue
            start = match.end()
            end = (
                matches[i + 1].start()
                if i + 1 < len(matches) else len(text))
            clause = text[start:end].strip()
            if clause:
                return clause
            return AtMentionParser.strip_mentions(text)
        return AtMentionParser.strip_mentions(text)


class AtRouter:
    """Resolve mentions to endpoints and apply invoke gate."""

    def __init__(
        self,
        endpoints_by_at_name: dict[str, AgentEndpoint],
        feature_flags: TeamFeatureFlags | None = None,
    ) -> None:
        self._by_at = {k: v for k, v in endpoints_by_at_name.items()}
        self._flags = feature_flags or TeamFeatureFlags()

    def resolve_targets(
        self,
        msg: InboundMessage,
        *,
        default_lead_at: str | None = None,
        require_online: bool = True,
    ) -> list[RouteTarget]:
        explicit = (msg.target_at_name or '').lstrip('@').strip()
        mentions = list(msg.mentions) or AtMentionParser.parse(msg.content)
        if explicit:
            # Worker-rail follow-up: only that agent, never default lead.
            mentions = [explicit]
        elif mentions:
            # @ targets are exclusive. Do not also dispatch default lead.
            pass
        elif default_lead_at:
            mentions = [default_lead_at]
        else:
            return []

        targets: list[RouteTarget] = []
        for at_name in mentions:
            endpoint = self._lookup(at_name)
            caller_is_owner = InvokeGate.check(
                endpoint, msg.sender_user_id, self._flags)
            if require_online:
                self._check_status(endpoint)
            tier = RemoteProfileEnforcer.permission_tier(
                endpoint, caller_is_owner)
            targets.append(
                RouteTarget(
                    endpoint=endpoint,
                    caller_is_owner=caller_is_owner,
                    permission_tier=tier,
                ))
        return targets

    def _lookup(self, at_name: str) -> AgentEndpoint:
        # Exact match first; then case-insensitive for latin names.
        if at_name in self._by_at:
            return self._by_at[at_name]
        lower_map = {k.lower(): v for k, v in self._by_at.items()}
        if at_name.lower() in lower_map:
            return lower_map[at_name.lower()]
        raise TeamError(
            ENDPOINT_NOT_FOUND,
            f'No agent endpoint registered for @{at_name}',
            http_status=404,
            details={'at_name': at_name},
        )

    @staticmethod
    def _check_status(endpoint: AgentEndpoint) -> None:
        if endpoint.status in ('online', 'busy'):
            return
        if endpoint.status == 'degraded':
            from ms_agent.team.errors import ENDPOINT_DEGRADED
            raise TeamError(
                ENDPOINT_DEGRADED,
                f'@{endpoint.at_name} is degraded (heartbeat delayed).',
                http_status=503,
                details={
                    'endpoint_id': endpoint.endpoint_id,
                    'status': endpoint.status,
                },
            )
        if endpoint.status == 'need_reauth':
            from ms_agent.team.errors import NEED_REAUTH
            raise TeamError(
                NEED_REAUTH,
                f'@{endpoint.at_name} requires re-authentication.',
                http_status=401,
                details={
                    'endpoint_id': endpoint.endpoint_id,
                    'status': endpoint.status,
                },
            )
        if endpoint.status == 'reconnecting':
            raise TeamError(
                ENDPOINT_RECONNECTING,
                f'@{endpoint.at_name} is reconnecting '
                f'(ephemeral endpoint may be rebuilding).',
                http_status=503,
                details={
                    'endpoint_id': endpoint.endpoint_id,
                    'status': endpoint.status,
                },
            )
        if endpoint.adapter_kind == 'cloud':
            # Cloud endpoints are always "routable" from the platform.
            return
        raise TeamError(
            ENDPOINT_OFFLINE,
            f'@{endpoint.at_name} is offline.',
            http_status=503,
            details={
                'endpoint_id': endpoint.endpoint_id,
                'status': endpoint.status,
            },
        )

    @staticmethod
    def visibility_for_viewer(
        endpoints: Sequence[AgentEndpoint],
        viewer_user_id: str,
    ) -> list[dict]:
        """List endpoints for @ autocomplete; grey-out non-owned owner_only."""
        result = []
        for ep in endpoints:
            is_owner = ep.owner_user_id == viewer_user_id
            usable = is_owner  # phase-1: only owner can invoke
            result.append({
                **ep.to_dict(),
                'usable_by_viewer': usable,
                'visibility_hint':
                None if usable else '仅本人可用',
            })
        return result
