# Copyright (c) ModelScope Contributors. All rights reserved.
"""Platform primitive tools for cross-endpoint collaboration.

These are general Tools (not domain playbooks). Lead Agents use them to
delegate, wait for ephemeral endpoints, and move artifacts. Tool descriptions
must stay self-explanatory so thick Skills are unnecessary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

from ms_agent.llm.utils import Tool
from ms_agent.team.errors import ARTIFACT_NOT_FOUND, ENDPOINT_NOT_FOUND, TeamError
from ms_agent.team.models import (
    Artifact,
    ContextBundle,
    DispatchEnvelope,
    EndpointToken,
    TeamTask,
    new_id,
)
from ms_agent.tools.base import ToolBase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamEndpointTools(ToolBase):
    """delegate / status / wait / issue_token — platform primitives."""

    SERVER_NAME = 'team_endpoint'

    def __init__(self, config, **kwargs):
        super().__init__(config)
        self._registry = kwargs.get('endpoint_store')
        self._dispatch_fn: Optional[Callable] = kwargs.get('dispatch_fn')
        self._token_store = kwargs.get('endpoint_token_store')
        self._owner_user_id = kwargs.get('owner_user_id', '')
        tool_cfg = getattr(getattr(config, 'tools', None), 'team_endpoint',
                           None)
        if tool_cfg is not None:
            self.exclude_func(tool_cfg)

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def _get_tools_inner(self) -> dict[str, list]:
        return {
            self.SERVER_NAME: [
                Tool(
                    tool_name='delegate_to_endpoint',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Delegate a sub-task to another AgentEndpoint by '
                        '@at_name (e.g. @me-eas-amd). The platform only routes '
                        'the prompt + optional context_bundle; it does NOT run '
                        'domain pipelines. Use for cross-machine collaboration. '
                        'Ephemeral endpoints may be offline while rebuilding — '
                        'call wait_for_endpoint_online first if needed.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'at_name': {
                                'type': 'string',
                                'description':
                                'Target @ name without leading @',
                            },
                            'prompt': {
                                'type': 'string',
                                'description': 'Sub-task prompt for the worker',
                            },
                            'project_id': {
                                'type': 'string',
                                'description': 'Project context id',
                            },
                            'context_bundle': {
                                'type': 'object',
                                'description':
                                'Optional artifacts[] / deployment_context',
                            },
                            'timeout_s': {
                                'type': 'integer',
                                'description': 'Wait timeout seconds',
                                'default': 600,
                            },
                        },
                        'required': ['at_name', 'prompt'],
                    },
                ),
                Tool(
                    tool_name='get_endpoint_status',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Get online/offline/reconnecting/busy status for an '
                        'endpoint. Ephemeral (EAS) endpoints show reconnecting '
                        'while a new container bootstraps the bridge.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'at_name': {
                                'type': 'string'
                            },
                            'endpoint_id': {
                                'type': 'string'
                            },
                        },
                    },
                ),
                Tool(
                    tool_name='wait_for_endpoint_online',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Poll until an endpoint is online or timeout. Platform '
                        'does NOT auto-resume domain work — the Lead Agent '
                        'decides next steps after this returns. Typical use: '
                        'after pushing a new EAS image, wait for @me-eas-amd.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'at_name': {
                                'type': 'string'
                            },
                            'endpoint_id': {
                                'type': 'string'
                            },
                            'timeout_s': {
                                'type': 'integer',
                                'default': 600
                            },
                            'poll_interval_s': {
                                'type': 'number',
                                'default': 5
                            },
                        },
                    },
                ),
                Tool(
                    tool_name='issue_endpoint_token',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Issue a scoped endpoint token for baking into an '
                        'ephemeral image ENV/ARG during docker build. The Lead '
                        'Agent injects the token; the platform does not write '
                        'Dockerfiles or trigger container entrypoints.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'endpoint_id': {
                                'type': 'string'
                            },
                            'ttl_seconds': {
                                'type': 'integer',
                                'default': 86400
                            },
                        },
                        'required': ['endpoint_id'],
                    },
                ),
            ]
        }

    async def call_tool(self, server_name: str, *, tool_name: str,
                        tool_args: dict) -> str:
        if tool_name == 'delegate_to_endpoint':
            return await self._delegate(tool_args)
        if tool_name == 'get_endpoint_status':
            return self._status(tool_args)
        if tool_name == 'wait_for_endpoint_online':
            return await self._wait(tool_args)
        if tool_name == 'issue_endpoint_token':
            return self._issue_token(tool_args)
        return json.dumps({'error': f'Unknown tool {tool_name}'})

    def _resolve_endpoint(self, args: dict):
        if self._registry is None:
            raise TeamError(ENDPOINT_NOT_FOUND, 'No endpoint store wired')
        if args.get('endpoint_id'):
            ep = self._registry.get(args['endpoint_id'])
        else:
            ep = self._registry.get_by_at_name(
                args.get('at_name', '').lstrip('@'))
        if ep is None:
            raise TeamError(ENDPOINT_NOT_FOUND, http_status=404)
        return ep

    async def _delegate(self, args: dict) -> str:
        ep = self._resolve_endpoint(args)
        # Never hardcode owner — enforce invoke gate against the acting user.
        from ms_agent.team.models import TeamFeatureFlags
        from ms_agent.team.policies import InvokeGate, RemoteProfileEnforcer

        sender = self._owner_user_id or ''
        flags = TeamFeatureFlags(remote_invoke_enabled=False)
        try:
            caller_is_owner = InvokeGate.check(ep, sender, flags)
        except TeamError as exc:
            return json.dumps(exc.to_dict())
        tier = RemoteProfileEnforcer.permission_tier(ep, caller_is_owner)
        bundle = ContextBundle.from_dict(args.get('context_bundle'))
        envelope = DispatchEnvelope(
            dispatch_id=new_id('d_'),
            prompt=args['prompt'],
            project_id=args.get('project_id') or ep.default_project_id or '',
            target_endpoint_id=ep.endpoint_id,
            target_at_name=ep.at_name,
            sender_user_id=sender or ep.owner_user_id,
            channel='web',
            thread_id=None,
            context_bundle=bundle,
            permission_tier=tier,  # type: ignore[arg-type]
            caller_is_owner=caller_is_owner,
        )
        if self._dispatch_fn is None:
            return json.dumps({
                'status': 'queued_local_stub',
                'dispatch': envelope.to_dict(),
                'note': 'No dispatch_fn wired; envelope built only.',
            })
        result = await self._dispatch_fn(envelope)
        return json.dumps({
            'status': 'ok',
            'dispatch_id': envelope.dispatch_id,
            'result': result,
        },
                          ensure_ascii=False,
                          default=str)

    def _status(self, args: dict) -> str:
        ep = self._resolve_endpoint(args)
        return json.dumps({
            'endpoint_id': ep.endpoint_id,
            'at_name': ep.at_name,
            'status': ep.status,
            'endpoint_type': ep.endpoint_type,
            'instance_id': ep.current_instance_id,
            'last_heartbeat': ep.last_heartbeat,
        })

    async def _wait(self, args: dict) -> str:
        timeout = int(args.get('timeout_s', 600))
        interval = float(args.get('poll_interval_s', 5))
        deadline = time.time() + timeout
        while time.time() < deadline:
            ep = self._resolve_endpoint(args)
            if ep.status == 'online':
                return json.dumps({
                    'status': 'online',
                    'endpoint_id': ep.endpoint_id,
                    'instance_id': ep.current_instance_id,
                })
            await asyncio.sleep(interval)
        ep = self._resolve_endpoint(args)
        return json.dumps({
            'status': 'timeout',
            'last_status': ep.status,
            'endpoint_id': ep.endpoint_id,
        })

    def _issue_token(self, args: dict) -> str:
        from ms_agent.team.models import new_secret_token
        ep = self._resolve_endpoint({'endpoint_id': args['endpoint_id']})
        ttl = int(args.get('ttl_seconds', 86400))
        token = new_secret_token('etok_')
        expires = (datetime.now(timezone.utc)
                   + timedelta(seconds=ttl)).isoformat()
        tok = EndpointToken(
            token=token,
            endpoint_id=ep.endpoint_id,
            owner_user_id=ep.owner_user_id,
            expires_at=expires,
        )
        if self._token_store is not None:
            self._token_store.put(tok)
        return json.dumps(tok.to_dict())


class TeamArtifactTools(ToolBase):
    """upload_artifact / download_artifact — platform locker."""

    SERVER_NAME = 'team_artifact'

    def __init__(self, config, **kwargs):
        super().__init__(config)
        self._store = kwargs.get('artifact_store')
        tool_cfg = getattr(getattr(config, 'tools', None), 'team_artifact',
                           None)
        if tool_cfg is not None:
            self.exclude_func(tool_cfg)

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def _get_tools_inner(self) -> dict[str, list]:
        return {
            self.SERVER_NAME: [
                Tool(
                    tool_name='upload_artifact',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Upload a file to the platform artifact store for '
                        'cross-endpoint transfer. Returns artifact_id, sha256, '
                        'and storage URL. Platform does not inspect contents.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'project_id': {
                                'type': 'string'
                            },
                            'filename': {
                                'type': 'string'
                            },
                            'content_base64': {
                                'type':
                                'string',
                                'description':
                                'Base64 file content (small/medium files)',
                            },
                            'local_path': {
                                'type':
                                'string',
                                'description':
                                'Local path on the calling endpoint (bridge)',
                            },
                        },
                        'required': ['project_id'],
                    },
                ),
                Tool(
                    tool_name='download_artifact',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Download artifact metadata (and optionally bytes) by '
                        'artifact_id. Use after another endpoint uploaded a package.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'artifact_id': {
                                'type': 'string'
                            },
                            'include_content_base64': {
                                'type': 'boolean',
                                'default': False
                            },
                        },
                        'required': ['artifact_id'],
                    },
                ),
            ]
        }

    async def call_tool(self, server_name: str, *, tool_name: str,
                        tool_args: dict) -> str:
        import base64
        import os

        if self._store is None:
            return json.dumps({'error': 'artifact_store not wired'})

        if tool_name == 'upload_artifact':
            data = b''
            if tool_args.get('content_base64'):
                data = base64.b64decode(tool_args['content_base64'])
            elif tool_args.get('local_path'):
                path = os.path.realpath(tool_args['local_path'])
                # Restrict to cwd / output_dir to avoid arbitrary file read.
                allowed_roots = [
                    os.path.realpath(os.getcwd()),
                    os.path.realpath(self.output_dir),
                ]
                if not any(
                        path == root or path.startswith(root + os.sep)
                        for root in allowed_roots):
                    return json.dumps({
                        'error':
                        'local_path outside allowed workspace'
                    })
                with open(path, 'rb') as f:
                    data = f.read()
            art = Artifact(
                artifact_id=new_id('art_'),
                project_id=tool_args['project_id'],
                sha256='',
                size=0,
                storage_url='',
                filename=tool_args.get('filename')
                or os.path.basename(tool_args.get('local_path') or 'blob'),
            )
            art = self._store.put(art, data=data)
            return json.dumps(art.to_dict())

        if tool_name == 'download_artifact':
            art = self._store.get(tool_args['artifact_id'])
            if art is None:
                raise TeamError(ARTIFACT_NOT_FOUND, http_status=404)
            out = art.to_dict()
            if tool_args.get('include_content_base64'):
                blob = self._store.get_bytes(art.artifact_id) or b''
                out['content_base64'] = base64.b64encode(blob).decode('ascii')
            return json.dumps(out)

        return json.dumps({'error': f'Unknown tool {tool_name}'})


class TeamTaskBoardTools(ToolBase):
    """Optional progress mirror — Agent writes; platform does not schedule."""

    SERVER_NAME = 'team_taskboard'

    def __init__(self, config, **kwargs):
        super().__init__(config)
        self._store = kwargs.get('task_store')
        self._dispatch_log = kwargs.get('dispatch_log')
        self._on_change: Optional[Callable] = kwargs.get('on_change')
        self._on_ask: Optional[Callable] = kwargs.get('on_ask')
        self._endpoints = kwargs.get('endpoint_store')
        tool_cfg = getattr(getattr(config, 'tools', None), 'team_taskboard',
                           None)
        if tool_cfg is not None:
            self.exclude_func(tool_cfg)

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def _get_tools_inner(self) -> dict[str, list]:
        return {
            self.SERVER_NAME: [
                Tool(
                    tool_name='task_board_write',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Create or update a task-board entry. New teammate '
                        'work MUST use status=pending plus target_at_name '
                        '(plain name, no at-sign). blocked_by is a dependency '
                        'DAG: Coordinator auto-dispatches when enabled, or a '
                        'human clicks 按板执行. Never send the original user '
                        'prompt back through Lead. Each prompt must be '
                        'self-contained. If a fact you need is missing, call '
                        'task_ask instead of inventing. Brace tokens in the '
                        'user job (for example replacing {DOC} with {DOC1}) '
                        'are allowed. Use parent_task_id for subtasks of a '
                        'larger job.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'task_id': {
                                'type': 'string'
                            },
                            'project_id': {
                                'type': 'string'
                            },
                            'status': {
                                'type':
                                'string',
                                'enum': [
                                    'pending', 'in_progress', 'completed',
                                    'failed', 'cancelled', 'wait_human'
                                ],
                            },
                            'prompt': {
                                'type': 'string'
                            },
                            'target_at_name': {
                                'type': 'string'
                            },
                            'blocked_by': {
                                'type': 'array',
                                'items': {
                                    'type': 'string'
                                }
                            },
                            'parent_task_id': {
                                'type': 'string'
                            },
                            'tags': {
                                'type': 'array',
                                'items': {'type': 'string'},
                                'description':
                                'Skill tags for self-claim. Empty tags stay '
                                'out of the unclaimed pool; Coordinator never '
                                'guesses an assignee.',
                            },
                            'output_artifacts': {
                                'type': 'array',
                                'items': {
                                    'type': 'string'
                                }
                            },
                            'deployment_context': {
                                'type': 'object'
                            },
                            'result_summary': {
                                'type': 'string'
                            },
                            'trigger_user_id': {
                                'type': 'string'
                            },
                        },
                        'required': ['project_id', 'status', 'prompt'],
                    },
                ),
                Tool(
                    tool_name='task_board_read',
                    server_name=self.SERVER_NAME,
                    description=(
                        'List task-board index rows for a project: status, '
                        '@at_name, last_dispatch_id, artifacts. Does NOT '
                        'include teammate transcripts. Use '
                        'dispatch_result_read with last_dispatch_id to fetch '
                        'the final reply.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'project_id': {
                                'type': 'string'
                            },
                            'task_id': {
                                'type': 'string'
                            },
                        },
                        'required': ['project_id'],
                    },
                ),
                Tool(
                    tool_name='dispatch_result_read',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Read a teammate dispatch\'s FINAL assistant reply by '
                        'dispatch_id (from the task board) or task_id. Returns '
                        'the completed result text, not tool traces. Use this '
                        'instead of find/ls/glob in the workspace. Works even '
                        'when the teammate did not write a file.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'dispatch_id': {
                                'type': 'string',
                                'description':
                                'last_dispatch_id from the task board',
                            },
                            'task_id': {
                                'type': 'string',
                            },
                        },
                    },
                ),
                Tool(
                    tool_name='task_ask',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Park this task as wait_human and ask for a missing '
                        'fact. Default audience is lead: the Lead patches the '
                        'prompt (your transcript is not dumped). audience=user '
                        'surfaces the question for the human on this rail. '
                        'Call this and stop instead of inventing files or '
                        'writing a question in the reply — a reply is treated '
                        'as a finished answer. Do not mention teammates with '
                        'an at-sign.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'project_id': {'type': 'string'},
                            'task_id': {'type': 'string'},
                            'question': {'type': 'string'},
                            'audience': {
                                'type': 'string',
                                'enum': ['user', 'lead'],
                            },
                        },
                        'required': ['project_id', 'question'],
                    },
                ),
                Tool(
                    tool_name='task_claim',
                    server_name=self.SERVER_NAME,
                    description=(
                        'Optimistic-lock claim of a pending task for this '
                        '@agent (Claude-style file lock). Pass claim_version '
                        'from the inbox / task_board_read. Concurrent claims '
                        'on the same task: only one succeeds.'
                    ),
                    parameters={
                        'type': 'object',
                        'properties': {
                            'task_id': {
                                'type': 'string'
                            },
                            'at_name': {
                                'type': 'string'
                            },
                            'expected_version': {
                                'type': 'integer'
                            },
                        },
                        'required': [
                            'task_id', 'at_name', 'expected_version'
                        ],
                    },
                ),
            ]
        }

    async def call_tool(self, server_name: str, *, tool_name: str,
                        tool_args: dict) -> str:
        if tool_name == 'dispatch_result_read':
            return json.dumps(self._dispatch_result(tool_args))

        if self._store is None:
            return json.dumps({'error': 'task_store not wired'})

        if tool_name == 'task_board_write':
            tid = tool_args.get('task_id') or new_id('task_')
            existing = self._store.get(tid)
            if 'target_at_name' in tool_args:
                target_at = tool_args.get('target_at_name')
            else:
                target_at = existing.target_at_name if existing else None
            if target_at:
                target_at = str(target_at).lstrip('@').strip() or None
            if 'parent_task_id' in tool_args:
                parent_id = tool_args.get('parent_task_id')
            else:
                parent_id = existing.parent_task_id if existing else None
            if 'blocked_by' in tool_args:
                blocked = list(tool_args.get('blocked_by') or [])
            else:
                blocked = list(
                    existing.blocked_by if existing else [])
            status = tool_args['status']
            live_id = existing.last_dispatch_id if existing else None
            # Lead often writes in_progress meaning "to do". Coordinator owns
            # inflight; assigned rows with no live dispatch start pending.
            if status == 'in_progress' and target_at and not live_id:
                status = 'pending'
            if 'tags' in tool_args:
                tags = [
                    str(x).strip() for x in (tool_args.get('tags') or [])
                    if str(x).strip()
                ]
            else:
                tags = list(existing.tags if existing else [])
            task = TeamTask(
                task_id=tid,
                project_id=tool_args['project_id'],
                status=status,
                prompt=tool_args.get('prompt')
                or (existing.prompt if existing else ''),
                trigger_user_id=tool_args.get('trigger_user_id')
                or (existing.trigger_user_id if existing else ''),
                target_at_name=target_at,
                target_endpoint_id=(
                    existing.target_endpoint_id if existing else None),
                blocked_by=blocked,
                output_artifacts=list(
                    tool_args.get('output_artifacts')
                    or (existing.output_artifacts if existing else [])),
                deployment_context=tool_args.get('deployment_context')
                if 'deployment_context' in tool_args else (
                    existing.deployment_context if existing else None),
                result_summary=tool_args.get('result_summary')
                if 'result_summary' in tool_args else (
                    existing.result_summary if existing else None),
                last_dispatch_id=(
                    existing.last_dispatch_id if existing else None),
                thread_id=existing.thread_id if existing else None,
                parent_task_id=parent_id,
                claim_version=existing.claim_version if existing else 0,
                tags=tags,
                created_at=existing.created_at if existing else _now_iso(),
                updated_at=_now_iso(),
            )
            from ms_agent.team.board import parent_completion_blocked
            from ms_agent.team.errors import PARENT_INCOMPLETE
            board = [
                t for t in self._store.list(task.project_id)
                if t.task_id != task.task_id
            ] + [task]
            blocked = parent_completion_blocked(board, task)
            if blocked:
                return json.dumps({
                    'error': PARENT_INCOMPLETE,
                    'message': blocked,
                })
            self._store.upsert(task)
            await self._maybe_kick(task)
            return json.dumps(task.to_dict())

        if tool_name == 'task_claim':
            from ms_agent.team.errors import TeamError
            at_name = str(tool_args['at_name'])
            caps: list[str] = []
            if self._endpoints is not None:
                ep = self._endpoints.get_by_at_name(at_name.lstrip('@'))
                if ep is not None:
                    caps = list(ep.capabilities or [])
            try:
                claimed = self._store.claim(
                    str(tool_args['task_id']),
                    claimant_at_name=at_name,
                    expected_version=int(tool_args['expected_version']),
                    claimant_capabilities=caps,
                )
            except TeamError as exc:
                return json.dumps({
                    'error': exc.code,
                    'message': exc.message,
                    'details': exc.details,
                })
            except Exception as exc:  # noqa: BLE001
                return json.dumps({'error': str(exc)})
            return json.dumps(claimed.to_dict())

        if tool_name == 'task_ask':
            from ms_agent.team.context import truncate_summary
            question = str(tool_args.get('question') or '').strip()
            if not question:
                return json.dumps({'error': 'question_required'})
            tid = tool_args.get('task_id') or new_id('task_')
            existing = self._store.get(tid)
            audience = str(tool_args.get('audience') or 'lead')
            if audience not in ('user', 'lead'):
                audience = 'lead'
            task = existing or TeamTask(
                task_id=tid,
                project_id=str(tool_args.get('project_id') or ''),
                status='wait_human',
                prompt=question,
                trigger_user_id='',
            )
            task.status = 'wait_human'
            task.result_summary = truncate_summary(question, 500)
            ctx = dict(task.deployment_context or {})
            ctx['needs_clarification'] = True
            ctx['ask_audience'] = audience
            task.deployment_context = ctx
            task.updated_at = _now_iso()
            self._store.upsert(task)
            if audience == 'lead' and self._on_ask is not None:
                maybe = self._on_ask(task)
                if hasattr(maybe, '__await__'):
                    await maybe
            return json.dumps(self._index_row(task))

        if tool_name == 'task_board_read':
            if tool_args.get('task_id'):
                t = self._store.get(tool_args['task_id'])
                return json.dumps(
                    self._index_row(t) if t else None)
            tasks = self._store.list(tool_args['project_id'])
            return json.dumps([self._index_row(t) for t in tasks])

        return json.dumps({'error': f'Unknown tool {tool_name}'})

    async def _maybe_kick(self, task: TeamTask) -> None:
        """Ask Coordinator to scan after a pending row is written."""
        cb = self._on_change
        if cb is None or task.status != 'pending':
            return
        try:
            maybe = cb(task)
            if hasattr(maybe, '__await__'):
                await maybe
        except Exception:  # noqa: BLE001
            logger.debug(
                'task_board_write on_change failed', exc_info=True)

    @staticmethod
    def _index_row(task: TeamTask) -> dict[str, Any]:
        from ms_agent.team.context import sanitize_agent_text

        summary = sanitize_agent_text(task.result_summary) or None
        return {
            'task_id': task.task_id,
            'project_id': task.project_id,
            'status': task.status,
            'at_name': task.target_at_name,
            'last_dispatch_id': task.last_dispatch_id,
            'output_artifacts': list(task.output_artifacts or []),
            'result_summary': summary,
            'read': ('dispatch_result_read'
                     if task.last_dispatch_id else None),
            'parent_task_id': task.parent_task_id,
            'blocked_by': list(task.blocked_by or []),
            'claim_version': int(task.claim_version or 0),
            'tags': list(getattr(task, 'tags', None) or []),
        }

    def _dispatch_result(self, tool_args: dict) -> dict[str, Any]:
        from ms_agent.team.context import final_text_from_dispatch_events

        did = str(tool_args.get('dispatch_id') or '').strip()
        tid = str(tool_args.get('task_id') or '').strip()
        at_name = None
        status = None
        artifacts: list[str] = []
        if not did and tid and self._store is not None:
            task = self._store.get(tid)
            if task is None:
                return {'error': 'task_not_found', 'task_id': tid}
            did = str(task.last_dispatch_id or '').strip()
            at_name = task.target_at_name
            status = task.status
            artifacts = list(task.output_artifacts or [])
            if task.result_summary:
                return {
                    'task_id': tid,
                    'dispatch_id': did or None,
                    'at_name': at_name,
                    'status': status,
                    'output_artifacts': artifacts,
                    'result_text': task.result_summary,
                    'source': 'result_summary',
                }
        if not did:
            return {
                'error': 'dispatch_id_required',
                'hint': 'Pass last_dispatch_id from the task board.',
            }
        if self._dispatch_log is None:
            return {'error': 'dispatch_log not wired', 'dispatch_id': did}
        events = self._dispatch_log.list(did)
        if not events:
            return {
                'error': 'dispatch_not_found',
                'dispatch_id': did,
                'hint': 'The index is last_dispatch_id; there is no file to find.',
            }
        text = final_text_from_dispatch_events(events)
        if not at_name:
            at_name = getattr(events[-1], 'at_name', None)
        done = next(
            (e for e in reversed(events)
             if getattr(e, 'type', '') in (
                 'team.dispatch_done', 'team.dispatch_error',
                 'team.dispatch_cancelled')),
            None,
        )
        if done is not None:
            status = {
                'team.dispatch_done': 'completed',
                'team.dispatch_error': 'failed',
                'team.dispatch_cancelled': 'cancelled',
            }.get(done.type, status)
        return {
            'task_id': tid or None,
            'dispatch_id': did,
            'at_name': at_name,
            'status': status or 'done',
            'output_artifacts': artifacts,
            'result_text': text,
            'source': 'dispatch_log',
        }
