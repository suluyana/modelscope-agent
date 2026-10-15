# Copyright (c) ModelScope Contributors. All rights reserved.
"""Address a Host Bridge on another machine should use to reach this API."""
from __future__ import annotations

import os
import re
import socket

_LOOPBACK = frozenset({
    '',
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
    '::',
    '::1',
})


def _is_loopback(host: str) -> bool:
    value = (host or '').strip().lower().strip('[]')
    return value in _LOOPBACK or value.startswith('127.')


def outbound_ipv4() -> str:
    """IP this machine uses for outbound traffic, else the first LAN address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.2)
        # UDP connect only selects a route; it does not send a packet.
        sock.connect(('8.8.8.8', 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = ''
    finally:
        sock.close()
    if ip and not _is_loopback(ip):
        return ip
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        candidate = info[4][0]
        if candidate and not _is_loopback(candidate):
            return candidate
    return '127.0.0.1'


def cli_api_base(port: int, *, host: str | None = None) -> str:
    """HTTP base a remote ``agent-bridge`` can open.

    ``MS_AGENT_PUBLIC_API_BASE`` wins. Otherwise a loopback host (the browser
    opened ``127.0.0.1``) is replaced with this machine's LAN address, because
    another computer cannot connect to loopback.
    """
    override = os.environ.get('MS_AGENT_PUBLIC_API_BASE', '').strip().rstrip('/')
    if override:
        return override
    chosen = (host or '').strip()
    if _is_loopback(chosen):
        chosen = outbound_ipv4()
    if ':' in chosen and not chosen.startswith('['):
        chosen = f'[{chosen}]'
    return f'http://{chosen}:{int(port)}'


_SAFE_SHELL = re.compile(r'^[A-Za-z0-9_./:@+-]+$')


def shell_quote(value: str) -> str:
    if _SAFE_SHELL.fullmatch(value or ''):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def _daemon_invocation(
    api_base: str,
    pair_code: str,
    *,
    dev: bool = False,
) -> str:
    flag = '--dev ' if dev else ''
    return (
        'python -m ms_agent.bridge.daemon '
        f'{flag}--api-base {shell_quote(api_base)} '
        f'--pair-code {shell_quote(pair_code)} '
        '--machine-label "$(hostname -s)" --no-auto-me'
    )


def local_debug_command(pair_code: str, port: int) -> str:
    """One-line command for a WebUI started with ``--dev``.

    ``--dev`` on the daemon selects that user's checkout and node. The
    command itself does not embed a path.
    """
    return _daemon_invocation(
        f'http://127.0.0.1:{int(port)}',
        pair_code,
        dev=True,
    )


def remote_connect_command(pair_code: str, api_base: str) -> str:
    """Production command: installed package, no ``--dev``."""
    return _daemon_invocation(api_base, pair_code)
