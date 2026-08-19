# Copyright (c) ModelScope Contributors. All rights reserved.
"""Invoke policy gate and remote profile enforcer (phase-1 + reserved hooks)."""
from __future__ import annotations

from typing import Any

from ms_agent.team.errors import AGENT_OWNER_ONLY, TeamError
from ms_agent.team.models import AgentEndpoint, InvokePolicy, TeamFeatureFlags


class InvokeGate:
    """Decide whether sender may @ an endpoint.

    Phase 1: if ``remote_invoke_enabled`` is false (platform default), any
    non-owner is rejected with AGENT_OWNER_ONLY — even if the endpoint's
    ``invoke_policy`` was changed to a looser value.
    """

    @staticmethod
    def check(
        endpoint: AgentEndpoint,
        sender_user_id: str,
        feature_flags: TeamFeatureFlags,
        *,
        group_member_ids: set[str] | None = None,
        project_member_ids: set[str] | None = None,
        org_member_ids: set[str] | None = None,
    ) -> bool:
        """Return True if caller is owner; raise TeamError otherwise.

        Returns ``caller_is_owner`` boolean for the DispatchEnvelope.
        """
        is_owner = sender_user_id == endpoint.owner_user_id
        if is_owner:
            return True

        # Platform master switch — phase 1 always false.
        if not feature_flags.remote_invoke_enabled:
            raise TeamError(
                AGENT_OWNER_ONLY,
                'Remote invoke by others is not enabled. '
                'Only the agent owner may @ this endpoint.',
                http_status=403,
                details={
                    'at_name': endpoint.at_name,
                    'owner_user_id': endpoint.owner_user_id,
                    'remote_invoke_enabled': False,
                },
            )

        # Reserved path for P4+ when remote_invoke_enabled=true.
        return InvokeGate._check_policy(
            endpoint,
            sender_user_id,
            group_member_ids=group_member_ids,
            project_member_ids=project_member_ids,
            org_member_ids=org_member_ids,
        )

    @staticmethod
    def _check_policy(
        endpoint: AgentEndpoint,
        sender_user_id: str,
        *,
        group_member_ids: set[str] | None = None,
        project_member_ids: set[str] | None = None,
        org_member_ids: set[str] | None = None,
    ) -> bool:
        policy = endpoint.invoke_policy
        if policy == InvokePolicy.OWNER_ONLY:
            raise TeamError(
                AGENT_OWNER_ONLY,
                'This agent is owner_only.',
                http_status=403,
                details={'at_name': endpoint.at_name},
            )
        if policy == InvokePolicy.ALLOWLIST:
            if sender_user_id not in set(endpoint.invoke_allowlist):
                raise TeamError(
                    AGENT_OWNER_ONLY,
                    'Sender not in invoke allowlist.',
                    http_status=403,
                )
            return False
        if policy == InvokePolicy.GROUP_MEMBERS:
            if not group_member_ids or sender_user_id not in group_member_ids:
                raise TeamError(
                    AGENT_OWNER_ONLY,
                    'Sender is not a group member.',
                    http_status=403,
                )
            return False
        if policy == InvokePolicy.PROJECT_MEMBERS:
            if (not project_member_ids
                    or sender_user_id not in project_member_ids):
                raise TeamError(
                    AGENT_OWNER_ONLY,
                    'Sender is not a project member.',
                    http_status=403,
                )
            return False
        if policy == InvokePolicy.ORG:
            if not org_member_ids or sender_user_id not in org_member_ids:
                raise TeamError(
                    AGENT_OWNER_ONLY,
                    'Sender is not an org member.',
                    http_status=403,
                )
            return False
        raise TeamError(
            AGENT_OWNER_ONLY,
            f'Unknown invoke_policy: {policy}',
            http_status=403,
        )


class RemoteProfileEnforcer:
    """Apply remote_profile restrictions when others invoke (P4+).

    Phase 1: no-op for owner path; non-owner never reaches here because
    InvokeGate rejects first.
    """

    @staticmethod
    def permission_tier(
        endpoint: AgentEndpoint,
        caller_is_owner: bool,
    ) -> str:
        if caller_is_owner:
            return 'owner'
        # Reserved: map remote_profile -> restricted / open later.
        _ = endpoint.remote_profile
        return 'restricted'

    @staticmethod
    def audit_event(
        endpoint: AgentEndpoint,
        sender_user_id: str,
        decision: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            'event': 'invoke_policy_decision',
            'at_name': endpoint.at_name,
            'endpoint_id': endpoint.endpoint_id,
            'owner_user_id': endpoint.owner_user_id,
            'sender_user_id': sender_user_id,
            'invoke_policy': endpoint.invoke_policy.value,
            'remote_profile': endpoint.remote_profile.value,
            'decision': decision,
            **extra,
        }
