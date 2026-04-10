# Copyright (c) ModelScope Contributors. All rights reserved.
import pytest

from ms_agent.trajectory.models import EventKind, Trajectory, TrajectoryEvent


def test_trajectory_event_round_trip():
    ev = TrajectoryEvent(
        id='abc',
        ts='2020-01-01T00:00:00+00:00',
        kind=EventKind.TOOL_CALL.value,
        agent_tag=None,
        call_id='c1',
        tool_name='t',
        data={'x': 1},
    )
    d = ev.to_dict()
    ev2 = TrajectoryEvent.from_dict(d)
    assert ev == ev2


def test_trajectory_from_dict_round_trip():
    tr = Trajectory(
        run_id='r',
        started_at='s',
        agent_tag='a',
        events=[
            TrajectoryEvent(
                id='1',
                ts='t',
                kind=EventKind.AGENT_START.value,
                agent_tag=None,
                call_id=None,
                tool_name='x',
                data={},
            )
        ],
    )
    tr2 = Trajectory.from_dict(tr.to_dict())
    assert tr.run_id == tr2.run_id
    assert len(tr2.events) == 1
    assert tr2.events[0].kind == EventKind.AGENT_START.value
