"""PermissionEnforcer: outer-layer user-intent permission control.

Checks blacklist/whitelist, session/persistent memory, and falls back to
the PermissionHandler for interactive user confirmation.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
from dataclasses import dataclass
from typing import Any, Literal

from .config import PermissionConfig
from .handler import (AutoPermissionHandler, PermissionAction,
                      PermissionHandler, PermissionResponse)
from .matcher import PermissionMatcher
from .memory import PermissionMemory
from .provider import (PermissionDecisionProvider, ProviderDecision,
                       request_provider_decision)
from .approval import (ApprovalConflictError, ApprovalStore,
                       approval_fingerprint)
from .pattern_guard import (
    UnsafePersistPattern,
    is_file_edit_tool,
    is_plan_readonly_tool,
    validate_persist_pattern,
)
from .routes import select_delegate_route
from .suggestions import generate_suggestions


@dataclass(frozen=True)
class PermissionDecision:
    action: Literal['allow', 'deny', 'ask']
    reason: str
    updated_args: dict[str, Any] | None = None


@dataclass
class _QueuedContinuation:
    request_id: str
    tool_name: str
    fingerprint: str
    token: str
    version: int


class PermissionEnforcer:
    """Outer-layer permission enforcement based on user intent and configuration."""

    def __init__(
        self,
        config: PermissionConfig,
        handler: PermissionHandler | None = None,
        memory: PermissionMemory | None = None,
        provider: PermissionDecisionProvider | None = None,
        llm_provider: PermissionDecisionProvider | None = None,
        agent_provider: PermissionDecisionProvider | None = None,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        self._config = config
        self._handler = handler or AutoPermissionHandler()
        self._memory = memory or PermissionMemory()
        # ``provider`` is the fallback when a route-specific slot is empty.
        self._provider = provider
        self._llm_provider = llm_provider
        self._agent_provider = agent_provider
        self._approval_store = approval_store
        self._continuations: list[_QueuedContinuation] = []
        self._matcher = PermissionMatcher()
        # Parallel tool calls (asyncio.gather in ToolManager.parallel_call_tool)
        # would otherwise invoke the interactive handler concurrently — N
        # prompts fighting over one terminal deadlocks. Serialize asks with a
        # lock created lazily per running loop (the per-turn TUI uses a fresh
        # loop each turn, so a single init-time Lock would bind to the wrong one).
        self._ask_lock: asyncio.Lock | None = None
        self._ask_lock_loop = None

    def _ask_lock_for_loop(self) -> 'asyncio.Lock':
        loop = asyncio.get_running_loop()
        if self._ask_lock is None or self._ask_lock_loop is not loop:
            self._ask_lock = asyncio.Lock()
            self._ask_lock_loop = loop
        return self._ask_lock

    def attach_continuations(
        self,
        *,
        session_id: str = '',
        dispatch_id: str = '',
        project_id: str = '',
    ) -> list[str]:
        """Load one-shot resume tokens for this session. Returns tool names.

        Does not replay stored args. The agent must re-issue the call; the
        next matching fingerprint consumes the token.
        """
        self._continuations = []
        store = self._approval_store
        if store is None:
            return []
        names: list[str] = []
        for request in store.list():
            if request.state not in ('resume_queued', 'resume_failed'):
                continue
            if session_id and request.session_id and request.session_id != session_id:
                continue
            if dispatch_id and request.dispatch_id and request.dispatch_id != dispatch_id:
                continue
            if project_id and request.project_id and request.project_id != project_id:
                continue
            try:
                queued = store.queue_resume(request.id, decision='allow_once')
            except (ApprovalConflictError, KeyError, ValueError):
                continue
            token = queued.continuation_token
            if not token:
                try:
                    queued = store.reissue_continuation(
                        queued.id,
                        fingerprint=queued.decision_fingerprint
                        or queued.fingerprint,
                    )
                    token = queued.continuation_token
                except (ApprovalConflictError, KeyError, ValueError):
                    continue
            if not token:
                continue
            fp = queued.decision_fingerprint or queued.fingerprint
            self._continuations.append(
                _QueuedContinuation(
                    request_id=queued.id,
                    tool_name=queued.tool_name,
                    fingerprint=fp,
                    token=token,
                    version=queued.version,
                ))
            names.append(queued.tool_name)
        return names

    def _consume_continuation(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionDecision | None:
        if not self._continuations or self._approval_store is None:
            return None
        incoming = approval_fingerprint(tool_name, tool_args)
        leftover: list[_QueuedContinuation] = []
        matched: _QueuedContinuation | None = None
        for item in self._continuations:
            if matched is None and hmac.compare_digest(item.fingerprint, incoming):
                matched = item
                continue
            if item.tool_name == tool_name and not hmac.compare_digest(
                    item.fingerprint, incoming):
                try:
                    self._approval_store.transition(
                        item.request_id,
                        'resume_failed',
                        expected_version=item.version,
                    )
                except (ApprovalConflictError, KeyError, ValueError):
                    pass
                continue
            leftover.append(item)
        self._continuations = leftover
        if matched is None:
            return None
        try:
            self._approval_store.transition(
                matched.request_id,
                'resumed',
                expected_version=matched.version,
                token=matched.token,
                fingerprint=incoming,
            )
        except (ApprovalConflictError, KeyError, ValueError):
            return None
        return PermissionDecision(
            action='allow',
            reason='Allowed by continuation (same fingerprint after restart)',
        )

    async def _serialized_ask(self, **kwargs) -> PermissionResponse:
        # ``call_id`` is a newer, optional kwarg (see check()). A handler that
        # predates it — or a lightweight test double — need not accept it; drop
        # it for such handlers so their fixed signature keeps working.
        if 'call_id' in kwargs and not self._handler_accepts('call_id'):
            kwargs.pop('call_id')
        async with self._ask_lock_for_loop():
            return await self._handler.ask(**kwargs)

    def _handler_accepts(self, param: str) -> bool:
        try:
            sig = inspect.signature(self._handler.ask)
        except (TypeError, ValueError):
            return True  # can't introspect — assume it takes it, don't strip
        params = sig.parameters.values()
        return (
            any(p.name == param for p in params)
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        )

    async def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        force_decision: PermissionDecision | None = None,
        call_id: str = '',
    ) -> PermissionDecision:
        # ``call_id`` (the tool_call this ask is gating) is threaded to the
        # handler so a UI can record/correlate the decision against the exact
        # call — important when a round fires several identical tool calls in
        # parallel. Empty when the LLM adapter didn't assign an id yet;
        # handlers must tolerate that.
        # 1. Blacklist → deny (not overridable in any mode)
        for pattern in self._config.blacklist:
            if self._matcher.match_with_content(pattern, tool_name, tool_args):
                return PermissionDecision(
                    action='deny',
                    reason=f'Denied by blacklist rule: {pattern}',
                )

        resumed = self._consume_continuation(tool_name, tool_args)
        if resumed is not None:
            return resumed

        if force_decision and force_decision.action == 'ask':
            return await self._ask_or_deny(
                tool_name,
                tool_args,
                context=force_decision.reason or '',
                call_id=call_id,
            )

        # Plan gate is read-only even if whitelist / memory would allow writes.
        if self._config.mode == 'plan':
            if is_plan_readonly_tool(tool_name):
                return PermissionDecision(
                    action='allow',
                    reason='Plan mode (read-only)',
                )
            return PermissionDecision(
                action='deny',
                reason='Plan mode is read-only until a human approves the plan',
            )

        # 2. Auto / strict mode → allow (safety handled by SafetyGuard + ask_resolver)
        if self._config.mode in ('auto', 'strict'):
            return PermissionDecision(
                action='allow',
                reason=f'{self._config.mode.capitalize()} mode')

        # 3. Whitelist → allow
        for pattern in self._config.whitelist:
            if self._matcher.match_with_content(pattern, tool_name, tool_args):
                return PermissionDecision(
                    action='allow',
                    reason=f'Allowed by whitelist rule: {pattern}',
                )

        # 4. Memory (session + persistent) → allow
        if self._memory.matches(tool_name, tool_args):
            return PermissionDecision(
                action='allow',
                reason='Allowed by remembered permission',
            )

        if self._config.mode == 'accept_edits' and is_file_edit_tool(tool_name):
            return PermissionDecision(
                action='allow',
                reason='Accept edits mode (file write/edit)',
            )

        if self._config.mode == 'delegate':
            return await self._delegate_check(
                tool_name, tool_args, call_id=call_id)

        # 5. Ask user via handler (serialized against parallel tool calls)
        suggestions = generate_suggestions(tool_name, tool_args)
        response = await self._serialized_ask(
            tool_name=tool_name,
            tool_args=tool_args,
            context='',
            suggestions=suggestions,
            call_id=call_id,
        )

        return self._process_response(response, tool_name, tool_args)

    def _process_response(
        self,
        response: PermissionResponse,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionDecision:
        if response.action == PermissionAction.ALLOW_ONCE:
            return PermissionDecision(
                action='allow', reason='User allowed once')

        if response.action == PermissionAction.ALLOW_SESSION:
            pattern = response.pattern or tool_name
            self._memory.add_session(pattern)
            return PermissionDecision(
                action='allow',
                reason=f'User allowed for session (pattern: {pattern})',
            )

        if response.action == PermissionAction.ALLOW_ALWAYS:
            pattern = response.pattern or tool_name
            try:
                pattern = validate_persist_pattern(pattern)
            except UnsafePersistPattern as exc:
                return PermissionDecision(
                    action='deny',
                    reason=str(exc),
                )
            self._memory.add(pattern, scope='project', source='user')
            return PermissionDecision(
                action='allow',
                reason=f'User allowed always (pattern: {pattern})',
            )

        if response.action == PermissionAction.MODIFY:
            return PermissionDecision(
                action='allow',
                reason='User modified args',
                updated_args=response.updated_args,
            )

        if response.action == PermissionAction.DENY:
            return PermissionDecision(
                action='deny',
                reason=response.feedback or 'User denied',
            )

        return PermissionDecision(action='deny', reason='Unknown action')

    def _human_available(self) -> bool:
        return not isinstance(self._handler, AutoPermissionHandler)

    async def _ask_or_deny(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        context: str,
        call_id: str,
    ) -> PermissionDecision:
        if not self._human_available():
            return PermissionDecision(
                action='deny',
                reason=context or 'No human available for delegated approval',
            )
        suggestions = generate_suggestions(tool_name, tool_args)
        response = await self._serialized_ask(
            tool_name=tool_name,
            tool_args=tool_args,
            context=context,
            suggestions=suggestions,
            call_id=call_id,
        )
        return self._process_response(response, tool_name, tool_args)

    async def _delegate_check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        call_id: str,
    ) -> PermissionDecision:
        route = select_delegate_route(
            self._config.delegate_routes,
            tool_name,
            tool_args,
            matcher=self._matcher,
        )
        provider_kind = (
            route.provider if route else self._config.default_delegate_provider)
        source = route.source if route else 'default'
        if provider_kind == 'skip':
            return PermissionDecision(
                action='allow',
                reason=f'Delegate skip ({source})',
            )
        if provider_kind == 'human':
            return await self._ask_or_deny(
                tool_name,
                tool_args,
                context=f'Delegate human ({source})',
                call_id=call_id,
            )

        provider = self._provider_for(provider_kind)
        if provider is None:
            # Missing Agent/LLM slot must never crash the requester.
            # Fall through to a human (or deny when no handler exists).
            return await self._ask_or_deny(
                tool_name,
                tool_args,
                context=(
                    f'Delegate {provider_kind} has no provider ({source})'),
                call_id=call_id,
            )

        suggestions = generate_suggestions(tool_name, tool_args)
        decision: ProviderDecision = await request_provider_decision(
            provider,
            tool_name=tool_name,
            tool_args=tool_args,
            context=f'delegate:{provider_kind}',
            suggestions=suggestions,
            timeout=self._timeout_for(provider_kind),
        )
        if decision.action == 'allow_once':
            return PermissionDecision(
                action='allow',
                reason=decision.reason or f'Delegate {provider_kind} allowed once',
            )
        if decision.action == 'deny':
            return PermissionDecision(
                action='deny',
                reason=(
                    decision.feedback or decision.reason
                    or f'Delegate {provider_kind} denied'),
            )
        return await self._ask_or_deny(
            tool_name,
            tool_args,
            context=decision.reason or f'Delegate {provider_kind} uncertain',
            call_id=call_id,
        )

    def _timeout_for(self, kind: str) -> float:
        if kind == 'agent':
            return self._config.agent_provider_timeout
        return self._config.provider_timeout

    def _provider_for(
        self,
        kind: str,
    ) -> PermissionDecisionProvider | None:
        if kind == 'llm':
            return self._llm_provider or self._provider
        if kind == 'agent':
            return self._agent_provider or self._provider
        return self._provider
