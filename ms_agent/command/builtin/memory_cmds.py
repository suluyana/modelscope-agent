"""Slash command for memory on/off (same files WebUI personalization / project)."""
from __future__ import annotations

from dataclasses import replace

from ms_agent.command.router import CommandRouter
from ms_agent.command.types import (CommandContext, CommandDef, CommandResult,
                                    CommandResultType)

CMD_MEMORY = CommandDef(
    name='memory',
    description='Turn unified memory on/off (shared with WebUI)',
    category='config',
)

_USAGE = (
    'usage:\n'
    '  /memory\n'
    '  /memory on|off                 (this project)\n'
    '  /memory project on|off\n'
    '  /memory global on|off          (default for newly opened folders)\n'
    '  /memory backend file|vector\n'
    'Project flag is what injects memory.unified_memory (same as WebUI). '
    'Global flag is the default for new projects. Vector stays WebUI-owned; '
    'TUI file backend writes MEMORY.md under <work>/.ms_agent/memory/.'
)

_ON = frozenset({'on', 'true', '1', 'enable', 'enabled'})
_OFF = frozenset({'off', 'false', '0', 'disable', 'disabled'})


def _work_dir(ctx: CommandContext) -> str | None:
    config = getattr(ctx.runtime, 'config', None) if ctx.runtime else None
    if config is None:
        return None
    work = getattr(config, 'output_dir', None)
    return str(work) if work else None


def _pm():
    from ms_agent.project import ProjectManager
    from ms_agent.project.paths import global_home
    return ProjectManager(base_dir=str(global_home()))


def _project(ctx: CommandContext):
    work = _work_dir(ctx)
    if not work:
        return None
    return _pm().find_by_path(work)


def _status_text(ctx: CommandContext) -> str:
    from ms_agent.personalization.settings import PersonalizationSettings
    loaded = PersonalizationSettings().load()
    g_on = 'on' if loaded.memory_enabled else 'off'
    g_be = loaded.memory_backend or 'file'
    lines = [
        f'Global default: {g_on}  backend={g_be}',
        '(applies when TUI/WebUI first opens a new folder)',
    ]
    project = _project(ctx)
    if project is None:
        lines.append('Project: (no work dir)')
    else:
        p_on = 'on' if project.memory_enabled else 'off'
        p_be = project.memory_backend or g_be
        lines.append(f'Project: {p_on}  backend={p_be}  id={project.id}')
    lines.append('')
    lines.append(_USAGE)
    return '\n'.join(lines)


def _parse_bool(token: str) -> bool | None:
    low = token.lower()
    if low in _ON:
        return True
    if low in _OFF:
        return False
    return None


async def _apply_live(ctx: CommandContext, project) -> str:
    from ms_agent.personalization.memory_apply import apply_project_memory
    agent = ctx.runtime
    cfg = getattr(agent, 'config', None) if agent is not None else None
    if cfg is None:
        return 'Takes effect on /new or restart.'
    kind = apply_project_memory(cfg, project)
    if kind == 'off':
        return 'Saved. /new to drop memory tools already loaded this session.'
    if kind == 'vector-unavailable':
        return (
            'Saved vector backend for WebUI. TUI does not start vector/mem0 '
            'this session (no silent file fallback). Use /memory backend file '
            'or open the project in WebUI.')
    tools = getattr(agent, 'memory_tools', None) or []
    if tools:
        return 'Saved. Memory already loaded; /new to rebuild.'
    load = getattr(agent, 'load_memory', None)
    if load is None:
        return 'Saved. /new to apply.'
    try:
        await load()
        return 'Memory tools registered for this session.'
    except Exception as exc:  # noqa: BLE001
        return f'Saved; load failed ({exc}). /new to apply.'


async def cmd_memory(ctx: CommandContext) -> CommandResult:
    arg = (ctx.args or '').strip()
    if not arg or arg in ('help', '-h', '--help', 'status'):
        return CommandResult(
            type=CommandResultType.MESSAGE, content=_status_text(ctx))

    import shlex
    try:
        parts = shlex.split(arg)
    except ValueError:
        parts = arg.split()
    action = parts[0].lower()
    rest = parts[1:]

    if action in ('global', 'project') or action in _ON or action in _OFF:
        return await _cmd_toggle(ctx, action, rest)
    if action == 'backend':
        return _cmd_backend(ctx, rest)
    return CommandResult(type=CommandResultType.MESSAGE, content=_USAGE)


async def _cmd_toggle(ctx: CommandContext, action: str,
                      rest: list[str]) -> CommandResult:
    from ms_agent.personalization.settings import PersonalizationSettings

    scope = 'project'
    token = action
    if action in ('global', 'project'):
        scope = action
        if not rest:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_USAGE)
        token = rest[0]
    enabled = _parse_bool(token)
    if enabled is None:
        return CommandResult(type=CommandResultType.MESSAGE, content=_USAGE)

    if scope == 'global':
        settings = PersonalizationSettings()
        loaded = settings.load()
        settings.save(replace(loaded, memory_enabled=enabled))
        state = 'on' if enabled else 'off'
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=(
                f'Global memory default → {state}. '
                'New folders inherit this; this project is unchanged.'),
        )

    project = _project(ctx)
    if project is None:
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content='Project memory needs a work dir (TUI --work-dir).',
        )
    updated = _pm().update(project.id, memory_enabled=enabled)
    note = await _apply_live(ctx, updated)
    state = 'on' if enabled else 'off'
    return CommandResult(
        type=CommandResultType.MESSAGE,
        content=f'Project memory → {state}. {note}',
    )


def _cmd_backend(ctx: CommandContext, rest: list[str]) -> CommandResult:
    from ms_agent.personalization.settings import PersonalizationSettings

    if not rest or rest[0].lower() not in ('file', 'vector'):
        return CommandResult(type=CommandResultType.MESSAGE, content=_USAGE)
    backend = rest[0].lower()
    settings = PersonalizationSettings()
    loaded = settings.load()
    settings.save(replace(loaded, memory_backend=backend))
    project = _project(ctx)
    extra = ''
    if project is not None:
        _pm().update(project.id, memory_backend=backend)
        extra = ' Project backend updated too.'
    return CommandResult(
        type=CommandResultType.MESSAGE,
        content=(
            f'Memory backend → {backend}.{extra} '
            '/memory on (and /new) to apply. Vector is WebUI-owned.'),
    )


def register_memory_commands(router: CommandRouter) -> None:
    router.register(CMD_MEMORY, cmd_memory)
