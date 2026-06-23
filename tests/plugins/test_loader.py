import json

from ms_agent.plugins.loader import PluginLoadContext, PluginLoader
from ms_agent.plugins.manifest import PluginManifest


def _write_plugin(root):
    (root / '.claude-plugin').mkdir(parents=True)
    (root / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({'name': 'hook-demo'}),
        encoding='utf-8',
    )
    skill = root / 'skills' / 'writer'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(
        '---\nname: Writer\ndescription: Write better text.\n---\n',
        encoding='utf-8',
    )
    (root / 'hooks').mkdir()
    (root / 'hooks' / 'hooks.json').write_text(
        json.dumps({
            'hooks': {
                'Stop': [{
                    'hooks': [{
                        'type': 'command',
                        'command': 'python ${CLAUDE_PLUGIN_ROOT}/hooks/stop.py '
                                   '${MS_AGENT_PLUGIN_DATA}/state.json',
                    }],
                }],
            },
        }),
        encoding='utf-8',
    )


def test_loader_contributes_skill_sources_and_hook_metadata(tmp_path):
    root = tmp_path / 'hook-demo'
    data_root = tmp_path / 'plugin-data'
    _write_plugin(root)
    manifest = PluginManifest.parse(root)

    result = PluginLoader.load(
        manifest,
        PluginLoadContext(
            project_path=str(tmp_path),
            session_id='s1',
            enabled_executors=frozenset({'command'}),
            plugin_data_root=data_root,
        ),
    )

    assert len(result.skill_sources) == 1
    assert result.skill_sources[0].origin == 'plugin'
    assert result.skill_sources[0].plugin_id == 'hook-demo'

    contrib = result.hook_registries[0]
    handler = contrib.registry.get_handlers('Stop')[0]
    assert handler.source_plugin_id == 'hook-demo'
    assert handler.source_plugin_root == str(root)
    assert handler.source_plugin_data_dir == str(data_root / 'hook-demo')
    assert '${CLAUDE_PLUGIN_ROOT}' not in handler.command
    assert '${MS_AGENT_PLUGIN_DATA}' not in handler.command


def test_loader_loads_hermes_shell_hooks_with_user_config(tmp_path):
    root = tmp_path / 'hermes-demo'
    data_root = tmp_path / 'plugin-data'
    _write_plugin(root)
    (root / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({
            'name': 'hermes-demo',
            'userConfig': {'mode': {'type': 'string'}},
        }),
        encoding='utf-8',
    )
    (data_root / 'hermes-demo').mkdir(parents=True)
    (data_root / 'hermes-demo' / 'config.json').write_text(
        json.dumps({'mode': 'strict'}),
        encoding='utf-8',
    )
    (root / 'hooks' / 'hermes.yaml').write_text(
        'hooks:\n'
        '  pre_tool_call:\n'
        '    - matcher: terminal\n'
        '      command: "python ${MS_AGENT_PLUGIN_ROOT}/hooks/check.py ${user_config.mode} ${CLAUDE_PLUGIN_DATA}"\n',
        encoding='utf-8',
    )
    manifest = PluginManifest.parse(root)

    result = PluginLoader.load(
        manifest,
        PluginLoadContext(
            project_path=str(tmp_path),
            session_id='s1',
            enabled_executors=frozenset({'command'}),
            plugin_data_root=data_root,
        ),
    )

    handlers = [
        handler
        for contrib in result.hook_registries
        for handler in contrib.registry.get_handlers(
            'PreToolUse', 'code_executor---shell_executor')
    ]
    assert any('strict' in handler.command for handler in handlers)
    assert all('${user_config.mode}' not in handler.command for handler in handlers)
    assert all('${CLAUDE_PLUGIN_DATA}' not in handler.command for handler in handlers)


def test_loader_uses_manifest_declared_hook_and_mcp_paths(tmp_path):
    root = tmp_path / 'path-demo'
    data_root = tmp_path / 'plugin-data'
    (root / '.claude-plugin').mkdir(parents=True)
    (root / '.claude-plugin' / 'plugin.json').write_text(
        json.dumps({
            'name': 'path-demo',
            'hooks': './custom/hooks.json',
            'mcpServers': './custom/mcp.json',
        }),
        encoding='utf-8',
    )
    (root / 'custom').mkdir()
    (root / 'custom' / 'hooks.json').write_text(
        json.dumps({
            'hooks': {
                'Stop': [{
                    'hooks': [{
                        'type': 'command',
                        'command': 'python ${MS_AGENT_PLUGIN_ROOT}/stop.py',
                    }],
                }],
            },
        }),
        encoding='utf-8',
    )
    (root / 'custom' / 'mcp.json').write_text(
        json.dumps({
            'mcpServers': {
                'declared': {
                    'command': 'node',
                    'args': ['${MS_AGENT_PLUGIN_ROOT}/server.js'],
                },
            },
        }),
        encoding='utf-8',
    )

    result = PluginLoader.load(
        PluginManifest.parse(root),
        PluginLoadContext(
            project_path=str(tmp_path),
            session_id='s1',
            enabled_executors=frozenset({'command'}),
            plugin_data_root=data_root,
        ),
    )

    handler = result.hook_registries[0].registry.get_handlers('Stop')[0]
    assert handler.command == f'python {root.resolve()}/stop.py'
    assert result.mcp_servers['declared']['args'] == [
        f'{root.resolve()}/server.js'
    ]


def test_load_all_preserves_cross_plugin_mcp_name_collisions(tmp_path):
    data_root = tmp_path / 'plugin-data'
    roots = []
    for plugin_id in ('plugin-a', 'plugin-b'):
        root = tmp_path / plugin_id
        roots.append(root)
        (root / '.claude-plugin').mkdir(parents=True)
        (root / '.claude-plugin' / 'plugin.json').write_text(
            json.dumps({'name': plugin_id}),
            encoding='utf-8',
        )
        (root / '.mcp.json').write_text(
            json.dumps({
                'mcpServers': {
                    'local': {
                        'command': 'node',
                        'args': [f'./{plugin_id}.js'],
                    },
                },
            }),
            encoding='utf-8',
        )

    result = PluginLoader.load_all(
        [PluginManifest.parse(root) for root in roots],
        PluginLoadContext(
            project_path=str(tmp_path),
            session_id='s1',
            enabled_executors=frozenset({'command'}),
            plugin_data_root=data_root,
        ),
    )

    assert set(result.mcp_servers) == {'local', 'plugin.plugin-b.local'}
    assert result.mcp_servers['local']['plugin_id'] == 'plugin-a'
    assert result.mcp_servers['plugin.plugin-b.local']['plugin_id'] == 'plugin-b'


def test_loader_reports_commands_agents_and_auxiliary_components(tmp_path):
    root = tmp_path / 'rich-demo'
    data_root = tmp_path / 'plugin-data'
    _write_plugin(root)
    (root / 'commands').mkdir()
    (root / 'commands' / 'help.md').write_text(
        '---\nname: help\ndescription: Show plugin help\nargument-hint: topic\n---\nHelp $ARGUMENTS\n',
        encoding='utf-8',
    )
    (root / 'agents').mkdir()
    (root / 'agents' / 'reviewer.md').write_text(
        '---\nname: reviewer\ndescription: Review changes\nmodel: qwen\n---\nBody\n',
        encoding='utf-8',
    )
    (root / 'bin').mkdir()
    (root / 'bin' / 'rich-tool').write_text('#!/bin/sh\n')
    (root / 'settings.json').write_text(
        json.dumps({'agent': {'max_chat_round': 3}, 'unsafe': True}),
        encoding='utf-8',
    )

    result = PluginLoader.load(
        PluginManifest.parse(root),
        PluginLoadContext(
            project_path=str(tmp_path),
            session_id='s1',
            enabled_executors=frozenset({'command'}),
            plugin_data_root=data_root,
        ),
    )

    assert result.command_defs[0].name == 'help'
    assert result.command_defs[0].description == 'Show plugin help'
    assert result.agent_defs[0].name == 'reviewer'
    assert result.bin_paths == [root.resolve() / 'bin']
    assert result.settings_patch == {'agent': {'max_chat_round': 3}}
