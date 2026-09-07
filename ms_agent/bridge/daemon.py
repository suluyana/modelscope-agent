# Copyright (c) ModelScope Contributors. All rights reserved.
"""Host Bridge daemon: one sidecar per machine, many Agents."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ms_agent.bridge.adapters.base import BridgeEvent
from ms_agent.bridge.adapters.factory import make_adapter
from ms_agent.bridge.discovery import discover_runtimes
from ms_agent.bridge.git_snapshot import collect_git_snapshot
from ms_agent.bridge.pair import pair_bridge_with_platform, register_agent_on_bridge
from ms_agent.bridge.permission import assert_dispatch_allowed
from ms_agent.bridge.queue import BridgeDispatchQueue
from ms_agent.bridge.ws_client import BridgeWSClient
from ms_agent.team.context import ContextBundleAssembler
from ms_agent.team.models import DispatchEnvelope

logger = logging.getLogger(__name__)


@dataclass
class AgentSlot:
    endpoint_id: str
    at_name: str
    runtime: str = 'claude_code'
    adapter_kind: str = 'acp'
    cwd: str | None = None
    adapter: Any = None


@dataclass
class PendingPermission:
    permission_request_id: str
    fingerprint: str
    dispatch_id: str
    endpoint_id: str
    runtime_session_id: str
    options: list[dict[str, Any]]
    future: asyncio.Future
    resolution_reason: str = 'resolved'


class BridgeDaemon:
    """Connects to platform WS as a MachineBridge; demuxes to local Agents."""

    def __init__(
        self,
        *,
        api_base: str,
        ws_url: str,
        bridge_id: str,
        owner_user_id: str,
        agents: list[AgentSlot] | None = None,
        machine_label: str = '',
        dry_run: bool = False,
        bridge_token: str | None = None,
        default_cwd: str | None = None,
        permission_timeout: float | None = None,
    ) -> None:
        self.api_base = api_base
        self.ws_url = ws_url
        self.bridge_id = bridge_id
        self.owner_user_id = owner_user_id
        self.machine_label = machine_label
        self.default_cwd = default_cwd or os.getcwd()
        self.instance_id = uuid.uuid4().hex
        self.queue = BridgeDispatchQueue()
        self.dry_run = dry_run
        configured_permission_timeout = (
            float(permission_timeout)
            if permission_timeout is not None
            else float(os.environ.get(
                'MS_AGENT_BRIDGE_PERMISSION_TIMEOUT', '240')))
        self.permission_timeout = min(
            max(configured_permission_timeout, 0.1), 290.0)
        self._bridge_token = bridge_token
        self._ws: BridgeWSClient | None = None
        self._agents: dict[str, AgentSlot] = {}
        self._pending_permissions: dict[str, PendingPermission] = {}
        self._resolved_permissions: set[str] = set()
        self._terminal_dispatches: set[str] = set()
        self._permission_state_lock = asyncio.Lock()
        self._interrupted_dispatches: dict[str, dict[str, Any]] = {}
        for slot in agents or []:
            if slot.adapter is None:
                slot.adapter = make_adapter(slot.runtime, dry_run=dry_run)
            if not slot.cwd:
                slot.cwd = self.default_cwd
            self._agents[slot.endpoint_id] = slot

    async def start(self) -> None:
        headers = {}
        if self._bridge_token:
            headers['Authorization'] = f'Bearer {self._bridge_token}'
        ws_url = self.ws_url
        if self._bridge_token and 'token=' not in ws_url:
            sep = '&' if '?' in ws_url else '?'
            ws_url = f'{ws_url}{sep}token={self._bridge_token}'

        async def on_message(msg: dict[str, Any]) -> None:
            await self._on_message(msg)

        self._ws = BridgeWSClient(
            ws_url,
            on_message=on_message,
            headers=headers,
            on_connect=self._on_ws_connect,
            on_disconnect=self._on_ws_disconnect,
        )

        async def _after_connect_heartbeat():
            for _ in range(50):
                if self._ws and self._ws._ws is not None:  # noqa: SLF001
                    await self._send_heartbeat()
                    return
                await asyncio.sleep(0.2)

        asyncio.create_task(_after_connect_heartbeat())
        asyncio.create_task(self._heartbeat_loop())
        try:
            await self._ws.run_forever()
        finally:
            from ms_agent.bridge.adapters.acp_client import get_acp_pool
            await get_acp_pool().close_all()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self._send_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.debug('heartbeat failed: %s', exc)
            await asyncio.sleep(20)

    async def _send_heartbeat(self) -> None:
        # Pick up Agents enabled from the UI since the last beat.
        try:
            await self._refresh_slots_from_api()
        except Exception:  # noqa: BLE001
            logger.debug('pre-heartbeat slot refresh failed', exc_info=True)
        candidates = await discover_runtimes(dry_run=self.dry_run)
        await self._send({
            'type': 'heartbeat',
            'bridge_id': self.bridge_id,
            'instance_id': self.instance_id,
            'status': 'online',
            'agents': [
                {
                    'endpoint_id': s.endpoint_id,
                    'at_name': s.at_name,
                    'status': 'online',
                    'instance_id': self.instance_id,
                }
                for s in self._agents.values()
            ],
            'candidates': [
                {
                    'candidate_id':
                    c.get('candidate_id') or f"cand_{c['runtime']}",
                    'runtime':
                    c['runtime'],
                    'adapter_kind':
                    c['adapter_kind'],
                    'label':
                    c.get('label') or c['runtime'],
                    'cwd':
                    c.get('cwd'),
                    'attachable':
                    bool(c.get('attachable', c.get('available'))),
                    'runtime_session_id':
                    c.get('runtime_session_id'),
                    'meta':
                    c.get('meta') or {},
                }
                for c in candidates
                if c.get('available')
            ],
        })

    async def _on_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get('type')
        if mtype == 'dispatch':
            envelope = DispatchEnvelope.from_dict(msg['envelope'])
            self._terminal_dispatches.discard(envelope.dispatch_id)
            logger.info(
                'Dispatch %s → @%s (%s)',
                envelope.dispatch_id,
                envelope.target_at_name,
                envelope.target_endpoint_id,
            )
            await self.queue.enqueue(envelope, self._handle_dispatch)
        elif mtype == 'cancel':
            dispatch_id = msg.get('dispatch_id') or ''
            session_id = msg.get('runtime_session_id') or dispatch_id
            async with self._permission_state_lock:
                self._mark_dispatch_terminal(dispatch_id)
                self._deny_permissions_for_dispatch(
                    dispatch_id, reason='cancelled')
            # Best-effort: cancel on all adapters.
            for slot in self._agents.values():
                if slot.adapter is not None:
                    await slot.adapter.cancel(session_id)
            await self._send({
                'type': 'dispatch_done',
                'dispatch_id': dispatch_id,
                'ok': False,
                'code': 'cancelled',
                'summary': 'cancelled',
                'artifacts': [],
            })
        elif mtype == 'permission_resolve':
            await self._resolve_permission(msg)
        elif mtype in ('revoke', 'policy_update', 'remote_profile_change'):
            logger.info('Received frame: %s', mtype)
        elif mtype == 'registered':
            logger.info('Received frame: registered')
            agents = msg.get('agents') or []
            if agents:
                self._sync_agent_slots(agents)
            # Platform accepted the socket — push presence immediately.
            try:
                await self._send_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.debug('post-register heartbeat failed: %s', exc)
        elif mtype == 'agents_updated':
            agents = msg.get('agents') or []
            if agents:
                self._sync_agent_slots(agents)
            try:
                await self._send_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.debug('post-agents_updated heartbeat failed: %s', exc)
        else:
            logger.debug('Unknown frame: %s', mtype)

    def _sync_agent_slots(self, agents: list[dict[str, Any]]) -> None:
        """Merge platform Agent rows into local demux slots (UI enable path)."""
        for row in agents:
            eid = row.get('endpoint_id')
            at_name = (row.get('at_name') or '').lstrip('@')
            if not eid or not at_name:
                continue
            runtime = row.get('runtime') or 'claude_code'
            existing = self._agents.get(eid)
            if existing is not None:
                existing.at_name = at_name
                if existing.runtime != runtime:
                    existing.runtime = runtime
                    existing.adapter = make_adapter(
                        runtime, dry_run=self.dry_run)
                continue
            # Drop stale slot with same @name but different endpoint_id (rebind).
            for old_eid, slot in list(self._agents.items()):
                if slot.at_name == at_name and old_eid != eid:
                    self._agents.pop(old_eid, None)
            self._agents[eid] = AgentSlot(
                endpoint_id=eid,
                at_name=at_name,
                runtime=runtime,
                adapter_kind=row.get('adapter_kind') or 'acp',
                cwd=self.default_cwd,
                adapter=make_adapter(runtime, dry_run=self.dry_run),
            )
            logger.info(
                'Synced local slot @%s (%s) runtime=%s', at_name, eid, runtime)

    def _resolve_slot(self, envelope: DispatchEnvelope):
        slot = self._agents.get(envelope.target_endpoint_id)
        if slot is not None:
            return slot
        # Fallback: match by @name after UI enable / rebind.
        want = (envelope.target_at_name or '').lstrip('@')
        if not want:
            return None
        for slot in self._agents.values():
            if slot.at_name == want:
                return slot
        return None

    async def _refresh_slots_from_api(self) -> None:
        """Pull Agent list from control plane (UI may have enabled after pair)."""
        import asyncio
        import json
        import urllib.request

        url = (
            self.api_base.rstrip('/')
            + f'/api/v1/team/bridges/{self.bridge_id}')

        def _fetch() -> dict[str, Any]:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read().decode())

        try:
            data = await asyncio.to_thread(_fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning('refresh slots failed: %s', exc)
            return
        agents = data.get('agents') or []
        if agents:
            self._sync_agent_slots(agents)

    async def _handle_dispatch(self, envelope: DispatchEnvelope) -> None:
        try:
            assert_dispatch_allowed(envelope)
        except Exception as exc:  # noqa: BLE001
            await self._send({
                'type': 'dispatch_done',
                'dispatch_id': envelope.dispatch_id,
                'endpoint_id': envelope.target_endpoint_id,
                'ok': False,
                'code': 'forbidden',
                'summary': str(exc),
                'artifacts': [],
            })
            return

        slot = self._resolve_slot(envelope)
        if slot is None:
            await self._refresh_slots_from_api()
            slot = self._resolve_slot(envelope)
        if slot is None:
            await self._send({
                'type': 'dispatch_done',
                'dispatch_id': envelope.dispatch_id,
                'endpoint_id': envelope.target_endpoint_id,
                'ok': False,
                'code': 'endpoint_not_found',
                'summary': (
                    f'No local agent slot for {envelope.target_endpoint_id} '
                    f'(@{envelope.target_at_name}) on this bridge'),
                'artifacts': [],
            })
            return

        bundle = envelope.context_bundle
        cwd = slot.cwd or self.default_cwd
        if bundle.git_snapshot is None:
            bundle = replace(bundle, git_snapshot=collect_git_snapshot(cwd))
        prompt = ContextBundleAssembler.merge_prompt(envelope.prompt, bundle)
        session_id = envelope.runtime_session_id or envelope.dispatch_id

        summary_parts: list[str] = []
        had_error = False

        async def _permission_handler(
                request: dict[str, Any]) -> dict[str, Any]:
            return await self._forward_permission_request(
                envelope, slot, request)

        try:
            async for event in slot.adapter.execute(
                    prompt=prompt,
                    session_id=session_id,
                    permission_tier=envelope.permission_tier,
                    cwd=cwd,
                    session_mode=envelope.session_mode,
                    permission_handler=_permission_handler,
            ):
                await self._send({
                    'type': 'stream_event',
                    'dispatch_id': envelope.dispatch_id,
                    'endpoint_id': envelope.target_endpoint_id,
                    'event': event.to_dict() if isinstance(event, BridgeEvent)
                    else event,
                })
                if getattr(event, 'type', None) == 'text' and getattr(
                        event, 'content', None):
                    summary_parts.append(event.content)
                if getattr(event, 'type', None) == 'error':
                    had_error = True
        except Exception as exc:  # noqa: BLE001
            await self._send({
                'type': 'dispatch_done',
                'dispatch_id': envelope.dispatch_id,
                'endpoint_id': envelope.target_endpoint_id,
                'ok': False,
                'code': 'session_attach_failed'
                if envelope.session_mode == 'attach' else 'internal',
                'summary': str(exc),
                'artifacts': [],
            })
            return
        finally:
            self._deny_permissions_for_dispatch(
                envelope.dispatch_id, reason='turn_ended')

        await self._send({
            'type': 'dispatch_done',
            'dispatch_id': envelope.dispatch_id,
            'endpoint_id': envelope.target_endpoint_id,
            'ok': not had_error,
            'code': 'internal' if had_error else None,
            'summary': '\n'.join(summary_parts)[-8000:],
            'artifacts': [],
        })

    async def _forward_permission_request(
        self,
        envelope: DispatchEnvelope,
        slot: AgentSlot,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward one ACP request and keep its JSON-RPC call pending."""
        if envelope.dispatch_id in self._terminal_dispatches:
            return {'outcome': {'outcome': 'cancelled'}}
        options = [
            dict(option) for option in (request.get('options') or [])
            if isinstance(option, dict)
        ]
        fingerprint_payload = {
            'bridge_id': self.bridge_id,
            'endpoint_id': envelope.target_endpoint_id,
            'dispatch_id': envelope.dispatch_id,
            'runtime_session_id': request.get('runtime_session_id') or '',
            'call_id': request.get('call_id') or '',
            'tool': request.get('tool') or 'tool',
            'args': request.get('args') or {},
            'options': options,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(',', ':'),
                default=str,
            ).encode('utf-8')).hexdigest()
        permission_request_id = f'perm_{uuid.uuid4().hex}'
        future = asyncio.get_running_loop().create_future()
        pending = PendingPermission(
            permission_request_id=permission_request_id,
            fingerprint=fingerprint,
            dispatch_id=envelope.dispatch_id,
            endpoint_id=envelope.target_endpoint_id,
            runtime_session_id=request.get('runtime_session_id') or '',
            options=options,
            future=future,
        )
        self._pending_permissions[permission_request_id] = pending
        frame = {
            'type': 'permission_request',
            'permission_request_id': permission_request_id,
            'bridge_id': self.bridge_id,
            'endpoint_id': envelope.target_endpoint_id,
            'dispatch_id': envelope.dispatch_id,
            'runtime_session_id': pending.runtime_session_id,
            'call_id': request.get('call_id') or '',
            'tool': request.get('tool') or 'tool',
            'args': request.get('args') or {},
            'options': options,
            'fingerprint': fingerprint,
        }
        if not await self._send(frame):
            self._pending_permissions.pop(permission_request_id, None)
            raise RuntimeError('permission continuation_required: bridge offline')
        await self._send({
            'type': 'dispatch_waiting_approval',
            'dispatch_id': envelope.dispatch_id,
            'endpoint_id': envelope.target_endpoint_id,
            'permission_request_id': permission_request_id,
            'status': 'waiting_approval',
        })
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.permission_timeout,
            )
        except asyncio.TimeoutError:
            pending.resolution_reason = 'timeout'
            result = {'outcome': {'outcome': 'cancelled'}}
            self._resolved_permissions.add(permission_request_id)
            if not future.done():
                future.set_result(result)
        finally:
            self._pending_permissions.pop(permission_request_id, None)
        if envelope.dispatch_id in self._terminal_dispatches:
            pending.resolution_reason = 'cancelled'
            result = {'outcome': {'outcome': 'cancelled'}}
        await self._send({
            'type': 'permission_resolved',
            'permission_request_id': permission_request_id,
            'dispatch_id': envelope.dispatch_id,
            'endpoint_id': envelope.target_endpoint_id,
            'fingerprint': fingerprint,
            'decision': 'allow'
            if result.get('outcome', {}).get('outcome') == 'selected'
            else 'deny',
            'option_id': result.get('outcome', {}).get('optionId'),
            'reason': pending.resolution_reason,
        })
        async with self._permission_state_lock:
            if (pending.resolution_reason not in ('cancelled', 'turn_ended')
                    and envelope.dispatch_id not in self._terminal_dispatches):
                await self._send({
                    'type': 'dispatch_resumed',
                    'dispatch_id': envelope.dispatch_id,
                    'endpoint_id': envelope.target_endpoint_id,
                    'permission_request_id': permission_request_id,
                    'status': 'in_progress',
                })
        if envelope.dispatch_id in self._terminal_dispatches:
            return {'outcome': {'outcome': 'cancelled'}}
        return result

    async def _resolve_permission(self, msg: dict[str, Any]) -> bool:
        """Resolve a pending request once; duplicate WS deliveries are no-ops."""
        request_id = str(msg.get('permission_request_id') or '')
        if not request_id or request_id in self._resolved_permissions:
            return False
        pending = self._pending_permissions.get(request_id)
        if pending is None or pending.future.done():
            return False
        supplied_fingerprint = str(msg.get('fingerprint') or '')
        if supplied_fingerprint and supplied_fingerprint != pending.fingerprint:
            logger.warning('Ignoring permission fingerprint mismatch: %s',
                           request_id)
            return False
        decision = str(msg.get('decision') or '').lower()
        if decision == 'allow':
            option_id = self._select_allow_option(
                pending.options,
                str(msg.get('option_id') or msg.get('optionId') or ''),
            )
            if option_id is None:
                result = {'outcome': {'outcome': 'cancelled'}}
            else:
                result = {
                    'outcome': {
                        'outcome': 'selected',
                        'optionId': option_id,
                    }
                }
        elif decision == 'deny':
            result = {'outcome': {'outcome': 'cancelled'}}
        else:
            return False
        self._resolved_permissions.add(request_id)
        # Bound duplicate-detection memory; stale duplicates are harmless once
        # there is no matching pending request.
        if len(self._resolved_permissions) > 4096:
            self._resolved_permissions.clear()
            self._resolved_permissions.add(request_id)
        pending.future.set_result(result)
        return True

    def _deny_permissions_for_dispatch(
        self,
        dispatch_id: str,
        *,
        reason: str,
    ) -> int:
        """Fail closed for cancellation/turn end without replaying tool args."""
        denied = 0
        for request_id, pending in list(self._pending_permissions.items()):
            if pending.dispatch_id != dispatch_id or pending.future.done():
                continue
            pending.resolution_reason = reason
            self._resolved_permissions.add(request_id)
            pending.future.set_result({'outcome': {'outcome': 'cancelled'}})
            denied += 1
        return denied

    def _mark_dispatch_terminal(self, dispatch_id: str) -> None:
        if not dispatch_id:
            return
        if len(self._terminal_dispatches) >= 4096:
            self._terminal_dispatches.clear()
        self._terminal_dispatches.add(dispatch_id)

    @staticmethod
    def _select_allow_option(
        options: list[dict[str, Any]],
        requested_option_id: str,
    ) -> str | None:
        for option in options:
            option_id = str(
                option.get('optionId') or option.get('option_id')
                or option.get('id') or '')
            kind = str(option.get('kind') or '').lower()
            if requested_option_id and option_id == requested_option_id:
                return option_id if 'allow' in kind else None
        if requested_option_id:
            return None
        for option in options:
            kind = str(option.get('kind') or '').lower()
            normalized_kind = kind.replace('-', '_')
            if normalized_kind != 'allow_once':
                continue
            option_id = str(
                option.get('optionId') or option.get('option_id')
                or option.get('id') or '')
            if option_id:
                return option_id
        return None

    async def _on_ws_disconnect(self) -> None:
        """Fail waiting ACP calls; reconnect must not pretend they resumed."""
        for request_id, pending in list(self._pending_permissions.items()):
            if pending.future.done():
                continue
            self._interrupted_dispatches[pending.dispatch_id] = {
                'dispatch_id': pending.dispatch_id,
                'endpoint_id': pending.endpoint_id,
                'permission_request_id': request_id,
                'runtime_session_id': pending.runtime_session_id,
            }
            self._resolved_permissions.add(request_id)
            if not pending.future.done():
                pending.future.set_exception(
                    RuntimeError('permission continuation_required after disconnect'))
        self._pending_permissions.clear()

    async def _on_ws_connect(self) -> None:
        """Report interrupted waits without replaying tool arguments."""
        for dispatch_id, interrupted in list(
                self._interrupted_dispatches.items()):
            sent = await self._send({
                'type': 'dispatch_continuation_required',
                **interrupted,
                'bridge_id': self.bridge_id,
                'instance_id': self.instance_id,
                'status': 'needs_manual_restart',
                'continuation_required': True,
            })
            if sent:
                self._interrupted_dispatches.pop(dispatch_id, None)

    async def _send(self, payload: dict[str, Any]) -> bool:
        if self._ws is None:
            return False
        try:
            await self._ws.send(payload)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning('send failed: %s', exc)
            return False


def _parse_agents_arg(raw: str) -> list[dict[str, str]]:
    """Parse ``coder:claude_code,reviewer:claude_code`` → agent specs."""
    specs = []
    for part in (raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        if ':' in part:
            name, runtime = part.split(':', 1)
        else:
            name, runtime = part, 'claude_code'
        specs.append({
            'at_name': name.lstrip('@'),
            'runtime': runtime.strip() or 'claude_code',
            'adapter_kind': 'acp',
        })
    return specs


def resolve_ws_url(api_base: str, ws_url: str | None = None) -> str:
    """Turn relative pair ``ws_url`` into an absolute ``ws://`` / ``wss://`` URL."""
    base = (api_base or '').rstrip('/')
    path = (ws_url or '').strip()
    if path.startswith('ws://') or path.startswith('wss://'):
        return path
    if path.startswith('http://'):
        return 'ws://' + path[len('http://'):]
    if path.startswith('https://'):
        return 'wss://' + path[len('https://'):]
    if not path:
        path = '/api/v1/team/bridge'
    if not path.startswith('/'):
        path = '/' + path
    if base.startswith('https://'):
        return 'wss://' + base[len('https://'):] + path
    if base.startswith('http://'):
        return 'ws://' + base[len('http://'):] + path
    return base + path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='agent-bridge')
    p.add_argument('--api-base', required=True, help='Platform HTTP base URL')
    p.add_argument('--pair-code', help='One-time pair code from UI')
    p.add_argument(
        '--agents',
        default='',
        help='(Advanced) Pre-register at_name[:runtime]. '
        'Default is discover-only + auto @me — prefer UI bind.')
    p.add_argument('--at-name', action='append', default=[],
                   help='(Advanced) Repeatable Agent @name')
    p.add_argument(
        '--no-auto-me',
        action='store_true',
        help='Do not auto-register @me from first discovered runtime')
    p.add_argument('--bridge-id', default='', help='Persistent bridge id')
    p.add_argument('--machine-label', default='')
    p.add_argument('--cwd', default=None)
    p.add_argument('--ws-url', default='', help='Override WS URL after pair')
    p.add_argument('--bridge-token', default=os.environ.get(
        'MS_AGENT_BRIDGE_TOKEN', ''))
    p.add_argument('--owner-user-id', default='')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument(
        '--discover-only',
        action='store_true',
        help='Print discovered runtimes and exit')
    return p


async def _amain(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO)
    if args.discover_only:
        runtimes = await discover_runtimes(dry_run=args.dry_run)
        print(runtimes)
        return

    ws_url = args.ws_url
    bridge_id = args.bridge_id or f'br-{uuid.uuid4().hex[:8]}'
    owner_user_id = args.owner_user_id
    bridge_token = args.bridge_token or None
    agent_specs = _parse_agents_arg(args.agents)
    for name in args.at_name:
        agent_specs.append({
            'at_name': name.lstrip('@'),
            'runtime': 'claude_code',
            'adapter_kind': 'acp',
        })

    # Easy path: no --agents → discover and offer auto @me after pair.
    # Prefer Codex when present — Claude CLI is often blocked locally.
    auto_me = not args.no_auto_me and not agent_specs
    if auto_me:
        found = await discover_runtimes(dry_run=args.dry_run)
        available = [c for c in found if c.get('available') and c.get('attachable')]
        prefer = ('codex', 'cursor', 'claude_code')
        pick = None
        for runtime in prefer:
            pick = next((c for c in available if c.get('runtime') == runtime),
                        None)
            if pick is not None:
                break
        if pick is None:
            pick = available[0] if available else {
                'runtime': 'codex',
                'adapter_kind': 'acp',
            }
        agent_specs = [{
            'at_name': 'me',
            'runtime': pick.get('runtime', 'codex'),
            'adapter_kind': pick.get('adapter_kind', 'acp'),
        }]
        logger.info(
            'Easy path: will register @me → %s (override with --agents '
            'or bind more Agents in the UI)',
            agent_specs[0]['runtime'],
        )

    if not args.machine_label:
        import socket
        args.machine_label = socket.gethostname().split('.')[0] or 'local'

    if args.pair_code:
        result = pair_bridge_with_platform(
            args.api_base,
            pair_code=args.pair_code,
            machine_label=args.machine_label,
            bridge_id=bridge_id or None,
            agents=agent_specs,
        )
        bridge_id = result['bridge']['bridge_id']
        owner_user_id = result['bridge']['owner_user_id']
        ws_url = result.get('ws_url') or ws_url
        bridge_token = result.get('bridge_token') or bridge_token
        slots = [
            AgentSlot(
                endpoint_id=a['endpoint_id'],
                at_name=a['at_name'],
                runtime=a.get('runtime', 'claude_code'),
                adapter_kind=a.get('adapter_kind', 'acp'),
                cwd=args.cwd,
            )
            for a in (result.get('agents') or [])
        ]
    else:
        slots = []
        for spec in agent_specs:
            if bridge_token and bridge_id:
                registered = register_agent_on_bridge(
                    args.api_base,
                    bridge_id=bridge_id,
                    at_name=spec['at_name'],
                    runtime=spec.get('runtime', 'claude_code'),
                    adapter_kind=spec.get('adapter_kind', 'acp'),
                )
                slots.append(
                    AgentSlot(
                        endpoint_id=registered['endpoint_id'],
                        at_name=registered['at_name'],
                        runtime=registered.get('runtime', 'claude_code'),
                        adapter_kind=registered.get('adapter_kind', 'acp'),
                        cwd=args.cwd,
                    ))

    if not ws_url:
        base = args.api_base.rstrip('/')
        if base.startswith('https://'):
            ws_url = 'wss://' + base[len('https://'):] + '/api/v1/team/bridge'
        elif base.startswith('http://'):
            ws_url = 'ws://' + base[len('http://'):] + '/api/v1/team/bridge'
        else:
            ws_url = base + '/api/v1/team/bridge'

    ws_url = resolve_ws_url(args.api_base, ws_url)
    logger.info('Connecting Bridge WS %s', ws_url.split('?')[0])

    daemon = BridgeDaemon(
        api_base=args.api_base,
        ws_url=ws_url,
        bridge_id=bridge_id,
        owner_user_id=owner_user_id or 'local',
        agents=slots,
        machine_label=args.machine_label,
        dry_run=args.dry_run,
        bridge_token=bridge_token,
        default_cwd=args.cwd,
    )
    await daemon.start()


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    asyncio.run(_amain(args))


if __name__ == '__main__':
    main()
