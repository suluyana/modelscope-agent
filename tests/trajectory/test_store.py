# Copyright (c) ModelScope Contributors. All rights reserved.
from ms_agent.trajectory.collector import TrajectoryCollector
from ms_agent.trajectory.models import EventKind
from ms_agent.trajectory.store import TrajectoryStore


def test_store_append_load_round_trip(tmp_path):
    coll = TrajectoryCollector('run-a', str(tmp_path), agent_tag='ag')
    coll.emit_tool_call('bash', {'c': 'd'}, call_id='x')
    coll.emit_tool_result('bash', 'out', call_id='x')
    coll.close()

    st = TrajectoryStore(str(tmp_path), 'run-a')
    loaded = st.load()
    assert loaded.run_id == 'run-a'
    assert len(loaded.events) == 2
    assert loaded.events[0].kind == EventKind.TOOL_CALL.value
    assert loaded.events[1].kind == EventKind.TOOL_RESULT.value


def test_store_load_without_close(tmp_path):
    coll = TrajectoryCollector('run-b', str(tmp_path))
    coll.emit_task_state('t1', 'shell', 'running')
    # no close — footer missing
    st = TrajectoryStore(str(tmp_path), 'run-b')
    loaded = st.load()
    assert len(loaded.events) == 1
