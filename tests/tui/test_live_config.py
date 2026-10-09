# Copyright (c) ModelScope Contributors. All rights reserved.
"""Next-message config sync stays on the same agent."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from omegaconf import OmegaConf

from ms_agent.tui.live_config import apply_live_config


@pytest.mark.asyncio
async def test_model_change_rebuilds_the_client_in_place():
    config = OmegaConf.create({
        'llm': {
            'service': 'openai',
            'model': 'gpt-4o',
        },
        'generation_config': {
            'stream': True,
        },
    })
    live = SimpleNamespace(model='old', config=config, _setup_stub=False)
    agent = SimpleNamespace(
        config=config,
        llm=live,
        runtime=SimpleNamespace(llm=live),
        memory_tools=[],
        _skill_runtime=None,
        mcp_runtime=None,
        _llm_fp='stale',
        _mcp_fp=None,
        _memory_guidance='',
    )

    class Rebuilt:
        def __init__(self):
            self.model = 'gpt-4o'
            self.config = config

    with patch('ms_agent.llm.LLM.from_config', return_value=Rebuilt()):
        await apply_live_config(agent, None)
    assert agent.llm.model == 'gpt-4o'
    assert agent.runtime.llm.model == 'gpt-4o'
    assert agent._llm_fp != 'stale'


@pytest.mark.asyncio
async def test_unchanged_token_does_not_rebuild():
    from ms_agent.llm.runtime_token import llm_runtime_token
    config = OmegaConf.create({
        'llm': {
            'service': 'openai',
            'model': 'gpt-4o',
        },
    })
    live = SimpleNamespace(model='gpt-4o', config=config, _setup_stub=False)
    agent = SimpleNamespace(
        config=config,
        llm=live,
        runtime=SimpleNamespace(llm=live),
        memory_tools=[],
        _skill_runtime=None,
        mcp_runtime=None,
        _llm_fp=llm_runtime_token(config),
        _mcp_fp=None,
    )
    with patch('ms_agent.llm.LLM.from_config') as rebuild:
        await apply_live_config(agent, None)
    rebuild.assert_not_called()
