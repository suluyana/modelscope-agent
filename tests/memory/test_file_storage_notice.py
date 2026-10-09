"""Memory tool results the user can act on."""
import pytest

from ms_agent.memory.unified.backends.file_based import FileBasedBackend
from ms_agent.memory.unified.config import MemoryConfig
from ms_agent.memory.unified.user_notice import CHAR_LIMIT_NOTICE


@pytest.mark.asyncio
async def test_char_limit_is_the_tool_result_and_does_not_stick(tmp_path):
    backend = FileBasedBackend(
        MemoryConfig(base_dir=str(tmp_path), char_limit=20))
    over = await backend.handle_tool_call('memory', {
        'action': 'add',
        'content': 'x' * 40,
    })
    assert over == CHAR_LIMIT_NOTICE
    assert not (tmp_path / 'MEMORY.md').exists()

    missed = await backend.handle_tool_call('memory', {
        'action': 'replace',
        'content': 'missing',
        'new_content': 'y',
    })
    assert missed == '更新失败'
    assert CHAR_LIMIT_NOTICE not in missed
