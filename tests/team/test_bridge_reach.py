# Copyright (c) ModelScope Contributors. All rights reserved.
"""Remote Host Bridge must be given a routable API address."""
from __future__ import annotations

from ms_agent.bridge.reach import (
    cli_api_base,
    local_debug_command,
    remote_connect_command,
)


def test_public_base_overrides_host(monkeypatch):
    monkeypatch.setenv(
        'MS_AGENT_PUBLIC_API_BASE', 'http://bridge.example:9000/')
    assert cli_api_base(8000, host='127.0.0.1') == 'http://bridge.example:9000'


def test_loopback_host_is_replaced_with_lan_address(monkeypatch):
    monkeypatch.delenv('MS_AGENT_PUBLIC_API_BASE', raising=False)
    monkeypatch.setattr(
        'ms_agent.bridge.reach.outbound_ipv4', lambda: '10.1.2.3')
    assert cli_api_base(8000, host='127.0.0.1') == 'http://10.1.2.3:8000'
    assert cli_api_base(8000, host='localhost') == 'http://10.1.2.3:8000'
    assert cli_api_base(8000) == 'http://10.1.2.3:8000'


def test_explicit_lan_host_is_kept(monkeypatch):
    monkeypatch.delenv('MS_AGENT_PUBLIC_API_BASE', raising=False)
    assert cli_api_base(8000, host='192.168.1.20') == 'http://192.168.1.20:8000'


def test_local_debug_is_a_dev_flag_not_a_checkout_path():
    command = local_debug_command('pair_abc', 8000)
    assert command == (
        'python -m ms_agent.bridge.daemon --dev '
        '--api-base http://127.0.0.1:8000 '
        '--pair-code pair_abc '
        '--machine-label "$(hostname -s)" --no-auto-me'
    )
    assert '/Users/' not in command
    assert 'PYTHONPATH' not in command


def test_remote_command_has_no_local_paths():
    command = remote_connect_command('pair_abc', 'http://10.1.2.3:8000')
    assert command == (
        'python -m ms_agent.bridge.daemon '
        '--api-base http://10.1.2.3:8000 '
        '--pair-code pair_abc '
        '--machine-label "$(hostname -s)" --no-auto-me'
    )
    assert '--dev' not in command
    assert 'PYTHONPATH' not in command
    assert '127.0.0.1' not in command
