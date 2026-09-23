# Copyright (c) ModelScope Contributors. All rights reserved.
"""Cloud Agent runtime: run a real in-process LLMAgent for cloud endpoints."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from ms_agent.team.context import ContextBundleAssembler
from ms_agent.team.events import TeamEvent
from ms_agent.team.models import DispatchEnvelope
from ms_agent.team.tools.endpoint_tools import (
    TeamArtifactTools,
    TeamEndpointTools,
    TeamTaskBoardTools,
)

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _ms_agent_home() -> Path:
    return Path(
        os.path.expanduser(os.environ.get('MS_AGENT_HOME') or '~/.ms_agent')
    ).resolve()


class _TeamEventSink:
    """Bridge LLMAgent event_sink → asyncio.Queue (ContentDelta, etc.)."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    def emit(self, event: Any) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


class CloudAgentRuntime:
    """Execute DispatchEnvelope against an in-process LLMAgent.

    Real execution is the default. Set ``MS_AGENT_TEAM_CLOUD_DRY_RUN=1`` only for
    offline tests. Requires LLM credentials in ``~/.ms_agent/settings.json`` or
    ``OPENAI_API_KEY`` / provider env vars.
    """

    def __init__(
        self,
        *,
        endpoint_store=None,
        artifact_store=None,
        task_store=None,
        dispatch_log=None,
        endpoint_token_store=None,
        project_store=None,
        coordinator=None,
        event_sink=None,
        agent_config_path: str | None = None,
        dry_run: bool | None = None,
        permission_store=None,
    ) -> None:
        self.endpoint_store = endpoint_store
        self.artifact_store = artifact_store
        self.task_store = task_store
        self.dispatch_log = dispatch_log
        self.endpoint_token_store = endpoint_token_store
        self.project_store = project_store
        self.coordinator = coordinator
        self.event_sink = event_sink
        self.agent_config_path = agent_config_path
        self._permission_store = permission_store
        if dry_run is None:
            dry_run = _env_flag('MS_AGENT_TEAM_CLOUD_DRY_RUN', False)
        self.dry_run = bool(dry_run)
        self._permission_handlers: dict[str, Any] = {}

    async def run(self, envelope: DispatchEnvelope) -> dict[str, Any]:
        parts: list[str] = []
        error: str | None = None
        async for ev in self.stream(envelope):
            if self.event_sink is not None:
                maybe = self.event_sink(ev)
                if hasattr(maybe, '__await__'):
                    await maybe
            if ev.type == 'team.stream':
                text = (ev.payload or {}).get('content') or ''
                if text:
                    parts.append(str(text))
            elif ev.type == 'team.dispatch_error':
                error = str((ev.payload or {}).get('error') or 'error')
        if error:
            return {
                'ok': False,
                'dispatch_id': envelope.dispatch_id,
                'summary': '\n'.join(parts)[-8000:],
                'error': error,
            }
        return {
            'ok': True,
            'dispatch_id': envelope.dispatch_id,
            'summary': '\n'.join(parts)[-8000:],
        }

    async def stream(
            self,
            envelope: DispatchEnvelope) -> AsyncIterator[TeamEvent]:
        prompt = ContextBundleAssembler.merge_prompt(
            envelope.prompt, envelope.context_bundle)

        yield TeamEvent(
            type='team.dispatch_start',
            project_id=envelope.project_id,
            dispatch_id=envelope.dispatch_id,
            endpoint_id=envelope.target_endpoint_id,
            at_name=envelope.target_at_name,
            payload={'prompt': envelope.prompt},
        )

        if self.dry_run:
            yield TeamEvent(
                type='team.stream',
                project_id=envelope.project_id,
                dispatch_id=envelope.dispatch_id,
                endpoint_id=envelope.target_endpoint_id,
                at_name=envelope.target_at_name,
                payload={
                    'type': 'text',
                    'content': f'[cloud dry-run] {prompt[:500]}',
                },
            )
            yield TeamEvent(
                type='team.dispatch_done',
                project_id=envelope.project_id,
                dispatch_id=envelope.dispatch_id,
                endpoint_id=envelope.target_endpoint_id,
                at_name=envelope.target_at_name,
                payload={'ok': True, 'summary': 'dry-run complete'},
            )
            return

        try:
            async for ev in self._run_agent(envelope, prompt):
                yield ev
        except Exception as exc:  # noqa: BLE001
            logger.exception('Cloud runtime failed')
            yield TeamEvent(
                type='team.dispatch_error',
                project_id=envelope.project_id,
                dispatch_id=envelope.dispatch_id,
                endpoint_id=envelope.target_endpoint_id,
                at_name=envelope.target_at_name,
                payload={'error': str(exc)},
            )

    async def _run_agent(
        self,
        envelope: DispatchEnvelope,
        prompt: str,
    ) -> AsyncIterator[TeamEvent]:
        from ms_agent.ui.events import (
            ContentDelta,
            ContentEnd,
            ErrorRaised,
            ToolCallCompleted,
            ToolCallStarted,
        )

        sink = _TeamEventSink()
        agent = self._build_agent(envelope, sink)

        final_text_parts: list[str] = []

        async def _drive() -> None:
            try:
                result = await agent.run(prompt, stream=True)
                if hasattr(result, '__aiter__'):
                    async for chunk in result:
                        text = _message_text(chunk)
                        if text:
                            final_text_parts.append(text)
                else:
                    text = _message_text(result)
                    if text:
                        final_text_parts.append(text)
            except Exception as exc:  # noqa: BLE001
                sink.emit(ErrorRaised(message=str(exc)))
            finally:
                await sink.queue.put(None)

        task = asyncio.create_task(_drive())
        had_text = False
        stream_text: list[str] = []
        try:
            while True:
                item = await sink.queue.get()
                if item is None:
                    break
                et = getattr(item, 'type', None) or getattr(
                    item, 'EVENT_TYPE', '')
                if et == 'content_delta' or isinstance(item, ContentDelta):
                    text = getattr(item, 'text', '') or ''
                    if not text:
                        continue
                    had_text = True
                    stream_text.append(text)
                    yield TeamEvent(
                        type='team.stream',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload={'type': 'text', 'content': text},
                    )
                elif et == 'tool_call_started' or isinstance(
                        item, ToolCallStarted):
                    yield TeamEvent(
                        type='team.stream',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload={
                            'type': 'tool_call',
                            'call_id': getattr(item, 'call_id', '') or '',
                            'name': getattr(item, 'name', '') or '',
                            'arguments': getattr(item, 'arguments', None),
                            'status': 'running',
                        },
                    )
                elif et == 'tool_call_completed' or isinstance(
                        item, ToolCallCompleted):
                    err = getattr(item, 'error', None)
                    yield TeamEvent(
                        type='team.stream',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload={
                            'type': 'tool_result',
                            'call_id': getattr(item, 'call_id', '') or '',
                            'name': getattr(item, 'name', '') or '',
                            'result': getattr(item, 'result', '') or '',
                            'error': err,
                            'status': 'error' if err else 'done',
                            'duration_s': getattr(item, 'duration_s', None),
                        },
                    )
                elif et == 'error' or isinstance(item, ErrorRaised):
                    msg = getattr(item, 'message', None) or str(item)
                    yield TeamEvent(
                        type='team.dispatch_error',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload={'error': msg},
                    )
                    return
                elif et == 'content_end' or isinstance(item, ContentEnd):
                    continue
                elif isinstance(item, dict) and item.get(
                        'type') == 'permission_request':
                    payload = dict(item)
                    payload['source'] = 'cloud'
                    yield TeamEvent(
                        type='team.permission_requested',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload=payload,
                    )
                    yield TeamEvent(
                        type='team.dispatch_waiting_approval',
                        project_id=envelope.project_id,
                        dispatch_id=envelope.dispatch_id,
                        endpoint_id=envelope.target_endpoint_id,
                        at_name=envelope.target_at_name,
                        payload={
                            'call_id': payload.get('call_id') or '',
                            'request_id': payload.get('request_id') or '',
                        },
                    )
        finally:
            self.drop_permission_handler(envelope.dispatch_id)
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            else:
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    raise exc

        if not had_text:
            fallback = ''.join(final_text_parts).strip()
            if fallback:
                yield TeamEvent(
                    type='team.stream',
                    project_id=envelope.project_id,
                    dispatch_id=envelope.dispatch_id,
                    endpoint_id=envelope.target_endpoint_id,
                    at_name=envelope.target_at_name,
                    payload={'type': 'text', 'content': fallback},
                )

        yield TeamEvent(
            type='team.dispatch_done',
            project_id=envelope.project_id,
            dispatch_id=envelope.dispatch_id,
            endpoint_id=envelope.target_endpoint_id,
            at_name=envelope.target_at_name,
            payload={
                'ok': True,
                'summary': (
                    ''.join(stream_text) or ''.join(final_text_parts)
                )[-4000:],
            },
        )

    def _workspace(self, envelope: DispatchEnvelope) -> str:
        if self.project_store is not None and envelope.project_id:
            try:
                meta = self.project_store.get(envelope.project_id)
                path = getattr(meta, 'workspace_path', None) or ''
                if path:
                    return str(Path(path).expanduser().resolve())
            except Exception:  # noqa: BLE001
                pass
        return str(_ms_agent_home() / 'team_workspaces' / (
            envelope.project_id or 'default'))

    def _teammate_at_names(self) -> list[str]:
        if self.endpoint_store is None:
            return []
        try:
            endpoints = self.endpoint_store.list()
        except Exception:  # noqa: BLE001
            return []
        names: list[str] = []
        seen: set[str] = set()
        for ep in endpoints:
            name = (getattr(ep, 'at_name', '') or '').lstrip('@').strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _plan_only(self, envelope: DispatchEnvelope) -> bool:
        if not envelope.referenced_task_id or self.task_store is None:
            return False
        try:
            task = self.task_store.get(envelope.referenced_task_id)
        except Exception:  # noqa: BLE001
            return False
        if task is None:
            return False
        project = None
        if self.project_store is not None and envelope.project_id:
            try:
                project = self.project_store.get(envelope.project_id)
            except Exception:  # noqa: BLE001
                project = None
        from ms_agent.team.plan_gate import task_needs_plan_pass
        return task_needs_plan_pass(task, project)

    def _system_prompt(self, envelope: DispatchEnvelope) -> str:
        audience = ''
        bundle = envelope.context_bundle
        if bundle is not None:
            audience = getattr(bundle, 'audience', '') or ''
        workspace = self._workspace(envelope)
        teammate_line = (
            ', '.join(self._teammate_at_names()) or '(none registered)')
        has_board = bool(getattr(bundle, 'task_snapshots', None)) if bundle else False
        if audience == 'lead':
            collect = (
                'To collect results: task_board_read, then '
                'dispatch_result_read with last_dispatch_id. Never glob/find/ls '
                'for teammate output, and never rewrite their final answers. '
                'Never use todo_write for teammate work — that is a local '
                'checklist. Only task_board_write creates dispatchable rows.\n'
            )
            if has_board:
                return (
                    f'You are {envelope.target_at_name}, the Team Lead.\n'
                    f'Project workspace: {workspace}\n'
                    'Teammates you may assign with task_board_write.target_at_name '
                    f'(plain names, never an at-sign): {teammate_line}.\n'
                    'The board already has rows. Do not write a second decompose. '
                    'Call task_board_read, then dispatch_result_read for completed '
                    'workers. Report status only. Only call task_board_write if a '
                    'needed row is missing or a wait_human row asked a question '
                    '(rewrite that prompt with the missing fact, status=pending).\n'
                    + collect
                )
            return (
                f'You are {envelope.target_at_name}, the Team Lead.\n'
                f'Project workspace: {workspace}\n'
                'Teammates you may assign with task_board_write.target_at_name '
                f'(plain names, never an at-sign): {teammate_line}.\n'
                'On a large job:\n'
                '1. Split work with task_board_write. Each prompt MUST be a '
                'complete instruction the worker can execute without the '
                'original user message. If the job itself contains brace '
                'tokens, copy them. If a required path or fact is missing, '
                'call task_ask with audience=user and stop — do not invent.\n'
                '2. New teammate work uses status=pending plus target_at_name. '
                'Independent tasks omit blocked_by. Dependent tasks set '
                'blocked_by to the blocking task_id values returned by '
                'previous writes.\n'
                '3. Do not mention teammates with an at-sign in chat — that '
                'bypasses the board.\n'
                '4. Do not solve extract or worker subtasks yourself. After '
                'writing the board, stop. Do not reply with a prose plan in '
                'chat instead of board rows — the board is the plan.\n'
                '5. Do not invent missing facts or write vague '
                '"read related docs" rows.\n'
                '6. If task_board_read shows wait_human with a question, that '
                'worker is blocked. Patch that row\'s prompt with the missing '
                'fact and set status=pending. Do not treat the question as a '
                'finished extract.\n'
                + collect
            )
        worker = (
            f'You are {envelope.target_at_name}, a teammate on MS-Agent Team. '
            f'Work inside {workspace}.\n'
            'If this task is to collect or merge teammate results: call '
            'task_board_read, then dispatch_result_read for each '
            'last_dispatch_id. Do not glob/find/ls for their output. Do not '
            're-solve their work. Quote their final replies; do not paraphrase.\n'
            'Otherwise use files, shell, and search as needed and reply with '
            'the result. If a required path or fact is missing, call '
            'task_ask (defaults to Lead; audience=user only if the human '
            'must answer). Stop after the tool. A question in the reply is '
            'treated as a finished answer.\n'
        )
        if self._plan_only(envelope):
            return (
                worker
                + 'PLAN GATE is on: read-only this turn. Do not write or '
                'edit files, and do not run mutating or network shell. '
                'Write the plan via task_board_write.result_summary and stop.'
            )
        return worker

    async def _on_board_write(self, task) -> None:
        coord = self.coordinator
        project_id = getattr(task, 'project_id', None)
        if coord is None or not project_id:
            return
        await coord.run(project_id)

    def _on_ask(self, task) -> None:
        ingress = getattr(self.coordinator, 'ingress', None)
        if ingress is None:
            return
        ingress.escalate_to_lead(task)

    def _build_agent(self, envelope: DispatchEnvelope, sink: _TeamEventSink):
        from ms_agent.agent.llm_agent import LLMAgent
        from ms_agent.agent.loader import AgentLoader
        from ms_agent.config import ConfigResolver
        from ms_agent.permission.handler import WebPermissionHandler
        from omegaconf import OmegaConf

        handler = WebPermissionHandler(
            sink,
            store=self._permission_store,
            project_id=envelope.project_id or '',
            dispatch_id=envelope.dispatch_id,
            session_id=envelope.runtime_session_id or envelope.dispatch_id,
        )
        self._permission_handlers[envelope.dispatch_id] = handler

        if self.agent_config_path:
            agent = AgentLoader.build(
                config_dir_or_id=self.agent_config_path,
                trust_remote_code=True,
                event_sink=sink,
            )
            agent.set_permission_handler(handler)
            agent.set_permission_approval_store(self._permission_store)
            try:
                agent.set_permission_mode(
                    'plan' if self._plan_only(envelope) else 'delegate')
            except Exception:  # noqa: BLE001
                logger.debug('could not set permission mode', exc_info=True)
            self._ensure_llm_credentials(agent.config)
            return agent

        home = _ms_agent_home()
        workspace = self._workspace(envelope)
        Path(workspace).mkdir(parents=True, exist_ok=True)

        session_key = envelope.runtime_session_id or envelope.dispatch_id
        sdir = home / 'team_sessions' / (envelope.project_id
                                         or 'default') / session_key
        sdir.mkdir(parents=True, exist_ok=True)

        resolver = ConfigResolver(
            global_dir=str(home), project_root=workspace)
        cfg = resolver.resolve(
            agent_config=None,
            project_path=workspace,
            session_overrides={
                'session_log': {
                    'dir': str(sdir),
                    'session_key': session_key,
                    'enabled': True,
                },
                'output_dir': workspace,
            },
        )

        system = self._system_prompt(envelope)
        # Team cloud is non-interactive: drop stdin input_callback and pin
        # local_dir so register_callback_from_config does not assert.
        existing_cbs = list(OmegaConf.select(cfg, 'callbacks') or [])
        callbacks = [
            c for c in existing_cbs
            if c not in ('input_callback', ) and not str(c).endswith(
                'input_callback')
        ]
        for key, value in {
                'interactive': False,
                'local_dir': workspace,
                'callbacks': callbacks,
                # Force legacy llm.* path — settings provider router often
                # points at api.openai.com without a usable key.
                'llm.use_provider_router': False,
                'generation_config.stream': True,
                # Must be True so ContentDelta reaches event_sink → Team SSE/timeline.
                'generation_config.stream_output': True,
                'generation_config.show_reasoning': False,
                'max_chat_round': int(
                    os.environ.get('MS_AGENT_TEAM_MAX_ROUND', '40')),
                'prompt.system': system,
                'session_log.enabled': True,
                'output_dir': workspace,
                'permission.mode': (
                    'plan' if self._plan_only(envelope) else 'delegate'),
        }.items():
            OmegaConf.update(cfg, key, value, merge=True)

        audience = ''
        if envelope.context_bundle is not None:
            audience = getattr(envelope.context_bundle, 'audience', '') or ''
        if audience == 'lead':
            OmegaConf.update(
                cfg, 'tools.todo_list.enabled', False, merge=True)

        self._ensure_llm_credentials(cfg, force_env=True)
        agent = LLMAgent(
            cfg,
            tag=f'team-{envelope.target_at_name}',
            trust_remote_code=True,
            event_sink=sink,
        )
        agent.set_permission_handler(handler)
        agent.set_permission_approval_store(self._permission_store)
        orig_prepare = agent.prepare_tools

        async def _prepare_and_attach():
            await orig_prepare()
            await self._maybe_attach_tools(agent, envelope)

        agent.prepare_tools = _prepare_and_attach
        return agent

    def resolve_permission(
        self,
        dispatch_id: str,
        request_id: str,
        action: str = 'allow_once',
        pattern: str | None = None,
    ) -> bool:
        """Wake a live cloud ``WebPermissionHandler`` Future."""
        from ms_agent.permission.handler import (
            PermissionAction,
            PermissionResponse,
        )

        handler = self._permission_handlers.get(dispatch_id)
        if handler is None:
            return False
        mapped = {
            'allow_once': PermissionAction.ALLOW_ONCE,
            'allow_session': PermissionAction.ALLOW_SESSION,
            'allow_always': PermissionAction.ALLOW_ALWAYS,
            'deny': PermissionAction.DENY,
            'reject': PermissionAction.DENY,
        }.get(action, PermissionAction.ALLOW_ONCE)
        handler.resolve(
            request_id,
            PermissionResponse(action=mapped, pattern=pattern),
        )
        return True

    def drop_permission_handler(self, dispatch_id: str) -> None:
        self._permission_handlers.pop(dispatch_id, None)

    def _ensure_llm_credentials(self, cfg, *, force_env: bool = False) -> None:
        """Fill llm.api_key / base_url from env when settings left them empty."""
        from omegaconf import OmegaConf

        llm = OmegaConf.select(cfg, 'llm') or {}
        api_key = str(getattr(llm, 'api_key', None) or '')
        base_url = str(getattr(llm, 'base_url', None) or '')

        if force_env or not api_key:
            for env_name in (
                    'OPENAI_API_KEY',
                    'MODELSCOPE_API_TOKEN',
                    'DASHSCOPE_API_KEY',
                    'MS_AGENT_API_KEY',
            ):
                val = os.environ.get(env_name, '').strip()
                if val:
                    OmegaConf.update(cfg, 'llm.api_key', val, merge=True)
                    api_key = val
                    break

        if force_env or not base_url:
            for env_name in ('OPENAI_BASE_URL', 'MODELSCOPE_API_BASE'):
                val = os.environ.get(env_name, '').strip()
                if val:
                    OmegaConf.update(cfg, 'llm.base_url', val, merge=True)
                    base_url = val
                    break

        model = os.environ.get('MS_AGENT_LLM_MODEL', '').strip()
        if model and (force_env or not OmegaConf.select(cfg, 'llm.model')):
            OmegaConf.update(cfg, 'llm.model', model, merge=True)
        provider = os.environ.get('MS_AGENT_LLM_PROVIDER', '').strip()
        if provider and (force_env or not OmegaConf.select(cfg, 'llm.service')):
            OmegaConf.update(cfg, 'llm.service', provider, merge=True)

        # Also peek settings.json providers.
        if not api_key:
            settings = _read_settings()
            providers = settings.get('providers') or {}
            llm_block = settings.get('llm') or {}
            provider_id = llm_block.get('provider') or llm_block.get('service')
            cand = providers.get(provider_id) if provider_id else None
            if isinstance(cand, dict) and cand.get('api_key'):
                OmegaConf.update(
                    cfg, 'llm.api_key', cand['api_key'], merge=True)
                api_key = cand['api_key']
                if cand.get('base_url') and not base_url:
                    OmegaConf.update(
                        cfg, 'llm.base_url', cand['base_url'], merge=True)
            if not api_key and llm_block.get('api_key'):
                OmegaConf.update(
                    cfg, 'llm.api_key', llm_block['api_key'], merge=True)
                api_key = llm_block['api_key']

        if not str(OmegaConf.select(cfg, 'llm.api_key') or '').strip():
            raise RuntimeError(
                'Cloud @agent needs LLM credentials. Set OPENAI_API_KEY '
                '(and OPENAI_BASE_URL if needed) in ms-agent-webui/backend/.env, '
                'or fill providers.*.api_key in ~/.ms_agent/settings.json. '
                'Alternatively pair a local Bridge (ACP Claude) instead of cloud.'
            )

    async def _maybe_attach_tools(self, agent, envelope: DispatchEnvelope) -> None:
        """Best-effort injection of team primitive tools."""
        try:
            from omegaconf import OmegaConf
            cfg = OmegaConf.create({'output_dir': self._workspace(envelope)})
            tools = [
                TeamEndpointTools(
                    cfg,
                    endpoint_store=self.endpoint_store,
                    endpoint_token_store=self.endpoint_token_store,
                    owner_user_id=envelope.sender_user_id,
                ),
                TeamArtifactTools(cfg, artifact_store=self.artifact_store),
                TeamTaskBoardTools(
                    cfg,
                    task_store=self.task_store,
                    dispatch_log=self.dispatch_log,
                    on_change=self._on_board_write,
                    on_ask=self._on_ask,
                    endpoint_store=self.endpoint_store,
                ),
            ]
            tm = getattr(agent, 'tool_manager', None)
            if tm is None:
                logger.warning(
                    'Team tools skipped: tool_manager is not ready')
                return
            extra = getattr(tm, 'extra_tools', None)
            if isinstance(extra, list):
                extra.extend(tools)
            index = getattr(tm, 'index_extra_tool', None)
            if callable(index):
                for tool in tools:
                    maybe = index(tool)
                    if hasattr(maybe, '__await__'):
                        await maybe
            logger.info(
                'Attached team tools for %s: %s',
                envelope.target_at_name,
                [type(t).__name__ for t in tools],
            )
        except Exception:  # noqa: BLE001
            logger.warning('Could not attach team tools', exc_info=True)


def _read_settings() -> dict:
    import json
    path = _ms_agent_home() / 'settings.json'
    try:
        return json.loads(path.read_text(encoding='utf-8')) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _message_text(chunk: Any) -> str:
    if chunk is None:
        return ''
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, list) and chunk:
        return _message_text(chunk[-1])
    content = getattr(chunk, 'content', None)
    if content is not None:
        return str(content)
    if isinstance(chunk, dict):
        return str(chunk.get('content') or '')
    return str(chunk)
