# Copyright (c) ModelScope Contributors. All rights reserved.
"""Team tests opt out of file persist so they never write ~/.ms_agent/team."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _team_persist_off(monkeypatch):
    monkeypatch.setenv('MS_AGENT_TEAM_PERSIST', '0')
    os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
