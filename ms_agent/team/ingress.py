# Copyright (c) ModelScope Contributors. All rights reserved.
"""Message ingress + dispatch orchestration (platform post office)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Deque, Dict

from ms_agent.team.circuit import CircuitBreaker, fingerprint
from ms_agent.team.context import (
    ContextBundleAssembler,
    format_dispatch_receipt,
    format_done_receipt,
)
from ms_agent.team.errors import (
    CIRCUIT_OPEN,
    DISPATCH_REJECTED,
    ENDPOINT_DEGRADED,
    TeamError,
)
from ms_agent.team.events import TeamEvent
from ms_agent.team.models import (
    DispatchEnvelope,
    InboundMessage,
    TeamFeatureFlags,
    TeamTask,
    TimelineMessage,
    new_id,
)
from ms_agent.team.project_resolve import ProjectResolver
from ms_agent.team.router import AtMentionParser, AtRouter, RouteTarget
from ms_agent.team.session_dir import SessionDirectory
from ms_agent.team.stores.base import (
    EndpointStore,
    ProjectMetaStore,
    TaskBoardStore,
    ThreadBindingStore,
    TimelineStore,
)

logger = logging.getLogger(__name__)

EventSink = Callable[[TeamEvent], Awaitable[None] | None]
DispatchHandler = Callable[[DispatchEnvelope], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IngressResult:
    project_id: str | None = None
    needs_card: bool = False
    candidates: list[dict[str, str]] = field(default_factory=list)
    dispatches: list[DispatchEnvelope] = field(default_factory=list)
    events: list[TeamEvent] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None


class PerEndpointQueue:
    """Serialize dispatches per endpoint (F-Team-08-3)."""

    def __init__(self) -> None:
        self._queues: Dict[str, Deque[DispatchEnvelope]] = defaultdict(deque)
        self._running: set[str] = set()
        self._active: Dict[str, str] = {}  # endpoint_id → dispatch_id
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        envelope: DispatchEnvelope,
        handler: DispatchHandler,
    ) -> None:
        async with self._lock:
            if envelope.dispatch_id in self._cancelled:
                self._cancelled.discard(envelope.dispatch_id)
                return
            self._queues[envelope.target_endpoint_id].append(envelope)
            if envelope.target_endpoint_id in self._running:
                return
            self._running.add(envelope.target_endpoint_id)
        asyncio.create_task(
            self._drain(envelope.target_endpoint_id, handler))

    async def cancel(self, dispatch_id: str) -> str:
        """Cancel queued or mark active. Returns: queued|active|not_found."""
        async with self._lock:
            for endpoint_id, q in self._queues.items():
                kept = deque()
                removed = False
                while q:
                    env = q.popleft()
                    if env.dispatch_id == dispatch_id:
                        removed = True
                    else:
                        kept.append(env)
                self._queues[endpoint_id] = kept
                if removed:
                    return 'queued'
            for endpoint_id, active_id in self._active.items():
                if active_id == dispatch_id:
                    self._cancelled.add(dispatch_id)
                    return 'active'
            self._cancelled.add(dispatch_id)
            return 'not_found'

    def is_cancelled(self, dispatch_id: str) -> bool:
        return dispatch_id in self._cancelled

    async def _drain(self, endpoint_id: str, handler: DispatchHandler) -> None:
        while True:
            async with self._lock:
                q = self._queues[endpoint_id]
                if not q:
                    self._running.discard(endpoint_id)
                    self._active.pop(endpoint_id, None)
                    return
                env = q.popleft()
                if env.dispatch_id in self._cancelled:
                    self._cancelled.discard(env.dispatch_id)
                    continue
                self._active[endpoint_id] = env.dispatch_id
            try:
                await handler(env)
            except Exception:  # noqa: BLE001
                logger.exception(
                    'Dispatch failed for %s', env.dispatch_id)
            finally:
                async with self._lock:
                    self._cancelled.discard(env.dispatch_id)
                    if self._active.get(endpoint_id) == env.dispatch_id:
                        self._active.pop(endpoint_id, None)


def _lead_thread_messages(
    thread_msgs: list,
    *,
    default_lead_at: str | None,
) -> list:
    """Human turns for Lead: skip @mentions that were routed to other agents."""
    lead = (default_lead_at or '').lstrip('@').lower()
    out: list = []
    for item in thread_msgs or []:
        content = ''
        if isinstance(item, TimelineMessage):
            if item.sender_type != 'human':
                continue
            content = item.content or ''
        else:
            content = str(item)
        stripped = content.lstrip()
        if stripped.startswith('@'):
            name = stripped[1:].split()[0].split('\n')[0].lstrip('@').lower()
            if name and name != lead:
                continue
        out.append(item)
    return out


class MessageIngress:
    """Unified entry: channel message → route → context → enqueue."""

    def __init__(
        self,
        endpoint_store: EndpointStore,
        project_store: ProjectMetaStore,
        timeline_store: TimelineStore,
        binding_store: ThreadBindingStore | None = None,
        feature_flags: TeamFeatureFlags | None = None,
        event_sink: EventSink | None = None,
        session_directory: SessionDirectory | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        task_store: TaskBoardStore | None = None,
    ) -> None:
        self.endpoints = endpoint_store
        self.projects = project_store
        self.timeline = timeline_store
        self.bindings = binding_store
        self.flags = feature_flags or TeamFeatureFlags()
        self.event_sink = event_sink
        self.sessions = session_directory
        self.circuit = circuit_breaker or CircuitBreaker()
        self.tasks = task_store
        self.queue = PerEndpointQueue()
        self._handlers: Dict[str, DispatchHandler] = {}
        self._default_handler: DispatchHandler | None = None
        self._envelopes: Dict[str, DispatchEnvelope] = {}

    def set_dispatch_handler(
        self,
        handler: DispatchHandler,
        *,
        adapter_kind: str | None = None,
    ) -> None:
        if adapter_kind is None:
            self._default_handler = handler
        else:
            self._handlers[adapter_kind] = handler

    async def handle(self, msg: InboundMessage) -> IngressResult:
        try:
            return await self._handle(msg)
        except TeamError as exc:
            return IngressResult(error=exc.to_dict())

    async def _handle(self, msg: InboundMessage) -> IngressResult:
        if not msg.mentions:
            msg.mentions = AtMentionParser.parse(msg.content)

        projects = self.projects.list()
        bindings = self.bindings.list_all() if self.bindings else []
        resolver = ProjectResolver(projects, bindings)
        resolve = resolver.resolve(msg)
        if resolve.needs_card or not resolve.project_id:
            if msg.operation_kind == 'write' or resolve.needs_card:
                return IngressResult(
                    needs_card=True,
                    candidates=resolve.candidates,
                    project_id=resolve.project_id,
                )
            # read with no project — still try if lead has default
            pass

        project_id = resolve.project_id
        if not project_id and msg.operation_kind == 'write':
            return IngressResult(
                needs_card=True,
                candidates=resolve.candidates or [{
                    'project_id': p.project_id,
                    'name': p.name
                } for p in projects],
            )

        project_meta = self.projects.get(project_id) if project_id else None
        default_lead = project_meta.default_lead_at if project_meta else None

        by_at = {
            e.at_name: e
            for e in self.endpoints.list()
        }
        router = AtRouter(by_at, self.flags)
        targets = router.resolve_targets(
            msg, default_lead_at=default_lead, require_online=True)

        if not targets:
            raise TeamError(
                DISPATCH_REJECTED,
                'No @ mention and no default lead agent configured.',
                http_status=400,
            )

        # Persist inbound human message.
        if project_id:
            self.timeline.append(
                TimelineMessage(
                    message_id=msg.message_id or new_id('msg_'),
                    project_id=project_id,
                    sender_type='human',
                    sender_id=msg.sender_user_id,
                    sender_name=msg.sender_user_id,
                    content=msg.content,
                    channel=msg.channel,
                    thread_id=msg.thread_id,
                ))

        thread_msgs = []
        if project_id and msg.thread_id:
            thread_msgs = self.timeline.list(
                project_id, thread_id=msg.thread_id, limit=10)

        board_tasks = (
            self.tasks.list(project_id) if self.tasks and project_id else [])
        envelopes: list[DispatchEnvelope] = []
        events: list[TeamEvent] = []
        receipts: list[dict[str, Any]] = []

        for target in targets:
            prompt = (
                AtMentionParser.clause_for(
                    msg.content, target.endpoint.at_name)
                or msg.content)
            # Reject write to degraded endpoints (reads still allowed via policy later).
            if (msg.operation_kind == 'write'
                    and target.endpoint.status == 'degraded'):
                raise TeamError(
                    ENDPOINT_DEGRADED,
                    f'@{target.endpoint.at_name} is degraded; retry later.',
                    http_status=503,
                    details={'endpoint_id': target.endpoint.endpoint_id},
                )

            fp = fingerprint(
                project_id or '', target.endpoint.endpoint_id, prompt)
            if not self.circuit.allow(fp):
                ev = TeamEvent(
                    type='team.circuit_open',
                    project_id=project_id,
                    endpoint_id=target.endpoint.endpoint_id,
                    at_name=target.endpoint.at_name,
                    payload={'fingerprint': fp},
                )
                await self._emit(ev)
                raise TeamError(
                    CIRCUIT_OPEN,
                    'Too many recent failures for this task fingerprint.',
                    http_status=409,
                    details={'fingerprint': fp},
                )

            is_explicit = bool(msg.mentions or msg.target_at_name)
            audience = 'worker' if is_explicit else 'lead'
            lead_thread = None
            if audience == 'lead':
                lead_thread = _lead_thread_messages(
                    thread_msgs, default_lead_at=default_lead)
            bundle = ContextBundleAssembler.build(
                audience=audience,
                thread_messages=lead_thread,
                referenced_task_id=msg.referenced_task_id,
                task_snapshots=(
                    ContextBundleAssembler.snapshots_from_tasks(board_tasks)
                    if audience == 'lead' else None),
                task_inbox=(
                    ContextBundleAssembler.inbox_from_tasks(
                        board_tasks, at_name=target.endpoint.at_name)
                    if audience == 'worker' else None),
            )

            env = self._build_envelope(
                msg, target, project_id or '', prompt, bundle)
            self._envelopes[env.dispatch_id] = env
            envelopes.append(env)

            task = self._upsert_dispatch_task(env, prompt)
            if task:
                env.referenced_task_id = (
                    env.referenced_task_id or task.task_id)

            session_ev = TeamEvent(
                type='team.session',
                project_id=project_id,
                dispatch_id=env.dispatch_id,
                endpoint_id=env.target_endpoint_id,
                at_name=env.target_at_name,
                payload={
                    'session_mode': env.session_mode,
                    'runtime_session_id': env.runtime_session_id,
                    'session_resolution': env.session_resolution,
                },
            )
            events.append(session_ev)
            await self._emit(session_ev)

            receipt_text = format_dispatch_receipt(
                at_name=env.target_at_name,
                session_mode=env.session_mode,
                session_resolution=env.session_resolution,
            )
            receipt = {
                'kind': 'dispatch_receipt',
                'text': receipt_text,
                'dispatch_id': env.dispatch_id,
                'at_name': env.target_at_name,
                'session_mode': env.session_mode,
                'session_resolution': env.session_resolution,
                'runtime_session_id': env.runtime_session_id,
            }
            receipts.append(receipt)
            if project_id:
                self.timeline.append(
                    TimelineMessage(
                        message_id=new_id('msg_'),
                        project_id=project_id,
                        sender_type='system',
                        sender_id='team',
                        sender_name='system',
                        content=receipt_text,
                        channel=msg.channel,
                        thread_id=msg.thread_id,
                        endpoint_id=env.target_endpoint_id,
                        dispatch_id=env.dispatch_id,
                        meta=receipt,
                    ))

            ev = TeamEvent(
                type='team.dispatch_start',
                project_id=project_id,
                dispatch_id=env.dispatch_id,
                endpoint_id=env.target_endpoint_id,
                at_name=env.target_at_name,
                payload={
                    'prompt': env.prompt,
                    'session_mode': env.session_mode,
                    'runtime_session_id': env.runtime_session_id,
                    'receipt': receipt_text,
                    'task_id': env.referenced_task_id,
                },
            )
            events.append(ev)
            await self._emit(ev)
            await self._enqueue(env)

        return IngressResult(
            project_id=project_id,
            dispatches=envelopes,
            events=events,
            receipts=receipts,
        )

    def get_envelope(self, dispatch_id: str) -> DispatchEnvelope | None:
        return self._envelopes.get(dispatch_id)

    def record_dispatch_outcome(
        self,
        envelope: DispatchEnvelope,
        *,
        ok: bool,
        summary: str | None = None,
        error_code: str | None = None,
    ) -> None:
        del summary  # idle: transcript stays off the receipt / board
        fp = fingerprint(
            envelope.project_id, envelope.target_endpoint_id, envelope.prompt)
        if ok:
            self.circuit.record_success(fp)
        else:
            self.circuit.record_failure(fp)
        self.complete_dispatch(envelope, ok=ok, error_code=error_code)

    def complete_dispatch(
        self,
        envelope: DispatchEnvelope,
        *,
        ok: bool,
        summary: str = '',
        error_code: str | None = None,
    ) -> str:
        """Write an idle receipt + board status. No transcript, no LLM summary.

        ``summary`` is accepted for callers but never copied onto the
        timeline or ``result_summary``. Worker conclusions are opt-in via
        ``task_board_write``.
        """
        del summary  # idle notify: output stays on the private stream
        receipt_text = format_done_receipt(
            at_name=envelope.target_at_name,
            ok=ok,
            error_code=error_code,
        )
        if envelope.project_id:
            self.timeline.append(
                TimelineMessage(
                    message_id=new_id('msg_'),
                    project_id=envelope.project_id,
                    sender_type='system',
                    sender_id='team',
                    sender_name='system',
                    content=receipt_text,
                    channel=envelope.channel,
                    thread_id=envelope.thread_id,
                    endpoint_id=envelope.target_endpoint_id,
                    dispatch_id=envelope.dispatch_id,
                    meta={
                        'kind': 'dispatch_done_receipt',
                        'ok': ok,
                        'at_name': envelope.target_at_name,
                        'error_code': error_code,
                    },
                ))
        task_id = envelope.referenced_task_id
        if self.tasks and task_id:
            existing = self.tasks.get(task_id)
            if existing:
                existing.status = 'completed' if ok else 'failed'
                existing.last_dispatch_id = envelope.dispatch_id
                existing.updated_at = _now().isoformat()
                self.tasks.upsert(existing)
        return receipt_text

    def _upsert_dispatch_task(
        self,
        envelope: DispatchEnvelope,
        prompt: str,
    ) -> TeamTask | None:
        if self.tasks is None or not envelope.project_id:
            return None
        if envelope.referenced_task_id:
            existing = self.tasks.get(envelope.referenced_task_id)
            if existing:
                existing.status = 'in_progress'
                existing.last_dispatch_id = envelope.dispatch_id
                existing.target_endpoint_id = envelope.target_endpoint_id
                existing.target_at_name = envelope.target_at_name
                if envelope.thread_id and not existing.thread_id:
                    existing.thread_id = envelope.thread_id
                existing.updated_at = _now().isoformat()
                return self.tasks.upsert(existing)
        task = TeamTask(
            task_id=new_id('task_'),
            project_id=envelope.project_id,
            status='in_progress',
            prompt=prompt,
            trigger_user_id=envelope.sender_user_id,
            target_endpoint_id=envelope.target_endpoint_id,
            target_at_name=envelope.target_at_name,
            last_dispatch_id=envelope.dispatch_id,
            thread_id=envelope.thread_id,
        )
        return self.tasks.upsert(task)

    def _build_envelope(
        self,
        msg: InboundMessage,
        target: RouteTarget,
        project_id: str,
        prompt: str,
        bundle,
    ) -> DispatchEnvelope:
        # Prefer endpoint default project for read-only when unresolved.
        pid = project_id or target.endpoint.default_project_id or ''
        dispatch_id = new_id('d_')
        session_mode = 'fresh'
        runtime_session_id = dispatch_id
        session_resolution = 'created'
        if self.sessions is not None:
            resolved = self.sessions.resolve(
                endpoint_id=target.endpoint.endpoint_id,
                project_id=pid,
                thread_id=msg.thread_id,
                adapter_kind=target.endpoint.adapter_kind,
                mode_hint=msg.session_mode,
                dispatch_id=dispatch_id,
            )
            session_mode = resolved.session_mode
            runtime_session_id = resolved.runtime_session_id
            session_resolution = resolved.session_resolution
        return DispatchEnvelope(
            dispatch_id=dispatch_id,
            prompt=prompt,
            project_id=pid,
            target_endpoint_id=target.endpoint.endpoint_id,
            target_at_name=target.endpoint.at_name,
            sender_user_id=msg.sender_user_id,
            channel=msg.channel,
            thread_id=msg.thread_id,
            context_bundle=bundle,
            permission_tier=target.permission_tier,  # type: ignore[arg-type]
            caller_is_owner=target.caller_is_owner,
            referenced_task_id=msg.referenced_task_id,
            session_mode=session_mode,  # type: ignore[arg-type]
            runtime_session_id=runtime_session_id,
            session_resolution=session_resolution,  # type: ignore[arg-type]
            cancel_token=dispatch_id,
        )

    async def _enqueue(self, envelope: DispatchEnvelope) -> None:
        ep = self.endpoints.get(envelope.target_endpoint_id)
        adapter = ep.adapter_kind if ep else None
        handler = self._handlers.get(adapter or '') or self._default_handler
        if handler is None:
            raise TeamError(
                DISPATCH_REJECTED,
                'No dispatch handler registered.',
                http_status=500,
            )
        await self.queue.enqueue(envelope, handler)

    async def _emit(self, event: TeamEvent) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result


class EndpointRegistryService:
    """High-level endpoint registration helpers."""

    def __init__(
        self,
        store: EndpointStore,
        feature_flags: TeamFeatureFlags | None = None,
    ) -> None:
        self.store = store
        self.flags = feature_flags or TeamFeatureFlags()

    def register(self, endpoint) -> Any:
        from ms_agent.team.errors import AT_NAME_CONFLICT
        from ms_agent.team.models import AgentEndpoint

        assert isinstance(endpoint, AgentEndpoint)
        # Force phase-1 policy defaults onto persisted record when flag off.
        if not self.flags.remote_invoke_enabled:
            endpoint.remote_invoke_enabled = False
            # Keep user-supplied invoke_policy field for forward-compat,
            # but platform behavior still rejects non-owners.
        existing = self.store.get_by_at_name(endpoint.at_name)
        if existing and existing.endpoint_id != endpoint.endpoint_id:
            raise TeamError(
                AT_NAME_CONFLICT,
                f'@{endpoint.at_name} is already registered.',
                http_status=409,
                details={'existing_endpoint_id': existing.endpoint_id},
            )
        endpoint.updated_at = _now().isoformat()
        return self.store.upsert(endpoint)

    def heartbeat(
        self,
        endpoint_id: str,
        *,
        instance_id: str | None = None,
        status: str = 'online',
    ):
        ep = self.store.get(endpoint_id)
        if ep is None:
            return None
        ep.status = status  # type: ignore[assignment]
        if instance_id:
            ep.current_instance_id = instance_id
        ep.last_heartbeat = _now().isoformat()
        ep.updated_at = ep.last_heartbeat
        return self.store.upsert(ep)

    def issue_pair_token(self, owner_user_id: str, store, ttl_minutes: int = 60):
        from ms_agent.team.models import PairToken, new_secret_token
        code = new_secret_token('pair_')
        expires = (_now() + timedelta(minutes=ttl_minutes)).isoformat()
        tok = PairToken(
            pair_code=code,
            owner_user_id=owner_user_id,
            expires_at=expires,
        )
        return store.put(tok)

    def issue_endpoint_token(
        self,
        endpoint_id: str,
        owner_user_id: str,
        store,
        ttl_seconds: int = 86400,
    ):
        from ms_agent.team.models import EndpointToken, new_secret_token
        ep = self.store.get(endpoint_id)
        if ep is None:
            from ms_agent.team.errors import ENDPOINT_NOT_FOUND
            raise TeamError(ENDPOINT_NOT_FOUND, http_status=404)
        if ep.owner_user_id != owner_user_id:
            from ms_agent.team.errors import AGENT_OWNER_ONLY
            raise TeamError(AGENT_OWNER_ONLY, http_status=403)
        token = new_secret_token('etok_')
        expires = (_now() + timedelta(seconds=ttl_seconds)).isoformat()
        return store.put(
            EndpointToken(
                token=token,
                endpoint_id=endpoint_id,
                owner_user_id=owner_user_id,
                expires_at=expires,
            ))
