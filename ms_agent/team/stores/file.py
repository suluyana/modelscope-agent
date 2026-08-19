# Copyright (c) ModelScope Contributors. All rights reserved.
"""File-backed stores for local / single-node Phase-1 deployments."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

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
from ms_agent.team.stores.memory import (
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


class FileEndpointStore(EndpointStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryEndpointStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('endpoints', []):
            self._mem.upsert(AgentEndpoint.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'endpoints': [e.to_dict() for e in self._mem.list()]}
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def upsert(self, endpoint: AgentEndpoint) -> AgentEndpoint:
        with self._lock:
            ep = self._mem.upsert(endpoint)
            self._save()
            return ep

    def get(self, endpoint_id: str) -> AgentEndpoint | None:
        return self._mem.get(endpoint_id)

    def get_by_at_name(self, at_name: str) -> AgentEndpoint | None:
        return self._mem.get_by_at_name(at_name)

    def list(self, owner_user_id: str | None = None) -> list[AgentEndpoint]:
        return self._mem.list(owner_user_id)

    def list_by_bridge(self, bridge_id: str) -> list[AgentEndpoint]:
        return self._mem.list_by_bridge(bridge_id)

    def delete(self, endpoint_id: str) -> bool:
        with self._lock:
            ok = self._mem.delete(endpoint_id)
            if ok:
                self._save()
            return ok


class FileBridgeStore(BridgeStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryBridgeStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('bridges', []):
            self._mem.upsert(MachineBridge.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'bridges': [b.to_dict() for b in self._mem.list()]}
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def upsert(self, bridge: MachineBridge) -> MachineBridge:
        with self._lock:
            b = self._mem.upsert(bridge)
            self._save()
            return b

    def get(self, bridge_id: str) -> MachineBridge | None:
        return self._mem.get(bridge_id)

    def list(self, owner_user_id: str | None = None) -> list[MachineBridge]:
        return self._mem.list(owner_user_id)

    def delete(self, bridge_id: str) -> bool:
        with self._lock:
            ok = self._mem.delete(bridge_id)
            if ok:
                self._save()
            return ok


class FileBridgeTokenStore(BridgeTokenStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryBridgeTokenStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('tokens', []):
            self._mem.put(BridgeToken.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'tokens': [t.to_dict() for t in self._mem._by_token.values()]  # noqa: SLF001
        }
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def put(self, token: BridgeToken) -> BridgeToken:
        with self._lock:
            tok = self._mem.put(token)
            self._save()
            return tok

    def get(self, token: str) -> BridgeToken | None:
        return self._mem.get(token)


class FileCandidateStore(CandidateStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryCandidateStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for bridge_id, items in (data.get('by_bridge') or {}).items():
            self._mem.replace_for_bridge(
                bridge_id, [RuntimeCandidate.from_dict(i) for i in items])

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        by_bridge = {
            bid: [c.to_dict() for c in cands]
            for bid, cands in self._mem._by_bridge.items()  # noqa: SLF001
        }
        payload = {'by_bridge': by_bridge}
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def replace_for_bridge(
        self,
        bridge_id: str,
        candidates,
    ) -> list[RuntimeCandidate]:
        with self._lock:
            items = self._mem.replace_for_bridge(bridge_id, candidates)
            self._save()
            return items

    def list_for_bridge(self, bridge_id: str) -> list[RuntimeCandidate]:
        return self._mem.list_for_bridge(bridge_id)


class FileTimelineStore(TimelineStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryTimelineStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                self._mem.append(TimelineMessage.from_dict(json.loads(line)))

    def append(self, message: TimelineMessage) -> TimelineMessage:
        with self._lock:
            msg = self._mem.append(message)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + '\n')
            return msg

    def list(
        self,
        project_id: str,
        *,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[TimelineMessage]:
        return self._mem.list(project_id, thread_id=thread_id, limit=limit)


class FileArtifactStore(ArtifactStore):

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._root / 'index.json'
        self._meta: dict[str, Artifact] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._meta_path.exists():
            return
        data = json.loads(self._meta_path.read_text(encoding='utf-8'))
        for item in data.get('artifacts', []):
            art = Artifact.from_dict(item)
            self._meta[art.artifact_id] = art

    def _save(self) -> None:
        payload = {'artifacts': [a.to_dict() for a in self._meta.values()]}
        tmp = self._meta_path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        tmp.rename(self._meta_path)

    def put(self, artifact: Artifact, data: bytes | None = None) -> Artifact:
        with self._lock:
            if data is not None:
                blob = self._root / 'blobs' / artifact.artifact_id
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(data)
                artifact.sha256 = hashlib.sha256(data).hexdigest()
                artifact.size = len(data)
                artifact.storage_url = str(blob)
            self._meta[artifact.artifact_id] = artifact
            self._save()
            return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._meta.get(artifact_id)

    def get_bytes(self, artifact_id: str) -> bytes | None:
        art = self._meta.get(artifact_id)
        if art is None:
            return None
        # Only read from controlled blob dir — ignore tainted storage_url paths.
        blob = (self._root / 'blobs' / artifact_id).resolve()
        root = self._root.resolve()
        if not str(blob).startswith(str(root) + os.sep) and blob != root:
            return None
        if blob.exists():
            return blob.read_bytes()
        return None


class FileTaskBoardStore(TaskBoardStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryTaskBoardStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('tasks', []):
            self._mem.upsert(TeamTask.from_dict(item))

    def _save(self) -> None:
        tasks = list(self._mem._tasks.values())  # noqa: SLF001
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'tasks': [t.to_dict() for t in tasks]
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def upsert(self, task: TeamTask) -> TeamTask:
        with self._lock:
            t = self._mem.upsert(task)
            self._save()
            return t

    def get(self, task_id: str) -> TeamTask | None:
        return self._mem.get(task_id)

    def list(self, project_id: str) -> list[TeamTask]:
        return self._mem.list(project_id)


class FileProjectMetaStore(ProjectMetaStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryProjectMetaStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('projects', []):
            self._mem.upsert(TeamProjectMeta.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'projects': [p.to_dict() for p in self._mem.list()]
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def upsert(self, project: TeamProjectMeta) -> TeamProjectMeta:
        with self._lock:
            p = self._mem.upsert(project)
            self._save()
            return p

    def get(self, project_id: str) -> TeamProjectMeta | None:
        return self._mem.get(project_id)

    def list(self) -> list[TeamProjectMeta]:
        return self._mem.list()


class FileThreadBindingStore(ThreadBindingStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryThreadBindingStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('bindings', []):
            self._mem.bind(ThreadBinding.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'bindings': [b.to_dict() for b in self._mem.list_all()]
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def bind(self, binding: ThreadBinding) -> ThreadBinding:
        with self._lock:
            b = self._mem.bind(binding)
            self._save()
            return b

    def get(self, chat_id: str, thread_id: str) -> ThreadBinding | None:
        return self._mem.get(chat_id, thread_id)

    def list_all(self) -> list[ThreadBinding]:
        return self._mem.list_all()


class FilePairTokenStore(PairTokenStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryPairTokenStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('tokens', []):
            self._mem.put(PairToken.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'tokens':
                [t.to_dict() for t in self._mem._items.values()]  # noqa: SLF001
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def put(self, token: PairToken) -> PairToken:
        with self._lock:
            t = self._mem.put(token)
            self._save()
            return t

    def get(self, pair_code: str) -> PairToken | None:
        return self._mem.get(pair_code)

    def consume(self, pair_code: str) -> PairToken | None:
        with self._lock:
            t = self._mem.consume(pair_code)
            if t:
                self._save()
            return t


class FileEndpointTokenStore(EndpointTokenStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemoryEndpointTokenStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('tokens', []):
            self._mem.put(EndpointToken.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'tokens':
                [t.to_dict() for t in self._mem._items.values()]  # noqa: SLF001
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def put(self, token: EndpointToken) -> EndpointToken:
        with self._lock:
            t = self._mem.put(token)
            self._save()
            return t

    def get(self, token: str) -> EndpointToken | None:
        return self._mem.get(token)


class FileSessionBindingStore(SessionBindingStore):

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem = MemorySessionBindingStore()
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding='utf-8'))
        for item in data.get('bindings', []):
            self._mem.upsert(SessionBinding.from_dict(item))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps({
                'bindings':
                [b.to_dict() for b in self._mem._items.values()]  # noqa: SLF001
            },
                       ensure_ascii=False,
                       indent=2),
            encoding='utf-8')
        tmp.rename(self._path)

    def upsert(self, binding: SessionBinding) -> SessionBinding:
        with self._lock:
            b = self._mem.upsert(binding)
            self._save()
            return b

    def get(self, binding_id: str) -> SessionBinding | None:
        return self._mem.get(binding_id)

    def get_active(
        self,
        endpoint_id: str,
        project_id: str,
        thread_id: str | None,
    ) -> SessionBinding | None:
        return self._mem.get_active(endpoint_id, project_id, thread_id)

    def list_for_endpoint(self, endpoint_id: str) -> list[SessionBinding]:
        return self._mem.list_for_endpoint(endpoint_id)

    def delete(self, binding_id: str) -> bool:
        with self._lock:
            ok = self._mem.delete(binding_id)
            if ok:
                self._save()
            return ok
