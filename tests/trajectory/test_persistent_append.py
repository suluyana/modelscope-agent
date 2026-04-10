# Copyright (c) ModelScope Contributors. All rights reserved.
from ms_agent.trajectory.models import EventKind
from ms_agent.trajectory.persistent_append import append_trajectory_event
from ms_agent.trajectory.store import TrajectoryStore


def test_persistent_append_writes_header_once(tmp_path):
    d = str(tmp_path)
    append_trajectory_event(
        d,
        'run-x',
        kind=EventKind.TOOL_CALL.value,
        tool_name='Bash',
        data={'x': 1},
    )
    append_trajectory_event(
        d,
        'run-x',
        kind=EventKind.TOOL_RESULT.value,
        tool_name='Bash',
        data={'y': 2},
    )
    st = TrajectoryStore(d, 'run-x')
    loaded = st.load()
    assert len(loaded.events) == 2
    assert loaded.events[0].kind == EventKind.TOOL_CALL.value
