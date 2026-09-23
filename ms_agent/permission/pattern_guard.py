"""Reject unsafe always-allow patterns before they land in PermissionMemory.

Same rules as TUI persist-row edits: no ``|`` alternatives, no bare wildcards,
no compound-shell prefixes. Settings CRUD and chat ``allow_always`` share this.
"""

from __future__ import annotations

import re

from .matcher import CONTENT_SEP, TOOL_SPLITER

_BARE_WILDCARDS = frozenset({'*', '?', '**'})
_COMPOUND_RE = re.compile(r'(?:&&|\|\||;|\n)')


class UnsafePersistPattern(ValueError):
    """Raised when a user-authored always-allow pattern is too broad."""


def classify_rule_kind(pattern: str) -> str:
    """Best-effort kind for settings display: shell / file / domain / tool."""
    text = (pattern or '').strip()
    lower = text.lower()
    if lower.startswith('domain:') or '://' in text:
        return 'domain'
    if 'shell_executor' in lower or lower.startswith('bash') or '---bash' in lower:
        return 'shell'
    if any(
            name in lower
            for name in ('write_file', 'edit_file', 'read_file', 'notebook')):
        return 'file'
    return 'tool'


def validate_persist_pattern(pattern: str) -> str:
    """Return a stripped pattern, or raise ``UnsafePersistPattern``.

    Rejects:
    * empty
    * ``|`` (alternation would persist two tools at once)
    * bare ``*`` / ``?`` / ``**`` (whole-tool or whole-content wildcards)
    * compound shell (``&&`` ``||`` ``;``) in the content suffix
    """
    text = (pattern or '').strip()
    if not text:
        raise UnsafePersistPattern('Pattern must not be empty')
    if '|' in text:
        raise UnsafePersistPattern(
            'Pattern must not contain | (persist one tool at a time)')
    content = text
    if CONTENT_SEP in text:
        _tool, content = text.split(CONTENT_SEP, 1)
    if text in _BARE_WILDCARDS or content.strip() in _BARE_WILDCARDS:
        raise UnsafePersistPattern(
            'Pattern must not be a bare wildcard (* / ? / **)')
    # Content that is only a wildcard token with spaces.
    if content.strip() in _BARE_WILDCARDS:
        raise UnsafePersistPattern(
            'Pattern must not be a bare wildcard (* / ? / **)')
    if TOOL_SPLITER in text and text.rsplit(TOOL_SPLITER, 1)[-1] in _BARE_WILDCARDS:
        raise UnsafePersistPattern(
            'Pattern must not be a bare wildcard (* / ? / **)')
    if _COMPOUND_RE.search(content):
        raise UnsafePersistPattern(
            'Compound shell commands cannot be persisted as always-allow')
    return text


def is_file_edit_tool(tool_name: str) -> bool:
    """Claude acceptEdits: workspace file write/edit, not shell or network."""
    name = tool_name or ''
    return name.endswith(f'{TOOL_SPLITER}write_file') or name.endswith(
        f'{TOOL_SPLITER}edit_file') or name.endswith(
            f'{TOOL_SPLITER}notebook_edit')


_PLAN_READONLY_SUFFIXES = (
    f'{TOOL_SPLITER}read_file',
    f'{TOOL_SPLITER}grep',
    f'{TOOL_SPLITER}glob',
    f'{TOOL_SPLITER}list_dir',
    f'{TOOL_SPLITER}list_directory',
    f'{TOOL_SPLITER}list_files',
)


def is_plan_readonly_tool(tool_name: str) -> bool:
    """Plan mode: reads + task-board writes (to record the plan)."""
    name = tool_name or ''
    if any(name.endswith(suf) for suf in _PLAN_READONLY_SUFFIXES):
        return True
    if name.startswith('team_taskboard---') or name.startswith(
            'team_dispatch---'):
        return True
    return False
