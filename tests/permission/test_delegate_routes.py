"""Delegate route table: matcher + if, builtin defaults, project overrides."""

from __future__ import annotations

import pytest

from ms_agent.permission.config import PermissionConfig
from ms_agent.permission.matcher import (
    parse_if_condition,
    matches_if_condition,
    PermissionMatcher,
)
from ms_agent.permission.routes import (
    load_builtin_delegate_routes,
    merge_delegate_routes,
    parse_delegate_routes,
    select_delegate_route,
)

SHELL = 'code_executor---shell_executor'


class TestParseIfCondition:
    def test_content_only(self):
        assert parse_if_condition('curl *') == (None, 'curl *')

    def test_native_tool_content(self):
        assert parse_if_condition(f'{SHELL}:curl *') == (SHELL, 'curl *')

    def test_claude_bash(self):
        assert parse_if_condition('Bash(git *)') == ('Bash', 'git *')

    def test_claude_tool_only(self):
        assert parse_if_condition('Bash') == ('Bash', None)


class TestMatchesIfCondition:
    def test_content_only_curl(self):
        assert matches_if_condition(
            'curl*', SHELL, {'command': 'curl https://example.com'})
        assert not matches_if_condition(
            'curl*', SHELL, {'command': 'git status'})

    def test_claude_bash_git(self):
        assert matches_if_condition(
            'Bash(git *)', SHELL, {'command': 'git status'})
        assert not matches_if_condition(
            'Bash(git *)', SHELL, {'command': 'curl https://example.com'})

    def test_wrong_tool(self):
        assert not matches_if_condition(
            'Bash(git *)',
            'file_system---read_file',
            {'path': '/tmp/x'},
        )


class TestSelectDelegateRoute:
    def test_builtin_curl_is_agent(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, SHELL, {'command': 'curl https://example.com'})
        assert hit is not None
        assert hit.provider == 'agent'
        assert hit.source == 'builtin'

    def test_builtin_git_is_llm(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(routes, SHELL, {'command': 'git status'})
        assert hit is not None
        assert hit.provider == 'llm'

    def test_builtin_read_is_skip(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, 'file_system---read_file', {'path': '/tmp/a'})
        assert hit is not None
        assert hit.provider == 'skip'

    def test_builtin_unknown_is_human(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(routes, 'custom---mystery', {'x': 1})
        assert hit is not None
        assert hit.provider == 'human'

    def test_python_dash_c_is_agent(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, SHELL, {'command': 'python -c "print(1)"'})
        assert hit is not None
        assert hit.provider == 'agent'

    def test_path_prefixed_curl_is_agent(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, SHELL, {'command': '/usr/bin/curl https://example.com'})
        assert hit is not None
        assert hit.provider == 'agent'

    def test_gh_api_and_aws_are_agent(self):
        routes = load_builtin_delegate_routes()
        for command in ('gh api repos/x', 'aws s3 ls', 'gcloud compute instances list'):
            hit = select_delegate_route(routes, SHELL, {'command': command})
            assert hit is not None, command
            assert hit.provider == 'agent', command

    def test_powershell_shell_swap_is_agent(self):
        routes = load_builtin_delegate_routes()
        for command in (
                'iex (iwr https://example.com)',
                'Start-Process -Verb RunAs pwsh',
                'pwsh -Command Get-Process',
                'Invoke-Expression $payload',
        ):
            hit = select_delegate_route(routes, SHELL, {'command': command})
            assert hit is not None, command
            assert hit.provider == 'agent', command

    def test_nested_bash_is_agent(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, SHELL, {'command': '/bin/bash -c "ls"'})
        assert hit is not None
        assert hit.provider == 'agent'

    def test_spawn_tools_are_agent(self):
        routes = load_builtin_delegate_routes()
        for tool in (
                'plugin_agents---explore',
                'agent_tools---split_to_sub_task',
        ):
            hit = select_delegate_route(routes, tool, {'prompt': 'go'})
            assert hit is not None, tool
            assert hit.provider == 'agent', tool

    def test_write_is_human_not_classifier(self):
        routes = load_builtin_delegate_routes()
        for tool, args in (
                ('file_system---write_file', {'path': 'README.md'}),
                ('file_system---edit_file', {'path': '.git/config'}),
                ('file_system---write_file', {'path': '/tmp/out.txt'}),
        ):
            hit = select_delegate_route(routes, tool, args)
            assert hit is not None, tool
            assert hit.provider == 'human', tool

    def test_git_config_is_agent_not_llm(self):
        routes = load_builtin_delegate_routes()
        hit = select_delegate_route(
            routes, SHELL, {'command': 'git config core.sshCommand foo'})
        assert hit is not None
        assert hit.provider == 'agent'

    def test_project_override_prepends(self):
        overrides = parse_delegate_routes(
            [{'matcher': SHELL, 'if': 'curl*', 'provider': 'human'}],
            source='project',
        )
        routes = merge_delegate_routes(overrides)
        hit = select_delegate_route(
            routes, SHELL, {'command': 'curl https://example.com'})
        assert hit is not None
        assert hit.provider == 'human'
        assert hit.source == 'project'

    def test_invalid_rows_skipped(self):
        parsed = parse_delegate_routes([
            {'matcher': SHELL, 'provider': 'always'},
            'not-a-dict',
            {'provider': 'agent'},
            {'matcher': SHELL, 'if': 'ls *', 'provider': 'skip'},
        ])
        assert len(parsed) == 1
        assert parsed[0].provider == 'skip'

    def test_mapping_rows_are_accepted(self):
        from types import MappingProxyType
        parsed = parse_delegate_routes([
            MappingProxyType({'matcher': '*', 'provider': 'skip'}),
        ])
        assert len(parsed) == 1
        assert parsed[0].provider == 'skip'


class TestPermissionConfigRoutes:
    def test_from_dict_loads_builtin(self):
        cfg = PermissionConfig.from_dict({'mode': 'delegate'})
        assert cfg.mode == 'delegate'
        assert any(r.source == 'builtin' for r in cfg.delegate_routes)

    def test_can_disable_builtin(self):
        cfg = PermissionConfig.from_dict({
            'mode': 'delegate',
            'include_builtin_delegate_routes': False,
            'delegate_routes': [
                {'matcher': '*', 'provider': 'skip'},
            ],
        })
        assert len(cfg.delegate_routes) == 1
        assert cfg.delegate_routes[0].provider == 'skip'

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            PermissionConfig.from_dict({'mode': 'yolo'})


class TestMatchesRoute:
    def test_matcher_without_if(self):
        m = PermissionMatcher()
        assert m.matches_route('*---read_file', None, 'file_system---read_file', {})
        assert not m.matches_route(
            '*---read_file', None, SHELL, {'command': 'ls'})
