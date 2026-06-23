from pathlib import Path

import pytest

from ms_agent.command.router import CommandRouter
from ms_agent.command.types import CommandContext, CommandResultType
from ms_agent.plugins.commands import register_plugin_commands
from ms_agent.plugins.types import CommandDef


def _make_ctx(text: str) -> CommandContext:
    cmd, args = CommandRouter.parse_input(text)
    return CommandContext(raw_input=text, command_name=cmd, args=args)


@pytest.mark.asyncio
async def test_plugin_command_registers_namespaced_command(tmp_path):
    command_path = tmp_path / 'help.md'
    command_path.write_text(
        '---\nname: help\ndescription: Help command\n---\nRun help for $ARGUMENTS.\n',
        encoding='utf-8',
    )
    router = CommandRouter()
    register_plugin_commands(
        router,
        [CommandDef(plugin_id='demo', name='help', path=str(command_path))],
    )

    assert router.is_command('/demo:help topic')
    result = await router.dispatch(_make_ctx('/demo:help topic'))
    assert result is not None
    assert result.type == CommandResultType.SUBMIT_PROMPT
    assert 'Run help for topic.' in result.content
    assert str(Path(command_path)) in result.content


@pytest.mark.asyncio
async def test_plugin_command_skips_unqualified_conflict(tmp_path):
    command_path = tmp_path / 'help.md'
    command_path.write_text('Body', encoding='utf-8')
    router = CommandRouter()

    async def builtin_handler(ctx):
        from ms_agent.command.types import CommandResult
        return CommandResult(type=CommandResultType.MESSAGE, content='builtin')

    from ms_agent.command.types import CommandDef as RouterCommandDef
    router.register(RouterCommandDef(name='help', description='builtin'), builtin_handler)
    register_plugin_commands(
        router,
        [CommandDef(plugin_id='demo', name='help', path=str(command_path))],
    )

    assert router.resolve('help').description == 'builtin'
    assert router.resolve('demo:help') is not None
