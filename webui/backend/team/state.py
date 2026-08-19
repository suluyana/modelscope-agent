# Copyright (c) ModelScope Contributors. All rights reserved.
"""Shared Team service state for WebUI backend."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ms_agent.team.circuit import CircuitBreaker
from ms_agent.team.health import HealthMonitor
from ms_agent.team.ingress import EndpointRegistryService, MessageIngress
from ms_agent.team.models import TeamFeatureFlags
from ms_agent.team.session_dir import SessionDirectory
from ms_agent.team.stores.memory import (
    MemoryArtifactStore,
    MemoryBridgeStore,
    MemoryBridgeTokenStore,
    MemoryCandidateStore,
    MemoryEndpointStore,
    MemoryEndpointTokenStore,
    MemoryPairTokenStore,
    MemoryProjectMetaStore,
    MemorySessionBindingStore,
    MemoryTaskBoardStore,
    MemoryThreadBindingStore,
    MemoryTimelineStore,
)

# Optional file persistence root
_DATA_ROOT = Path(
    os.environ.get('MS_AGENT_TEAM_DATA',
                   str(Path.home() / '.ms_agent' / 'team')))


class TeamAppState:
    """Singleton-ish container wired into FastAPI routes."""

    def __init__(self, *, use_file: bool = False) -> None:
        self.flags = TeamFeatureFlags.from_dict({
            'remote_invoke_enabled':
            os.environ.get('MS_AGENT_REMOTE_INVOKE', 'false').lower()
            == 'true',
        })
        if use_file:
            from ms_agent.team.stores.file import (
                FileArtifactStore,
                FileBridgeStore,
                FileBridgeTokenStore,
                FileCandidateStore,
                FileEndpointStore,
                FileEndpointTokenStore,
                FilePairTokenStore,
                FileProjectMetaStore,
                FileSessionBindingStore,
                FileTaskBoardStore,
                FileThreadBindingStore,
                FileTimelineStore,
            )
            root = _DATA_ROOT
            root.mkdir(parents=True, exist_ok=True)
            self.endpoints = FileEndpointStore(root / 'endpoints.json')
            self.bridges = FileBridgeStore(root / 'bridges.json')
            self.bridge_tokens = FileBridgeTokenStore(
                root / 'bridge_tokens.json')
            self.candidates = FileCandidateStore(root / 'candidates.json')
            self.timeline = FileTimelineStore(root / 'timeline.jsonl')
            self.artifacts = FileArtifactStore(root / 'artifacts')
            self.tasks = FileTaskBoardStore(root / 'tasks.json')
            self.projects = FileProjectMetaStore(root / 'projects.json')
            self.bindings = FileThreadBindingStore(root / 'bindings.json')
            self.pair_tokens = FilePairTokenStore(root / 'pair_tokens.json')
            self.endpoint_tokens = FileEndpointTokenStore(
                root / 'endpoint_tokens.json')
            self.session_bindings = FileSessionBindingStore(
                root / 'sessions.json')
        else:
            self.endpoints = MemoryEndpointStore()
            self.bridges = MemoryBridgeStore()
            self.bridge_tokens = MemoryBridgeTokenStore()
            self.candidates = MemoryCandidateStore()
            self.timeline = MemoryTimelineStore()
            self.artifacts = MemoryArtifactStore()
            self.tasks = MemoryTaskBoardStore()
            self.projects = MemoryProjectMetaStore()
            self.bindings = MemoryThreadBindingStore()
            self.pair_tokens = MemoryPairTokenStore()
            self.endpoint_tokens = MemoryEndpointTokenStore()
            self.session_bindings = MemorySessionBindingStore()

        self.registry = EndpointRegistryService(self.endpoints, self.flags)
        self.sessions = SessionDirectory(self.session_bindings)
        self.circuit = CircuitBreaker()
        self.bridge_hub = None  # set by ws_bridge_hub
        self.event_subscribers: list = []
        self._event_buffer: list = []
        self._event_buffer_max = 256
        if use_file:
            from ms_agent.team.stores.dispatch_log import FileDispatchLogStore
            self.dispatch_log = FileDispatchLogStore(root / 'dispatch_logs')
        else:
            from ms_agent.team.stores.dispatch_log import MemoryDispatchLogStore
            self.dispatch_log = MemoryDispatchLogStore()
        self._health_task: asyncio.Task | None = None
        self.health = HealthMonitor(
            self.endpoints,
            event_sink=self._fanout_event,
            bridges=self.bridges,
        )
        self.ingress = MessageIngress(
            endpoint_store=self.endpoints,
            project_store=self.projects,
            timeline_store=self.timeline,
            binding_store=self.bindings,
            feature_flags=self.flags,
            event_sink=self._fanout_event,
            session_directory=self.sessions,
            circuit_breaker=self.circuit,
            task_store=self.tasks,
        )

    async def _fanout_event(self, event) -> None:
        did = getattr(event, 'dispatch_id', None)
        mismatch = None
        if did:
            env = self.ingress.get_envelope(str(did))
            if env is not None:
                if not getattr(event, 'at_name', None):
                    event.at_name = env.target_at_name
                if not getattr(event, 'project_id', None):
                    event.project_id = env.project_id
                if not getattr(event, 'endpoint_id', None):
                    event.endpoint_id = env.target_endpoint_id
                if getattr(event, 'type', None) != 'team.attribution_mismatch':
                    from ms_agent.team.events import reconcile_event_attribution
                    mismatch = reconcile_event_attribution(
                        event, env.target_at_name)
            try:
                self.dispatch_log.append(event)
                if mismatch is not None:
                    self.dispatch_log.append(mismatch)
            except Exception:  # noqa: BLE001
                pass
        to_emit = [event]
        if mismatch is not None:
            to_emit.append(mismatch)
        for item in to_emit:
            self._event_buffer.append(item)
            if len(self._event_buffer) > self._event_buffer_max:
                self._event_buffer = self._event_buffer[-self._event_buffer_max:]
            for sub in list(self.event_subscribers):
                try:
                    maybe = sub(item)
                    if hasattr(maybe, '__await__'):
                        await maybe
                except Exception:  # noqa: BLE001
                    pass

    def recent_events(self, *, project_id: str | None = None, limit: int = 50):
        items = self._event_buffer
        if project_id:
            items = [
                e for e in items
                if getattr(e, 'project_id', None) in (None, project_id)
            ]
        return items[-limit:]

    def ensure_health_loop(self) -> None:
        """Start background health tick once an event loop is available."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._health_task and not self._health_task.done():
            return

        async def _loop():
            while True:
                try:
                    await self.health.tick()
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(15)

        self._health_task = loop.create_task(_loop())


_STATE: TeamAppState | None = None


def get_team_state() -> TeamAppState:
    global _STATE
    if _STATE is None:
        # Default on: private-stream logs and registry survive restart.
        # Tests set MS_AGENT_TEAM_PERSIST=0.
        use_file = os.environ.get('MS_AGENT_TEAM_PERSIST', '1') == '1'
        _STATE = TeamAppState(use_file=use_file)
    return _STATE


def reset_team_state_for_tests() -> None:
    global _STATE
    _STATE = None
