# Copyright (c) ModelScope Contributors. All rights reserved.
"""Unit tests for Host Bridge ACP client + process pool."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ms_agent.bridge.adapters.acp_client import (
    AcpProcessPool,
    AcpSession,
    AuthRequired,
    attach_fallback_allowed,
)
from ms_agent.bridge.adapters.acp_runtime import run_acp_turn
from ms_agent.bridge.adapters.factory import make_adapter


class _FakeProc:
    def __init__(self, replies: list[dict[str, Any]]):
        self._replies = list(replies)
        self.returncode = None
        self.stdin = self
        self.stdout = self
        self.stderr = None
        self._buf: list[bytes] = []
        self._out_q: asyncio.Queue = asyncio.Queue()
        self._closed = False
        for msg in self._replies:
            self._out_q.put_nowait(
                (json.dumps(msg) + '\n').encode('utf-8'))

    def write(self, data: bytes) -> None:
        self._buf.append(data)
        try:
            msg = json.loads(data.decode().strip())
        except Exception:
            return
        req_id = msg.get('id')
        method = msg.get('method')
        if method == 'initialize':
            self._out_q.put_nowait(
                json.dumps({
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'protocolVersion': 1,
                        'authMethods': [],
                    },
                }).encode() + b'\n')
        elif method == 'authenticate':
            self._out_q.put_nowait(
                json.dumps({
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {},
                }).encode() + b'\n')
        elif method == 'session/new':
            self._out_q.put_nowait(
                json.dumps({
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'sessionId': 'sess_new_1'
                    },
                }).encode() + b'\n')
        elif method == 'session/load':
            sid = (msg.get('params') or {}).get('sessionId')
            if sid in ('bad', '00000000-0000-0000-0000-00000000dead'):
                self._out_q.put_nowait(
                    json.dumps({
                        'jsonrpc': '2.0',
                        'id': req_id,
                        'error': {
                            'message': 'not found'
                        },
                    }).encode() + b'\n')
            else:
                self._out_q.put_nowait(
                    json.dumps({
                        'jsonrpc': '2.0',
                        'id': req_id,
                        'result': {
                            'sessionId': sid
                        },
                    }).encode() + b'\n')
        elif method == 'session/prompt':
            # notification then result
            self._out_q.put_nowait(
                json.dumps({
                    'jsonrpc': '2.0',
                    'method': 'session/update',
                    'params': {
                        'update': {
                            'sessionUpdate': 'agent_message_chunk',
                            'content': {
                                'text': 'PONG'
                            },
                        }
                    },
                }).encode() + b'\n')
            self._out_q.put_nowait(
                json.dumps({
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'stopReason': 'end_turn'
                    },
                }).encode() + b'\n')

    async def drain(self) -> None:
        return None

    async def readline(self) -> bytes:
        if self._closed and self._out_q.empty():
            return b''
        item = await self._out_q.get()
        return item

    def terminate(self) -> None:
        self.returncode = 0
        self._closed = True
        self._out_q.put_nowait(b'')

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        self.returncode = 0
        return 0


@pytest.mark.asyncio
async def test_pool_reuses_same_session(monkeypatch):
    created = []

    async def fake_exec(*cmd, **kwargs):
        proc = _FakeProc([])
        created.append(proc)
        return proc

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    pool = AcpProcessPool()
    a = await pool.get('codex', ['fake-acp'], cwd='/tmp/x')
    await a.ensure_ready()
    b = await pool.get('codex', ['fake-acp'], cwd='/tmp/x')
    assert a is b
    assert len(created) == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_attach_fails_without_fallback(monkeypatch):
    monkeypatch.setenv('MS_AGENT_ACP_ATTACH_FALLBACK', 'error')
    assert attach_fallback_allowed() is False

    async def fake_exec(*cmd, **kwargs):
        return _FakeProc([])

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    # Reset singleton pool by replacing module pool
    import ms_agent.bridge.adapters.acp_client as client_mod
    client_mod._POOL = AcpProcessPool()

    events = []
    async for ev in run_acp_turn(
            runtime='codex',
            command=['fake-acp'],
            prompt='hi',
            session_id='00000000-0000-0000-0000-00000000dead',
            cwd='/tmp',
            session_mode='attach',
    ):
        events.append(ev)
    assert any(e.type == 'error' and 'session_attach_failed' in e.content
               for e in events)
    await client_mod._POOL.close_all()


@pytest.mark.asyncio
async def test_fresh_prompt_streams_text(monkeypatch):
    async def fake_exec(*cmd, **kwargs):
        return _FakeProc([])

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    import ms_agent.bridge.adapters.acp_client as client_mod
    client_mod._POOL = AcpProcessPool()

    events = []
    async for ev in run_acp_turn(
            runtime='codex',
            command=['fake-acp'],
            prompt='Reply PONG',
            session_id='ignored',
            cwd='/tmp',
            session_mode='fresh',
    ):
        events.append(ev)
    texts = [e.content for e in events if e.type == 'text']
    assert any('PONG' in t for t in texts)
    assert events[-1].type == 'done'
    await client_mod._POOL.close_all()


def test_looks_like_acp_session_id():
    from ms_agent.bridge.adapters.acp_runtime import looks_like_acp_session_id

    assert looks_like_acp_session_id('019fdb0e-3c2c-76a0-a9c7-0f15b081cefc')
    assert not looks_like_acp_session_id('sess_97e22adab146')
    assert not looks_like_acp_session_id('d_5849d6ff7cdd')
    assert not looks_like_acp_session_id('')
    assert not looks_like_acp_session_id(None)


@pytest.mark.asyncio
async def test_attach_with_platform_sess_id_uses_session_new(monkeypatch):
    """Team sess_* must not call session/load (would Internal error on Codex)."""
    methods: list[str] = []

    class _Proc(_FakeProc):
        def write(self, data: bytes) -> None:
            try:
                msg = json.loads(data.decode().strip())
            except Exception:
                return
            method = msg.get('method')
            if method:
                methods.append(method)
            super().write(data)

    async def fake_exec(*cmd, **kwargs):
        return _Proc([])

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    import ms_agent.bridge.adapters.acp_client as client_mod
    client_mod._POOL = AcpProcessPool()

    events = []
    async for ev in run_acp_turn(
            runtime='codex',
            command=['fake-acp'],
            prompt='hi',
            session_id='sess_platform_binding',
            cwd='/tmp',
            session_mode='attach',
    ):
        events.append(ev)

    assert 'session/load' not in methods
    assert 'session/new' in methods
    assert any(e.content == 'attach_skipped_platform_session_id' for e in events)
    await client_mod._POOL.close_all()


def test_factory_codex_is_acp_adapter():
    ad = make_adapter('codex', dry_run=True)
    assert ad.name == 'codex'
    assert type(ad).__name__ == 'AcpCodexAdapter'


def test_codex_cli_module_removed():
    with pytest.raises(ModuleNotFoundError):
        __import__('ms_agent.bridge.adapters.codex_cli')


def test_inject_codex_cli_credentials_from_auth_json(tmp_path, monkeypatch):
    from ms_agent.bridge.adapters import acp_codex as codex_mod

    home = tmp_path / 'codex'
    home.mkdir()
    (home / 'auth.json').write_text(
        json.dumps({
            'auth_mode': 'apikey',
            'OPENAI_API_KEY': 'sk-auth-json-only',
        }),
        encoding='utf-8',
    )
    (home / 'config.toml').write_text(
        '[model_providers.dashscope]\n'
        'env_key = "DASHSCOPE_API_KEY"\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('CODEX_API_KEY', raising=False)

    out = codex_mod.materialize_codex_cli_login_env({})
    # auth.json fills ACP handshake vars; provider env_key reuses that CLI key
    # (still never loads Team .env).
    assert out['OPENAI_API_KEY'] == 'sk-auth-json-only'
    assert out['CODEX_API_KEY'] == 'sk-auth-json-only'
    assert out['DASHSCOPE_API_KEY'] == 'sk-auth-json-only'

    # Explicit shell provider key wins over auth reuse.
    out2 = codex_mod.materialize_codex_cli_login_env(
        {'DASHSCOPE_API_KEY': 'sk-from-shell'})
    assert out2['DASHSCOPE_API_KEY'] == 'sk-from-shell'
