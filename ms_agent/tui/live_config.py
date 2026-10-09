# Copyright (c) ModelScope Contributors. All rights reserved.
"""Apply ledger changes on the next user message, inside the same agent.

TUI sets :attr:`LLMAgent._on_user_turn` to :func:`apply_live_config`.
WebUI leaves the attribute unset and keeps its own rebuild path.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from omegaconf import OmegaConf

from ms_agent.llm.message_text import prepend_text
from ms_agent.utils import get_logger

logger = get_logger()

_SKILL_NOTICE = (
    '<system-reminder>\n'
    'Skill inventory updated. The list in the system prompt is from '
    'session start; skills_list is current. Do not mention this notice '
    'to the user.\n'
    '</system-reminder>'
)


def _token(payload: Any) -> str:
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _work_dir(agent) -> str | None:
    work = str(OmegaConf.select(agent.config, 'output_dir', default='') or '')
    return work or None


def _install_llm(agent, rebuilt) -> None:
    from ms_agent.command.builtin.config_cmds import _install_rebuilt_llm
    target = agent.llm
    if target is not None and _install_rebuilt_llm(target, rebuilt):
        if agent.runtime is not None:
            agent.runtime.llm = target
        return
    agent.llm = rebuilt
    if agent.runtime is not None:
        agent.runtime.llm = rebuilt


async def _sync_llm(agent) -> None:
    from ms_agent.llm import LLM
    from ms_agent.llm.runtime_token import llm_runtime_token
    if agent.llm is None or getattr(agent.llm, '_setup_stub', False):
        return
    token = llm_runtime_token(agent.config)
    if getattr(agent, '_llm_fp', None) == token:
        return
    try:
        rebuilt = LLM.from_config(agent.config)
    except Exception:
        logger.debug('live llm rebuild skipped', exc_info=True)
        return
    if rebuilt is None:
        return
    _install_llm(agent, rebuilt)
    agent._llm_fp = token
    for orch in list(getattr(agent, 'memory_tools', None) or []):
        set_llm = getattr(orch, 'set_llm', None)
        if set_llm is not None and agent.llm is not None:
            set_llm(agent.llm)


async def _sync_mcp(agent) -> None:
    runtime = getattr(agent, 'mcp_runtime', None)
    if runtime is None:
        return
    from ms_agent.config.mcp_schema import ResolvedMCPConfig
    from ms_agent.project.paths import global_home
    from ms_agent.tui.managed_config import resolve_mcp_config
    raw = resolve_mcp_config(
        str(global_home()),
        _work_dir(agent),
        getattr(agent, 'mcp_server_file', None),
    ) or {}
    servers = dict((raw.get('mcpServers') or {}))
    plugin = getattr(agent, '_plugin_runtime', None)
    loaded = getattr(getattr(plugin, 'load_result', None), 'mcp_servers', None)
    if isinstance(loaded, dict):
        for name, entry in loaded.items():
            servers.setdefault(name, entry)
    token = _token(servers)
    if getattr(agent, '_mcp_fp', None) == token:
        return
    try:
        await runtime.apply_config(ResolvedMCPConfig(mcp_servers=servers))
    except Exception:
        logger.debug('live mcp apply failed', exc_info=True)
        return
    agent._mcp_fp = token


def _sync_skills(agent, messages) -> None:
    runtime = getattr(agent, '_skill_runtime', None)
    skills = getattr(agent.config, 'skills', None)
    if runtime is None or not skills:
        return
    from ms_agent.project.paths import global_home
    from ms_agent.tui.managed_config import merge_skills_into_config
    try:
        merge_skills_into_config(agent.config, str(global_home()),
                                 _work_dir(agent))
        changed = runtime.sync_with_config(agent.config.skills)
    except Exception:
        logger.debug('live skill sync failed', exc_info=True)
        return
    if not changed or not bool(getattr(skills, 'update_notice', False)):
        return
    if not messages or getattr(messages[-1], 'role', None) != 'user':
        return
    messages[-1].content = prepend_text(messages[-1].content, _SKILL_NOTICE)


def _unregister_memory(agent) -> None:
    from ms_agent.memory.unified.memory_tool import MemoryTool
    manager = getattr(agent, 'tool_manager', None)
    if manager is not None:
        doomed = [
            tool for tool in manager.extra_tools
            if isinstance(tool, MemoryTool)
        ]
        manager.extra_tools = [
            tool for tool in manager.extra_tools if tool not in doomed
        ]
        index = getattr(manager, '_tool_index', None) or {}
        for key, val in list(index.items()):
            if val and val[0] in doomed:
                index.pop(key, None)
    agent.memory_tools.clear()
    agent._memory_guidance = ''


async def _sync_memory(agent) -> None:
    work = _work_dir(agent)
    if not work:
        return
    from ms_agent.personalization.memory_apply import apply_project_memory
    from ms_agent.project import ProjectManager
    from ms_agent.project.paths import global_home
    project = ProjectManager(base_dir=str(global_home())).find_by_path(work)
    if project is None:
        return
    kind = apply_project_memory(agent.config, project)
    want = kind == 'file'
    have = bool(getattr(agent, 'memory_tools', None))
    if want == have:
        return
    if have:
        _unregister_memory(agent)
        try:
            from ms_agent.memory.memory_manager import SharedMemoryManager
            from ms_agent.project.paths import memory_dir
            await SharedMemoryManager.close_matching(str(memory_dir(work)))
        except Exception:
            logger.debug('memory store close skipped', exc_info=True)
    if want:
        load = getattr(agent, 'load_memory', None)
        if load is not None:
            await load()


async def apply_live_config(agent, messages=None) -> None:
    """Refresh skills, MCP, the model client, and memory tools.

    ``messages`` is the turn that is about to run. When it ends in a user
    message, a skill change is prefixed there. Pass ``None`` before the
    first ``create_messages`` so the frozen skill section includes edits
    made at the opening prompt.
    """
    try:
        _sync_skills(agent, messages)
        await _sync_mcp(agent)
        await _sync_llm(agent)
        await _sync_memory(agent)
    except Exception:
        logger.debug('live config sync failed', exc_info=True)
