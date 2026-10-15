# Copyright (c) ModelScope Contributors. All rights reserved.
"""--dev selects the runner's checkout; production does not."""
from __future__ import annotations

from ms_agent.bridge.devmode import (
    bridge_dev_mode,
    prepare_dev_runtime,
    resolve_checkout,
)


def test_bridge_dev_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv('MS_AGENT_DEV', raising=False)
    assert bridge_dev_mode() is False
    monkeypatch.setenv('MS_AGENT_DEV', '1')
    assert bridge_dev_mode() is True


def test_resolve_checkout_walks_parents(tmp_path, monkeypatch):
    monkeypatch.delenv('MS_AGENT_ROOT', raising=False)
    root = tmp_path / 'src' / 'modelscope-agent'
    (root / 'ms_agent' / 'bridge').mkdir(parents=True)
    (root / 'ms_agent' / 'bridge' / 'daemon.py').write_text('#\n', encoding='utf-8')
    nested = root / 'webui' / 'backend'
    nested.mkdir(parents=True)
    assert resolve_checkout(nested) == root


def test_resolve_checkout_prefers_ms_agent_root(tmp_path, monkeypatch):
    chosen = tmp_path / 'mine'
    chosen.mkdir()
    decoy = tmp_path / 'decoy'
    (decoy / 'ms_agent' / 'bridge').mkdir(parents=True)
    (decoy / 'ms_agent' / 'bridge' / 'daemon.py').write_text('#\n', encoding='utf-8')
    monkeypatch.setenv('MS_AGENT_ROOT', str(chosen))
    assert resolve_checkout(decoy) == chosen.resolve()


def test_prepare_reexecs_when_import_is_not_the_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'ms_agent' / 'bridge').mkdir(parents=True)
    (root / 'ms_agent' / 'bridge' / 'daemon.py').write_text('#\n', encoding='utf-8')
    monkeypatch.delenv('MS_AGENT_ROOT', raising=False)
    monkeypatch.delenv('MS_AGENT_BRIDGE_DEV_READY', raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        'ms_agent.bridge.devmode._running_from', lambda _root: False)
    calls: list[tuple] = []

    def _exec(executable, args, env):
        calls.append((executable, args, env))

    monkeypatch.setattr('os.execvpe', _exec)
    prepare_dev_runtime(['--dev', '--api-base', 'http://127.0.0.1:8000'])
    assert len(calls) == 1
    _executable, args, env = calls[0]
    assert args[1:3] == ['-m', 'ms_agent.bridge.daemon']
    assert '--dev' in args
    assert env['PYTHONPATH'].startswith(str(root))
    assert env['MS_AGENT_BRIDGE_DEV_READY'] == '1'


def test_prepare_skips_reexec_when_already_on_the_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'ms_agent' / 'bridge').mkdir(parents=True)
    (root / 'ms_agent' / 'bridge' / 'daemon.py').write_text('#\n', encoding='utf-8')
    monkeypatch.delenv('MS_AGENT_ROOT', raising=False)
    monkeypatch.delenv('MS_AGENT_BRIDGE_DEV_READY', raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        'ms_agent.bridge.devmode._running_from', lambda _root: True)
    monkeypatch.setattr(
        'ms_agent.bridge.devmode.ensure_node_on_path', lambda: None)
    calls: list[Path] = []
    monkeypatch.setattr('os.execvpe', lambda *args: calls.append(args))
    prepare_dev_runtime(['--dev'])
    assert calls == []
