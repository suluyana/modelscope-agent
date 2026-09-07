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
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal, Protocol

from .approval import (ApprovalConflictError, ApprovalRequest, ApprovalStore,
                       MemoryApprovalStore)
from .provider import sanitize_sensitive_text, sanitize_tool_args

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
    scope: Literal['project', 'global'] = 'project'


class PermissionHandler(Protocol):

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
        workspace_root: str = '',
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
        workspace_root: str = '',
    ) -> PermissionResponse:
        return PermissionResponse(action=PermissionAction.ALLOW_ONCE)


def _args_preview(tool_args: dict[str, Any]) -> str:
    args_display = json.dumps(
        sanitize_tool_args(tool_args), ensure_ascii=False, indent=2)
    if len(args_display) > 500:
        args_display = args_display[:500] + '...'
    return args_display


class CLIPermissionHandler:
    """One-layer CLI permission prompt (scene options, single stdin read)."""

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
        workspace_root: str = '',
    ) -> PermissionResponse:
        from .ask_options import (build_ask_options, format_ask_menu,
                                  parse_ask_choice)
        options = build_ask_options(
            tool_name,
            tool_args,
            workspace_root=workspace_root or None,
            suggestions=suggestions,
        )
        menu = format_ask_menu(
            options,
            tool_name=tool_name,
            args_preview=_args_preview(tool_args),
            context=sanitize_sensitive_text(context) if context else '',
        )
        print(f'\n{menu}', file=sys.stderr)
        print('choice: ', end='', file=sys.stderr, flush=True)
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            return PermissionResponse(action=PermissionAction.DENY)
        if raw == '':
            return PermissionResponse(action=PermissionAction.DENY)
        return parse_ask_choice(raw, options)


class EventEmitter(Protocol):
    """Protocol for pushing events to the frontend."""

    def emit(self, event: dict[str, Any]) -> None:
        ...


class WebPermissionHandler:
    """Async handler that suspends on a Future until the frontend responds."""

    def __init__(
        self,
        event_emitter: EventEmitter,
        timeout: float = 120.0,
        store: ApprovalStore | None = None,
    ) -> None:
        self._pending: dict[
            str,
            tuple[asyncio.Future[PermissionResponse], asyncio.AbstractEventLoop],
        ] = {}
        self._pending_lock = threading.RLock()
        self._event_emitter = event_emitter
        self._timeout = timeout
        self._store = store or MemoryApprovalStore()

    async def ask(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str,
        suggestions: list[str] | None = None,
        call_id: str = '',
        workspace_root: str = '',
    ) -> PermissionResponse:
        from .ask_options import build_ask_options
        ask_options = build_ask_options(
            tool_name,
            tool_args,
            workspace_root=workspace_root or None,
            suggestions=suggestions,
        )
        request = ApprovalRequest.create(
            tool_name,
            tool_args,
            call_id=call_id,
            context=context,
            suggestions=suggestions or [],
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=self._timeout)
            ).isoformat(),
        )
        request_id = request.id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionResponse] = loop.create_future()

        # Durability comes before visibility: consumers can immediately fetch
        # every request they observe from the emitted event.
        try:
            self._store.create(request)
            with self._pending_lock:
                self._pending[request_id] = (future, loop)
            try:
                self._event_emitter.emit({
                    'type': 'permission_request',
                    'request_id': request_id,
                    'call_id': call_id,
                    'tool_name': tool_name,
                    'tool_args': request.tool_args,
                    'context': sanitize_sensitive_text(context),
                    'suggestions': list(request.suggestions),
                    'options': [opt.key for opt in ask_options],
                    'ask_options': [
                        {
                            'key': opt.key,
                            'label': opt.label,
                            'action': opt.action.value,
                            'pattern': opt.pattern,
                            'editable': opt.editable,
                            'edit_value': opt.edit_value,
                        }
                        for opt in ask_options
                    ],
                    'approval_token': request.token,
                    'fingerprint': request.fingerprint,
                    'version': request.version,
                })
            except Exception as exc:
                self._cancel_pending(request_id, request.version)
                return PermissionResponse(
                    action=PermissionAction.DENY,
                    feedback=(
                        'Permission request could not be delivered: '
                        f'{type(exc).__name__}'),
                )
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=self._timeout)
        except asyncio.TimeoutError:
            current = self._store.get(request_id)
            if current is not None and current.state in ('approved', 'denied'):
                try:
                    self._store.transition(
                        request_id,
                        'resume_queued',
                        expected_version=current.version,
                    )
                except (ApprovalConflictError, ValueError):
                    pass
            elif current is not None and current.state == 'pending':
                try:
                    self._store.transition(
                        request_id,
                        'expired',
                        expected_version=current.version,
                    )
                except (ApprovalConflictError, ValueError):
                    pass
            return PermissionResponse(
                action=PermissionAction.DENY,
                feedback='Permission request timed out',
            )
        except asyncio.CancelledError:
            current = self._store.get(request_id)
            if current is not None and current.state in ('approved', 'denied'):
                try:
                    self._store.transition(
                        request_id,
                        'resume_queued',
                        expected_version=current.version,
                    )
                except (ApprovalConflictError, ValueError):
                    pass
            elif current is not None and current.state == 'pending':
                self._cancel_pending(request_id, current.version)
            raise
        except Exception as exc:
            return PermissionResponse(
                action=PermissionAction.DENY,
                feedback=(
                    'Permission request could not be persisted: '
                    f'{type(exc).__name__}'),
            )
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _cancel_pending(self, request_id: str, version: int) -> None:
        try:
            self._store.transition(
                request_id,
                'cancelled',
                expected_version=version,
            )
        except (ApprovalConflictError, KeyError, ValueError):
            pass

    @staticmethod
    def _set_future_result(
        future: asyncio.Future[PermissionResponse],
        response: PermissionResponse,
    ) -> None:
        if not future.done():
            future.set_result(response)

    def resolve(
        self,
        request_id: str,
        response: PermissionResponse,
        *,
        token: str | None = None,
        fingerprint: str | None = None,
        decided_by: str = '',
    ) -> bool:
        request = self._store.get(request_id)
        if request is None:
            return False
        state = (
            'denied'
            if response.action == PermissionAction.DENY else 'approved')
        if request.state != 'pending':
            return (
                request.state in (state, 'resume_queued', 'resumed')
                and request.decision == response.action.value
                and request.pattern == (response.pattern or '')
                and request.scope == response.scope
                and request.decision_args == (
                    sanitize_tool_args(response.updated_args)
                    if response.updated_args is not None else None)
            )
        try:
            decided = self._store.transition(
                request_id,
                state,
                expected_version=request.version,
                token=token,
                fingerprint=fingerprint,
                feedback=response.feedback or '',
                decision=response.action.value,
                pattern=response.pattern or '',
                scope=response.scope,
                decided_by=decided_by,
                decision_args=response.updated_args,
            )
        except (ApprovalConflictError, ValueError):
            return False
        with self._pending_lock:
            waiter = self._pending.get(request_id)
        if waiter is not None:
            future, loop = waiter
            if not future.done():
                loop.call_soon_threadsafe(
                    self._set_future_result, future, response)
                return True
        try:
            queued = self._store.transition(
                request_id,
                'resume_queued',
                expected_version=decided.version,
            )
        except (ApprovalConflictError, ValueError):
            return False
        try:
            self._event_emitter.emit({
                'type': 'permission_resume_queued',
                'request_id': request_id,
                'decision': response.action.value,
                'updated_args': queued.decision_args,
                'continuation_token': queued.continuation_token,
                'fingerprint': (
                    queued.decision_fingerprint or queued.fingerprint),
                'version': queued.version,
            })
        except Exception as exc:
            try:
                self._store.transition(
                    request_id,
                    'resume_failed',
                    expected_version=queued.version,
                    feedback=f'Resume delivery failed: {type(exc).__name__}',
                )
            except (ApprovalConflictError, ValueError):
                pass
            return False
        return True
