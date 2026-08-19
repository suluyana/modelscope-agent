# Copyright (c) ModelScope Contributors. All rights reserved.
"""Phase A: SessionDirectory, HealthMonitor, CircuitBreaker tests."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from ms_agent.team.circuit import CircuitBreaker, fingerprint
from ms_agent.team.errors import CIRCUIT_OPEN, SESSION_ATTACH_FAILED, TeamError
from ms_agent.team.health import HealthMonitor
from ms_agent.team.ingress import MessageIngress, PerEndpointQueue
from ms_agent.team.models import (
    AgentEndpoint,
    DispatchEnvelope,
    InboundMessage,
    MachineBridge,
    TeamProjectMeta,
    new_id,
)
from ms_agent.team.session_dir import SessionDirectory
from ms_agent.team.stores.memory import (
    MemoryBridgeStore,
    MemoryEndpointStore,
    MemoryProjectMetaStore,
    MemorySessionBindingStore,
    MemoryTimelineStore,
)


def _endpoint(**kwargs) -> AgentEndpoint:
    defaults = dict(
        endpoint_id='ep1',
        at_name='coder',
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='claude_code',
        adapter_kind='acp',
        status='online',
    )
    defaults.update(kwargs)
    return AgentEndpoint(**defaults)  # type: ignore[arg-type]


def test_session_directory_auto_then_attach():
    store = MemorySessionBindingStore()
    directory = SessionDirectory(store)
    first = directory.resolve(
        endpoint_id='ep1',
        project_id='p1',
        thread_id='t1',
        adapter_kind='acp',
        mode_hint='auto',
        dispatch_id='d1',
    )
    assert first.session_mode == 'fresh'
    assert first.session_resolution == 'created'
    assert first.runtime_session_id.startswith('sess_')

    second = directory.resolve(
        endpoint_id='ep1',
        project_id='p1',
        thread_id='t1',
        adapter_kind='acp',
        mode_hint='auto',
        dispatch_id='d2',
    )
    assert second.session_mode == 'attach'
    assert second.session_resolution == 'bound'
    assert second.runtime_session_id == first.runtime_session_id


def test_session_directory_fresh_rotates_binding():
    store = MemorySessionBindingStore()
    directory = SessionDirectory(store)
    a = directory.resolve(
        endpoint_id='ep1',
        project_id='p1',
        thread_id=None,
        adapter_kind='acp',
        mode_hint='fresh',
    )
    b = directory.resolve(
        endpoint_id='ep1',
        project_id='p1',
        thread_id=None,
        adapter_kind='acp',
        mode_hint='fresh',
    )
    assert a.runtime_session_id != b.runtime_session_id
    assert store.get(a.binding.binding_id).status == 'invalid'


def test_session_attach_fallback_error(monkeypatch):
    monkeypatch.setenv('MS_AGENT_SESSION_ATTACH_FALLBACK', 'error')
    store = MemorySessionBindingStore()
    directory = SessionDirectory(store)
    with pytest.raises(TeamError) as ei:
        directory.resolve(
            endpoint_id='ep1',
            project_id='p1',
            thread_id='t1',
            adapter_kind='acp',
            mode_hint='attach',
        )
    assert ei.value.code == SESSION_ATTACH_FAILED


def test_session_forced_fresh_when_attach_unsupported(monkeypatch):
    monkeypatch.setenv('MS_AGENT_SESSION_ATTACH_SUPPORTED', '0')
    store = MemorySessionBindingStore()
    directory = SessionDirectory(store)
    resolved = directory.resolve(
        endpoint_id='ep1',
        project_id='p1',
        thread_id='t1',
        adapter_kind='acp',
        mode_hint='attach',
    )
    assert resolved.session_resolution == 'forced_fresh'
    assert resolved.session_mode == 'fresh'


@pytest.mark.asyncio
async def test_health_monitor_age_transitions():
    endpoints = MemoryEndpointStore()
    ep = _endpoint()
    ep.last_heartbeat = (
        datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    endpoints.upsert(ep)
    events = []

    async def sink(ev):
        events.append(ev)

    monitor = HealthMonitor(
        endpoints,
        event_sink=sink,
        degraded_after_s=45,
        offline_after_s=120,
    )
    changed = await monitor.tick()
    assert changed
    assert endpoints.get('ep1').status == 'degraded'
    assert events[-1].type == 'endpoint.status'

    ep = endpoints.get('ep1')
    ep.last_heartbeat = (
        datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    endpoints.upsert(ep)
    await monitor.tick()
    assert endpoints.get('ep1').status == 'offline'
    # Sticky offline: forging a fresh last_heartbeat must not auto-revive;
    # only observe()/real bridge heartbeat does.
    ep = endpoints.get('ep1')
    ep.last_heartbeat = datetime.now(timezone.utc).isoformat()
    endpoints.upsert(ep)
    await monitor.tick()
    assert endpoints.get('ep1').status == 'offline'


@pytest.mark.asyncio
async def test_health_monitor_marks_stale_bridge_offline():
    endpoints = MemoryEndpointStore()
    bridges = MemoryBridgeStore()
    bridges.upsert(
        MachineBridge(
            bridge_id='br1',
            owner_user_id='u1',
            machine_label='mac',
            status='online',
            last_heartbeat=(
                datetime.now(timezone.utc) - timedelta(seconds=300)
            ).isoformat(),
        ))
    ep = _endpoint()
    ep.bridge_id = 'br1'
    ep.status = 'online'
    ep.last_heartbeat = (
        datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    endpoints.upsert(ep)

    monitor = HealthMonitor(
        endpoints,
        bridges=bridges,
        offline_after_s=120,
        degraded_after_s=45,
    )
    retired = monitor.reconcile_bridges()
    assert retired == ['br1']
    assert bridges.get('br1').status == 'offline'
    assert endpoints.get('ep1').status == 'offline'

    # Fresh heartbeat age alone must not revive a sticky-offline bridge.
    br = bridges.get('br1')
    br.last_heartbeat = datetime.now(timezone.utc).isoformat()
    bridges.upsert(br)
    assert monitor.reconcile_bridges() == []
    assert bridges.get('br1').status == 'offline'

    await monitor.observe('ep1', status='online')
    assert endpoints.get('ep1').status == 'online'


@pytest.mark.asyncio
async def test_offline_keeps_at_name_until_reclaimed():
    """No timed release — disconnected @name stays reserved for reconnect."""
    endpoints = MemoryEndpointStore()
    ep = _endpoint(bridge_id='br1', at_name='lily')
    now = datetime.now(timezone.utc)
    ep.status = 'offline'
    ep.last_heartbeat = (now - timedelta(seconds=600)).isoformat()
    ep.updated_at = (now - timedelta(seconds=600)).isoformat()
    endpoints.upsert(ep)

    monitor = HealthMonitor(endpoints, offline_after_s=120)
    await monitor.tick()
    assert endpoints.get_by_at_name('lily') is not None
    assert endpoints.get('ep1').status == 'offline'

    await monitor.observe('ep1', status='online')
    assert endpoints.get_by_at_name('lily').status == 'online'


@pytest.mark.asyncio
async def test_release_bridge_frees_at_name_for_rebind():
    from ms_agent.team.release import release_bridge_local_agents
    endpoints = MemoryEndpointStore()
    endpoints.upsert(_endpoint(bridge_id='br1', at_name='lily'))
    released = await release_bridge_local_agents(
        endpoints=endpoints, bridge_id='br1', reason='bridge_disconnect')
    assert released == ['lily']
    assert endpoints.get_by_at_name('lily') is None
    # Name can be claimed again.
    endpoints.upsert(
        _endpoint(endpoint_id='ep2', bridge_id='br2', at_name='lily'))
    assert endpoints.get_by_at_name('lily').endpoint_id == 'ep2'


def test_circuit_breaker_opens_and_cools():
    cb = CircuitBreaker(failure_threshold=3, window_s=60, cooldown_s=0.05)
    fp = fingerprint('p', 'e', 'same prompt')
    assert cb.allow(fp)
    assert cb.record_failure(fp) is False
    assert cb.record_failure(fp) is False
    assert cb.record_failure(fp) is True
    assert cb.allow(fp) is False
    import time
    time.sleep(0.06)
    assert cb.allow(fp) is True  # half-open probe
    cb.record_success(fp)
    assert cb.allow(fp) is True


@pytest.mark.asyncio
async def test_per_endpoint_queue_cancel_queued():
    q = PerEndpointQueue()
    started = asyncio.Event()
    released = asyncio.Event()
    seen = []

    async def handler(env: DispatchEnvelope):
        seen.append(env.dispatch_id)
        started.set()
        await released.wait()

    def _env(did: str) -> DispatchEnvelope:
        from ms_agent.team.models import ContextBundle
        return DispatchEnvelope(
            dispatch_id=did,
            prompt='x',
            project_id='p',
            target_endpoint_id='ep1',
            target_at_name='coder',
            sender_user_id='u1',
            channel='web',
            thread_id=None,
            context_bundle=ContextBundle(),
            permission_tier='owner',
            caller_is_owner=True,
            runtime_session_id='sess_1',
        )

    await q.enqueue(_env('d1'), handler)
    await started.wait()
    await q.enqueue(_env('d2'), handler)
    where = await q.cancel('d2')
    assert where == 'queued'
    released.set()
    await asyncio.sleep(0.05)
    assert 'd2' not in seen


@pytest.mark.asyncio
async def test_ingress_sets_runtime_session_id():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    sessions = SessionDirectory(MemorySessionBindingStore())
    ep = _endpoint(status='online')
    endpoints.upsert(ep)
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='coder'))

    handled = []

    async def handler(env: DispatchEnvelope):
        handled.append(env)

    ingress = MessageIngress(
        endpoints,
        projects,
        timeline,
        session_directory=sessions,
    )
    ingress.set_dispatch_handler(handler)

    msg = InboundMessage(
        message_id=new_id('msg_'),
        sender_user_id='u1',
        content='@coder hello',
        channel='web',
        project_id='p1',
        thread_id='t1',
        session_mode='auto',
    )
    result = await ingress.handle(msg)
    assert result.error is None
    assert len(result.dispatches) == 1
    env = result.dispatches[0]
    assert env.runtime_session_id
    assert env.runtime_session_id != env.dispatch_id
    assert env.session_mode in ('fresh', 'attach')
    await asyncio.sleep(0.05)
    assert handled and handled[0].runtime_session_id == env.runtime_session_id

    # Second message attaches.
    msg2 = InboundMessage(
        message_id=new_id('msg_'),
        sender_user_id='u1',
        content='@coder again',
        channel='web',
        project_id='p1',
        thread_id='t1',
        session_mode='auto',
    )
    result2 = await ingress.handle(msg2)
    assert result2.dispatches[0].session_mode == 'attach'
    assert result2.dispatches[0].runtime_session_id == env.runtime_session_id


@pytest.mark.asyncio
async def test_ingress_circuit_open():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    cb = CircuitBreaker(failure_threshold=2, window_s=60, cooldown_s=600)
    endpoints.upsert(_endpoint(status='online'))
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='coder'))
    ingress = MessageIngress(
        endpoints,
        projects,
        timeline,
        session_directory=SessionDirectory(MemorySessionBindingStore()),
        circuit_breaker=cb,
    )

    async def handler(env):
        return None

    ingress.set_dispatch_handler(handler)
    fp = fingerprint('p1', 'ep1', 'boom')
    cb.record_failure(fp)
    cb.record_failure(fp)

    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@coder boom',
            channel='web',
            project_id='p1',
        ))
    assert result.error and result.error['error'] == CIRCUIT_OPEN
