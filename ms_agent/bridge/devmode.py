# Copyright (c) ModelScope Contributors. All rights reserved.
"""Local debug mode for the Host Bridge daemon.

Production starts ``python -m ms_agent.bridge.daemon`` and uses the installed
package. ``--dev`` re-execs onto the checkout that belongs to the person
running the command and puts their node on ``PATH``.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_DEV_READY = 'MS_AGENT_BRIDGE_DEV_READY'


def bridge_dev_mode() -> bool:
    """True when this WebUI process was started with ``--dev``."""
    return os.environ.get('MS_AGENT_DEV', '').strip().lower() in {
        '1', 'true', 'yes', 'on',
    }


def resolve_checkout(start: Path | None = None) -> Path | None:
    """Checkout for the person running the command, not the server's path.

    ``MS_AGENT_ROOT`` wins. Otherwise walk from ``start`` (default: cwd)
    toward the filesystem root until ``ms_agent/bridge/daemon.py`` exists.
    """
    override = os.environ.get('MS_AGENT_ROOT', '').strip()
    if override:
        root = Path(override).expanduser().resolve()
        return root if root.is_dir() else None
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        marker = directory / 'ms_agent' / 'bridge' / 'daemon.py'
        if marker.is_file():
            return directory
    return None


def _running_from(root: Path) -> bool:
    import ms_agent

    try:
        Path(ms_agent.__file__).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _version_key(name: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in name.lstrip('v').split('.'):
        digits = ''.join(char for char in piece if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def ensure_node_on_path() -> None:
    """Prepend this user's node when the process cannot already see one."""
    if shutil.which('node'):
        return
    nvm_root = Path(os.environ.get('NVM_DIR') or (Path.home() / '.nvm'))
    versions = nvm_root / 'versions' / 'node'
    if not versions.is_dir():
        return
    installed = [
        path for path in versions.iterdir()
        if (path / 'bin' / 'node').is_file()
    ]
    if not installed:
        return
    bindir = max(installed, key=lambda path: _version_key(path.name)) / 'bin'
    os.environ['PATH'] = str(bindir) + os.pathsep + os.environ.get('PATH', '')


def prepare_dev_runtime(argv: list[str]) -> None:
    """Switch onto the local checkout, then return.

    When the imported ``ms_agent`` is not that checkout, replace this process
    with ``PYTHONPATH`` pointing at it. A second entry sees
    ``MS_AGENT_BRIDGE_DEV_READY`` and only fixes ``PATH``.
    """
    if os.environ.get(_DEV_READY) == '1':
        ensure_node_on_path()
        return
    root = resolve_checkout()
    if root is not None and not _running_from(root):
        env = os.environ.copy()
        previous = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = (
            str(root) + (os.pathsep + previous if previous else '')
        )
        env['MS_AGENT_ROOT'] = str(root)
        env[_DEV_READY] = '1'
        os.execvpe(
            sys.executable,
            [sys.executable, '-m', 'ms_agent.bridge.daemon', *argv],
            env,
        )
        return
    os.environ[_DEV_READY] = '1'
    ensure_node_on_path()
