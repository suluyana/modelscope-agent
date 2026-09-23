"""PermissionHandler protocol and implementations.

Three implementations:
  - AutoPermissionHandler: always allow (fallback).
  - CLIPermissionHandler: interactive terminal menu.
  - WebPermissionHandler: Future-based async with event emitter.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .approval import (
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStore,
    MemoryApprovalStore,
)


class PermissionAction(str, Enum):
    ALLOW_ONCE = 'allow_once'
    ALLOW_SESSION = 'allow_session'
    ALLOW_ALWAYS = 'allow_always'
    DENY = 'deny'
    MODIFY = 'modify'


@dataclass(frozen=True)
class PermissionResponse:
    action: PermissionAction
    updated_args: dict[str, Any] | None = None
    pattern: str | None = None
    feedback: str | None = None


class PermissionHandler(Protocol):

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
    ) -> PermissionResponse: ...


class AutoPermissionHandler:
    """Always allows — used as fallback or in auto mode."""

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
    ) -> PermissionResponse:
        return PermissionResponse(action=PermissionAction.ALLOW_ONCE)


class CLIPermissionHandler:
    """Interactive CLI permission prompt."""

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
    ) -> PermissionResponse:
        args_display = json.dumps(tool_args, ensure_ascii=False, indent=2)
        if len(args_display) > 500:
            args_display = args_display[:500] + '...'

        suggestion = suggestions[0] if suggestions else tool_name

        print(f'\n{"="*60}', file=sys.stderr)
        print(f' Permission Required', file=sys.stderr)
        print(f'{"="*60}', file=sys.stderr)
        print(f' Tool: {tool_name}', file=sys.stderr)
        print(f' Args: {args_display}', file=sys.stderr)
        if context:
            print(f' Context: {context}', file=sys.stderr)
        print(f'{"─"*60}', file=sys.stderr)
        print(f' [y] Allow this once', file=sys.stderr)
        print(f' [s] Allow for this session', file=sys.stderr)
        print(f' [a] Always allow (pattern: {suggestion})', file=sys.stderr)
        print(f' [e] Edit args then execute', file=sys.stderr)
        print(f' [n] Deny', file=sys.stderr)
        print(f'{"="*60}', file=sys.stderr)

        loop = asyncio.get_running_loop()
        choice = await loop.run_in_executor(
            None, lambda: input('Choice [y/s/a/e/n]: ').strip().lower())

        if choice == 's':
            return PermissionResponse(
                action=PermissionAction.ALLOW_SESSION,
                pattern=suggestion,
            )
        elif choice == 'a':
            edited = await loop.run_in_executor(
                None,
                lambda: input(f'Pattern [{suggestion}]: ').strip(),
            )
            final_pattern = edited if edited else suggestion
            return PermissionResponse(
                action=PermissionAction.ALLOW_ALWAYS,
                pattern=final_pattern,
            )
        elif choice == 'e':
            edited_raw = await loop.run_in_executor(
                None,
                lambda: input('New args (JSON): ').strip(),
            )
            try:
                new_args = json.loads(edited_raw)
            except json.JSONDecodeError:
                print('Invalid JSON, denying.', file=sys.stderr)
                return PermissionResponse(action=PermissionAction.DENY)
            return PermissionResponse(
                action=PermissionAction.MODIFY,
                updated_args=new_args,
            )
        elif choice == 'n':
            return PermissionResponse(action=PermissionAction.DENY)
        else:
            return PermissionResponse(action=PermissionAction.ALLOW_ONCE)


class EventEmitter(Protocol):
    """Protocol for pushing events to the frontend."""

    def emit(self, event: dict[str, Any]) -> None:
        ...


@dataclass
class _PendingAsk:
    future: asyncio.Future[PermissionResponse]
    request: ApprovalRequest


class WebPermissionHandler:
    """Async handler that suspends on a Future until the frontend responds.

    Pending asks are written to ``ApprovalStore`` (the ask ledger). Always-allow
    rules are *not* written here — Enforcer records those on ``PermissionMemory``.
    """

    def __init__(
        self,
        event_emitter: EventEmitter,
        timeout: float | None = None,
        store: ApprovalStore | None = None,
        *,
        project_id: str = '',
        session_id: str = '',
        dispatch_id: str = '',
    ) -> None:
        """``timeout=None`` waits until the user answers.

        A 120s cap used to deny the tool call, so closing the tab and
        approving later could not resume that same Future. Bound the wait
        only when the host explicitly asks (tests, headless jobs).
        """
        self._pending: dict[str, _PendingAsk] = {}
        self._event_emitter = event_emitter
        self._timeout = timeout
        self._store = store or MemoryApprovalStore()
        self._project_id = project_id
        self._session_id = session_id
        self._dispatch_id = dispatch_id

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
    ) -> PermissionResponse:
        request = ApprovalRequest.create(
            tool_name,
            tool_args,
            call_id=call_id,
            context=context,
            suggestions=suggestions or [],
            project_id=self._project_id,
            session_id=self._session_id,
            dispatch_id=self._dispatch_id,
        )
        request_id = request.id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionResponse] = loop.create_future()
        self._store.create(request)
        self._pending[request_id] = _PendingAsk(future=future, request=request)

        self._event_emitter.emit({
            'type': 'permission_request',
            'request_id': request_id,
            'call_id': call_id,
            'tool_name': tool_name,
            'tool_args': request.tool_args,
            'context': context,
            'suggestions': suggestions or [],
            'options': [a.value for a in PermissionAction],
            'fingerprint': request.fingerprint,
            'version': request.version,
        })

        try:
            if self._timeout is None:
                return await future
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            try:
                current = self._store.get(request_id)
                if current is not None and current.state == 'pending':
                    self._store.transition(
                        request_id,
                        'expired',
                        expected_version=current.version,
                    )
            except (ApprovalConflictError, KeyError, ValueError):
                pass
            return PermissionResponse(
                action=PermissionAction.DENY,
                feedback=(
                    'TIMEOUT: no response within '
                    f'{self._timeout:.0f}s (not a refusal). '
                    'Do not re-request the same call as if the user denied it.'
                ),
            )
        finally:
            if future.done():
                self._pending.pop(request_id, None)

    def resolve(self, request_id: str, response: PermissionResponse) -> bool:
        pending = self._pending.get(request_id)
        if pending is not None and not pending.future.done():
            pending.future.set_result(response)
            self._commit_decision(pending.request, response)
            return True
        stored = self._store.get(request_id)
        if stored is None:
            return False
        if response.action == PermissionAction.DENY:
            if stored.state == 'pending':
                try:
                    self._store.transition(
                        request_id,
                        'denied',
                        expected_version=stored.version,
                        token=stored.token or None,
                        fingerprint=stored.fingerprint,
                        decision='deny',
                        host_resume=not stored.token,
                    )
                except (ApprovalConflictError, KeyError, ValueError, TypeError):
                    pass
            return False
        try:
            self._store.queue_resume(
                request_id,
                decision=response.action.value,
                pattern=response.pattern or '',
            )
            return True
        except (ApprovalConflictError, KeyError, ValueError, TypeError):
            return False

    def _commit_decision(
        self,
        request: ApprovalRequest,
        response: PermissionResponse,
    ) -> None:
        state = (
            'denied'
            if response.action == PermissionAction.DENY else 'approved')
        try:
            self._store.transition(
                request.id,
                state,  # type: ignore[arg-type]
                expected_version=request.version,
                token=request.token,
                fingerprint=request.fingerprint,
                decision=response.action.value,
                pattern=response.pattern or '',
            )
        except (ApprovalConflictError, KeyError, ValueError):
            pass
