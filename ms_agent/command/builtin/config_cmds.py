import os

from ms_agent.command.router import CommandRouter
from ms_agent.command.types import (CommandContext, CommandDef, CommandResult,
                                    CommandResultType)

CMD_MODEL = CommandDef(
    name='model',
    description='Show, switch, or manage model providers (shared with WebUI)',
    category='config',
)

CMD_CONFIG = CommandDef(
    name='config',
    description='Show current runtime configuration',
    category='config',
    aliases=('settings', ),
)

_MODEL_USAGE = (
    'usage:\n'
    '  /model\n'
    '  /model list\n'
    '  /model <model>  or  /model <provider>/<model>\n'
    '  /model provider add <id> [key=] [url=] [protocol=openai|anthropic] [name=]\n'
    '  /model provider set <id> [key=] [url=] [protocol=] [name=]\n'
    '  /model provider key <id> <value>|clear\n'
    '  /model provider url <id> <url>|clear\n'
    '  /model provider remove <id>\n'
    '  /model catalog add <id> <model>\n'
    '  /model catalog remove <id> <model>\n'
    'Providers/keys land in ~/.ms_agent/settings.json (same as WebUI model settings).'
)

_CLEAR = frozenset({'clear', '-', 'none'})


def _persist_model_to_config(config, new_model: str, service=None):
    """Persist the model (and optional service) change to the project patch.

    Writes ``llm.model`` (and ``llm.service`` when a provider switch happened)
    into ``<work_dir>/.ms_agent/config.yaml`` rather than mutating the
    version-controlled source YAML. Anchored to the **work dir** (``output_dir``)
    — the project — not the config file's directory, so running a shared or
    packaged template config (e.g. ``demos/``) never scatters a ``.ms_agent/``
    next to it. The work-dir patch is merged back (patch wins) on the next run.
    Returns the patch path on success, or None if no dir is known / write fails.
    """
    from omegaconf import OmegaConf

    # Prefer the work dir; fall back to the config's own dir only if unset.
    base_dir = (
        getattr(config, 'output_dir', None)
        or getattr(config, 'local_dir', None))
    if not base_dir:
        return None
    # Write to the new .ms_agent/ dir; migrate an existing legacy
    # .ms-agent/config.yaml so a single patch file remains authoritative.
    patch_dir = os.path.join(str(base_dir), '.ms_agent')
    patch_path = os.path.join(patch_dir, 'config.yaml')
    legacy_path = os.path.join(str(base_dir), '.ms-agent', 'config.yaml')
    try:
        os.makedirs(patch_dir, exist_ok=True)
        source = patch_path if os.path.isfile(patch_path) else legacy_path
        patch = (
            OmegaConf.load(source)
            if os.path.isfile(source) else OmegaConf.create({}))
        OmegaConf.update(patch, 'llm.model', new_model, merge=True)
        if service:
            OmegaConf.update(patch, 'llm.service', service, merge=True)
        OmegaConf.save(patch, patch_path)
        return patch_path
    except OSError:
        return None


def _mgr():
    from ms_agent.config.model_settings import ModelSettingsManager
    from ms_agent.project.paths import global_home
    return ModelSettingsManager(global_home())


def _builtin_ids() -> set[str]:
    from ms_agent.llm.spec import get_registry
    return {spec.name for spec in get_registry().list_providers()}


def _mask_key(value) -> str:
    if not value:
        return 'missing'
    return 'set'


def _provider_status_lines(mgr) -> list[str]:
    lines = ['Providers (settings.json, shared with WebUI):']
    default = mgr.get_default_model()
    if default:
        lines.append(f'Default: {default}')
    custom = mgr.list_custom_providers()
    order: list[str] = []
    rows: dict = {}
    for provider in mgr.list_providers():
        pid = provider['id']
        if pid not in rows:
            order.append(pid)
        if pid not in rows or provider.get('overrides_builtin'):
            rows[pid] = provider
    for pid in order:
        provider = rows[pid]
        override = custom.get(pid) or {}
        models = list(override.get('models') or provider.get('models') or [])
        model_txt = ', '.join(models) or '(none listed)'
        if provider.get('builtin') or provider.get('overrides_builtin'):
            kind = 'builtin'
            if override:
                kind += '+override'
        else:
            kind = 'custom'
        key = _mask_key(override.get('api_key'))
        url = override.get('base_url') or '(default)'
        proto = override.get('protocol') or provider.get('protocol') or ''
        lines.append(f'  {pid} ({kind}): {model_txt}')
        lines.append(f'    protocol={proto}  key={key}  url={url}')
    return lines


def _rebuild_llm(ctx: CommandContext) -> None:
    if not ctx.runtime or not ctx.runtime.llm:
        return
    from ms_agent.llm import LLM
    target = ctx.runtime.llm
    config = target.config
    try:
        rebuilt = LLM.from_config(config)
    except Exception:  # noqa: BLE001 - best-effort; in-place update stands
        rebuilt = None
    if rebuilt is None:
        return
    if type(rebuilt) is not type(target):
        target.__class__ = rebuilt.__class__
    target.__dict__ = rebuilt.__dict__


def _push_provider_to_runtime(ctx: CommandContext, provider_id: str) -> str:
    if not ctx.runtime or not ctx.runtime.llm:
        return ''
    from omegaconf import OmegaConf
    from ms_agent.tui.app import TuiApp
    config = ctx.runtime.llm.config
    service = str(OmegaConf.select(config, 'llm.service', default='') or '')
    if service != provider_id:
        return ' Switch with /model <id>/<model> to use this provider live.'
    TuiApp._apply_provider_credentials(config, overwrite=True)
    _rebuild_llm(ctx)
    return ' Live credentials applied.'


def _split_kv(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    rest: list[str] = []
    fields: dict[str, str] = {}
    for tok in tokens:
        if '=' in tok:
            key, val = tok.split('=', 1)
            fields[key] = val
        else:
            rest.append(tok)
    return rest, fields


def _kv_alias(fields: dict[str, str]) -> dict[str, str]:
    out = dict(fields)
    if 'url' in out and 'base_url' not in out:
        out['base_url'] = out.pop('url')
    elif 'url' in out:
        out.pop('url')
    if 'key' in out and 'api_key' not in out:
        out['api_key'] = out.pop('key')
    elif 'key' in out:
        out.pop('key')
    return out


async def cmd_model(ctx: CommandContext) -> CommandResult:
    if not ctx.runtime or not ctx.runtime.llm:
        return CommandResult(
            type=CommandResultType.MESSAGE, content='No active agent.')

    if not ctx.args:
        model = ctx.runtime.llm.model
        service = getattr(ctx.runtime.llm.config.llm, 'service', 'unknown')
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=(
                f'Model: {model}\nService: {service}\n'
                + _MODEL_USAGE),
        )

    arg = ctx.args.strip()
    import shlex
    try:
        parts = shlex.split(arg)
    except ValueError:
        parts = arg.split()
    head = parts[0].lower()
    if head == 'help' or arg in ('-h', '--help'):
        return CommandResult(
            type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
    if head == 'list':
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content='\n'.join(_provider_status_lines(_mgr())),
        )
    if head == 'provider':
        return _cmd_model_provider(ctx, parts[1:])
    if head == 'catalog':
        return _cmd_model_catalog(parts[1:])
    return _cmd_model_switch(ctx, arg)


def _cmd_model_switch(ctx: CommandContext, arg: str) -> CommandResult:
    service_override = None
    new_model = arg
    if '/' in arg:
        service_override, new_model = arg.split('/', 1)
        service_override = service_override.strip()
        new_model = new_model.strip()

    from omegaconf import OmegaConf
    from ms_agent.tui.app import TuiApp

    target = ctx.runtime.llm
    config = target.config
    OmegaConf.update(config, 'llm.model', new_model, merge=True)
    if service_override:
        OmegaConf.update(config, 'llm.service', service_override, merge=True)
        TuiApp._apply_provider_credentials(config, overwrite=True)

    # Always apply the cheap in-place update: legacy LLM classes read
    # ``self.model`` at generate time, so this alone switches the model for
    # them (and keeps behavior unchanged when nothing else is possible).
    target.model = new_model
    _rebuild_llm(ctx)

    saved_path = _persist_model_to_config(config, new_model, service_override)
    settings_provider = service_override or str(
        getattr(getattr(config, 'llm', None), 'service', '') or '') or None
    _mgr().set_default_model(new_model, provider=settings_provider)
    switched = (f'{settings_provider}/{new_model}'
                if settings_provider else new_model)
    content = f'Switched to: {switched}'
    content += '\nSaved default_model in settings.json (shared with WebUI).'
    if saved_path:
        content += f'\nAlso saved project patch: {saved_path}'
    return CommandResult(
        type=CommandResultType.MUTATE_STATE,
        content=content,
    )


def _cmd_model_provider(ctx: CommandContext, tokens: list[str]) -> CommandResult:
    mgr = _mgr()
    if not tokens:
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content='\n'.join(_provider_status_lines(mgr)),
        )
    action = tokens[0].lower()
    rest = tokens[1:]
    if action in ('list', 'ls'):
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content='\n'.join(_provider_status_lines(mgr)),
        )
    if action == 'add':
        names, fields = _split_kv(rest)
        if not names:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        pid = names[0]
        fields = _kv_alias(fields)
        mgr.add_provider(
            pid,
            name=fields.get('name') or pid,
            protocol=fields.get('protocol') or 'openai',
            api_key=fields.get('api_key'),
            base_url=fields.get('base_url'),
        )
        note = _push_provider_to_runtime(ctx, pid)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Provider {pid} saved.{note}',
        )
    if action in ('set', 'update'):
        names, fields = _split_kv(rest)
        if not names:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        pid = names[0]
        fields = _kv_alias(fields)
        if not fields:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        mgr.patch_provider(
            pid,
            name=fields.get('name'),
            protocol=fields.get('protocol'),
            api_key=fields.get('api_key'),
            base_url=fields.get('base_url'),
            clear_api_key=fields.get('api_key', None) == '',
            clear_base_url=fields.get('base_url', None) == '',
        )
        note = _push_provider_to_runtime(ctx, pid)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Updated provider {pid}.{note}',
        )
    if action == 'key':
        if len(rest) < 2:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        pid, value = rest[0], ' '.join(rest[1:])
        if value.lower() in _CLEAR:
            mgr.patch_provider(pid, clear_api_key=True)
            note = _push_provider_to_runtime(ctx, pid)
            return CommandResult(
                type=CommandResultType.MESSAGE,
                content=f'Cleared API key for {pid}.{note}',
            )
        mgr.patch_provider(pid, api_key=value)
        note = _push_provider_to_runtime(ctx, pid)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Saved API key for {pid}.{note}',
        )
    if action in ('url', 'base_url'):
        if len(rest) < 2:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        pid, value = rest[0], rest[1]
        if value.lower() in _CLEAR:
            mgr.patch_provider(pid, clear_base_url=True)
            note = _push_provider_to_runtime(ctx, pid)
            return CommandResult(
                type=CommandResultType.MESSAGE,
                content=f'Cleared base URL for {pid}.{note}',
            )
        mgr.patch_provider(pid, base_url=value)
        note = _push_provider_to_runtime(ctx, pid)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Saved base URL for {pid}.{note}',
        )
    if action == 'remove':
        if not rest:
            return CommandResult(
                type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
        pid = rest[0]
        custom = mgr.list_custom_providers()
        if pid in _builtin_ids() and pid not in custom:
            return CommandResult(
                type=CommandResultType.MESSAGE,
                content=(
                    f'Cannot remove builtin provider {pid}. '
                    'Clear its override with /model provider key '
                    f'{pid} clear'),
            )
        mgr.remove_provider(pid)
        extra = ''
        if pid in _builtin_ids():
            extra = ' Builtin catalog remains; credential override cleared.'
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Removed provider {pid}.{extra}',
        )
    return CommandResult(type=CommandResultType.MESSAGE, content=_MODEL_USAGE)


def _cmd_model_catalog(tokens: list[str]) -> CommandResult:
    if len(tokens) < 3:
        return CommandResult(
            type=CommandResultType.MESSAGE, content=_MODEL_USAGE)
    action = tokens[0].lower()
    pid = tokens[1]
    model = ' '.join(tokens[2:])
    mgr = _mgr()
    if action == 'add':
        mgr.add_model(pid, model)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Added {pid}/{model} to catalog.',
        )
    if action == 'remove':
        if pid not in mgr.list_custom_providers():
            return CommandResult(
                type=CommandResultType.MESSAGE,
                content=f'No catalog override for {pid}.',
            )
        mgr.remove_model(pid, model)
        return CommandResult(
            type=CommandResultType.MESSAGE,
            content=f'Removed {pid}/{model} from catalog.',
        )
    return CommandResult(type=CommandResultType.MESSAGE, content=_MODEL_USAGE)


async def cmd_config(ctx: CommandContext) -> CommandResult:
    if not ctx.runtime or not ctx.runtime.llm:
        return CommandResult(
            type=CommandResultType.MESSAGE, content='No active agent.')

    config = ctx.runtime.llm.config
    from omegaconf import OmegaConf

    # mask sensitive info
    safe = OmegaConf.to_container(config, resolve=True)
    for key in list(safe.get('llm', {}).keys()):
        if 'key' in key.lower():
            safe['llm'][key] = '***'

    import yaml

    text = yaml.dump(safe, default_flow_style=False, allow_unicode=True)
    if len(text) > 2000:
        text = text[:2000] + '\n... (truncated)'
    return CommandResult(type=CommandResultType.MESSAGE, content=text)


def register_config_commands(router: CommandRouter) -> None:
    router.register(CMD_MODEL, cmd_model)
    router.register(CMD_CONFIG, cmd_config)
