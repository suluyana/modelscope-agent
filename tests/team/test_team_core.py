# Copyright (c) ModelScope Contributors. All rights reserved.
"""Unit tests for Agent Team domain layer."""
from __future__ import annotations

import pytest

from ms_agent.team.context import ContextBundleAssembler
from ms_agent.team.errors import AGENT_OWNER_ONLY, TeamError
from ms_agent.team.ingress import EndpointRegistryService, MessageIngress
from ms_agent.team.models import (
    AgentEndpoint,
    InboundMessage,
    InvokePolicy,
    TeamFeatureFlags,
    TeamProjectMeta,
    TimelineMessage,
    new_id,
)
from ms_agent.team.policies import InvokeGate, RemoteProfileEnforcer
from ms_agent.team.project_resolve import ProjectResolver
from ms_agent.team.router import AtMentionParser, AtRouter
from ms_agent.team.stores.memory import (
    MemoryEndpointStore,
    MemoryProjectMetaStore,
    MemoryTimelineStore,
)


def test_at_mention_parser():
    names = AtMentionParser.parse('@我-gpu 完成发布 @me-eas-amd 冒烟')
    assert '我-gpu' in names
    assert 'me-eas-amd' in names
    stripped = AtMentionParser.strip_mentions('@coder hello world')
    assert 'hello world' in stripped


def test_clause_for_splits_dual_mentions():
    content = '@codex 写A @lily 写B'
    assert AtMentionParser.clause_for(content, 'codex') == '写A'
    assert AtMentionParser.clause_for(content, 'lily') == '写B'
    assert '写B' not in AtMentionParser.clause_for(content, 'codex')
    shared = AtMentionParser.clause_for('@codex @lily do both', 'codex')
    assert 'do both' in shared
    assert AtMentionParser.clause_for('no mentions here', 'codex') == 'no mentions here'


def test_invoke_gate_owner_only_phase1():
    ep = AgentEndpoint(
        endpoint_id='ep1',
        at_name='zhangsan',
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='claude_code',
        adapter_kind='acp',
        invoke_policy=InvokePolicy.GROUP_MEMBERS,  # even if loosened...
    )
    flags = TeamFeatureFlags(remote_invoke_enabled=False)
    assert InvokeGate.check(ep, 'u1', flags) is True
    with pytest.raises(TeamError) as ei:
        InvokeGate.check(ep, 'u2', flags)
    assert ei.value.code == AGENT_OWNER_ONLY
    assert ei.value.http_status == 403


def test_invoke_gate_reserved_allowlist_when_enabled():
    ep = AgentEndpoint(
        endpoint_id='ep1',
        at_name='zhangsan',
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='claude_code',
        adapter_kind='acp',
        invoke_policy=InvokePolicy.ALLOWLIST,
        invoke_allowlist=['u2'],
    )
    flags = TeamFeatureFlags(remote_invoke_enabled=True)
    assert InvokeGate.check(ep, 'u2', flags) is False  # not owner
    with pytest.raises(TeamError):
        InvokeGate.check(ep, 'u3', flags)


def test_remote_profile_tier():
    ep = AgentEndpoint(
        endpoint_id='ep1',
        at_name='zhangsan',
        owner_user_id='u1',
        endpoint_type='persistent',
        runtime='claude_code',
        adapter_kind='acp',
    )
    assert RemoteProfileEnforcer.permission_tier(ep, True) == 'owner'
    assert RemoteProfileEnforcer.permission_tier(ep, False) == 'restricted'


def test_project_resolver_write_requires_card():
    projects = [
        TeamProjectMeta(project_id='p1', name='支付服务'),
        TeamProjectMeta(project_id='p2', name='支付后台'),
    ]
    resolver = ProjectResolver(projects)
    msg = InboundMessage(
        message_id='m1',
        sender_user_id='u1',
        content='@张三 修登录',
        channel='web',
        operation_kind='write',
    )
    result = resolver.resolve(msg)
    assert result.needs_card is True


def test_project_resolver_explicit_id():
    projects = [TeamProjectMeta(project_id='p1', name='支付服务')]
    resolver = ProjectResolver(projects)
    msg = InboundMessage(
        message_id='m1',
        sender_user_id='u1',
        content='hello',
        channel='web',
        project_id='p1',
        operation_kind='write',
    )
    assert resolver.resolve(msg).project_id == 'p1'


def test_context_bundle_caps():
    msgs = [
        TimelineMessage(
            message_id=new_id(),
            project_id='p1',
            sender_type='human',
            sender_id='u',
            sender_name='u',
            content=f'msg-{i}',
        ) for i in range(30)
    ]
    worker = ContextBundleAssembler.build(project_timeline=msgs)
    assert worker.project_timeline == []
    assert worker.thread_messages == []
    merged_worker = ContextBundleAssembler.merge_prompt('do it', worker)
    assert 'do it' in merged_worker
    assert 'one-shot' in merged_worker

    legacy = ContextBundleAssembler.build(
        project_timeline=msgs, include_project_timeline=True)
    assert len(legacy.project_timeline) == 10
    merged = ContextBundleAssembler.merge_prompt('do it', legacy)
    assert 'Context' in merged
    assert 'do it' in merged


def test_lead_bundle_has_snapshots_not_other_agent_text():
    from ms_agent.team.models import TeamTask
    tasks = [
        TeamTask(
            task_id='task_1',
            project_id='p1',
            status='completed',
            prompt='找北京美景',
            trigger_user_id='u1',
            target_at_name='codex',
            result_summary='故宫、长城',
        )
    ]
    snaps = ContextBundleAssembler.snapshots_from_tasks(tasks)
    bundle = ContextBundleAssembler.build(
        audience='lead', task_snapshots=snaps)
    merged = ContextBundleAssembler.merge_prompt('做完了吗', bundle)
    assert 'task_board' in merged
    assert 'task_board_read' in merged
    assert 'completed @codex' not in merged
    assert '故宫' not in merged
    assert 'Do not re-run' in merged
    assert '找北京美景' not in merged
    assert 'dispatch_result_read' in merged


def test_lead_empty_board_still_gets_project_pointer():
    bundle = ContextBundleAssembler.build(
        audience='lead', project_id='p_empty')
    merged = ContextBundleAssembler.merge_prompt('请拆到任务板', bundle)
    assert '[task_board] project=p_empty' in merged
    assert 'task_board_read' in merged
    assert 'dispatch_result_read' in merged
    worker = ContextBundleAssembler.build(
        audience='worker', project_id='p_empty')
    merged_w = ContextBundleAssembler.merge_prompt('do it', worker)
    assert 'do it' in merged_w
    assert 'one-shot' in merged_w


def test_truncate_strips_codex_model_metadata_warning():
    from ms_agent.team.context import truncate_summary
    raw = (
        'Warning: Model metadata for `qwen3.7-plus` not found. '
        'Defaulting to fallback metadata; this can degrade performance '
        'and cause issues.\n\n故宫、长城'
    )
    assert truncate_summary(raw) == '故宫、长城'


def test_done_receipt_is_idle_without_transcript():
    from ms_agent.team.context import format_done_receipt

    assert format_done_receipt(at_name='codex', ok=True) == '@codex 已结束执行'
    assert '故宫' not in format_done_receipt(at_name='codex', ok=True)
    assert format_done_receipt(at_name='codex', ok=False) == (
        '@codex 已结束执行（失败）')
    assert format_done_receipt(
        at_name='codex', ok=False, error_code='timeout') == (
            '@codex 已结束执行（失败） · timeout')


def test_lead_snapshot_idle_has_index_not_invented_summary():
    from ms_agent.team.models import TeamTask
    tasks = [
        TeamTask(
            task_id='task_1',
            project_id='p1',
            status='completed',
            prompt='找北京美景',
            trigger_user_id='u1',
            target_at_name='codex',
            last_dispatch_id='d_abc',
            result_summary='故宫、长城、颐和园全文过程',
        )
    ]
    snaps = ContextBundleAssembler.snapshots_from_tasks(tasks)
    merged = ContextBundleAssembler.merge_prompt(
        '做完了吗',
        ContextBundleAssembler.build(audience='lead', task_snapshots=snaps),
    )
    assert 'project=p1' in merged
    assert 'task_board_read' in merged
    assert 'dispatch=d_abc' not in merged
    assert 'completed @codex' not in merged
    assert '找北京美景' not in merged
    assert '故宫' not in merged
    assert '颐和园' not in merged


@pytest.mark.asyncio
async def test_complete_dispatch_does_not_copy_stream_into_result_summary():
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-codex',
            at_name='codex',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)

    async def handler(_env):
        return None

    ingress.set_dispatch_handler(handler)
    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@codex 你找北京美景',
            channel='web',
            project_id='p1',
            operation_kind='write',
        ))
    env = result.dispatches[0]
    receipt = ingress.complete_dispatch(
        env, ok=True, summary='故宫、长城、颐和园')
    assert receipt == '@codex 已结束执行'
    assert '故宫' not in receipt
    board = tasks.list('p1')
    assert board[0].status == 'completed'
    assert board[0].result_summary is None
    assert board[0].last_dispatch_id == env.dispatch_id
    board[0].result_summary = 'Worker 自写：烤鸭'
    tasks.upsert(board[0])
    fail_receipt = ingress.complete_dispatch(
        env, ok=False, summary='Traceback: boom', error_code='timeout')
    assert fail_receipt == '@codex 已结束执行（失败） · timeout'
    assert 'Traceback' not in fail_receipt
    again = tasks.list('p1')[0]
    assert again.status == 'failed'
    assert again.result_summary == 'Worker 自写：烤鸭'


def test_looks_like_clarification_detects_missing_path():
    from ms_agent.team.context import looks_like_clarification

    asked = (
        '`{DOC}` 是一个未替换的占位符。请问你希望我读取哪个文档？'
        '请告诉我具体要读哪一个，或者直接给出绝对路径。'
    )
    assert looks_like_clarification(asked)
    assert looks_like_clarification(
        "The placeholder `{DOC}` wasn't replaced. Could you provide the "
        'absolute path to the document?')
    assert looks_like_clarification(
        'I need the path to the document before I can continue.')
    assert not looks_like_clarification('故宫、长城、颐和园')
    assert not looks_like_clarification('完结回执写「已结束执行」，不写 Worker 正文。')
    # Quoting the architecture doc is not a question.
    assert not looks_like_clarification(
        'Never write unreplaced placeholders such as {DOC}; copy real '
        'absolute paths from the user message.')
    # Brace tokens in the job are not a question.
    assert not looks_like_clarification(
        '把整本书里的 {DOC} 替换成 {DOC1}。')
    assert not looks_like_clarification(
        '用户消息中的 {DOC} 是一个占位符，没有提供实际路径。',
        audience='lead',
    )


@pytest.mark.asyncio
async def test_complete_dispatch_does_not_park_on_question_text():
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-bibo',
            at_name='bibo',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='codex',
            adapter_kind='acp',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)

    async def handler(_env):
        return None

    ingress.set_dispatch_handler(handler)
    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@bibo 阅读 {DOC}',
            channel='web',
            project_id='p1',
            operation_kind='write',
        ))
    env = result.dispatches[0]
    question = (
        '`{DOC}` 是未替换占位符。请问你希望我读取哪个文档？请给出绝对路径。')
    receipt = ingress.complete_dispatch(env, ok=True, summary=question)
    assert '澄清' not in receipt
    board = tasks.list('p1')
    assert board[0].status == 'completed'
    assert not (board[0].deployment_context or {}).get('needs_clarification')


@pytest.mark.asyncio
async def test_complete_dispatch_keeps_wait_human_after_task_ask():
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-bibo',
            at_name='bibo',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='codex',
            adapter_kind='acp',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)

    async def handler(_env):
        return None

    ingress.set_dispatch_handler(handler)
    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@bibo 阅读文档',
            channel='web',
            project_id='p1',
            operation_kind='write',
        ))
    env = result.dispatches[0]
    row = tasks.list('p1')[0]
    ingress.park_question(row, 'I need the path to the document.', audience='user')
    receipt = ingress.complete_dispatch(env, ok=True, summary='ok')
    assert '澄清' in receipt
    again = tasks.get(row.task_id)
    assert again.status == 'wait_human'
    assert (again.deployment_context or {}).get('needs_clarification') is True


@pytest.mark.asyncio
async def test_lead_prose_does_not_create_wait_human_ticket():
    from ms_agent.team.models import ContextBundle
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-lead',
            at_name='planner',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)

    async def handler(_env):
        return None

    ingress.set_dispatch_handler(handler)
    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='请拆任务，文档路径是 {DOC}',
            channel='web',
            project_id='p1',
            operation_kind='write',
        ))
    env = result.dispatches[0]
    env.context_bundle = ContextBundle(audience='lead', project_id='p1')
    question = (
        '用户没有给出要读的文件。请问具体路径是什么？我不能编造。')
    ingress.complete_dispatch(env, ok=True, summary=question)
    board = tasks.list('p1')
    waiting = [t for t in board if t.status == 'wait_human']
    assert not waiting


def test_escalate_to_lead_prompt_has_no_at_sign():
    from ms_agent.team.models import TeamTask
    from ms_agent.team.stores.memory import MemoryTaskBoardStore

    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-lead',
            at_name='planner',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    captured = []

    async def handler(env):
        captured.append(env.prompt)
        return None

    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    ingress.set_dispatch_handler(handler)
    task = tasks.upsert(
        TeamTask(
            task_id='task_ask',
            project_id='p1',
            status='wait_human',
            prompt='read the doc',
            trigger_user_id='u1',
            target_at_name='bibo',
            result_summary='I need the path to the document.',
        ))
    # escalate schedules handle() on the running loop; call handle directly.
    ingress.escalate_to_lead(task)
    assert (tasks.get('task_ask').deployment_context or {}).get(
        'escalated_to_lead') is True
    assert '@' not in (
        'Worker bibo parked task task_ask as wait_human. '
        'Question: I need the path to the document.')


@pytest.mark.asyncio
async def test_task_board_write_allows_brace_tokens_and_task_ask_parks():
    from omegaconf import OmegaConf
    from ms_agent.team.tools.endpoint_tools import TeamTaskBoardTools
    from ms_agent.team.stores.memory import MemoryTaskBoardStore
    import json

    tasks = MemoryTaskBoardStore()
    asked = []

    def on_ask(task):
        asked.append(task.task_id)

    ttools = TeamTaskBoardTools(
        OmegaConf.create({'output_dir': '.'}),
        task_store=tasks,
        on_ask=on_ask,
    )
    await ttools.connect()
    written = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='task_board_write',
            tool_args={
                'project_id': 'p1',
                'status': 'pending',
                'prompt': '把整本书的 {DOC} 替换成 {DOC1}',
                'target_at_name': 'bibo',
            },
        ))
    assert written.get('error') is None
    assert written['status'] == 'pending'
    assert '{DOC1}' in written['prompt']
    parked = json.loads(
        await ttools.call_tool(
            'team_taskboard',
            tool_name='task_ask',
            tool_args={
                'project_id': 'p1',
                'task_id': 'task_1',
                'question': 'I need the path to the document.',
            },
        ))
    assert parked['status'] == 'wait_human'
    assert asked == ['task_1']
    assert (tasks.get('task_1').deployment_context or {}).get(
        'ask_audience') == 'lead'


def test_lead_merge_prompt_empty_user_keeps_board_only():
    from ms_agent.team.models import TeamTask
    tasks = [
        TeamTask(
            task_id='task_1',
            project_id='p1',
            status='completed',
            prompt='找北京美食',
            trigger_user_id='u1',
            target_at_name='bibo',
            result_summary='烤鸭、炸酱面',
        )
    ]
    snaps = ContextBundleAssembler.snapshots_from_tasks(tasks)
    bundle = ContextBundleAssembler.build(
        audience='lead', task_snapshots=snaps)
    merged = ContextBundleAssembler.merge_prompt('', bundle)
    assert '[task_board]' in merged
    assert 'task_board_read' in merged
    assert '@bibo' not in merged
    assert '烤鸭' not in merged
    assert '# User prompt' not in merged


@pytest.mark.asyncio
async def test_mention_does_not_dispatch_default_lead():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    from ms_agent.team.stores.memory import MemoryTaskBoardStore
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    for name, eid in (('planner', 'ep-lead'), ('codex', 'ep-codex')):
        endpoints.upsert(
            AgentEndpoint(
                endpoint_id=eid,
                at_name=name,
                owner_user_id='u1',
                endpoint_type='persistent',
                runtime='ms_agent',
                adapter_kind='cloud',
                status='online',
            ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    seen: list[str] = []

    async def handler(env):
        seen.append(env.target_at_name)

    ingress.set_dispatch_handler(handler)

    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@codex 你找北京美景',
            channel='web',
            project_id='p1',
            operation_kind='write',
        ))
    assert result.error is None
    assert [d.target_at_name for d in result.dispatches] == ['codex']
    assert result.receipts
    assert result.receipts[0]['text'].startswith('已派 @codex')
    import asyncio
    await asyncio.sleep(0.05)
    assert seen == ['codex']
    system = [
        m for m in timeline.list('p1') if m.sender_type == 'system'
    ]
    assert any('已派 @codex' in m.content for m in system)
    board = tasks.list('p1')
    assert len(board) == 1
    assert board[0].target_at_name == 'codex'
    assert board[0].status == 'in_progress'


@pytest.mark.asyncio
async def test_lead_first_turn_empty_board_has_project_pointer():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    from ms_agent.team.stores.memory import MemoryTaskBoardStore
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-lead',
            at_name='planner',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    captured = []

    async def handler(env):
        captured.append(env)

    ingress.set_dispatch_handler(handler)
    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='请把这件大任务拆到任务板',
            channel='web',
            project_id='p1',
        ))
    assert result.error is None
    assert [d.target_at_name for d in result.dispatches] == ['planner']
    import asyncio
    await asyncio.sleep(0.05)
    assert captured
    env = captured[0]
    assert env.context_bundle.audience == 'lead'
    assert env.context_bundle.project_id == 'p1'
    merged = ContextBundleAssembler.merge_prompt(env.prompt, env.context_bundle)
    assert '[task_board] project=p1' in merged
    assert tasks.list('p1') == []


@pytest.mark.asyncio
async def test_lead_followup_sees_task_snapshot_not_worker_transcript():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    from ms_agent.team.stores.memory import MemoryTaskBoardStore
    tasks = MemoryTaskBoardStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-lead',
            at_name='planner',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    ingress = MessageIngress(
        endpoints, projects, timeline, task_store=tasks)
    captured = []

    async def handler(env):
        captured.append(env)

    ingress.set_dispatch_handler(handler)
    from ms_agent.team.models import TeamTask
    tasks.upsert(
        TeamTask(
            task_id='task_done',
            project_id='p1',
            status='completed',
            prompt='找北京美景',
            trigger_user_id='u1',
            target_at_name='codex',
            result_summary='故宫、长城',
        ))
    timeline.append(
        TimelineMessage(
            message_id='m-agent',
            project_id='p1',
            sender_type='agent',
            sender_id='ep-codex',
            sender_name='codex',
            content='关于 @codex，我目前无法调用其他 agent，让我整理美食',
        ))

    result = await ingress.handle(
        InboundMessage(
            message_id='m2',
            sender_user_id='u1',
            content='做完了吗',
            channel='web',
            project_id='p1',
            thread_id='web-main',
        ))
    assert result.error is None
    assert [d.target_at_name for d in result.dispatches] == ['planner']
    import asyncio
    await asyncio.sleep(0.05)
    assert captured
    bundle = captured[0].context_bundle
    merged = ContextBundleAssembler.merge_prompt(captured[0].prompt, bundle)
    assert '找北京美景' not in merged
    assert 'completed @codex' not in merged
    assert 'task_board_read' in merged
    assert 'dispatch_result_read' in merged
    assert '故宫' not in merged
    assert '无法调用其他 agent' not in merged
    assert '无法调用其他 agent' not in ''.join(bundle.project_timeline)


def test_at_router_visibility():
    eps = [
        AgentEndpoint(
            endpoint_id='ep1',
            at_name='zhangsan',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            status='online',
        )
    ]
    vis = AtRouter.visibility_for_viewer(eps, 'u2')
    assert vis[0]['usable_by_viewer'] is False
    assert vis[0]['visibility_hint'] == '仅本人可用'
    vis2 = AtRouter.visibility_for_viewer(eps, 'u1')
    assert vis2[0]['usable_by_viewer'] is True


@pytest.mark.asyncio
async def test_ingress_owner_dispatch_cloud():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    projects.upsert(
        TeamProjectMeta(
            project_id='p1', name='demo', default_lead_at='planner'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep-cloud',
            at_name='planner',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='ms_agent',
            adapter_kind='cloud',
            status='online',
        ))
    ingress = MessageIngress(endpoints, projects, timeline)
    seen = []

    async def handler(env):
        seen.append(env.dispatch_id)

    ingress.set_dispatch_handler(handler, adapter_kind='cloud')
    ingress.set_dispatch_handler(handler)

    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u1',
            content='@planner 总结一下进度',
            channel='web',
            project_id='p1',
            operation_kind='read',
        ))
    assert result.error is None
    assert len(result.dispatches) == 1
    # allow queue drain
    import asyncio
    await asyncio.sleep(0.05)
    assert seen


@pytest.mark.asyncio
async def test_ingress_rejects_non_owner():
    endpoints = MemoryEndpointStore()
    projects = MemoryProjectMetaStore()
    timeline = MemoryTimelineStore()
    projects.upsert(TeamProjectMeta(project_id='p1', name='demo'))
    endpoints.upsert(
        AgentEndpoint(
            endpoint_id='ep1',
            at_name='zhangsan',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
            status='online',
        ))
    ingress = MessageIngress(endpoints, projects, timeline)
    ingress.set_dispatch_handler(lambda e: None)

    result = await ingress.handle(
        InboundMessage(
            message_id='m1',
            sender_user_id='u2',
            content='@zhangsan 帮我看下',
            channel='web',
            project_id='p1',
            operation_kind='read',
        ))
    assert result.error is not None
    assert result.error['error'] == AGENT_OWNER_ONLY


def test_token_expiry():
    from datetime import datetime, timedelta, timezone
    from ms_agent.team.models import EndpointToken, PairToken
    from ms_agent.team.stores.memory import (
        MemoryEndpointTokenStore,
        MemoryPairTokenStore,
    )
    from ms_agent.team.token_utils import is_expired

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert is_expired(past) is True
    assert is_expired(future) is False

    pairs = MemoryPairTokenStore()
    pairs.put(
        PairToken(
            pair_code='pair_x',
            owner_user_id='u1',
            expires_at=past,
        ))
    assert pairs.consume('pair_x') is None

    etoks = MemoryEndpointTokenStore()
    etoks.put(
        EndpointToken(
            token='etok_x',
            endpoint_id='ep1',
            owner_user_id='u1',
            expires_at=past,
        ))
    assert etoks.get('etok_x') is None


def test_registry_at_name_conflict():
    store = MemoryEndpointStore()
    reg = EndpointRegistryService(store)
    reg.register(
        AgentEndpoint(
            endpoint_id='ep1',
            at_name='me-gpu',
            owner_user_id='u1',
            endpoint_type='persistent',
            runtime='claude_code',
            adapter_kind='acp',
        ))
    with pytest.raises(TeamError):
        reg.register(
            AgentEndpoint(
                endpoint_id='ep2',
                at_name='me-gpu',
                owner_user_id='u1',
                endpoint_type='persistent',
                runtime='claude_code',
                adapter_kind='acp',
            ))


def test_final_text_prefers_stream_over_truncated_summary():
    from ms_agent.team.context import final_text_from_dispatch_events
    from ms_agent.team.events import TeamEvent

    events = [
        TeamEvent(
            type='team.stream',
            dispatch_id='d1',
            payload={'type': 'text', 'content': 'FULL-ORIGINAL-BODY'},
        ),
        TeamEvent(
            type='team.dispatch_done',
            dispatch_id='d1',
            payload={'ok': True, 'summary': 'TRUNCATED-TAIL'},
        ),
    ]
    assert final_text_from_dispatch_events(events) == 'FULL-ORIGINAL-BODY'


def test_task_result_view_uses_worker_summary_then_log():
    from ms_agent.team.context import task_result_view
    from ms_agent.team.events import TeamEvent
    from ms_agent.team.models import TeamTask

    task = TeamTask(
        task_id='t1',
        project_id='p1',
        status='completed',
        prompt='extract',
        trigger_user_id='u1',
        last_dispatch_id='d1',
        result_summary='worker-wrote-this',
    )
    events = [
        TeamEvent(
            type='team.stream',
            dispatch_id='d1',
            payload={'type': 'text', 'content': 'stream-body'},
        ),
    ]
    view = task_result_view(task, events)
    assert view['result_source'] == 'result_summary'
    assert view['result_text'] == 'worker-wrote-this'

    task.result_summary = None
    view = task_result_view(task, events)
    assert view['result_source'] == 'dispatch_log'
    assert view['result_text'] == 'stream-body'

    long_body = 'PLAN-START' + ('x' * 2000) + 'MEMO-END'
    events = [
        TeamEvent(
            type='team.stream',
            dispatch_id='d1',
            payload={'type': 'text', 'content': long_body},
        ),
    ]
    view = task_result_view(task, events, preview_limit=1200)
    assert view['result_truncated'] is True
    assert view['result_text'].startswith('…')
    assert view['result_text'].endswith('MEMO-END')
    assert 'PLAN-START' not in view['result_text']
