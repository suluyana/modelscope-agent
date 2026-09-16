"""TUI must boot on the packaged yaml and run WebUI's default model.

Tester gaps this file locks down:

1. Writing ``tools.todo_list.plan_filename`` without ``mcp: false`` makes
   ToolManager treat todo_list as an MCP server (``'url' or 'command'
   parameter is required``). WebUI seeds settings.json; a fresh TUI home
   does not.
2. ``/model list`` already read settings.json, but the live LLM came from
   Config.from_task(agent.yaml). Default TUI now goes through ConfigResolver
   so ``default_model`` / ``llm`` is what the first turn actually uses.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from ms_agent.config.config import Config
from ms_agent.config.resolver import ConfigResolver
from ms_agent.project import SessionManager
from ms_agent.tui.app import TUI_RESOLVER_DEFAULTS, TuiApp
from ms_agent.tui.state import TuiState
from ms_agent.tools.mcp_client import MCPClient
from ms_agent.tools.tool_manager import ToolManager


def _write_settings(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / 'settings.json').write_text(
        json.dumps(payload), encoding='utf-8')


def test_plan_filename_without_mcp_flag_is_treated_as_mcp():
    """Document the crash the testers hit: update plan path, omit mcp:false."""
    cfg = OmegaConf.create({})
    OmegaConf.update(
        cfg, 'tools.todo_list.plan_filename', '/tmp/plan.json', merge=True)
    servers = Config.convert_mcp_servers_to_json(cfg)['mcpServers']
    assert 'todo_list' in servers
    client = MCPClient(config=cfg)
    assert 'todo_list' in client.mcp_config['mcpServers']

    async def _boom():
        try:
            await client.connect()
        finally:
            await client.cleanup()

    with pytest.raises(ValueError, match='url.*command|command.*url'):
        asyncio.run(_boom())


def test_bind_todo_list_session_sets_mcp_false(tmp_path):
    cfg = OmegaConf.create({})
    TuiApp._bind_todo_list_session(cfg, str(tmp_path / 'sess'))
    assert cfg.tools.todo_list.mcp is False
    assert cfg.tools.todo_list.plan_filename.endswith('plan.json')
    servers = Config.convert_mcp_servers_to_json(cfg)['mcpServers']
    assert 'todo_list' not in servers
    client = MCPClient(config=cfg)
    assert 'todo_list' not in client.mcp_config['mcpServers']


def test_fresh_home_default_yaml_does_not_mcp_connect_todo_list(
        tmp_path, monkeypatch):
    """No WebUI-seeded settings.json: default TUI still must not MCP todo_list."""
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()

    cfg = TuiApp._load_runtime_config('unused.yaml', str(work),
                                      explicit_config=False)
    cfg = TuiApp._prepare_config(cfg, None, str(work))
    TuiApp._bind_todo_list_session(cfg, str(work / 'sess'))
    assert cfg.tools.todo_list.mcp is False
    servers = Config.convert_mcp_servers_to_json(cfg)['mcpServers']
    assert 'todo_list' not in servers

    async def _connect():
        # This is the call that raised "'url' or 'command' parameter is
        # required" when todo_list lacked mcp:false.
        client = MCPClient(config=cfg)
        try:
            await client.connect()
        finally:
            await client.cleanup()
        OmegaConf.update(cfg, 'llm.api_key', 'sk-test', merge=True)
        OmegaConf.update(cfg, 'llm.modelscope_api_key', 'sk-test', merge=True)
        manager = ToolManager(cfg)
        try:
            await manager.connect()
            names = {
                getattr(t, 'SERVER_NAME', None)
                for t in (manager.extra_tools or [])
            }
            assert 'todo_list' in names
        finally:
            await manager.cleanup()

    asyncio.run(_connect())


def test_default_tui_uses_webui_default_model(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()
    _write_settings(home, {
        'default_model': 'openai/qwen3.7-plus',
        'llm': {
            'provider': 'openai',
            'model': 'qwen3.7-plus',
        },
        'providers': {
            'openai': {
                'api_key': 'sk-from-catalog',
                'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'protocol': 'openai',
            },
        },
    })

    cfg = TuiApp._load_runtime_config('unused.yaml', str(work),
                                      explicit_config=False)
    cfg = TuiApp._prepare_config(cfg, None, str(work))
    assert cfg.llm.service == 'openai'
    assert cfg.llm.model == 'qwen3.7-plus'
    assert cfg.llm.use_provider_router is True
    assert cfg.llm.openai_api_key == 'sk-from-catalog'
    assert cfg.llm.protocol == 'openai'


def test_default_model_only_no_llm_block(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()
    _write_settings(home, {'default_model': 'openai/qwen3.7-plus'})

    cfg = TuiApp._load_runtime_config('unused.yaml', str(work),
                                      explicit_config=False)
    assert cfg.llm.service == 'openai'
    assert cfg.llm.model == 'qwen3.7-plus'


def test_project_patch_wins_over_settings_model(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()
    _write_settings(home, {
        'default_model': 'openai/qwen3.7-plus',
        'llm': {'provider': 'openai', 'model': 'qwen3.7-plus'},
    })
    patch_dir = work / '.ms_agent'
    patch_dir.mkdir()
    (patch_dir / 'config.yaml').write_text(
        'llm:\n  service: modelscope\n  model: patched-model\n',
        encoding='utf-8')

    cfg = TuiApp._load_runtime_config('unused.yaml', str(work),
                                      explicit_config=False)
    assert cfg.llm.model == 'patched-model'
    assert cfg.llm.service == 'modelscope'


def test_explicit_config_yaml_wins_over_settings(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()
    _write_settings(home, {
        'default_model': 'openai/qwen3.7-plus',
        'llm': {'provider': 'openai', 'model': 'qwen3.7-plus'},
    })
    yaml_path = tmp_path / 'custom.yaml'
    yaml_path.write_text(
        'llm:\n  service: modelscope\n  model: from-yaml\n'
        'tools:\n  file_system:\n    mcp: false\n',
        encoding='utf-8')

    cfg = TuiApp._load_runtime_config(
        str(yaml_path), str(work), explicit_config=True)
    assert cfg.llm.model == 'from-yaml'
    assert cfg.llm.service == 'modelscope'


def test_resolver_defaults_keep_mcp_false_when_plan_paths_merge(tmp_path):
    """WebUI-shaped overlay: plan filenames without repeating mcp:false."""
    resolver = ConfigResolver(
        global_dir=str(tmp_path / 'home'),
        defaults=TUI_RESOLVER_DEFAULTS,
    )
    cfg = resolver.resolve(
        session_overrides={
            'tools': {
                'todo_list': {
                    'plan_filename': '/tmp/s/plan.json',
                    'plan_md_filename': '/tmp/s/plan.md',
                },
            },
        })
    assert cfg.tools.todo_list.mcp is False
    assert 'todo_list' not in Config.convert_mcp_servers_to_json(
        cfg)['mcpServers']


def test_apply_session_then_toolmanager_connect(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('MS_AGENT_HOME', str(home))
    work = tmp_path / 'repo'
    work.mkdir()
    app = TuiApp.__new__(TuiApp)
    app._project = TuiApp._open_project(str(work))
    app._sm = SessionManager(app._project)
    session = app._sm.create(name='chat')
    cfg = TuiApp._load_runtime_config('unused.yaml', str(work),
                                      explicit_config=False)
    cfg = TuiApp._prepare_config(cfg, None, str(work), app._project)
    app.agent = SimpleNamespace(config=cfg, load_cache=False)
    app.state = TuiState(model='m', perm='auto', work_dir=str(work))
    app._apply_session(session, resume=False)

    async def _connect():
        client = MCPClient(config=app.agent.config)
        try:
            await client.connect()
            assert 'todo_list' not in (
                client.mcp_config.get('mcpServers') or {})
        finally:
            await client.cleanup()
        OmegaConf.update(app.agent.config, 'llm.api_key', 'sk-test', merge=True)
        OmegaConf.update(
            app.agent.config, 'llm.modelscope_api_key', 'sk-test', merge=True)
        manager = ToolManager(app.agent.config)
        try:
            await manager.connect()
            servers = getattr(manager.servers, 'mcp_config', {}) or {}
            assert 'todo_list' not in (servers.get('mcpServers') or {})
        finally:
            await manager.cleanup()

    asyncio.run(_connect())


def test_fill_provider_catalog_does_not_clobber_llm_keys():
    cfg = ConfigResolver._settings_to_agent_config({
        'llm': {
            'provider': 'openai',
            'model': 'qwen3.7-plus',
            'api_key': 'sk-llm-block',
        },
        'providers': {
            'openai': {
                'api_key': 'sk-catalog',
                'base_url': 'https://example.invalid/v1',
                'protocol': 'openai',
            },
        },
    })
    assert cfg.llm.openai_api_key == 'sk-llm-block'
    assert cfg.llm.openai_base_url == 'https://example.invalid/v1'
    assert cfg.llm.protocol == 'openai'
