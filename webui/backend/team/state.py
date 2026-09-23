# Copyright (c) ModelScope Contributors. All rights reserved.
"""Shared Team service state for WebUI backend."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
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
            'coordinator_enabled':
            os.environ.get('MS_AGENT_COORDINATOR', 'true').lower()
            == 'true',
            'max_parallel_agents_per_project':
            int(os.environ.get('MS_AGENT_TEAM_MAX_PARALLEL', '5') or 5),
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
            roster_path = root / 'roster.json'
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
            roster_path = None

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
        from ms_agent.team.coordinator import Coordinator
        self.coordinator = Coordinator(
            self.ingress,
            task_store=self.tasks,
            enabled=self.flags.coordinator_enabled,
            max_inflight_global=max(
                1, int(self.flags.max_parallel_agents_per_project or 5)),
        )
        from ms_agent.team.roster import RosterStore
        self.roster = RosterStore(roster_path)
        self.ingress.roster = self.roster
        from team.permission_inbox import PermissionInbox
        from ms_agent.permission.approval import (
            FileApprovalStore,
            MemoryApprovalStore,
        )
        from ms_agent.permission.ledger import PermissionLedger, default_asks_path
        if use_file:
            asks_store = FileApprovalStore(default_asks_path())
        else:
            asks_store = MemoryApprovalStore()
        self.permission_ledger = PermissionLedger(asks_store=asks_store)
        self.permission_inbox = PermissionInbox(store=asks_store)
        self.cloud_runtime = None

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
            self._record_permission_ask(event)
            try:
                self.dispatch_log.append(event)
                if mismatch is not None:
                    self.dispatch_log.append(mismatch)
            except Exception:  # noqa: BLE001
                pass
        else:
            self._record_permission_ask(event)
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

    def _record_permission_ask(self, event) -> None:
        et = getattr(event, 'type', '') or ''
        payload = dict(getattr(event, 'payload', None) or {})
        nested = payload.get('payload') if isinstance(
            payload.get('payload'), dict) else {}
        blob = dict(payload)
        if nested:
            for key, value in nested.items():
                if blob.get(key) in (None, '', {}, []):
                    blob[key] = value
        is_ask = (
            et == 'team.permission_requested'
            or payload.get('type') == 'permission_request'
            or blob.get('type') == 'permission_request')
        if is_ask:
            self.permission_inbox.put({
                'request_id': blob.get('request_id') or blob.get('acp_id'),
                'call_id': blob.get('call_id') or '',
                'acp_id': blob.get('acp_id'),
                'dispatch_id': getattr(event, 'dispatch_id', None),
                'project_id': getattr(event, 'project_id', None),
                'endpoint_id': getattr(event, 'endpoint_id', None),
                'at_name': getattr(event, 'at_name', None),
                'tool_name': blob.get('tool_name') or blob.get('name')
                or payload.get('content') or '',
                'tool_args': blob.get('tool_args') or blob.get('arguments')
                or {},
                'options': blob.get('options') or [],
                'suggestions': blob.get('suggestions') or [],
                'source': blob.get('source') or (
                    'acp' if blob.get('acp_id') is not None else 'cloud'),
            })
            self._set_task_waiting(
                getattr(event, 'dispatch_id', None),
                getattr(event, 'project_id', None),
                waiting=True,
            )
        elif et == 'team.permission_resolved':
            rid = blob.get('request_id') or blob.get('call_id')
            action = str(blob.get('action') or '')
            if rid:
                row = self.permission_inbox.get(str(rid))
                if row is None:
                    row = self.permission_inbox.get_by_call(
                        str(getattr(event, 'dispatch_id', None) or ''),
                        str(blob.get('call_id') or rid),
                    )
                target = str((row or {}).get('request_id') or rid)
                if action == 'cancelled':
                    self.permission_inbox.pop(target)
                elif action:
                    self.permission_inbox.decide(
                        target,
                        action,
                        pattern=str(blob.get('pattern') or ''),
                    )
            self._set_task_waiting(
                getattr(event, 'dispatch_id', None),
                getattr(event, 'project_id', None),
                waiting=False,
            )
        elif et in (
                'team.dispatch_done',
                'team.dispatch_error',
                'team.dispatch_cancelled',
        ):
            self._set_task_waiting(
                getattr(event, 'dispatch_id', None),
                getattr(event, 'project_id', None),
                waiting=False,
                force_clear=True,
            )

    def _task_for_dispatch(
        self,
        dispatch_id: str | None,
        project_id: str | None = None,
    ):
        did = str(dispatch_id or '')
        if not did or self.tasks is None:
            return None
        env = self.ingress.get_envelope(did)
        tid = getattr(env, 'referenced_task_id', None) if env else None
        if tid:
            task = self.tasks.get(tid)
            if task is not None:
                return task
        pids: list[str] = []
        env_pid = getattr(env, 'project_id', None) if env else None
        for pid in (project_id, env_pid):
            if pid and pid not in pids:
                pids.append(str(pid))
        if not pids:
            pids = [p.project_id for p in self.projects.list()]
        for pid in pids:
            for task in self.tasks.list(pid):
                if str(task.last_dispatch_id or '') == did:
                    return task
        return None

    def _set_task_waiting(
        self,
        dispatch_id: str | None,
        project_id: str | None,
        *,
        waiting: bool,
        force_clear: bool = False,
    ) -> None:
        task = self._task_for_dispatch(dispatch_id, project_id)
        if task is None:
            return
        did = str(dispatch_id or '')
        if waiting:
            if task.status in ('in_progress', 'waiting_approval'):
                task.status = 'waiting_approval'
                task.updated_at = datetime.now(timezone.utc).isoformat()
                self.tasks.upsert(task)
            return
        remaining = []
        if did and not force_clear:
            remaining = self.permission_inbox.list_pending(dispatch_id=did)
        if remaining:
            return
        if task.status == 'waiting_approval':
            task.status = 'in_progress'
            task.updated_at = datetime.now(timezone.utc).isoformat()
            self.tasks.upsert(task)

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
