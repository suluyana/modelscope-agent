import json

import pytest

from ms_agent.plugins.manifest import (
    AmbiguousPluginManifest,
    EmptyPluginError,
    InvalidPluginManifest,
    PluginManifest,
)
from ms_agent.plugins.types import PluginFormat


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def _write_skill(path, name='Sample Skill'):
    path.mkdir(parents=True, exist_ok=True)
    (path / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: A sample plugin skill.\n---\nBody\n',
        encoding='utf-8',
    )


def test_parse_claude_manifest_and_scan_components(tmp_path):
    root = tmp_path / 'sample-plugin'
    _write_json(
        root / '.claude-plugin' / 'plugin.json',
        {
            'name': 'sample-plugin',
            'version': '1.2.3',
            'description': 'Sample plugin',
        },
    )
    _write_skill(root / 'skills' / 'writer')
    _write_json(root / 'hooks' / 'hooks.json', {'hooks': {'Stop': []}})
    (root / 'commands').mkdir()
    (root / 'commands' / 'help.md').write_text('---\ndescription: Help\n---\n')
    (root / 'agents').mkdir()
    (root / 'agents' / 'reviewer.md').write_text(
        '---\ndescription: Review code\n---\nBody\n')

    manifest = PluginManifest.parse(root)

    assert manifest.plugin_id == 'sample-plugin'
    assert manifest.format == PluginFormat.CLAUDE
    assert manifest.manifest_path == '.claude-plugin/plugin.json'
    assert manifest.capabilities >= frozenset({'skills', 'hooks', 'commands', 'agents'})
    assert manifest.components['skills'].count == 1
    assert manifest.components['commands'].count == 1
    assert manifest.components['agents'].count == 1


def test_parse_root_skill_plugin(tmp_path):
    root = tmp_path / 'root-skill'
    _write_json(root / 'plugin.json', {'name': 'root-skill'})
    (root / 'SKILL.md').write_text(
        '---\nname: Root Skill\ndescription: Single skill plugin.\n---\nBody\n',
        encoding='utf-8',
    )

    manifest = PluginManifest.parse(root)

    assert 'skills' in manifest.capabilities
    assert manifest.resolve_paths('skills') == [root]


def test_manifest_default_enabled_is_respected(tmp_path):
    root = tmp_path / 'disabled-by-default'
    _write_json(
        root / '.claude-plugin' / 'plugin.json',
        {'name': 'disabled-by-default', 'defaultEnabled': False},
    )
    _write_skill(root / 'skills' / 'writer')

    manifest = PluginManifest.parse(root)

    assert manifest.enabled is False


def test_missing_declared_hook_path_is_not_loadable(tmp_path):
    root = tmp_path / 'missing-hook'
    _write_json(
        root / '.claude-plugin' / 'plugin.json',
        {'name': 'missing-hook', 'hooks': './missing-hooks.json'},
    )

    with pytest.raises(EmptyPluginError):
        PluginManifest.parse(root)


def test_missing_declared_mcp_path_is_not_loadable(tmp_path):
    root = tmp_path / 'missing-mcp'
    _write_json(
        root / '.claude-plugin' / 'plugin.json',
        {'name': 'missing-mcp', 'mcpServers': './missing-mcp.json'},
    )

    with pytest.raises(EmptyPluginError):
        PluginManifest.parse(root)


def test_locked_manifest_path_must_stay_under_plugin_root(tmp_path):
    root = tmp_path / 'locked-path'
    outside = tmp_path / 'outside'
    _write_json(outside / 'plugin.json', {'name': 'locked-path'})
    _write_skill(root / 'skills' / 'writer')

    with pytest.raises(InvalidPluginManifest):
        PluginManifest.parse(
            root,
            record={
                'id': 'locked-path',
                'path': str(root),
                'format': 'generic',
                'manifest_path': '../outside/plugin.json',
            },
        )


def test_ambiguous_non_native_manifests_require_format_hint(tmp_path):
    root = tmp_path / 'mixed'
    _write_json(root / '.claude-plugin' / 'plugin.json', {'name': 'mixed'})
    _write_json(root / '.codex-plugin' / 'plugin.json', {'name': 'mixed'})
    _write_skill(root / 'skills' / 'writer')

    with pytest.raises(AmbiguousPluginManifest):
        PluginManifest.parse(root)

    manifest = PluginManifest.parse(root, format_hint='codex')
    assert manifest.format == PluginFormat.CODEX


def test_manifest_component_paths_must_stay_under_plugin_root(tmp_path):
    root = tmp_path / 'unsafe'
    _write_json(
        root / '.claude-plugin' / 'plugin.json',
        {'name': 'unsafe', 'skills': '../outside'},
    )
    _write_skill(tmp_path / 'outside' / 'writer')

    with pytest.raises(InvalidPluginManifest):
        PluginManifest.parse(root)


def test_openclaw_bundle_without_manifest_is_detected(tmp_path):
    root = tmp_path / 'openclaw-pack'
    (root / 'hooks' / 'logger').mkdir(parents=True)
    (root / 'hooks' / 'logger' / 'HOOK.md').write_text(
        '---\nname: logger\n---\n',
        encoding='utf-8',
    )
    (root / 'hooks' / 'logger' / 'handler.ts').write_text('', encoding='utf-8')
    _write_skill(root / 'skills' / 'writer')
    _write_json(root / 'package.json', {
        'name': 'openclaw-pack',
        'openclaw': {'hooks': ['hooks/logger']},
    })

    manifest = PluginManifest.parse(root)

    assert manifest.format == PluginFormat.OPENCLAW
    assert manifest.components['hooks_openclaw_internal'].status == 'unsupported'
    assert 'skills' in manifest.capabilities


def test_hermes_shell_bundle_without_manifest_is_detected(tmp_path):
    root = tmp_path / 'hermes-pack'
    (root / 'hooks').mkdir(parents=True)
    (root / 'hooks' / 'hermes.yaml').write_text(
        'hooks:\n  pre_tool_call:\n    - command: echo ok\n',
        encoding='utf-8',
    )

    manifest = PluginManifest.parse(root)

    assert manifest.format == PluginFormat.HERMES
    assert manifest.plugin_id == 'hermes-pack'
    assert 'hooks' in manifest.capabilities


def test_hermes_config_yaml_bundle_without_manifest_is_loadable(tmp_path):
    root = tmp_path / 'hermes-config-pack'
    (root / 'hooks').mkdir(parents=True)
    (root / 'hooks' / 'config.yaml').write_text(
        'hooks:\n  pre_tool_call:\n    - command: echo ok\n',
        encoding='utf-8',
    )

    manifest = PluginManifest.parse(root)

    assert manifest.format == PluginFormat.HERMES
    assert 'hooks' in manifest.capabilities
