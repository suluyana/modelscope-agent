"""Golden E2E tests for the official hookify community plugin."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from ms_agent.command.router import CommandRouter
from ms_agent.command.types import CommandContext, CommandResultType
from ms_agent.hooks.factory import build_hook_runtime
from ms_agent.plugins.config_manager import PluginConfigManager
from ms_agent.plugins.installer import PluginInstaller
from ms_agent.plugins.loader import PluginLoadContext, PluginLoader
from ms_agent.plugins.manifest import PluginManifest
from ms_agent.plugins.runtime import PluginRuntime
from ms_agent.plugins.types import PluginFormat
from ms_agent.skill.catalog import SkillCatalog
from ms_agent.skill.runtime import SkillRuntime

FIXTURE_ROOT = Path(__file__).parent / 'fixtures' / 'hookify'
HOOKIFY_URI = 'github://anthropics/claude-plugins-official@main#plugins/hookify'
HOOKIFY_MARKETPLACE = 'hookify@claude-plugins-official'
EXPECTED_COMMANDS = ('configure', 'help', 'hookify', 'list')
HOOK_EVENTS = ('PreToolUse', 'PostToolUse', 'Stop', 'UserPromptSubmit')


def _fixture_manifest() -> PluginManifest:
    return PluginManifest.parse(FIXTURE_ROOT)


def _load_context(tmp_path: Path) -> PluginLoadContext:
    return PluginLoadContext(
        project_path=str(tmp_path),
        session_id='hookify-e2e',
        enabled_executors=frozenset({'command'}),
        plugin_data_root=tmp_path / '.ms_agent' / 'plugins' / 'data',
    )


def _make_command_ctx(text: str) -> CommandContext:
    cmd, args = CommandRouter.parse_input(text)
    return CommandContext(raw_input=text, command_name=cmd, args=args)


def test_golden_hookify_install_and_manifest(tmp_path):
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    installer = PluginInstaller(config_manager=manager, global_root=global_dir)

    manifest = installer.install(str(FIXTURE_ROOT), scope='global')

    assert manifest.plugin_id == 'hookify'
    assert manifest.format == PluginFormat.CLAUDE
    assert manifest.manifest_path == '.claude-plugin/plugin.json'
    assert set(manifest.capabilities) >= {
        'skills', 'commands', 'agents', 'hooks',
    }
    record = manager.get('hookify', scope='global')
    assert record is not None
    assert record.enabled is True
    assert (global_dir / 'plugins' / 'hookify' / 'hooks' / 'hooks.json').is_file()


def test_golden_hookify_fixture_vendor_sha_matches_manifest():
    vendor_sha = (FIXTURE_ROOT / 'VENDOR_SHA').read_text(encoding='utf-8').strip()
    assert len(vendor_sha) == 40
    manifest = _fixture_manifest()
    assert manifest.plugin_id == 'hookify'
    assert manifest.components['skills'].count == 1
    assert manifest.components['commands'].count == 4
    assert manifest.components['agents'].count == 1
    assert manifest.components['hooks'].count == 1


def test_golden_hookify_loader_contributions(tmp_path):
    manifest = _fixture_manifest()
    result = PluginLoader.load(manifest, _load_context(tmp_path))

    assert len(result.skill_sources) == 1
    assert result.skill_sources[0].plugin_id == 'hookify'
    assert tuple(cmd.name for cmd in result.command_defs) == EXPECTED_COMMANDS
    assert [agent.name for agent in result.agent_defs] == ['conversation-analyzer']
    assert len(result.hook_registries) == 1

    contrib = result.hook_registries[0]
    assert contrib.plugin_id == 'hookify'
    for event in HOOK_EVENTS:
        handlers = contrib.registry.get_handlers(event)
        assert handlers, f'missing hook handlers for {event}'
        assert str(FIXTURE_ROOT.resolve()) in handlers[0].command
        assert '${CLAUDE_PLUGIN_ROOT}' not in handlers[0].command


def test_golden_hookify_skills_loaded(tmp_path):
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(FIXTURE_ROOT), scope='global')

    config = OmegaConf.create({})
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 'hookify-e2e', config=config)

    catalog = SkillCatalog()
    catalog.load_from_config(config.skills)
    skill_runtime = SkillRuntime(catalog)
    skills = skill_runtime.list_all()

    assert 'writing-rules' in {item['skill_id'] for item in skills}
    hookify_skill = next(
        item for item in skills if item['skill_id'] == 'writing-rules')
    assert hookify_skill['plugin_id'] == 'hookify'
    assert hookify_skill['name'] == 'writing-hookify-rules'


def test_golden_hookify_hooks_registered(tmp_path):
    manifest = _fixture_manifest()
    load_result = PluginLoader.load(manifest, _load_context(tmp_path))
    config = OmegaConf.create({
        'hooks': {
            'enabled_sources': ['native', 'plugin'],
            'enabled_executors': ['command'],
        },
        'local_dir': str(tmp_path),
    })

    hook_runtime = build_hook_runtime(
        config,
        session_id='hookify-e2e',
        plugin_hook_registries=load_result.hook_registries,
    )

    for event in HOOK_EVENTS:
        handlers = hook_runtime.registry.get_handlers(event)
        assert handlers, f'hook runtime missing {event}'
        assert handlers[0].source_plugin_id == 'hookify'


@pytest.mark.asyncio
async def test_golden_hookify_slash_command(tmp_path):
    from ms_agent.plugins.commands import register_plugin_commands

    manifest = _fixture_manifest()
    load_result = PluginLoader.load(manifest, _load_context(tmp_path))
    router = CommandRouter()
    register_plugin_commands(router, load_result.command_defs)

    assert router.is_command('/hookify')
    assert router.is_command('/hookify:help')
    result = await router.dispatch(_make_command_ctx('/hookify avoid rm -rf'))
    assert result is not None
    assert result.type == CommandResultType.SUBMIT_PROMPT
    assert 'Hookify - Create Hooks' in result.content
    assert 'avoid rm -rf' in result.content


@pytest.mark.asyncio
async def test_golden_hookify_toggle_disable(tmp_path):
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    PluginInstaller(config_manager=manager, global_root=global_dir).install(
        str(FIXTURE_ROOT), scope='global')

    config = OmegaConf.create({
        'skills': {'sources': []},
        'hooks': {
            'enabled_sources': ['native', 'plugin'],
            'enabled_executors': ['command'],
        },
        'local_dir': str(tmp_path),
    })
    runtime = PluginRuntime(config_manager=manager, global_root=global_dir)
    runtime.start_sync(str(tmp_path), 'hookify-e2e', config=config)

    catalog = SkillCatalog()
    catalog.load_from_config(config.skills)
    assert 'writing-rules' in catalog.get_enabled_skills()

    hook_runtime = build_hook_runtime(
        config,
        session_id='hookify-e2e',
        plugin_hook_registries=runtime.load_result.hook_registries,
    )
    assert hook_runtime.registry.get_handlers('PreToolUse')

    await runtime.toggle('hookify', False, project_path=str(tmp_path))

    catalog_after = SkillCatalog()
    catalog_after.load_from_config(config.skills)
    assert 'writing-rules' not in catalog_after.get_enabled_skills()
    assert runtime.load_result.hook_registries == []
    hook_runtime_after = build_hook_runtime(
        config,
        session_id='hookify-e2e',
        plugin_hook_registries=runtime.load_result.hook_registries,
    )
    assert not hook_runtime_after.registry.get_handlers('PreToolUse')


@pytest.mark.skipif(
    not (Path('/usr/bin/git').exists() or Path('/opt/homebrew/bin/git').exists()),
    reason='git is required for github install integration test',
)
def test_golden_hookify_github_install_integration(tmp_path):
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    installer = PluginInstaller(config_manager=manager, global_root=global_dir)

    manifest = installer.install(HOOKIFY_URI, scope='global')

    assert manifest.plugin_id == 'hookify'
    record = manager.get('hookify', scope='global')
    assert record.source.type == 'github'
    assert record.source.uri == HOOKIFY_URI
    assert (global_dir / 'plugins' / 'hookify' / 'skills' / 'writing-rules' / 'SKILL.md').is_file()


def test_golden_hookify_marketplace_alias_install(tmp_path, monkeypatch):
    global_dir = tmp_path / '.ms_agent'
    manager = PluginConfigManager(global_dir=global_dir)
    installer = PluginInstaller(config_manager=manager, global_root=global_dir)

    def fake_run(cmd, check, capture_output=True, text=True):
        if cmd[:3] == ['git', 'clone', '--depth']:
            clone_root = Path(cmd[-1])
            plugin = clone_root / 'plugins' / 'hookify'
            plugin.parent.mkdir(parents=True)
            (plugin / '.claude-plugin').mkdir(parents=True)
            (plugin / '.claude-plugin' / 'plugin.json').write_text(
                json.dumps({'name': 'hookify', 'description': 'hookify'}),
                encoding='utf-8',
            )
            skill = plugin / 'skills' / 'writing-rules'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text(
                '---\nname: writing-hookify-rules\n'
                'description: Hookify rules.\n---\n',
                encoding='utf-8',
            )
        return subprocess.CompletedProcess(cmd, 0, stdout='abc123\n', stderr='')

    import ms_agent.plugins.installer as installer_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(
        installer_mod,
        'resolve_marketplace_plugin_uri',
        lambda plugin_name, marketplace, *, ref='main': (
            f'github://anthropics/claude-plugins-official@{ref}#plugins/hookify'
            if plugin_name == 'hookify' and marketplace == 'claude-plugins-official'
            else (_ for _ in ()).throw(
                installer_mod.UnsupportedPluginSource('unexpected marketplace lookup'))
        ),
    )

    manifest = installer.install(HOOKIFY_MARKETPLACE, scope='global')

    assert manifest.plugin_id == 'hookify'
    record = manager.get('hookify', scope='global')
    assert record.source.uri == HOOKIFY_MARKETPLACE
    assert record.source.type == 'github'
