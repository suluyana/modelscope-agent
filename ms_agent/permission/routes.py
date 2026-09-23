"""Data-driven delegate routing: matcher + if → skip | llm | agent | human."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from .matcher import PermissionMatcher

DelegateProvider = Literal['skip', 'llm', 'agent', 'human']
VALID_PROVIDERS: frozenset[str] = frozenset(
    ('skip', 'llm', 'agent', 'human'))

_BUILTIN_YAML = Path(__file__).with_name('delegate_routes.yaml')


@dataclass(frozen=True)
class DelegateRoute:
    """One matcher/if row. First match wins."""

    matcher: str
    provider: DelegateProvider
    if_pattern: str = ''
    source: str = 'builtin'


def parse_delegate_routes(
    raw: list[Any] | tuple[Any, ...] | None,
    *,
    source: str = 'project',
) -> tuple[DelegateRoute, ...]:
    """Parse a list of route dicts. Invalid rows are skipped."""
    if not raw:
        return ()
    routes: list[DelegateRoute] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        matcher = str(item.get('matcher') or '').strip()
        provider = item.get('provider')
        if not matcher or provider not in VALID_PROVIDERS:
            continue
        if_pattern = str(
            item.get('if') or item.get('if_pattern') or '').strip()
        routes.append(
            DelegateRoute(
                matcher=matcher,
                provider=provider,
                if_pattern=if_pattern,
                source=source,
            ))
    return tuple(routes)


@lru_cache(maxsize=1)
def load_builtin_delegate_routes() -> tuple[DelegateRoute, ...]:
    """Load the packaged default table."""
    with open(_BUILTIN_YAML, encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        return ()
    return parse_delegate_routes(data, source='builtin')


def merge_delegate_routes(
    overrides: tuple[DelegateRoute, ...] | list[DelegateRoute] = (),
    *,
    include_builtin: bool = True,
) -> tuple[DelegateRoute, ...]:
    """Project/plugin rows first, then the shipped defaults."""
    head = tuple(overrides)
    if not include_builtin:
        return head
    return head + load_builtin_delegate_routes()


def select_delegate_route(
    routes: tuple[DelegateRoute, ...] | list[DelegateRoute],
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    matcher: PermissionMatcher | None = None,
) -> DelegateRoute | None:
    """Return the first matching route, or None if the table is empty."""
    m = matcher or PermissionMatcher()
    for route in routes:
        if m.matches_route(
                route.matcher, route.if_pattern or None, tool_name, tool_args):
            return route
    return None
