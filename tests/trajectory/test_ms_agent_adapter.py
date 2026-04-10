# Copyright (c) ModelScope Contributors. All rights reserved.
import asyncio

from omegaconf import OmegaConf

from ms_agent.tools.agent_tool import AgentTool
from ms_agent.trajectory import MsAgentAdapter, TrajectoryCollector


def test_adapter_task_manager_emits_task_state(tmp_path):
    from ms_agent.utils.task_manager import TaskManager

    async def _run() -> None:
        coll = TrajectoryCollector('r', str(tmp_path))
        tm = TaskManager()
        ad = MsAgentAdapter([], tm)
        ad.attach(coll)
        tid = tm.register('agent', 'mytool', 'desc')
        await tm.complete(tid, 'done')
        ad.detach()
        coll.close()
        st = coll.store
        loaded = st.load()
        assert any(e.kind == 'task_state' for e in loaded.events)

    asyncio.run(_run())


def test_agent_tool_forwards_start_end_to_collector(tmp_path):
    cfg = OmegaConf.create(
        {'output_dir': str(tmp_path), 'tag': 'parent', 'tools': {}})
    tool = AgentTool(cfg)
    coll = TrajectoryCollector('r', str(tmp_path))
    tool.set_trajectory_collector(coll)
    tool._emit_chunk_event('start', {'tool_name': 'sub', 'call_id': 'c1'})
    tool._emit_chunk_event('end', {'tool_name': 'sub', 'call_id': 'c1'})
    coll.close()
    kinds = [e.kind for e in coll.trajectory.events]
    from ms_agent.trajectory.models import EventKind

    assert EventKind.AGENT_START.value in kinds
    assert EventKind.AGENT_END.value in kinds
