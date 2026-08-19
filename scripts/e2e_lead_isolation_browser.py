#!/usr/bin/env python3
"""Browser E2E: Lead must not inherit teammate prompts or tokens (C-06).

Flow on main chat (new session):
  1. @bibo → unique token B
  2. @lily → unique token L
  3. Assert no Lead bubble while only teammates ran
  4. 你好 → Lead replies
  5. Lead bubble / Lead session / view=llm must not contain B or L
     or the teammate assignment text
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5173'
API = 'http://127.0.0.1:8000'
OUT = Path('/tmp/team-lead-isolation-e2e')
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)


def dump_fail(page, name: str) -> None:
    shot(page, name)
    (OUT / f'{name}.html').write_text(page.content(), encoding='utf-8')
    (OUT / f'{name}.url').write_text(page.url, encoding='utf-8')


def api_json(path: str):
    with urllib.request.urlopen(API + path, timeout=20) as r:
        body = json.loads(r.read())
    return body.get('data') if isinstance(body, dict) and 'data' in body else body


def type_home(page, text: str) -> None:
    box = page.locator("[contenteditable='true']").first
    box.wait_for(timeout=20_000)
    box.click()
    page.keyboard.press('Meta+A')
    page.keyboard.press('Backspace')
    page.keyboard.type(text, delay=4)
    page.keyboard.press('Enter')


def session_id_from_url(url: str) -> str | None:
    m = re.search(r'/sessions/([0-9a-f]+)', url)
    return m.group(1) if m else None


def wait_session(page, timeout=30_000) -> str:
    deadline = time.time() + timeout / 1000
    sid = None
    while time.time() < deadline:
        sid = session_id_from_url(page.url)
        if sid:
            return sid
        page.wait_for_timeout(200)
    raise AssertionError(f'no session id in url {page.url}')


def jsonl_rows(session_id: str) -> list[dict]:
    path = (
        Path.home()
        / '.ms_agent/projects/_default/sessions'
        / session_id
        / f'session_{session_id}.jsonl'
    )
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def run(page, token_b: str, token_l: str) -> dict:
    page.goto(f'{BASE}/', wait_until='domcontentloaded')
    page.wait_for_timeout(800)
    shot(page, '01-home')

    type_home(
        page,
        f'@bibo Reply with exactly the single word {token_b} and nothing else.',
    )
    sid = wait_session(page)
    page.locator('[data-testid="team-reply-bibo"]').first.wait_for(
        state='visible', timeout=120_000)
    shot(page, '02-bibo')

    type_home(
        page,
        f'@lily Reply with exactly the single word {token_l} and nothing else.',
    )
    page.locator('[data-testid="team-reply-lily"]').first.wait_for(
        state='visible', timeout=120_000)
    page.locator('[data-testid="team-receipt"]').first.wait_for(
        state='visible', timeout=30_000)
    shot(page, '03-lily')

    lead_before = page.locator('[data-testid="team-reply-lead"]').count()
    if lead_before != 0:
        raise AssertionError(
            f'Lead must not run on teammate-only turns, got {lead_before} Lead bubbles')

    type_home(page, '你好')
    page.locator('[data-testid="team-reply-lead"]').first.wait_for(
        state='visible', timeout=90_000)
    # Let the Lead turn finish streaming.
    page.wait_for_timeout(8_000)
    shot(page, '04-lead-hello')

    lead = page.locator('[data-testid="team-reply-lead"]').first
    lead_text = lead.inner_text()
    forbidden = (token_b, token_l, 'Reply with exactly', '烤鸭', '颐和园', '故宫')
    for needle in forbidden:
        if needle in lead_text:
            raise AssertionError(
                f'Lead bubble contains {needle!r}: {lead_text[:400]}')

    lead.locator('[data-testid="team-full-process"]').click()
    page.locator('[data-testid="team-lead-session"]').wait_for(
        state='visible', timeout=20_000)
    page.wait_for_timeout(1_500)
    shot(page, '05-lead-session')
    rail = page.locator('[data-testid="team-lead-session"]').inner_text()
    for needle in (token_b, token_l, 'Reply with exactly'):
        if needle in rail:
            raise AssertionError(
                f'Lead session rail leaked {needle!r}: {rail[:600]}')

    human = api_json(f'/api/sessions/{sid}/messages') or []
    llm = api_json(f'/api/sessions/{sid}/messages?view=llm') or []
    human_blob = json.dumps(human, ensure_ascii=False)
    llm_blob = json.dumps(llm, ensure_ascii=False)
    if '@bibo' not in human_blob:
        raise AssertionError('human view missing @bibo user turn')
    if token_b not in human_blob:
        raise AssertionError('human view missing bibo token (worker preview)')
    for needle in (token_b, token_l, 'Reply with exactly'):
        if needle in llm_blob:
            raise AssertionError(f'view=llm leaked {needle!r}')
    if '[task_board]' not in llm_blob:
        raise AssertionError('view=llm missing task_board pointer for Lead')
    if 'task_board_read' not in llm_blob:
        raise AssertionError('view=llm missing task_board_read hint')
    if 'completed @bibo' in llm_blob:
        raise AssertionError('view=llm inlined board rows; should be a pointer only')

    rows = jsonl_rows(sid)
    types = [r.get('_type') or r.get('role') for r in rows]
    team_users = [r for r in rows if r.get('_type') == 'team_user']
    llm_users = [
        r for r in rows
        if r.get('role') == 'user' and r.get('_type') not in (
            'team_user', 'team_receipt', 'team_reply')
    ]
    # Tagged team_user OR legacy pairing: teammate prompts must not be
    # untagged user rows sitting in get_all_messages.
    untagged_at = [
        r for r in llm_users
        if str(r.get('content') or '').lstrip().startswith('@bibo')
        or str(r.get('content') or '').lstrip().startswith('@lily')
    ]
    if untagged_at and not team_users:
        # New code should have written team_user. Fail clearly.
        raise AssertionError(
            'teammate @ prompts persisted as untagged user rows (Lead would see them)')

    return {
        'ok': True,
        'session_id': sid,
        'lead_text': lead_text[:500],
        'lead_session_preview': rail[:400],
        'jsonl_types': types,
        'team_user_count': len(team_users),
        'llm_user_contents': [r.get('content') for r in llm_users],
        'llm_msg_count': len(llm),
        'human_msg_count': len(human),
        'url': page.url,
    }


def main() -> int:
    stamp = int(time.time())
    token_b = f'LEAKB_{stamp}'
    token_l = f'LEAKL_{stamp}'
    results: dict = {'token_b': token_b, 'token_l': token_l}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1440, 'height': 960},
            locale='zh-CN',
            extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9'},
            record_video_dir=str(OUT / 'video'),
        )
        page = context.new_page()
        page.set_default_timeout(30_000)
        try:
            results['run'] = run(page, token_b, token_l)
        except Exception as e:
            dump_fail(page, 'FAIL')
            results['run'] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            results['url'] = page.url
        context.close()
        browser.close()
    (OUT / 'results.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if (results.get('run') or {}).get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
