#!/usr/bin/env python3
"""Browser E2E: Lead retrieves teammate results by dispatch id, not by file.

Regression this pins: the Lead had no ``team_taskboard`` tools in its live tool
index (they were registered before the agent built its ToolManager), so asked to
summarize it shell-ed out for ``dispatch_result_*.txt`` and reported "no
results" — teammates that write no file were unreachable.

Flow on main chat (new session):
  1. @bibo → unique token B (reply only, no file written)
  2. @lily → unique token L
  3. Ask Lead for both results
  4. Assert Lead called team_taskboard---dispatch_result_read, never a
     shell/glob workspace hunt, and its answer carries both tokens
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
OUT = Path('/tmp/team-lead-lookup-e2e')
OUT.mkdir(parents=True, exist_ok=True)

HUNT_TOOLS = ('shell_executor', 'file_system---glob', 'file_system---grep',
              'file_system---read_file')


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)


def dump_fail(page, name: str) -> None:
    shot(page, name)
    (OUT / f'{name}.html').write_text(page.content(), encoding='utf-8')
    (OUT / f'{name}.url').write_text(page.url, encoding='utf-8')


def api_json(path: str):
    with urllib.request.urlopen(API + path, timeout=20) as r:
        body = json.loads(r.read())
    return body.get('data') if isinstance(body,
                                         dict) and 'data' in body else body


def type_home(page, text: str) -> None:
    box = page.locator("[contenteditable='true']").first
    box.wait_for(timeout=20_000)
    box.click()
    page.keyboard.press('Meta+A')
    page.keyboard.press('Backspace')
    page.keyboard.type(text, delay=4)
    page.keyboard.press('Enter')


def wait_session(page, timeout=30_000) -> str:
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        m = re.search(r'/sessions/([0-9a-f]+)', page.url)
        if m:
            return m.group(1)
        page.wait_for_timeout(200)
    raise AssertionError(f'no session id in url {page.url}')


def jsonl_rows(session_id: str) -> list[dict]:
    path = (Path.home() / '.ms_agent/projects/_default/sessions' / session_id
            / f'session_{session_id}.jsonl')
    rows: list[dict] = []
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


def tool_calls(rows: list[dict]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for call in row.get('tool_calls') or []:
            name = call.get('tool_name') or (call.get('function')
                                             or {}).get('name')
            if name:
                names.append(str(name))
    return names


def wait_lead_turn_end(session_id: str, after_seq: int,
                       timeout=180) -> list[dict]:
    """Block until a loop_end past ``after_seq`` lands in the session log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = jsonl_rows(session_id)
        if any(r.get('_type') == 'loop_end'
               and int(r.get('seq', 0)) > after_seq for r in rows):
            return rows
        time.sleep(1)
    raise AssertionError(f'lead turn did not finish within {timeout}s')


def run(page, token_b: str, token_l: str) -> dict:
    page.goto(f'{BASE}/', wait_until='domcontentloaded')
    page.wait_for_timeout(800)

    type_home(
        page,
        f'@bibo Reply with exactly the single word {token_b} and nothing else. '
        'Do not create any file.',
    )
    sid = wait_session(page)
    page.locator('[data-testid="team-reply-bibo"]').first.wait_for(
        state='visible', timeout=120_000)
    shot(page, '01-bibo')

    type_home(
        page,
        f'@lily Reply with exactly the single word {token_l} and nothing else. '
        'Do not create any file.',
    )
    page.locator('[data-testid="team-reply-lily"]').first.wait_for(
        state='visible', timeout=120_000)
    shot(page, '02-lily')

    before = jsonl_rows(sid)
    last_seq = max([int(r.get('seq', 0)) for r in before] or [0])

    type_home(page, '把 bibo 和 lily 刚才各自回复的内容原样告诉我。')
    page.locator('[data-testid="team-reply-lead"]').first.wait_for(
        state='visible', timeout=120_000)
    rows = wait_lead_turn_end(sid, last_seq)
    page.wait_for_timeout(2_000)
    shot(page, '03-lead-lookup')

    turn = [r for r in rows if int(r.get('seq', 0)) > last_seq]
    names = tool_calls(turn)
    if not any('dispatch_result_read' in n for n in names):
        raise AssertionError(
            f'Lead never called dispatch_result_read; tools used: {names}')
    hunted = [n for n in names if any(h in n for h in HUNT_TOOLS)]
    if hunted:
        raise AssertionError(
            f'Lead hunted the workspace for teammate output: {hunted}')

    errors = [
        r for r in turn if r.get('role') == 'tool' and r.get('is_error')
        and 'dispatch_result_read' in str(r.get('name') or '')
    ]
    if errors:
        raise AssertionError(
            f'dispatch_result_read failed: {str(errors[0].get("content"))[:400]}')

    tool_blob = '\n'.join(
        str(r.get('content') or '') for r in turn if r.get('role') == 'tool')
    for token in (token_b, token_l):
        if token not in tool_blob:
            raise AssertionError(
                f'tool result missing {token!r}: {tool_blob[:600]}')

    lead_text = page.locator(
        '[data-testid="team-reply-lead"]').last.inner_text()
    for token in (token_b, token_l):
        if token not in lead_text:
            raise AssertionError(
                f'Lead answer missing {token!r}: {lead_text[:600]}')

    return {
        'ok': True,
        'session_id': sid,
        'tools_used': names,
        'lead_text': lead_text[:600],
        'url': page.url,
    }


def main() -> int:
    stamp = int(time.time())
    token_b = f'FOODTOK{stamp}'
    token_l = f'VIEWTOK{stamp}'
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
        except Exception as e:  # noqa: BLE001 — report, keep artifacts
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
