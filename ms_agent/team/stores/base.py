# Copyright (c) ModelScope Contributors. All rights reserved.
"""Store abstractions for Agent Team persistence."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

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


class EndpointStore(ABC):

    @abstractmethod
    def upsert(self, endpoint: AgentEndpoint) -> AgentEndpoint:
        ...

    @abstractmethod
    def get(self, endpoint_id: str) -> AgentEndpoint | None:
        ...

    @abstractmethod
    def get_by_at_name(self, at_name: str) -> AgentEndpoint | None:
        ...

    @abstractmethod
    def list(self, owner_user_id: str | None = None) -> list[AgentEndpoint]:
        ...

    @abstractmethod
    def list_by_bridge(self, bridge_id: str) -> list[AgentEndpoint]:
        ...

    @abstractmethod
    def delete(self, endpoint_id: str) -> bool:
        ...


class BridgeStore(ABC):

    @abstractmethod
    def upsert(self, bridge: MachineBridge) -> MachineBridge:
        ...

    @abstractmethod
    def get(self, bridge_id: str) -> MachineBridge | None:
        ...

    @abstractmethod
    def list(self, owner_user_id: str | None = None) -> list[MachineBridge]:
        ...

    @abstractmethod
    def delete(self, bridge_id: str) -> bool:
        ...


class BridgeTokenStore(ABC):

    @abstractmethod
    def put(self, token: BridgeToken) -> BridgeToken:
        ...

    @abstractmethod
    def get(self, token: str) -> BridgeToken | None:
        ...


class CandidateStore(ABC):
    """Runtime candidates reported by a MachineBridge."""

    @abstractmethod
    def replace_for_bridge(
        self,
        bridge_id: str,
        candidates: Sequence[RuntimeCandidate],
    ) -> list[RuntimeCandidate]:
        ...

    @abstractmethod
    def list_for_bridge(self, bridge_id: str) -> list[RuntimeCandidate]:
        ...


class TimelineStore(ABC):

    @abstractmethod
    def append(self, message: TimelineMessage) -> TimelineMessage:
        ...

    @abstractmethod
    def list(
        self,
        project_id: str,
        *,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[TimelineMessage]:
        ...


class ArtifactStore(ABC):

    @abstractmethod
    def put(self, artifact: Artifact, data: bytes | None = None) -> Artifact:
        ...

    @abstractmethod
    def get(self, artifact_id: str) -> Artifact | None:
        ...

    @abstractmethod
    def get_bytes(self, artifact_id: str) -> bytes | None:
        ...


class TaskBoardStore(ABC):

    @abstractmethod
    def upsert(self, task: TeamTask) -> TeamTask:
        ...

    @abstractmethod
    def get(self, task_id: str) -> TeamTask | None:
        ...

    @abstractmethod
    def list(self, project_id: str) -> list[TeamTask]:
        ...


class ProjectMetaStore(ABC):

    @abstractmethod
    def upsert(self, project: TeamProjectMeta) -> TeamProjectMeta:
        ...

    @abstractmethod
    def get(self, project_id: str) -> TeamProjectMeta | None:
        ...

    @abstractmethod
    def list(self) -> list[TeamProjectMeta]:
        ...


class ThreadBindingStore(ABC):

    @abstractmethod
    def bind(self, binding: ThreadBinding) -> ThreadBinding:
        ...

    @abstractmethod
    def get(self, chat_id: str, thread_id: str) -> ThreadBinding | None:
        ...

    @abstractmethod
    def list_all(self) -> list[ThreadBinding]:
        ...


class PairTokenStore(ABC):

    @abstractmethod
    def put(self, token: PairToken) -> PairToken:
        ...

    @abstractmethod
    def get(self, pair_code: str) -> PairToken | None:
        ...

    @abstractmethod
    def consume(self, pair_code: str) -> PairToken | None:
        ...


class EndpointTokenStore(ABC):

    @abstractmethod
    def put(self, token: EndpointToken) -> EndpointToken:
        ...

    @abstractmethod
    def get(self, token: str) -> EndpointToken | None:
        ...


class SessionBindingStore(ABC):
    """Persist SessionBinding for attach/fresh resolution."""

    @abstractmethod
    def upsert(self, binding: SessionBinding) -> SessionBinding:
        ...

    @abstractmethod
    def get(self, binding_id: str) -> SessionBinding | None:
        ...

    @abstractmethod
    def get_active(
        self,
        endpoint_id: str,
        project_id: str,
        thread_id: str | None,
    ) -> SessionBinding | None:
        ...

    @abstractmethod
    def list_for_endpoint(self, endpoint_id: str) -> list[SessionBinding]:
        ...

    @abstractmethod
    def delete(self, binding_id: str) -> bool:
        ...
