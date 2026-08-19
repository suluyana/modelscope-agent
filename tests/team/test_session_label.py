# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

from ms_agent.bridge.discovery import _select_session_candidates
from ms_agent.bridge.session_label import (
    extract_preview,
    is_smoke_session,
    suggest_at_name,
)


def test_extract_preview_from_context_dump():
    title = (
        '# Context (assembled by platform)\n\n'
        '[thread_messages]\n'
        '[2026-08-10T03:45:44.229916+00:00] u1: @lily 改名叫tina\n'
        '[2026-08-10T03:45:50.000000+00:00] lily: 好的\n'
    )
    assert extract_preview(title) == '@lily 改名叫tina'
    assert suggest_at_name(title, '019fe984-9332-7193-985f-e625bf6e43fb') == 'lily'


def test_smoke_filter_and_name_dedupe():
    sessions = [
        {
            'runtime_session_id': 'a1',
            'title': 'Reply with exactly PONG',
            'suggested_at_name': 'Reply_with_exact',
            'updated_at': 100.0,
        },
        {
            'runtime_session_id': 'a2',
            'title': '现在开始，你叫lily',
            'preview': '现在开始，你叫lily',
            'suggested_at_name': 'lily',
            'cwd': '/tmp/modelscope-agent',
            'updated_at': 50.0,
        },
        {
            'runtime_session_id': 'a3',
            'title': '现在开始你叫lily',
            'preview': '现在开始你叫lily',
            'suggested_at_name': 'lily',
            'updated_at': 80.0,
        },
        {
            'runtime_session_id': 'a4',
            'title': '现在开始，你叫alex',
            'preview': '现在开始，你叫alex',
            'suggested_at_name': 'alex',
            'updated_at': 70.0,
        },
    ]
    assert is_smoke_session(sessions[0]['title'])
    picked = _select_session_candidates(sessions, runtime='codex', limit=6)
    ids = [s['runtime_session_id'] for s in picked]
    assert 'a1' not in ids
    assert ids.count('a2') + ids.count('a3') == 1  # one lily
    assert 'a4' in ids
    # newer lily wins
    assert 'a3' in ids
