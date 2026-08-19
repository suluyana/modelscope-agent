# Copyright (c) ModelScope Contributors. All rights reserved.
"""In-memory stores for tests and Phase-1 local mode."""
from __future__ import annotations

import hashlib
import threading
from typing import Dict

from ms_agent.team.models import (
    AgentEndpoint,
    Artifact,
    BridgeToken,
    EndpointToken,
    MachineBridge,
    PairToken,
    RuntimeCandidate,
    SessionBinding,
    TeamProjectMeta,
    TeamTask,
    ThreadBinding,
    TimelineMessage,
)
from ms_agent.team.stores.base import (
    ArtifactStore,
    BridgeStore,
    BridgeTokenStore,
    CandidateStore,
    EndpointStore,
    EndpointTokenStore,
    PairTokenStore,
    ProjectMetaStore,
    SessionBindingStore,
    TaskBoardStore,
    ThreadBindingStore,
    TimelineStore,
)


class MemoryEndpointStore(EndpointStore):

    def __init__(self) -> None:
        self._by_id: Dict[str, AgentEndpoint] = {}
        self._lock = threading.RLock()

    def upsert(self, endpoint: AgentEndpoint) -> AgentEndpoint:
        with self._lock:
            self._by_id[endpoint.endpoint_id] = endpoint
            return endpoint

    def get(self, endpoint_id: str) -> AgentEndpoint | None:
        return self._by_id.get(endpoint_id)

    def get_by_at_name(self, at_name: str) -> AgentEndpoint | None:
        for ep in self._by_id.values():
            if ep.at_name == at_name:
                return ep
        lower = at_name.lower()
        for ep in self._by_id.values():
            if ep.at_name.lower() == lower:
                return ep
        return None

    def list(self, owner_user_id: str | None = None) -> list[AgentEndpoint]:
        items = list(self._by_id.values())
        if owner_user_id is not None:
            items = [e for e in items if e.owner_user_id == owner_user_id]
        return items

    def list_by_bridge(self, bridge_id: str) -> list[AgentEndpoint]:
        return [e for e in self._by_id.values() if e.bridge_id == bridge_id]

    def delete(self, endpoint_id: str) -> bool:
        with self._lock:
            return self._by_id.pop(endpoint_id, None) is not None


class MemoryBridgeStore(BridgeStore):

    def __init__(self) -> None:
        self._by_id: Dict[str, MachineBridge] = {}
        self._lock = threading.RLock()

    def upsert(self, bridge: MachineBridge) -> MachineBridge:
        with self._lock:
            self._by_id[bridge.bridge_id] = bridge
            return bridge

    def get(self, bridge_id: str) -> MachineBridge | None:
        return self._by_id.get(bridge_id)

    def list(self, owner_user_id: str | None = None) -> list[MachineBridge]:
        items = list(self._by_id.values())
        if owner_user_id is not None:
            items = [b for b in items if b.owner_user_id == owner_user_id]
        return items

    def delete(self, bridge_id: str) -> bool:
        with self._lock:
            return self._by_id.pop(bridge_id, None) is not None


class MemoryBridgeTokenStore(BridgeTokenStore):

    def __init__(self) -> None:
        self._by_token: Dict[str, BridgeToken] = {}
        self._lock = threading.RLock()

    def put(self, token: BridgeToken) -> BridgeToken:
        with self._lock:
            self._by_token[token.token] = token
            return token

    def get(self, token: str) -> BridgeToken | None:
        return self._by_token.get(token)


class MemoryCandidateStore(CandidateStore):

    def __init__(self) -> None:
        self._by_bridge: Dict[str, list[RuntimeCandidate]] = {}
        self._lock = threading.RLock()

    def replace_for_bridge(
        self,
        bridge_id: str,
        candidates: list[RuntimeCandidate] | tuple,
    ) -> list[RuntimeCandidate]:
        items = list(candidates)
        with self._lock:
            self._by_bridge[bridge_id] = items
            return items

    def list_for_bridge(self, bridge_id: str) -> list[RuntimeCandidate]:
        return list(self._by_bridge.get(bridge_id) or [])


class MemoryTimelineStore(TimelineStore):

    def __init__(self) -> None:
        self._msgs: list[TimelineMessage] = []
        self._lock = threading.RLock()

    def append(self, message: TimelineMessage) -> TimelineMessage:
        with self._lock:
            self._msgs.append(message)
            return message

    def list(
        self,
        project_id: str,
        *,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[TimelineMessage]:
        items = [m for m in self._msgs if m.project_id == project_id]
        if thread_id is not None:
            items = [m for m in items if m.thread_id == thread_id]
        return items[-limit:]


class MemoryArtifactStore(ArtifactStore):

    def __init__(self) -> None:
        self._meta: Dict[str, Artifact] = {}
        self._blob: Dict[str, bytes] = {}
        self._lock = threading.RLock()

    def put(self, artifact: Artifact, data: bytes | None = None) -> Artifact:
        with self._lock:
            if data is not None:
                self._blob[artifact.artifact_id] = data
                if not artifact.sha256:
                    artifact.sha256 = hashlib.sha256(data).hexdigest()
                artifact.size = len(data)
                if not artifact.storage_url:
                    artifact.storage_url = f'memory://{artifact.artifact_id}'
            self._meta[artifact.artifact_id] = artifact
            return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._meta.get(artifact_id)

    def get_bytes(self, artifact_id: str) -> bytes | None:
        return self._blob.get(artifact_id)


class MemoryTaskBoardStore(TaskBoardStore):

    def __init__(self) -> None:
        self._tasks: Dict[str, TeamTask] = {}
        self._lock = threading.RLock()

    def upsert(self, task: TeamTask) -> TeamTask:
        with self._lock:
            self._tasks[task.task_id] = task
            return task

    def get(self, task_id: str) -> TeamTask | None:
        return self._tasks.get(task_id)

    def list(self, project_id: str) -> list[TeamTask]:
        return [t for t in self._tasks.values() if t.project_id == project_id]


class MemoryProjectMetaStore(ProjectMetaStore):

    def __init__(self) -> None:
        self._items: Dict[str, TeamProjectMeta] = {}

    def upsert(self, project: TeamProjectMeta) -> TeamProjectMeta:
        self._items[project.project_id] = project
        return project

    def get(self, project_id: str) -> TeamProjectMeta | None:
        return self._items.get(project_id)

    def list(self) -> list[TeamProjectMeta]:
        return list(self._items.values())


class MemoryThreadBindingStore(ThreadBindingStore):

    def __init__(self) -> None:
        self._items: Dict[tuple[str, str], ThreadBinding] = {}

    def bind(self, binding: ThreadBinding) -> ThreadBinding:
        self._items[(binding.chat_id, binding.thread_id)] = binding
        return binding

    def get(self, chat_id: str, thread_id: str) -> ThreadBinding | None:
        return self._items.get((chat_id, thread_id))

    def list_all(self) -> list[ThreadBinding]:
        return list(self._items.values())


class MemoryPairTokenStore(PairTokenStore):

    def __init__(self) -> None:
        self._items: Dict[str, PairToken] = {}

    def put(self, token: PairToken) -> PairToken:
        self._items[token.pair_code] = token
        return token

    def get(self, pair_code: str) -> PairToken | None:
        return self._items.get(pair_code)

    def consume(self, pair_code: str) -> PairToken | None:
        tok = self._items.get(pair_code)
        if tok is None or tok.consumed:
            return None
        from ms_agent.team.token_utils import is_expired
        if is_expired(tok.expires_at):
            return None
        tok.consumed = True
        self._items[pair_code] = tok
        return tok


class MemoryEndpointTokenStore(EndpointTokenStore):

    def __init__(self) -> None:
        self._items: Dict[str, EndpointToken] = {}

    def put(self, token: EndpointToken) -> EndpointToken:
        self._items[token.token] = token
        return token

    def get(self, token: str) -> EndpointToken | None:
        tok = self._items.get(token)
        if tok is None:
            return None
        from ms_agent.team.token_utils import is_expired
        if is_expired(tok.expires_at):
            return None
        return tok


class MemorySessionBindingStore(SessionBindingStore):

    def __init__(self) -> None:
        self._items: Dict[str, SessionBinding] = {}
        self._lock = threading.RLock()

    def upsert(self, binding: SessionBinding) -> SessionBinding:
        with self._lock:
            binding.updated_at = binding.updated_at or binding.created_at
            self._items[binding.binding_id] = binding
            return binding

    def get(self, binding_id: str) -> SessionBinding | None:
        return self._items.get(binding_id)

    def get_active(
        self,
        endpoint_id: str,
        project_id: str,
        thread_id: str | None,
    ) -> SessionBinding | None:
        matches = [
            b for b in self._items.values()
            if b.endpoint_id == endpoint_id and b.project_id == project_id
            and b.thread_id == thread_id and b.status == 'active'
        ]
        if not matches:
            return None
        matches.sort(key=lambda b: b.updated_at, reverse=True)
        return matches[0]

    def list_for_endpoint(self, endpoint_id: str) -> list[SessionBinding]:
        return [
            b for b in self._items.values() if b.endpoint_id == endpoint_id
        ]

    def delete(self, binding_id: str) -> bool:
        with self._lock:
            return self._items.pop(binding_id, None) is not None
