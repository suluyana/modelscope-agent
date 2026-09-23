# Copyright (c) ModelScope Contributors. All rights reserved.
"""LLMAgent.set_permission_mode: live mode switch used by the TUI /permission."""
import pytest

from ms_agent.agent.llm_agent import LLMAgent
from ms_agent.permission.config import PermissionConfig


def _agent_with_enforcer():
    agent = LLMAgent.__new__(LLMAgent)
    cfg = PermissionConfig()  # default mode='auto'

    class _Enf:
        pass

    class _TM:
        pass

    enf = _Enf()
    enf._config = cfg
    tm = _TM()
    tm._permission_mode = 'auto'
    tm._permission_config = cfg
    tm._permission_enforcer = enf
    agent.tool_manager = tm
    return agent, tm, enf


def test_switch_updates_toolmanager_and_enforcer():
    agent, tm, enf = _agent_with_enforcer()
    assert agent.set_permission_mode('strict') == 'strict'
    assert tm._permission_mode == 'strict'
    assert tm._permission_config.mode == 'strict'
    assert enf._config.mode == 'strict'


def test_restricted_normalizes_to_interactive():
    agent, tm, enf = _agent_with_enforcer()
    assert agent.set_permission_mode('restricted') == 'interactive'
    assert enf._config.mode == 'interactive'


def test_invalid_mode_raises():
    agent, _, _ = _agent_with_enforcer()
    with pytest.raises(ValueError):
        agent.set_permission_mode('bogus')


def test_accept_edits_and_plan_are_valid():
    agent, tm, enf = _agent_with_enforcer()
    assert agent.set_permission_mode('acceptEdits') == 'accept_edits'
    assert enf._config.mode == 'accept_edits'
    assert agent.set_permission_mode('plan') == 'plan'
    assert enf._config.mode == 'plan'
