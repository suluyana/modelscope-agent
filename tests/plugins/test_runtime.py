import json

import pytest
from omegaconf import OmegaConf

from ms_agent.plugins.config_manager import PluginConfigManager
from ms_agent.plugins.installer import PluginInstaller
from ms_agent.plugins.runtime import PluginRuntime, dedupe_mcp_server_names


def _sample_plugin(root):
    (root / '.claude-plugin').mkdir(parents=True)
    (root / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({'name': 'runtime-demo', 'version': '0.1.0'}),
        encoding='utf-8',
    )
    skill = root / 'skills' / 'writer'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(
        '---\nname: Writer\ndescription: Write better text.\n---\n',
        encoding='utf-8',
    )


def _mcp_plugin(root):
    _sample_plugin(root)
    (root / '.mcp.json').write_text(
        json.dumps({
            'mcpServers': {
                'demo-mcp': {
                    'command': 'node',
                    'args': ['${MS_AGENT_PLUGIN_ROOT}/server.js'],
                    'env': {
                        'STATE': '${MS_AGENT_PLUGIN_DATA}/state.json',
                    },
                },
            },
        }),
        encoding='utf-8',
    )


def test_runtime_start_updates_config_skill_sources(tmp_path):
    source = tmp_path / 'source-plugin'
    _sample_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({'skills': {'sources': []}})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    assert runtime.list_all()[0]['plugin_id'] == 'runtime-demo'
    assert config.skills.sources[0].origin == 'plugin'
    assert config.skills.sources[0].plugin_id == 'runtime-demo'


def test_runtime_preserves_existing_string_skill_sources(tmp_path):
    source = tmp_path / 'source-plugin'
    _sample_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({'skills': {'sources': ['/existing/skills']}})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    sources = list(config.skills.sources)
    assert sources[0] == '/existing/skills'
    assert sources[1].plugin_id == 'runtime-demo'


def test_runtime_merges_managed_and_explicit_plugin_paths(tmp_path):
    managed_source = tmp_path / 'managed-source'
    explicit_source = tmp_path / 'explicit-source'
    _sample_plugin(managed_source)
    _sample_plugin(explicit_source)
    (explicit_source / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({'name': 'explicit-demo', 'version': '0.1.0'}),
        encoding='utf-8',
    )
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(managed_source), scope='global')

    config = OmegaConf.create({'plugins': [str(explicit_source)]})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    ids = {item['plugin_id'] for item in runtime.list_all()}
    loaded_ids = {manifest.plugin_id for manifest in runtime.manifests}
    assert ids == {'runtime-demo'}
    assert loaded_ids == {'runtime-demo', 'explicit-demo'}


def test_runtime_injects_plugin_mcp_into_tools_config(tmp_path):
    source = tmp_path / 'mcp-source'
    _mcp_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    server = config.tools['demo-mcp']
    assert server.plugin_id == 'runtime-demo'
    assert '${MS_AGENT_PLUGIN_ROOT}' not in server.args[0]
    assert '${MS_AGENT_PLUGIN_DATA}' not in server.env.STATE


def test_runtime_does_not_override_existing_mcp_server_name(tmp_path):
    source = tmp_path / 'mcp-source'
    _mcp_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({
        'tools': {
            'demo-mcp': {
                'command': 'user-server',
                'args': ['server.js'],
            },
        },
    })
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    assert config.tools['demo-mcp'].command == 'user-server'
    assert config.tools['plugin.runtime-demo.demo-mcp'].plugin_id == 'runtime-demo'
    assert 'plugin.runtime-demo.demo-mcp' in runtime.load_result.mcp_servers


def test_fresh_runtime_does_not_duplicate_existing_plugin_mcp(tmp_path):
    source = tmp_path / 'mcp-source'
    _mcp_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({})
    first = PluginRuntime(config_manager=manager, global_root=global_dir)
    first.start_sync(str(tmp_path), 's1', config=config)
    second = PluginRuntime(config_manager=manager, global_root=global_dir)
    second.start_sync(str(tmp_path), 's2', config=config)

    plugin_servers = [
        name for name, server in config.tools.items()
        if getattr(server, 'source', None) == 'plugin'
    ]
    assert plugin_servers == ['demo-mcp']


def test_dedupe_mcp_server_names_preserves_legacy_mcp_config_names():
    result = dedupe_mcp_server_names(
        {'local': {'command': 'plugin', 'plugin_id': 'demo'}},
        {'local'},
    )

    assert 'plugin.demo.local' in result
    assert result['plugin.demo.local']['command'] == 'plugin'


def test_runtime_applies_settings_bin_paths_and_user_config(tmp_path):
    source = tmp_path / 'source-plugin'
    _sample_plugin(source)
    (source / 'bin').mkdir()
    (source / 'bin' / 'runtime-tool').write_text('#!/bin/sh\n', encoding='utf-8')
    (source / 'settings.json').write_text(
        json.dumps({'agent': {'max_chat_round': 7}, 'unsafe': True}),
        encoding='utf-8',
    )
    (source / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({
            'name': 'runtime-demo',
            'version': '0.1.0',
            'userConfig': {'mode': {'type': 'string'}},
        }),
        encoding='utf-8',
    )
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({'tools': {'code_executor': {}}})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    assert config.agent.max_chat_round == 7
    assert str(global_dir / 'plugins' / 'runtime-demo' / 'bin') in list(
        config.tools.code_executor.plugin_bin_paths)
    assert runtime.list_all()[0]['user_config_schema']['mode']['type'] == 'string'


@pytest.mark.asyncio
async def test_toggle_disable_removes_config_contributions(tmp_path):
    source = tmp_path / 'source-plugin'
    _mcp_plugin(source)
    (source / 'bin').mkdir()
    (source / 'bin' / 'runtime-tool').write_text('#!/bin/sh\n', encoding='utf-8')
    (source / 'settings.json').write_text(
        json.dumps({'agent': {'max_chat_round': 7}}),
        encoding='utf-8',
    )
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    config = OmegaConf.create({'skills': {'sources': []}, 'tools': {'code_executor': {}}})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=config)

    assert config.skills.sources[0].plugin_id == 'runtime-demo'
    assert 'demo-mcp' in config.tools
    assert config.agent.max_chat_round == 7

    await runtime.toggle('runtime-demo', False, project_path=str(tmp_path))

    assert list(config.skills.sources) == []
    assert 'demo-mcp' not in config.tools
    assert OmegaConf.to_container(config._merged_mcp, resolve=True) == {
        'servers': {}
    }
    assert list(config.tools.code_executor.plugin_bin_paths) == []
    assert 'agent' not in config


@pytest.mark.asyncio
async def test_uninstall_purge_link_only_removes_managed_symlink(tmp_path):
    source = tmp_path / 'linked-source'
    _sample_plugin(source)
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global', link=True)
    record = manager.get('runtime-demo', scope='global')

    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    await runtime.uninstall('runtime-demo', scope='global', purge=True)

    assert source.is_dir()
    assert not (global_dir / 'plugins' / 'runtime-demo').exists()
    assert record.path == str(global_dir / 'plugins' / 'runtime-demo')


def test_bad_plugin_hooks_do_not_block_runtime_start(tmp_path):
    source = tmp_path / 'bad-hooks'
    _sample_plugin(source)
    (source / 'hooks').mkdir()
    (source / 'hooks' / 'hooks.json').write_text('{not json', encoding='utf-8')
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(source), scope='global')

    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 's1', config=OmegaConf.create({}))

    assert runtime.list_all()[0]['plugin_id'] == 'runtime-demo'
