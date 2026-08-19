# Copyright (c) ModelScope Contributors. All rights reserved.
"""Agent Team domain models (including reserved policy fields)."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = '') -> str:
    """Short opaque id for non-secret resources (messages, dispatches)."""
    body = uuid.uuid4().hex[:12]
    return f'{prefix}{body}' if prefix else body


def new_secret_token(prefix: str = '') -> str:
    """Cryptographically strong token for pairing / endpoint auth."""
    import secrets
    body = secrets.token_urlsafe(32)
    return f'{prefix}{body}' if prefix else body


class InvokePolicy(str, Enum):
    """Who may @ this agent. Phase-1 platform forces owner_only behavior."""

    OWNER_ONLY = 'owner_only'
    GROUP_MEMBERS = 'group_members'  # reserved
    PROJECT_MEMBERS = 'project_members'  # reserved
    ORG = 'org'  # reserved
    ALLOWLIST = 'allowlist'  # reserved


class RemoteProfile(str, Enum):
    """Capability profile when others @ the agent. Phase-1 fixed owner_only."""

    OWNER_ONLY = 'owner_only'
    COLLABORATIVE = 'collaborative'  # reserved P4+
    OPEN = 'open'  # reserved P4+


EndpointType = Literal['persistent', 'ephemeral']
EndpointStatus = Literal['online', 'offline', 'reconnecting', 'busy',
                         'degraded', 'need_reauth']
AdapterKind = Literal['acp', 'hermes', 'openclaw', 'cloud', 'ms_agent']
ChannelKind = Literal['web', 'dingtalk', 'feishu', 'wecom']
PermissionTier = Literal['owner', 'restricted']
OperationKind = Literal['read', 'write']
TaskStatus = Literal['pending', 'in_progress', 'completed', 'failed',
                     'cancelled']
SessionMode = Literal['attach', 'fresh']
SessionModeHint = Literal['attach', 'fresh', 'auto']
SessionResolutionReason = Literal[
    'bound',
    'created',
    'attach_fallback_fresh',
    'forced_fresh',
]
SessionBindingStatus = Literal['active', 'invalid', 'need_reauth']
HealthState = Literal['online', 'busy', 'degraded', 'offline', 'need_reauth',
                      'unregistered']


@dataclass
class TeamFeatureFlags:
    """Platform feature flags. remote_invoke_enabled is false in phase 1."""

    remote_invoke_enabled: bool = False
    max_parallel_agents_per_project: int = 5
    dingtalk_enabled: bool = True
    feishu_enabled: bool = False
    wecom_enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TeamFeatureFlags:
        data = data or {}
        channels = data.get('channels') or {}
        return cls(
            remote_invoke_enabled=bool(
                data.get('remote_invoke_enabled', False)),
            max_parallel_agents_per_project=int(
                data.get('max_parallel_agents_per_project', 5)),
            dingtalk_enabled=bool(
                (channels.get('dingtalk') or {}).get('enabled', True)),
            feishu_enabled=bool(
                (channels.get('feishu') or {}).get('enabled', False)),
            wecom_enabled=bool(
                (channels.get('wecom') or {}).get('enabled', False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'remote_invoke_enabled': self.remote_invoke_enabled,
            'max_parallel_agents_per_project':
            self.max_parallel_agents_per_project,
            'channels': {
                'dingtalk': {
                    'enabled': self.dingtalk_enabled
                },
                'feishu': {
                    'enabled': self.feishu_enabled
                },
                'wecom': {
                    'enabled': self.wecom_enabled
                },
            },
        }


@dataclass
class AgentEndpoint:
    """Routable Agent identity (product: Agent; may sit on a MachineBridge).

    Local/ACP agents MUST set ``bridge_id``. Cloud agents leave it None.
    """

    endpoint_id: str
    at_name: str
    owner_user_id: str
    endpoint_type: EndpointType
    runtime: str
    adapter_kind: AdapterKind
    machine_label: str = ''
    bridge_id: str | None = None
    capabilities: list[str] = field(default_factory=list)
    default_project_id: str | None = None
    # Reserved policy fields — always persisted; phase-1 behavior is forced.
    invoke_policy: InvokePolicy = InvokePolicy.OWNER_ONLY
    remote_profile: RemoteProfile = RemoteProfile.OWNER_ONLY
    invoke_allowlist: list[str] = field(default_factory=list)
    remote_invoke_enabled: bool = False
    status: EndpointStatus = 'offline'
    current_instance_id: str | None = None
    last_heartbeat: str | None = None
    bootstrap_version: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['invoke_policy'] = self.invoke_policy.value
        d['remote_profile'] = self.remote_profile.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEndpoint:
        return cls(
            endpoint_id=data['endpoint_id'],
            at_name=data['at_name'],
            owner_user_id=data['owner_user_id'],
            endpoint_type=data.get('endpoint_type', 'persistent'),
            runtime=data.get('runtime', 'claude_code'),
            adapter_kind=data.get('adapter_kind', 'acp'),
            machine_label=data.get('machine_label', ''),
            bridge_id=data.get('bridge_id'),
            capabilities=list(data.get('capabilities') or []),
            default_project_id=data.get('default_project_id'),
            invoke_policy=InvokePolicy(
                data.get('invoke_policy', InvokePolicy.OWNER_ONLY.value)),
            remote_profile=RemoteProfile(
                data.get('remote_profile', RemoteProfile.OWNER_ONLY.value)),
            invoke_allowlist=list(data.get('invoke_allowlist') or []),
            remote_invoke_enabled=bool(
                data.get('remote_invoke_enabled', False)),
            status=data.get('status', 'offline'),
            current_instance_id=data.get('current_instance_id'),
            last_heartbeat=data.get('last_heartbeat'),
            bootstrap_version=data.get('bootstrap_version'),
            created_at=data.get('created_at', _now_iso()),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class MachineBridge:
    """One sidecar per machine; owns a single platform WebSocket."""

    bridge_id: str
    owner_user_id: str
    machine_label: str = ''
    status: EndpointStatus = 'offline'
    current_instance_id: str | None = None
    last_heartbeat: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineBridge:
        return cls(
            bridge_id=data['bridge_id'],
            owner_user_id=data['owner_user_id'],
            machine_label=data.get('machine_label', ''),
            status=data.get('status', 'offline'),
            current_instance_id=data.get('current_instance_id'),
            last_heartbeat=data.get('last_heartbeat'),
            created_at=data.get('created_at', _now_iso()),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class BridgeToken:
    """Auth token for the single Bridge WebSocket (bound to bridge_id)."""

    token: str
    bridge_id: str
    owner_user_id: str
    expires_at: str
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BridgeToken:
        return cls(**data)


@dataclass
class RuntimeCandidate:
    """Discovered local runtime/session before or after Agent bind."""

    candidate_id: str
    bridge_id: str
    runtime: str
    adapter_kind: AdapterKind = 'acp'
    label: str = ''
    cwd: str | None = None
    runtime_session_id: str | None = None
    attachable: bool = True
    bound_endpoint_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeCandidate:
        return cls(
            candidate_id=data['candidate_id'],
            bridge_id=data['bridge_id'],
            runtime=data.get('runtime', 'claude_code'),
            adapter_kind=data.get('adapter_kind', 'acp'),
            label=data.get('label', ''),
            cwd=data.get('cwd'),
            runtime_session_id=data.get('runtime_session_id'),
            attachable=bool(data.get('attachable', True)),
            bound_endpoint_id=data.get('bound_endpoint_id'),
            meta=dict(data.get('meta') or {}),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class ContextBundle:
    """Referential context assembled before dispatch (no task_mode)."""

    thread_messages: list[str] = field(default_factory=list)
    project_timeline: list[str] = field(default_factory=list)
    git_snapshot: dict[str, Any] | None = None
    referenced_task_id: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    deployment_context: dict[str, Any] | None = None
    audience: str = 'worker'
    task_snapshots: list[dict[str, Any]] = field(default_factory=list)
    task_inbox: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ContextBundle:
        data = data or {}
        return cls(
            thread_messages=list(data.get('thread_messages') or []),
            project_timeline=list(data.get('project_timeline') or []),
            git_snapshot=data.get('git_snapshot'),
            referenced_task_id=data.get('referenced_task_id'),
            artifacts=list(data.get('artifacts') or []),
            deployment_context=data.get('deployment_context'),
            audience=data.get('audience') or 'worker',
            task_snapshots=list(data.get('task_snapshots') or []),
            task_inbox=list(data.get('task_inbox') or []),
        )


@dataclass
class DispatchEnvelope:
    """Payload sent to cloud runtime or local bridge. No task_mode field."""

    dispatch_id: str
    prompt: str
    project_id: str
    target_endpoint_id: str
    target_at_name: str
    sender_user_id: str
    channel: ChannelKind
    thread_id: str | None
    context_bundle: ContextBundle
    permission_tier: PermissionTier
    caller_is_owner: bool
    referenced_task_id: str | None = None
    session_mode: SessionMode = 'fresh'
    runtime_session_id: str | None = None
    cancel_token: str | None = None
    parent_dispatch_id: str | None = None
    session_resolution: SessionResolutionReason | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            'dispatch_id': self.dispatch_id,
            'prompt': self.prompt,
            'project_id': self.project_id,
            'target_endpoint_id': self.target_endpoint_id,
            'target_at_name': self.target_at_name,
            'sender_user_id': self.sender_user_id,
            'channel': self.channel,
            'thread_id': self.thread_id,
            'context_bundle': self.context_bundle.to_dict(),
            'permission_tier': self.permission_tier,
            'caller_is_owner': self.caller_is_owner,
            'referenced_task_id': self.referenced_task_id,
            'session_mode': self.session_mode,
            'runtime_session_id': self.runtime_session_id,
            'cancel_token': self.cancel_token,
            'parent_dispatch_id': self.parent_dispatch_id,
            'session_resolution': self.session_resolution,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DispatchEnvelope:
        return cls(
            dispatch_id=data['dispatch_id'],
            prompt=data['prompt'],
            project_id=data['project_id'],
            target_endpoint_id=data['target_endpoint_id'],
            target_at_name=data['target_at_name'],
            sender_user_id=data['sender_user_id'],
            channel=data.get('channel', 'web'),
            thread_id=data.get('thread_id'),
            context_bundle=ContextBundle.from_dict(
                data.get('context_bundle')),
            permission_tier=data.get('permission_tier', 'owner'),
            caller_is_owner=bool(data.get('caller_is_owner', False)),
            referenced_task_id=data.get('referenced_task_id'),
            session_mode=data.get('session_mode', 'fresh'),
            runtime_session_id=data.get('runtime_session_id'),
            cancel_token=data.get('cancel_token'),
            parent_dispatch_id=data.get('parent_dispatch_id'),
            session_resolution=data.get('session_resolution'),
            created_at=data.get('created_at', _now_iso()),
        )


@dataclass
class InboundMessage:
    """Channel-agnostic inbound message."""

    message_id: str
    sender_user_id: str
    content: str
    channel: ChannelKind
    project_id: str | None = None
    thread_id: str | None = None
    chat_id: str | None = None
    reply_to_message_id: str | None = None
    mentions: list[str] = field(default_factory=list)
    operation_kind: OperationKind = 'write'
    referenced_task_id: str | None = None
    session_mode: SessionModeHint = 'auto'
    target_at_name: str | None = None


@dataclass
class Artifact:
    artifact_id: str
    project_id: str
    sha256: str
    size: int
    storage_url: str
    filename: str = ''
    created_by_dispatch_id: str | None = None
    expires_at: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(
            artifact_id=data['artifact_id'],
            project_id=data['project_id'],
            sha256=data.get('sha256', ''),
            size=int(data.get('size') or 0),
            storage_url=data.get('storage_url', ''),
            filename=data.get('filename', ''),
            created_by_dispatch_id=data.get('created_by_dispatch_id'),
            expires_at=data.get('expires_at'),
            created_at=data.get('created_at', _now_iso()),
        )


@dataclass
class TeamTask:
    """Shared task-board entry written by agents — not a CI scheduler."""

    task_id: str
    project_id: str
    status: TaskStatus
    prompt: str
    trigger_user_id: str
    target_endpoint_id: str | None = None
    target_at_name: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    deployment_context: dict[str, Any] | None = None
    result_summary: str | None = None
    last_dispatch_id: str | None = None
    thread_id: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamTask:
        return cls(
            task_id=data['task_id'],
            project_id=data['project_id'],
            status=data.get('status', 'pending'),
            prompt=data.get('prompt', ''),
            trigger_user_id=data.get('trigger_user_id', ''),
            target_endpoint_id=data.get('target_endpoint_id'),
            target_at_name=data.get('target_at_name'),
            blocked_by=list(data.get('blocked_by') or []),
            output_artifacts=list(data.get('output_artifacts') or []),
            deployment_context=data.get('deployment_context'),
            result_summary=data.get('result_summary'),
            last_dispatch_id=data.get('last_dispatch_id'),
            thread_id=data.get('thread_id'),
            created_at=data.get('created_at', _now_iso()),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class TimelineMessage:
    """Project timeline entry (multi-speaker, multi-channel)."""

    message_id: str
    project_id: str
    sender_type: Literal['human', 'agent', 'system']
    sender_id: str
    sender_name: str
    content: str
    channel: ChannelKind = 'web'
    thread_id: str | None = None
    endpoint_id: str | None = None
    dispatch_id: str | None = None
    created_at: str = field(default_factory=_now_iso)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimelineMessage:
        return cls(
            message_id=data['message_id'],
            project_id=data['project_id'],
            sender_type=data.get('sender_type', 'human'),
            sender_id=data.get('sender_id', ''),
            sender_name=data.get('sender_name', ''),
            content=data.get('content', ''),
            channel=data.get('channel', 'web'),
            thread_id=data.get('thread_id'),
            endpoint_id=data.get('endpoint_id'),
            dispatch_id=data.get('dispatch_id'),
            created_at=data.get('created_at', _now_iso()),
            meta=dict(data.get('meta') or {}),
        )


@dataclass
class ThreadBinding:
    """Maps an IM / UI thread to a project."""

    chat_id: str
    thread_id: str
    project_id: str
    bound_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreadBinding:
        return cls(
            chat_id=data['chat_id'],
            thread_id=data['thread_id'],
            project_id=data['project_id'],
            bound_at=data.get('bound_at', _now_iso()),
        )


@dataclass
class PairToken:
    """One-time bridge pairing token."""

    pair_code: str
    owner_user_id: str
    expires_at: str
    created_at: str = field(default_factory=_now_iso)
    consumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PairToken:
        return cls(**data)


@dataclass
class EndpointToken:
    """Scoped token for baking into ephemeral images."""

    token: str
    endpoint_id: str
    owner_user_id: str
    expires_at: str
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointToken:
        return cls(**data)


@dataclass
class TeamProjectMeta:
    """Team-facing project extensions (release_config, default lead)."""

    project_id: str
    name: str
    workspace_path: str = ''
    default_lead_at: str | None = None
    release_config: dict[str, Any] = field(default_factory=dict)
    members: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamProjectMeta:
        return cls(
            project_id=data['project_id'],
            name=data.get('name', data['project_id']),
            workspace_path=data.get('workspace_path', ''),
            default_lead_at=data.get('default_lead_at'),
            release_config=dict(data.get('release_config') or {}),
            members=list(data.get('members') or []),
            created_at=data.get('created_at', _now_iso()),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class SessionBinding:
    """Maps (endpoint, project, thread) → stable runtime session id."""

    binding_id: str
    endpoint_id: str
    project_id: str
    runtime_session_id: str
    adapter_kind: str
    thread_id: str | None = None
    last_dispatch_id: str | None = None
    status: SessionBindingStatus = 'active'
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionBinding:
        return cls(
            binding_id=data['binding_id'],
            endpoint_id=data['endpoint_id'],
            project_id=data['project_id'],
            runtime_session_id=data['runtime_session_id'],
            adapter_kind=data.get('adapter_kind', 'acp'),
            thread_id=data.get('thread_id'),
            last_dispatch_id=data.get('last_dispatch_id'),
            status=data.get('status', 'active'),
            created_at=data.get('created_at', _now_iso()),
            updated_at=data.get('updated_at', _now_iso()),
        )


@dataclass
class EndpointHealth:
    """Computed health view for an endpoint (orchestration UI)."""

    endpoint_id: str
    state: HealthState
    last_seen: str | None = None
    reason: str = ''
    detail: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
