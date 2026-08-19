# Copyright (c) ModelScope Contributors. All rights reserved.
"""Agent Team collaboration layer.

Platform = post office + locker. Lead Agents orchestrate via Tools.
"""
from ms_agent.team.context import ContextBundleAssembler
from ms_agent.team.errors import TeamError
from ms_agent.team.events import TeamEvent
from ms_agent.team.health import HealthMonitor
from ms_agent.team.ingress import EndpointRegistryService, MessageIngress
from ms_agent.team.models import (
    AgentEndpoint,
    Artifact,
    ContextBundle,
    DispatchEnvelope,
    EndpointHealth,
    InboundMessage,
    InvokePolicy,
    RemoteProfile,
    SessionBinding,
    TeamFeatureFlags,
    TeamProjectMeta,
    TeamTask,
    TimelineMessage,
)
from ms_agent.team.policies import InvokeGate, RemoteProfileEnforcer
from ms_agent.team.project_resolve import ProjectResolver
from ms_agent.team.router import AtMentionParser, AtRouter
from ms_agent.team.session_dir import SessionDirectory, SessionResolution

__all__ = [
    'AgentEndpoint',
    'Artifact',
    'AtMentionParser',
    'AtRouter',
    'ContextBundle',
    'ContextBundleAssembler',
    'DispatchEnvelope',
    'EndpointHealth',
    'EndpointRegistryService',
    'HealthMonitor',
    'InboundMessage',
    'InvokeGate',
    'InvokePolicy',
    'MessageIngress',
    'ProjectResolver',
    'RemoteProfile',
    'RemoteProfileEnforcer',
    'SessionBinding',
    'SessionDirectory',
    'SessionResolution',
    'TeamError',
    'TeamEvent',
    'TeamFeatureFlags',
    'TeamProjectMeta',
    'TeamTask',
    'TimelineMessage',
]
