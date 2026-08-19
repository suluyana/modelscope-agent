# Copyright (c) ModelScope Contributors. All rights reserved.
"""Host Bridge multi-Agent model tests."""
from __future__ import annotations

import asyncio

import pytest

from ms_agent.team.models import (
    AgentEndpoint,
    ContextBundle,
    DispatchEnvelope,
    MachineBridge,
    new_id,
)
from ms_agent.team.stores.memory import (
    MemoryBridgeStore,
    MemoryBridgeTokenStore,
    MemoryCandidateStore,
    MemoryEndpointStore,
)
from team.ws_bridge_hub import BridgeHub


def test_agent_requires_bridge_id_field():
    ep = AgentEndpoint(
        endpoint_id='ep1',
        at_name='coder',
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='claude_code',
        adapter_kind='acp',
        bridge_id='br1',
        status='online',
    )
    d = ep.to_dict()
    assert d['bridge_id'] == 'br1'
    assert AgentEndpoint.from_dict(d).bridge_id == 'br1'


def test_list_by_bridge():
    store = MemoryEndpointStore()
    store.upsert(
        AgentEndpoint(
            endpoint_id='ep1',
            at_name='coder',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            bridge_id='br1',
        ))
    store.upsert(
        AgentEndpoint(
            endpoint_id='ep2',
            at_name='reviewer',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            bridge_id='br1',
        ))
    store.upsert(
        AgentEndpoint(
            endpoint_id='ep3',
            at_name='cloud',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            bridge_id=None,
        ))
    assert {e.at_name for e in store.list_by_bridge('br1')} == {
        'coder', 'reviewer'
    }


@pytest.mark.asyncio
async def test_hub_dispatch_resolves_bridge_id(monkeypatch):
    """Two agents on one bridge share the same WS connection key."""
    from team import state as state_mod

    state_mod.reset_team_state_for_tests()
    state = state_mod.get_team_state()
    state.bridges.upsert(
        MachineBridge(bridge_id='br1', owner_user_id='u1', status='online'))
    for eid, name in (('ep1', 'coder'), ('ep2', 'reviewer')):
        state.endpoints.upsert(
            AgentEndpoint(
                endpoint_id=eid,
                at_name=name,
                owner_user_id='u1',
                endpoint_type='persistent',
                runtime='claude_code',
                adapter_kind='acp',
                bridge_id='br1',
                status='online',
            ))

    hub = BridgeHub()
    sent = []

    class FakeWS:
        async def send_json(self, payload):
            sent.append(payload)

    await hub.register('br1', FakeWS())

    env1 = DispatchEnvelope(
        dispatch_id=new_id('d_'),
        prompt='hi',
        project_id='p1',
        target_endpoint_id='ep1',
        target_at_name='coder',
        sender_user_id='u1',
        channel='web',
        thread_id=None,
        context_bundle=ContextBundle(),
        permission_tier='owner',
        caller_is_owner=True,
    )
    # Complete immediately so wait doesn't hang.
    async def _complete():
        await asyncio.sleep(0.01)
        hub.complete_dispatch(env1.dispatch_id, {
            'ok': True,
            'dispatch_id': env1.dispatch_id,
            'summary': 'ok',
        })

    asyncio.create_task(_complete())
    result = await hub.dispatch(env1, timeout=2.0)
    assert result['ok'] is True
    assert sent and sent[0]['type'] == 'dispatch'
    assert sent[0]['envelope']['target_endpoint_id'] == 'ep1'

    env2 = DispatchEnvelope(
        dispatch_id=new_id('d_'),
        prompt='review',
        project_id='p1',
        target_endpoint_id='ep2',
        target_at_name='reviewer',
        sender_user_id='u1',
        channel='web',
        thread_id=None,
        context_bundle=ContextBundle(),
        permission_tier='owner',
        caller_is_owner=True,
    )

    async def _complete2():
        await asyncio.sleep(0.01)
        hub.complete_dispatch(env2.dispatch_id, {
            'ok': True,
            'dispatch_id': env2.dispatch_id,
        })

    asyncio.create_task(_complete2())
    result2 = await hub.dispatch(env2, timeout=2.0)
    assert result2['ok'] is True
    assert len(sent) == 2
    # Same bridge connection — both dispatches went out (one FakeWS).
    assert {s['envelope']['target_endpoint_id'] for s in sent} == {
        'ep1', 'ep2'
    }


def test_daemon_demux_unknown_agent():
    from ms_agent.bridge.daemon import AgentSlot, BridgeDaemon

    daemon = BridgeDaemon(
        api_base='http://127.0.0.1:8000',
        ws_url='ws://127.0.0.1:8000/api/v1/team/bridge',
        bridge_id='br1',
        owner_user_id='u1',
        agents=[
            AgentSlot(
                endpoint_id='ep1', at_name='coder', cwd='/tmp'),
        ],
        dry_run=True,
    )
    assert 'ep1' in daemon._agents  # noqa: SLF001
    assert daemon._agents.get('ep_missing') is None  # noqa: SLF001


def test_candidate_store_replace():
    from ms_agent.team.models import RuntimeCandidate

    store = MemoryCandidateStore()
    store.replace_for_bridge('br1', [
        RuntimeCandidate(
            candidate_id='c1',
            bridge_id='br1',
            runtime='claude_code',
            attachable=True,
        )
    ])
    assert len(store.list_for_bridge('br1')) == 1
    assert store.list_for_bridge('br2') == []


def test_resolve_ws_url_relative():
    from ms_agent.bridge.daemon import resolve_ws_url

    assert resolve_ws_url(
        'http://127.0.0.1:8000',
        '/api/v1/team/bridge') == 'ws://127.0.0.1:8000/api/v1/team/bridge'
    assert resolve_ws_url(
        'https://example.com',
        '') == 'wss://example.com/api/v1/team/bridge'
    assert resolve_ws_url(
        'http://127.0.0.1:8000',
        'ws://127.0.0.1:8000/api/v1/team/bridge'
    ) == 'ws://127.0.0.1:8000/api/v1/team/bridge'


def test_bind_agent_rebinds_same_owner():
    """Re-pair / enable must move existing @name onto the new bridge."""
    from team import state as state_mod
    from team.api_bridges import _bind_agent_to_bridge

    state_mod.reset_team_state_for_tests()
    state = state_mod.get_team_state()
    old = MachineBridge(
        bridge_id='br_old', owner_user_id='u1', status='offline')
    new = MachineBridge(
        bridge_id='br_new', owner_user_id='u1', status='offline')
    state.bridges.upsert(old)
    state.bridges.upsert(new)
    state.registry.register(
        AgentEndpoint(
            endpoint_id='ep_me',
            at_name='me',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            bridge_id='br_old',
            status='offline',
        ))
    saved = _bind_agent_to_bridge(
        state, bridge=new, at_name='me', status='online')
    assert saved.endpoint_id == 'ep_me'
    assert saved.bridge_id == 'br_new'
    assert saved.status == 'online'
    assert state.endpoints.get_by_at_name('me').bridge_id == 'br_new'


def test_pair_reuses_canonical_same_machine_bridge(monkeypatch):
    """Re-pair on the same machine reuses the online bridge — no dual online."""
    monkeypatch.setenv('MS_AGENT_TEAM_PERSIST', '0')
    from ms_agent.team.models import PairToken, new_secret_token
    from team import state as state_mod
    from team.api_bridges import pair_bridge, BridgePairRequest

    state_mod.reset_team_state_for_tests()
    state = state_mod.get_team_state()
    label = 'U-TEST-MAC'
    state.bridges.upsert(
        MachineBridge(
            bridge_id='br_live',
            owner_user_id='u1',
            machine_label=label,
            status='online',
            last_heartbeat='2099-01-02T00:00:00+00:00',
        ))
    state.registry.register(
        AgentEndpoint(
            endpoint_id='ep_bibo',
            at_name='bibo',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='codex',
            adapter_kind='acp',
            bridge_id='br_live',
            machine_label=label,
            status='online',
        ))
    state.bridges.upsert(
        MachineBridge(
            bridge_id='br_stale_offline',
            owner_user_id='u1',
            machine_label=label,
            status='offline',
        ))
    code = new_secret_token('pair_')
    state.pair_tokens.put(
        PairToken(
            pair_code=code,
            owner_user_id='u1',
            expires_at='2099-01-01T00:00:00+00:00'))

    out = pair_bridge(
        BridgePairRequest(
            pair_code=code,
            machine_label=label,
            # Client proposes a new id — server must ignore and reuse br_live.
            bridge_id='br_should_not_win',
        ))
    assert out['reused_bridge'] is True
    assert out['bridge']['bridge_id'] == 'br_live'
    assert out['bridge']['status'] == 'offline'  # until new WS connects
    assert set(out['retired_bridge_ids']) == {'br_stale_offline'}
    assert state.bridges.get('br_should_not_win') is None
    assert state.bridges.get('br_stale_offline') is None
    assert state.bridges.get('br_live') is not None
    assert state.endpoints.get_by_at_name('bibo').bridge_id == 'br_live'


def test_pair_retires_same_machine_bridges(monkeypatch):
    """New pair on the same machine_label deletes older bridges and migrates agents."""
    monkeypatch.setenv('MS_AGENT_TEAM_PERSIST', '0')
    from ms_agent.team.models import PairToken, new_secret_token
    from team import state as state_mod
    from team.api_bridges import pair_bridge, BridgePairRequest

    state_mod.reset_team_state_for_tests()
    state = state_mod.get_team_state()
    label = 'U-TEST-MAC'
    for i, bid in enumerate(('br_old1', 'br_old2')):
        state.bridges.upsert(
            MachineBridge(
                bridge_id=bid,
                owner_user_id='u1',
                machine_label=label,
                status='offline',
            ))
        state.registry.register(
            AgentEndpoint(
                endpoint_id=f'ep_{i}',
                at_name='bibo' if i == 0 else 'codex',
                owner_user_id='u1',
                endpoint_type='persistent',
                runtime='codex',
                adapter_kind='acp',
                bridge_id=bid,
                machine_label=label,
                status='offline',
            ))
    # Different machine must survive.
    state.bridges.upsert(
        MachineBridge(
            bridge_id='br_other',
            owner_user_id='u1',
            machine_label='other-host',
            status='offline',
        ))
    # Offline-only peers: newest offline becomes canonical and is reused.
    # (No online peer — first connect after a restart.)
    code = new_secret_token('pair_')
    state.pair_tokens.put(
        PairToken(pair_code=code, owner_user_id='u1', expires_at='2099-01-01T00:00:00+00:00'))

    out = pair_bridge(
        BridgePairRequest(
            pair_code=code,
            machine_label=label,
            bridge_id='br_client_new',
        ))
    # Reuses one of the existing same-machine bridges (latest updated).
    assert out['reused_bridge'] is True
    keep_id = out['bridge']['bridge_id']
    assert keep_id in ('br_old1', 'br_old2')
    assert keep_id != 'br_client_new'
    assert state.bridges.get('br_client_new') is None
    assert state.bridges.get('br_other') is not None
    assert state.bridges.get(keep_id) is not None
    assert state.endpoints.get_by_at_name('bibo').bridge_id == keep_id
    assert state.endpoints.get_by_at_name('codex').bridge_id == keep_id
    # The other same-machine offline bridge was retired.
    retired = set(out['retired_bridge_ids'])
    assert retired == ({'br_old1', 'br_old2'} - {keep_id})

def test_pair_skips_retire_when_machine_label_empty(monkeypatch):
    monkeypatch.setenv('MS_AGENT_TEAM_PERSIST', '0')
    from ms_agent.team.models import PairToken, new_secret_token
    from team import state as state_mod
    from team.api_bridges import pair_bridge, BridgePairRequest

    state_mod.reset_team_state_for_tests()
    state = state_mod.get_team_state()
    state.bridges.upsert(
        MachineBridge(bridge_id='br_keep', owner_user_id='u1', machine_label='',
                      status='offline'))
    code = new_secret_token('pair_')
    state.pair_tokens.put(
        PairToken(pair_code=code, owner_user_id='u1', expires_at='2099-01-01T00:00:00+00:00'))
    out = pair_bridge(
        BridgePairRequest(pair_code=code, machine_label='', bridge_id='br_new'))
    assert out['retired_bridge_ids'] == []
    assert state.bridges.get('br_keep') is not None
