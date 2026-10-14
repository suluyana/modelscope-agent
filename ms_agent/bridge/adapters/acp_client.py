# Copyright (c) ModelScope Contributors. All rights reserved.
"""Shared ACP JSON-RPC client + long-lived process pool for Host Bridge."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

NotificationHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


def _ask_text(params: Any) -> str:
    """Best-effort question text from ACP ask/permission params."""
    if not isinstance(params, dict):
        return str(params or '').strip()
    for key in ('question', 'title', 'message', 'prompt', 'toolCall'):
        val = params.get(key)
        if isinstance(val, dict):
            text = str(val.get('title') or val.get('kind') or val.get('id') or '')
            if text:
                return text
        elif val:
            return str(val)
    questions = params.get('questions') or params.get('options') or []
    if isinstance(questions, list) and questions:
        first = questions[0]
        if isinstance(first, dict):
            return str(first.get('question') or first.get('header') or first)
        return str(first)
    return json.dumps(params, ensure_ascii=False)[:800]


def _permission_call_id(params: Any) -> str:
    if not isinstance(params, dict):
        return ''
    tool_call = params.get('toolCall') or params.get('tool_call') or {}
    if isinstance(tool_call, dict):
        return str(
            tool_call.get('toolCallId')
            or tool_call.get('tool_call_id')
            or tool_call.get('id')
            or '')
    return str(params.get('toolCallId') or params.get('call_id') or '')


class AuthRequired(RuntimeError):
    """ACP agent requires interactive / CLI login."""


class AcpSession:
    """JSON-RPC ACP client over a long-lived stdio agent process."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        auto_allow: bool = False,
        auth_method_id: str | None = None,
    ) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self.auto_allow = auto_allow
        self.auth_method_id = auth_method_id
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_buf: bytearray = bytearray()
        self._init_result: dict[str, Any] | None = None
        self._ready = False
        self.on_notification: NotificationHandler | None = None
        self._ready_lock: asyncio.Lock | None = None
        # call_id / request_id → ACP JSON-RPC id for session/request_permission
        self._permission_asks: dict[str, Any] = {}

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def _stderr_tail(self, limit: int = 800) -> str:
        text = bytes(self._stderr_buf).decode('utf-8', errors='replace').strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    async def start(self) -> None:
        if self.alive:
            return
        env = dict(self.env or os.environ)
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or None,
            env=env,
        )
        self._stderr_buf = bytearray()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._ready = False

    async def close(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reader_task = None
        self._stderr_task = None
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None
        self._ready = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError('ACP session closed'))
        self._pending.clear()

    def _ensure_ready_lock(self) -> asyncio.Lock:
        if self._ready_lock is None:
            self._ready_lock = asyncio.Lock()
        return self._ready_lock

    async def ensure_ready(self) -> dict[str, Any]:
        await self.start()
        async with self._ensure_ready_lock():
            if self._ready and self._init_result is not None:
                return self._init_result
            self._init_result = await self.initialize()
            await self.authenticate()
            self._ready = True
            return self._init_result

    async def initialize(self) -> dict[str, Any]:
        return await self.request(
            'initialize',
            {
                'protocolVersion': 1,
                'clientCapabilities': {
                    'fs': {
                        'readTextFile': False,
                        'writeTextFile': False,
                    },
                    'terminal': False,
                },
                'clientInfo': {
                    'name': 'ms-agent-bridge',
                    'version': '0.2.0',
                },
            },
            timeout=float(os.environ.get('MS_AGENT_ACP_INIT_TIMEOUT', '20')),
        )

    async def authenticate(self) -> dict[str, Any]:
        methods = []
        if isinstance(self._init_result, dict):
            methods = self._init_result.get('authMethods') or []
        method_id = self.auth_method_id
        if not method_id and methods:
            first = methods[0]
            if isinstance(first, dict):
                method_id = first.get('id')
            elif isinstance(first, str):
                method_id = first
        if not method_id:
            # Agent did not advertise auth — treat as already authenticated.
            return {}
        try:
            return await self.request(
                'authenticate',
                {'methodId': method_id},
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise AuthRequired(
                f'ACP authentication required (methodId={method_id}). '
                f'This is the local runtime CLI login — not a Team API key. '
                f'Log in via the CLI for this runtime, then retry. '
                f'Detail: {exc}') from exc

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = 60.0,
    ) -> dict[str, Any]:
        if not self.alive:
            await self.start()
        assert self.proc and self.proc.stdin
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        payload = {
            'jsonrpc': '2.0',
            'id': req_id,
            'method': method,
            'params': params,
        }
        self.proc.stdin.write((json.dumps(payload) + '\n').encode('utf-8'))
        await self.proc.stdin.drain()
        try:
            if timeout is None:
                result = await fut
            else:
                result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            detail = self._stderr_tail()
            rc = self.proc.returncode if self.proc else None
            if rc is not None:
                raise RuntimeError(
                    f'ACP {method} failed: agent exited early '
                    f'(exit={rc}). {detail or "no stderr"}'
                ) from exc
            raise TimeoutError(
                f'ACP {method} timed out'
                + (f'; {detail}' if detail else '')
            ) from exc
        if isinstance(result, dict) and result.get('__error__'):
            err = result['__error__']
            msg = err.get('message') if isinstance(err, dict) else str(err)
            low = str(msg).lower()
            if 'auth' in low or 'login' in low:
                raise AuthRequired(str(msg))
            raise RuntimeError(f'ACP {method} failed: {msg}')
        return result if isinstance(result, dict) else {'value': result}
    async def session_cancel(self, session_id: str) -> None:
        if not self.alive:
            return
        try:
            await self.request(
                'session/cancel',
                {'sessionId': session_id},
                timeout=10.0,
            )
        except Exception:  # noqa: BLE001
            logger.debug('session/cancel failed', exc_info=True)

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='replace').strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                logger.debug('ACP non-json: %s', text[:200])
                continue
            await self._dispatch(msg)
        # Process ended — fail any in-flight RPCs with stderr context.
        detail = self._stderr_tail() or 'ACP agent closed stdout'
        err = RuntimeError(
            f'ACP agent exited'
            f'{"" if self.proc is None or self.proc.returncode is None else f" (exit={self.proc.returncode})"}'
            f': {detail}')
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()
        self._ready = False

    async def _read_stderr(self) -> None:
        if not self.proc or self.proc.stderr is None:
            return
        while True:
            chunk = await self.proc.stderr.read(512)
            if not chunk:
                break
            self._stderr_buf.extend(chunk)
            if len(self._stderr_buf) > 16_000:
                del self._stderr_buf[:-8_000]

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if 'id' in msg and ('result' in msg or 'error' in msg):
            fut = self._pending.pop(msg['id'], None)
            if fut and not fut.done():
                if 'error' in msg:
                    fut.set_result({'__error__': msg['error']})
                else:
                    fut.set_result(msg.get('result') or {})
            return

        method = msg.get('method') or ''
        if method == 'session/request_permission':
            params = msg.get('params') or {}
            if self.auto_allow:
                if self.on_notification:
                    try:
                        note = self.on_notification({
                            'method': 'session/update',
                            'params': {
                                'update': {
                                    'sessionUpdate': 'agent_message_chunk',
                                    'content': {
                                        'type': 'text',
                                        'text': (
                                            '[permission auto-approved] '
                                            + _ask_text(params)
                                        ),
                                    },
                                },
                            },
                        })
                        if asyncio.iscoroutine(note):
                            await note
                    except Exception:  # noqa: BLE001
                        logger.debug('permission notice failed', exc_info=True)
                await self._respond(
                    msg['id'],
                    {
                        'outcome': {
                            'outcome': 'selected',
                            'optionId': 'allow-once',
                        }
                    },
                )
                return
            self._remember_permission_ask(msg)
            if self.on_notification:
                try:
                    note = self.on_notification(msg)
                    if asyncio.iscoroutine(note):
                        await note
                except Exception:  # noqa: BLE001
                    logger.debug('permission forward failed', exc_info=True)
            return
        if method in ('cursor/ask_question', 'cursor/create_plan'):
            params = msg.get('params') or {}
            question = _ask_text(params)
            if self.on_notification and question:
                try:
                    note = self.on_notification({
                        'method': 'session/update',
                        'params': {
                            'update': {
                                'sessionUpdate': 'agent_message_chunk',
                                'content': {
                                    'type': 'text',
                                    'text': question,
                                },
                            },
                        },
                    })
                    if asyncio.iscoroutine(note):
                        await note
                except Exception:  # noqa: BLE001
                    logger.debug('ask_question notice failed', exc_info=True)
            await self._respond(
                msg['id'],
                {'outcome': {
                    'outcome': 'cancelled'
                }},
            )
            return
        if method == 'session/update' and self.on_notification:
            try:
                result = self.on_notification(msg)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.debug('notification handler failed', exc_info=True)

    def _remember_permission_ask(self, msg: dict[str, Any]) -> None:
        acp_id = msg.get('id')
        if acp_id is None:
            return
        params = msg.get('params') or {}
        call_id = _permission_call_id(params)
        if call_id:
            self._permission_asks[str(call_id)] = acp_id
        self._permission_asks[str(acp_id)] = acp_id

    def _forget_permission_ask(self, acp_id: Any) -> None:
        for key, value in list(self._permission_asks.items()):
            if value == acp_id:
                self._permission_asks.pop(key, None)

    async def answer_permission(
        self,
        lookup: str,
        *,
        option_id: str = 'allow-once',
        deny: bool = False,
    ) -> bool:
        """Respond to a pending ``session/request_permission`` by call_id or rpc id."""
        acp_id = self._permission_asks.get(str(lookup))
        if acp_id is None:
            return False
        self._forget_permission_ask(acp_id)
        if deny:
            await self._respond(acp_id, {'outcome': {'outcome': 'cancelled'}})
        else:
            await self._respond(
                acp_id,
                {
                    'outcome': {
                        'outcome': 'selected',
                        'optionId': option_id or 'allow-once',
                    }
                },
            )
        return True

    async def _respond(self, req_id: Any, result: dict) -> None:
        assert self.proc and self.proc.stdin
        payload = {'jsonrpc': '2.0', 'id': req_id, 'result': result}
        self.proc.stdin.write((json.dumps(payload) + '\n').encode('utf-8'))
        await self.proc.stdin.drain()


class AcpProcessPool:
    """Long-lived ACP agent processes keyed by (runtime, cwd)."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], AcpSession] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _key(self, runtime: str, cwd: str | None) -> tuple[str, str]:
        return (runtime, os.path.abspath(cwd) if cwd else '')

    async def get(
        self,
        runtime: str,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        auto_allow: bool = False,
        auth_method_id: str | None = None,
    ) -> AcpSession:
        key = self._key(runtime, cwd)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            sess = self._sessions.get(key)
            if sess is not None and sess.alive:
                # Reuse while initialize is still in flight. Closing a live
                # process here races concurrent session/list / dispatch.
                sess.auto_allow = auto_allow
                if auth_method_id:
                    sess.auth_method_id = auth_method_id
                return sess
            if sess is not None:
                await sess.close()
            sess = AcpSession(
                command,
                cwd=cwd,
                env=env,
                auto_allow=auto_allow,
                auth_method_id=auth_method_id,
            )
            await sess.start()
            self._sessions[key] = sess
            return sess

    async def drop(self, runtime: str, cwd: str | None = None) -> None:
        key = self._key(runtime, cwd)
        sess = self._sessions.pop(key, None)
        if sess is not None:
            await sess.close()


    async def answer_permission(
        self,
        lookup: str,
        *,
        option_id: str = 'allow-once',
        deny: bool = False,
    ) -> bool:
        """Resolve a pending ACP permission ask on any pooled session."""
        for sess in list(self._sessions.values()):
            if not sess.alive:
                continue
            if await sess.answer_permission(
                    lookup, option_id=option_id, deny=deny):
                return True
        return False

    async def close_all(self) -> None:
        keys = list(self._sessions.keys())
        for key in keys:
            sess = self._sessions.pop(key, None)
            if sess is not None:
                await sess.close()


_POOL: Optional[AcpProcessPool] = None


def get_acp_pool() -> AcpProcessPool:
    global _POOL
    if _POOL is None:
        _POOL = AcpProcessPool()
    return _POOL


def attach_fallback_allowed() -> bool:
    """Default: fail attach loudly. Set MS_AGENT_ACP_ATTACH_FALLBACK=fresh to allow."""
    return os.environ.get('MS_AGENT_ACP_ATTACH_FALLBACK', 'error').lower() in (
        'fresh',
        '1',
        'true',
        'yes',
    )
