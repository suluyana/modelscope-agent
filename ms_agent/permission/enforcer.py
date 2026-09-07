"""PermissionEnforcer: outer-layer user-intent permission control.

Checks blacklist/whitelist, session/persistent memory, and falls back to
the PermissionHandler for interactive user confirmation.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Literal

from .config import PermissionConfig
from .handler import (AutoPermissionHandler, PermissionAction,
                      PermissionHandler, PermissionResponse)
from .matcher import CONTENT_SEP, PermissionMatcher
from .memory import PermissionMemory
from .provider import (PermissionDecisionProvider, ProviderDecision,
                       request_provider_decision)
from .suggestions import generate_suggestions


@dataclass(frozen=True)
class PermissionDecision:
    action: Literal['allow', 'deny', 'ask']
    reason: str
    updated_args: dict[str, Any] | None = None


class PermissionEnforcer:
    """Outer-layer permission enforcement based on user intent and configuration."""

    def __init__(
        self,
        config: PermissionConfig,
        handler: PermissionHandler | None = None,
        memory: PermissionMemory | None = None,
        provider: PermissionDecisionProvider | None = None,
    ) -> None:
        self._config = config
        self._handler = handler or AutoPermissionHandler()
        self._memory = memory or PermissionMemory()
        self._provider = provider
        self._matcher = PermissionMatcher()
        # Parallel tool calls (asyncio.gather in ToolManager.parallel_call_tool)
        # would otherwise invoke the interactive handler concurrently — N
        # prompts fighting over one terminal deadlocks. Serialize asks with a
        # lock created lazily per running loop (the per-turn TUI uses a fresh
        # loop each turn, so a single init-time Lock would bind to the wrong one).
        self._ask_lock: asyncio.Lock | None = None
        self._ask_lock_loop = None
        self._ask_thread_lock = threading.RLock()

    def _ask_lock_for_loop(self) -> 'asyncio.Lock':
        loop = asyncio.get_running_loop()
        with self._ask_thread_lock:
            if self._ask_lock is None or self._ask_lock_loop is not loop:
                self._ask_lock = asyncio.Lock()
                self._ask_lock_loop = loop
            return self._ask_lock

    async def _serialized_ask(self, **kwargs) -> PermissionResponse:
        # ``call_id`` is a newer, optional kwarg (see check()). A handler that
        # predates it — or a lightweight test double — need not accept it; drop
        # it for such handlers so their fixed signature keeps working.
        if 'call_id' in kwargs and not self._handler_accepts('call_id'):
            kwargs.pop('call_id')
        if (
            'workspace_root' in kwargs
            and not self._handler_accepts('workspace_root')
        ):
            kwargs.pop('workspace_root')
        while not self._ask_thread_lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            async with self._ask_lock_for_loop():
                return await self._handler.ask(**kwargs)
        finally:
            self._ask_thread_lock.release()

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

        if force_decision and force_decision.action == 'deny':
            return force_decision

        if force_decision and force_decision.action == 'ask':
            if not self._human_approval_available():
                return PermissionDecision(
                    action='deny',
                    reason=(
                        'Safety approval requires a human, but no human '
                        'approval handler is available'),
                )
            suggestions = generate_suggestions(tool_name, tool_args)
            response = await self._serialized_ask(
                tool_name=tool_name,
                tool_args=tool_args,
                context=force_decision.reason or '',
                suggestions=suggestions,
                call_id=call_id,
                workspace_root=self._workspace_root(),
            )
            return self._process_response(response, tool_name, tool_args)

        # 2. Full-access / legacy auto / strict mode → allow (SafetyGuard
        # remains the non-bypassable inner layer).
        if self._config.mode in ('auto', 'strict', 'full_access'):
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

        # 5. Delegate unknown calls to an injected automated provider.
        if self._config.mode == 'delegate':
            return await self._delegate(tool_name, tool_args, call_id=call_id)

        # 6. Ask user via handler (serialized against parallel tool calls)
        if not self._human_approval_available():
            return PermissionDecision(
                action='deny',
                reason='Interactive approval requires a human handler',
            )
        suggestions = generate_suggestions(tool_name, tool_args)
        response = await self._serialized_ask(
            tool_name=tool_name,
            tool_args=tool_args,
            context='',
            suggestions=suggestions,
            call_id=call_id,
            workspace_root=self._workspace_root(),
        )

        return self._process_response(response, tool_name, tool_args)

    def _workspace_root(self) -> str:
        root = getattr(self._memory, 'project_root', None)
        return str(root) if root else ''

    def _human_approval_available(self) -> bool:
        # AutoPermissionHandler cannot prompt — ignore the YAML flag.
        # Otherwise honor ``human_approval_available``, and treat interactive
        # mode as a person at the terminal even if the constructor defaulted
        # the flag to False.
        if isinstance(self._handler, AutoPermissionHandler):
            return False
        if self._config.human_approval_available:
            return True
        return self._config.mode == 'interactive'

    async def _delegate(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        call_id: str,
    ) -> PermissionDecision:
        suggestions = generate_suggestions(tool_name, tool_args)
        if self._provider is None:
            provider_decision = ProviderDecision(
                'uncertain', 'No permission decision provider is configured')
        else:
            provider_decision = await request_provider_decision(
                self._provider,
                tool_name=tool_name,
                tool_args=tool_args,
                context='',
                suggestions=suggestions,
                timeout=self._config.provider_timeout,
            )

        if provider_decision.action == 'allow_once':
            return PermissionDecision(
                action='allow',
                reason=provider_decision.reason or 'Delegated provider allowed once',
            )
        if provider_decision.action == 'deny':
            return PermissionDecision(
                action='deny',
                reason=(
                    provider_decision.feedback
                    or provider_decision.reason
                    or 'Delegated provider denied'
                ),
            )

        context = (
            provider_decision.reason
            or 'Delegated provider was uncertain')
        if self._human_approval_available():
            response = await self._serialized_ask(
                tool_name=tool_name,
                tool_args=tool_args,
                context=context,
                suggestions=suggestions,
                call_id=call_id,
                workspace_root=self._workspace_root(),
            )
            return self._process_response(response, tool_name, tool_args)
        return PermissionDecision(
            action='deny',
            reason=f'Delegated provider uncertain: {context}',
        )

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
            content = (
                pattern.split(CONTENT_SEP, 1)[1]
                if CONTENT_SEP in pattern else pattern)
            if '|' in content:
                return PermissionDecision(
                    action='deny',
                    reason='Refusing to persist a rule that uses | alternatives',
                )
            self._memory.add(pattern, scope=response.scope, source='user')
            return PermissionDecision(
                action='allow',
                reason=(
                    f'User allowed always '
                    f'(scope: {response.scope}, pattern: {pattern})'
                ),
            )

        if response.action == PermissionAction.MODIFY:
            updated = response.updated_args or tool_args
            for pattern in self._config.blacklist:
                if self._matcher.match_with_content(
                        pattern, tool_name, updated):
                    return PermissionDecision(
                        action='deny',
                        reason=f'Denied by blacklist after edit: {pattern}',
                    )
            return PermissionDecision(
                action='allow',
                reason='User modified args',
                updated_args=updated,
            )

        if response.action == PermissionAction.DENY:
            return PermissionDecision(
                action='deny',
                reason=response.feedback or 'User denied',
            )

        return PermissionDecision(action='deny', reason='Unknown action')
