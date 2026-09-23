"""Configuration parsing for the permission module.

Reads the ``permission`` section from agent YAML and produces frozen
dataclasses consumed by SafetyGuard and PermissionEnforcer.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from .routes import (DelegateProvider, DelegateRoute, merge_delegate_routes,
                     parse_delegate_routes)

# Default safety rules baked into SafetyConfig when none are configured.
_DEFAULT_SAFETY_PATTERNS: tuple[str, ...] = (
    'code_executor---shell_executor:rm -rf /*',
    'code_executor---shell_executor:mkfs *',
    'code_executor---shell_executor:dd if=*',
)

_DEFAULT_SENSITIVE_PATHS: tuple[str, ...] = (
    '/etc/*',
    '/sys/*',
    '/boot/*',
    '/dev/*',
    '/proc/*',
    '~/.ssh/*',
    '~/.gnupg/*',
    '~/.bashrc',
    '~/.zshrc',
    '~/.profile',
    '.git/config',
    '.git/hooks/*',
    '**/.git/**',
)

_DEFAULT_DANGEROUS_REMOVAL: tuple[str, ...] = (
    '*',
    '/*',
    '/',
    '~',
)


@dataclass(frozen=True)
class SafetyConfig:
    """Inner-layer safety configuration (non-bypassable)."""
    patterns: tuple[str, ...] = _DEFAULT_SAFETY_PATTERNS
    sensitive_paths: tuple[str, ...] = _DEFAULT_SENSITIVE_PATHS
    dangerous_removal_paths: tuple[str, ...] = _DEFAULT_DANGEROUS_REMOVAL
    read_policy: Literal['loose', 'strict'] = 'loose'
    max_command_chars: int = 8192
    allowed_directories: tuple[str, ...] = ()
    read_only_directories: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls,
                  d: dict[str, Any],
                  project_root: str | None = None) -> SafetyConfig:
        patterns = tuple(d.get('patterns', _DEFAULT_SAFETY_PATTERNS))
        sensitive = tuple(d.get('sensitive_paths', _DEFAULT_SENSITIVE_PATHS))
        dangerous = tuple(
            d.get('dangerous_removal_paths', _DEFAULT_DANGEROUS_REMOVAL))

        path_validation = d.get('path_validation', {})
        read_policy = path_validation.get('read_policy', 'loose')
        max_chars = path_validation.get('max_command_chars', 8192)

        def _expand_dirs(raw: list[str]) -> tuple[str, ...]:
            out: list[str] = []
            for entry in raw:
                if entry == '${PROJECT_ROOT}' and project_root:
                    out.append(project_root)
                else:
                    out.append(os.path.expandvars(entry))
            return tuple(out)

        allowed = _expand_dirs(list(d.get('allowed_directories', [])))
        read_only = _expand_dirs(list(d.get('read_only_directories', [])))

        return cls(
            patterns=patterns,
            sensitive_paths=sensitive,
            dangerous_removal_paths=dangerous,
            read_policy=read_policy,
            max_command_chars=max_chars,
            allowed_directories=allowed,
            read_only_directories=read_only,
        )


_DEFAULT_BLACKLIST: tuple[str, ...] = (
    'code_executor---shell_executor:curl *',
    'code_executor---shell_executor:wget *',
    'code_executor---shell_executor:ssh *',
    'code_executor---shell_executor:scp *',
    'code_executor---shell_executor:rsync *',
    'code_executor---shell_executor:nc *',
    'code_executor---shell_executor:netcat *',
)


_VALID_MODES = frozenset({
    'auto',
    'strict',
    'interactive',
    'delegate',
    'accept_edits',
    'plan',
})
_MODE_ALIASES = {
    'restricted': 'interactive',
    'acceptEdits': 'accept_edits',
}


def _positive_timeout(value: Any, name: str) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f'{name} must be finite and greater than zero')
    return timeout


@dataclass(frozen=True)
class PermissionConfig:
    """Top-level permission configuration from agent YAML."""
    mode: Literal[
        'auto', 'strict', 'interactive', 'delegate', 'accept_edits',
        'plan'] = 'auto'
    whitelist: tuple[str, ...] = ()
    blacklist: tuple[str, ...] = ()
    ask_rules: tuple[str, ...] = ()
    safety: SafetyConfig = SafetyConfig()
    delegate_routes: tuple[DelegateRoute, ...] = field(
        default_factory=lambda: merge_delegate_routes())
    default_delegate_provider: DelegateProvider = 'human'
    # One-shot LLM classifier. Agent verifier is a multi-turn repo
    # inspect (see agent_provider_timeout).
    provider_timeout: float = 30.0
    agent_provider_timeout: float = 60.0

    @classmethod
    def from_dict(cls,
                  d: dict[str, Any],
                  project_root: str | None = None) -> PermissionConfig:
        if not d:
            return cls()

        raw_mode = d.get('mode', 'auto')
        mode = _MODE_ALIASES.get(raw_mode, raw_mode)
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Unknown permission mode {raw_mode!r} "
                '(auto | restricted | strict | interactive | delegate | '
                'accept_edits | plan)')
        whitelist = tuple(d.get('whitelist', ()))
        ask_rules = tuple(d.get('ask_rules', ()))
        user_blacklist = tuple(d.get('blacklist', ()))
        # Network-egress commands (curl/wget/ssh/...) are allowed by
        # default. ``allow_network: false`` restores the built-in egress
        # blacklist. Legacy ``no_default_blacklist: true`` is treated as
        # allow_network. User blacklist entries always apply.
        allow_network = bool(
            d.get('allow_network', True)
            or d.get('no_default_blacklist', False))
        base_blacklist = () if allow_network else _DEFAULT_BLACKLIST
        blacklist = base_blacklist + tuple(
            p for p in user_blacklist if p not in base_blacklist)

        safety_raw = d.get('safety_rules', {})
        # Merge directory configs from top level into safety config
        for _dir_key in ('allowed_directories', 'read_only_directories'):
            if _dir_key in d and _dir_key not in safety_raw:
                safety_raw = dict(safety_raw)
                safety_raw[_dir_key] = d[_dir_key]
        if 'path_validation' in d and 'path_validation' not in safety_raw:
            safety_raw = dict(safety_raw)
            safety_raw['path_validation'] = d['path_validation']

        safety = SafetyConfig.from_dict(safety_raw, project_root=project_root)

        include_builtin = bool(
            d.get('include_builtin_delegate_routes', True))
        overrides = parse_delegate_routes(
            d.get('delegate_routes'), source='project')
        delegate_routes = merge_delegate_routes(
            overrides, include_builtin=include_builtin)

        default_provider = d.get('default_delegate_provider', 'human')
        if default_provider not in ('skip', 'llm', 'agent', 'human'):
            raise ValueError(
                "default_delegate_provider must be skip | llm | agent | human")

        provider_timeout = _positive_timeout(
            d.get('provider_timeout', 30.0), 'provider_timeout')
        agent_provider_timeout = _positive_timeout(
            d.get('agent_provider_timeout', 60.0), 'agent_provider_timeout')

        return cls(
            mode=mode,
            whitelist=whitelist,
            blacklist=blacklist,
            ask_rules=ask_rules,
            safety=safety,
            delegate_routes=delegate_routes,
            default_delegate_provider=default_provider,
            provider_timeout=provider_timeout,
            agent_provider_timeout=agent_provider_timeout,
        )
