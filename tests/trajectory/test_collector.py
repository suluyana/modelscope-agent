# Copyright (c) ModelScope Contributors. All rights reserved.
import pytest

from ms_agent.trajectory.collector import TrajectoryCollector
from ms_agent.trajectory.models import EventKind


def test_collector_event_kinds(tmp_path):
    c = TrajectoryCollector('r', str(tmp_path), agent_tag='g')
    c.emit_llm_turn([{'role': 'user', 'content': 'hi'}])
    c.emit_tool_call('t', {}, call_id='1')
    c.emit_tool_result('t', 'r', call_id='1')
    c.emit_file_op(EventKind.FILE_READ, '/a')
    c.emit_file_op(EventKind.FILE_WRITE, '/b')
    c.emit_agent_start(None, 'sub', '1')
    c.emit_agent_end(None, 'sub', 'completed', '1')
    c.emit_task_state('tid', 'ag', 'completed')
    c.close()
    kinds = [e.kind for e in c.trajectory.events]
    assert kinds == [
        EventKind.LLM_TURN.value,
        EventKind.TOOL_CALL.value,
        EventKind.TOOL_RESULT.value,
        EventKind.FILE_READ.value,
        EventKind.FILE_WRITE.value,
        EventKind.AGENT_START.value,
        EventKind.AGENT_END.value,
        EventKind.TASK_STATE.value,
    ]


def test_emit_file_op_invalid_kind(tmp_path):
    c = TrajectoryCollector('r', str(tmp_path))
    with pytest.raises(ValueError):
        c.emit_file_op(EventKind.TOOL_CALL, '/x')
    c.close()
