"""Shared wildcard matching for permission rules.

Rule format: ``server---tool`` or ``server---tool:content_pattern``
Supports ``*`` / ``?`` wildcards via fnmatch, ``|`` to separate alternatives.
"""

from __future__ import annotations

from typing import Any

from ms_agent.utils.pattern_matcher import match_pattern

TOOL_SPLITER = '---'
CONTENT_SEP = ':'

# Claude / Cursor tool names that appear in hook ``if`` conditions.
_EXTERNAL_TOOL_ALIASES: dict[str, str] = {
    'Bash': f'*{TOOL_SPLITER}shell_executor',
    'Shell': f'*{TOOL_SPLITER}shell_executor',
    'Read': f'*{TOOL_SPLITER}read_file',
    'Write': f'*{TOOL_SPLITER}write_file',
    'Edit': f'*{TOOL_SPLITER}edit_file',
    'WebFetch': f'*{TOOL_SPLITER}fetch_page',
    'Agent': f'plugin_agents{TOOL_SPLITER}*',
}


def extract_match_content(tool_name: str, tool_args: dict[str,
                                                          Any]) -> str | None:
    """Extract the primary content string from tool args for content-pattern matching."""
    val = None
    if tool_name.endswith(f'{TOOL_SPLITER}shell_executor'):
        val = tool_args.get('command')
    elif tool_name.endswith(f'{TOOL_SPLITER}write_file'):
        val = tool_args.get('path')
    elif tool_name.endswith(f'{TOOL_SPLITER}read_file'):
        val = tool_args.get('path')
    elif tool_name.endswith(f'{TOOL_SPLITER}edit_file'):
        val = tool_args.get('path')
    elif tool_name.endswith(f'{TOOL_SPLITER}grep'):
        val = tool_args.get('pattern')
    elif tool_name.endswith(f'{TOOL_SPLITER}glob'):
        val = tool_args.get('pattern')
    else:
        for key in ('path', 'command', 'query', 'url', 'pattern'):
            if key in tool_args:
                val = tool_args[key]
                break
    return str(val) if val is not None else None


def _with_bare_command_variants(content_pattern: str) -> str:
    """Add an argument-less variant for every ``<cmd> *`` alternative.

    ``curl *`` means "the curl command with any arguments" — and running it with
    NONE is a case of that. fnmatch disagrees: it wants the space and at least
    one character after it, so bare ``curl`` slipped past a rule written to gate
    exactly that, and a remembered ``whoami *`` failed to match the very
    ``whoami`` it was generated from.

    Only the space-star idiom of shell commands is extended. Path patterns end
    in ``/*`` (``~/.ssh/*``) or ``=*`` (``dd if=*``) and are left alone — there
    the trailing component is meaningful, not an optional argument list.
    """
    alts = [a.strip() for a in content_pattern.split('|')]
    out = list(alts)
    for alt in alts:
        if alt.endswith(' *'):
            out.append(alt[:-2].rstrip())
    return '|'.join(p for p in out if p)


class PermissionMatcher:
    """Wildcard matcher for permission rules, shared by both SafetyGuard and PermissionEnforcer."""

    def match(self, pattern: str, tool_call: str) -> bool:
        """Match a tool call string against a pattern using fnmatch.

        Supports ``|`` separated alternatives: ``read_file|write_file``.
        """
        return match_pattern(pattern, tool_call)

    def match_with_content(
        self,
        pattern: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> bool:
        """Match with optional content pattern after ``:``.

        Examples::

            "file_system---read_file"               → matches tool name only
            "code_executor---shell_executor:pip *"   → matches tool name + command content
            "file_system---*"                        → wildcard on tool name
        """
        if CONTENT_SEP in pattern:
            tool_pattern, content_pattern = pattern.split(CONTENT_SEP, 1)
        else:
            tool_pattern = pattern
            content_pattern = None

        if not self.match(tool_pattern, tool_name):
            return False

        if content_pattern is None:
            return True

        content = extract_match_content(tool_name, tool_args)
        if content is None:
            return False

        return self.match(
            _with_bare_command_variants(content_pattern), content)

    def matches_route(
        self,
        matcher: str,
        if_pattern: str | None,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> bool:
        """Two-layer filter: tool ``matcher`` then optional content ``if``."""
        if matcher and not self.match(matcher, tool_name):
            return False
        if not if_pattern:
            return True
        return matches_if_condition(
            if_pattern, tool_name, tool_args, matcher=self)


def resolve_external_tool_matcher(pattern: str) -> str:
    """Map a Claude-style tool name to an ms-agent matcher when possible."""
    stripped = pattern.strip()
    if not stripped:
        return stripped
    if TOOL_SPLITER in stripped or stripped == '*':
        return stripped
    return _EXTERNAL_TOOL_ALIASES.get(stripped, stripped)


def parse_if_condition(raw: str) -> tuple[str | None, str | None]:
    """Split an ``if`` string into ``(tool_matcher, content_pattern)``.

    Accepted forms::

        curl *                                      → (None, 'curl *')
        code_executor---shell_executor:curl *       → (tool, content)
        Bash(git *)                                 → ('Bash', 'git *')
        Bash                                        → ('Bash', None)
    """
    text = (raw or '').strip()
    if not text:
        return None, None
    if CONTENT_SEP in text:
        tool_part, content_part = text.split(CONTENT_SEP, 1)
        if TOOL_SPLITER in tool_part:
            return tool_part.strip() or None, content_part.strip() or None
    if '(' in text and text.endswith(')'):
        name, _, rest = text.partition('(')
        return name.strip() or None, rest[:-1].strip() or None
    if TOOL_SPLITER in text:
        return text, None
    if resolve_external_tool_matcher(text) != text:
        return text, None
    return None, text


def matches_if_condition(
    if_pattern: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    matcher: PermissionMatcher | None = None,
) -> bool:
    """Evaluate a hook/route ``if`` against a concrete tool call."""
    m = matcher or PermissionMatcher()
    tool_pat, content_pat = parse_if_condition(if_pattern)
    if tool_pat:
        native = resolve_external_tool_matcher(tool_pat)
        if not m.match(native, tool_name) and not m.match(tool_pat, tool_name):
            return False
    if content_pat is None:
        return True
    content = extract_match_content(tool_name, tool_args)
    if content is None:
        return False
    if m.match(content_pat, content):
        return True
    # PowerShell is case-insensitive; Unix binaries are usually lowercase.
    # Routing false-positive to agent/human is safer than missing a match.
    return m.match(content_pat.lower(), content.lower())
