# Copyright (c) ModelScope Contributors. All rights reserved.
import io
import json
import os
import sys

from ms_agent.trajectory import cc_hook


def test_cc_hook_post_tool_use(tmp_path, monkeypatch):
    monkeypatch.setenv('MS_AGENT_TRAJECTORY_DIR', str(tmp_path))
    payload = {
        'session_id': 'sess-1',
        'hook_event_name': 'PostToolUse',
        'tool_name': 'Write',
        'tool_response': {'success': True},
    }
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    cc_hook.main()
    from ms_agent.trajectory.store import TrajectoryStore

    st = TrajectoryStore(str(tmp_path), 'sess-1')
    loaded = st.load()
    assert len(loaded.events) >= 1
    assert loaded.events[-1].kind == 'tool_result'
