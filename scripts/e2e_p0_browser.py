#!/usr/bin/env python3
"""P0 browser click E2E: C-01..C-06 on main chat + /team.

Requires a running WebUI (default http://127.0.0.1:5173) and API
(http://127.0.0.1:8000) with live @bibo (and @lily for the dual-@ case).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5173'
API = 'http://127.0.0.1:8000'
OUT = Path('/tmp/team-p0-browser-e2e')
OUT.mkdir(parents=True, exist_ok=True)

CLICK_JS = """
(label) => {
  const norm = (s) => (s || '').replace(/\\s+/g, '');
  const want = norm(label);
  const btns = [...document.querySelectorAll('button')];
  const el = btns.find((n) => norm(n.innerText).includes(want));
  if (!el) return { ok: false };
  el.click();
  return { ok: true };
}
"""


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)


def dump_fail(page, name: str) -> None:
    shot(page, name)
    (OUT / f'{name}.html').write_text(page.content(), encoding='utf-8')
    (OUT / f'{name}.url').write_text(page.url, encoding='utf-8')


def click_button(page, label: str) -> None:
    got = page.evaluate(CLICK_JS, label)
    if not got or not got.get('ok'):
        raise RuntimeError(f'button {label!r} not found')


def wait_text(page, text: str, timeout=90_000):
    page.get_by_text(text).first.wait_for(state='visible', timeout=timeout)


def api_json(path: str):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        body = json.loads(r.read())
    return body.get('data') if isinstance(body, dict) and 'data' in body else body


def live_at_names() -> set[str]:
    try:
        rows = api_json('/api/v1/team/endpoints') or []
    except Exception:
        return set()
    if isinstance(rows, dict):
        rows = rows.get('endpoints') or rows.get('items') or []
    names = set()
    for e in rows:
        if str(e.get('status') or '').lower() != 'online':
            continue
        names.add(str(e.get('at_name') or '').lstrip('@').lower())
    return names


def type_home(page, text: str) -> None:
    box = page.locator("[contenteditable='true']").first
    box.wait_for(timeout=20_000)
    box.click()
    page.keyboard.press('Meta+A')
    page.keyboard.press('Backspace')
    page.keyboard.type(text, delay=4)
    page.keyboard.press('Enter')


def close_private_stream(page) -> None:
    try:
        page.locator('[data-testid="team-private-stream"]').locator(
            'button').first.click(timeout=3000)
    except Exception:
        pass


def case_main_chat(page, token: str) -> dict:
    """P0-A C-01, P0-C C-06, P0-D C-04, P0-E C-05."""
    prompt = (
        f'@bibo Reply with exactly the single word {token} and nothing else.'
    )
    page.goto(f'{BASE}/', wait_until='domcontentloaded')
    page.wait_for_timeout(600)
    shot(page, 'a01-home')
    type_home(page, prompt)
    shot(page, 'a02-sent')

    wait_text(page, '已派给 @bibo', timeout=30_000)
    page.locator('[data-testid="team-reply-bibo"]').first.wait_for(
        state='visible', timeout=120_000)
    page.locator('[data-testid="team-full-process"]').first.wait_for(
        state='visible', timeout=20_000)
    shot(page, 'a03-bubble')

    bibo_count = page.locator('[data-testid="team-reply-bibo"]').count()
    lily_count = page.locator('[data-testid="team-reply-lily"]').count()
    if bibo_count != 1:
        raise AssertionError(f'C-01 expected one @bibo bubble, got {bibo_count}')
    if lily_count != 0:
        raise AssertionError(f'C-01 leaked @lily bubble ({lily_count})')

    # C-04: idle receipts survive the history swap.
    page.locator('[data-testid="team-receipt"]').first.wait_for(
        state='visible', timeout=30_000)
    body = page.inner_text('body')
    if '已结束执行' not in body and '已派 @bibo' not in body:
        # Live ack is 已派给; persisted start receipt is 已派 @bibo.
        if '已派给 @bibo' not in body:
            raise AssertionError('C-04 missing dispatch receipt')
    toggle = page.locator('[data-testid="team-preview-toggle"]')
    if toggle.count():
        toggle.first.click()
        page.wait_for_timeout(200)
        toggle.first.click()

    # C-05: click 完整过程 → private stream with the token.
    page.locator('[data-testid="team-full-process"]').first.click()
    page.locator('[data-testid="team-private-stream"]').wait_for(
        state='visible', timeout=20_000)
    wait_text(page, token, timeout=20_000)
    wait_text(page, '已结束执行', timeout=15_000)
    shot(page, 'a04-private-stream')
    stream_text = page.locator('[data-testid="team-private-stream"]').inner_text()
    if token not in stream_text:
        raise AssertionError('C-05 private stream missing token')
    close_private_stream(page)

    # C-06: no-@ follow-up stays with Lead — no second @bibo dispatch bubble.
    type_home(page, '做完了吗')
    shot(page, 'a05-followup-sent')
    # A mistaken second @bibo dispatch typically lands a bubble in 15–40s.
    page.wait_for_timeout(45_000)
    n = page.locator('[data-testid="team-reply-bibo"]').count()
    if n != 1:
        raise AssertionError(
            f'C-06 expected still one @bibo bubble after 做完了吗, got {n}')
    shot(page, 'a06-followup')
    return {
        'ok': True,
        'bibo_bubbles': page.locator('[data-testid="team-reply-bibo"]').count(),
        'receipts': page.locator('[data-testid="team-receipt"]').count(),
        'has_token': token in page.inner_text('body'),
    }


def case_team_dual(page, token_b1: str, token_b2: str) -> dict:
    """P0-B C-02/C-03: two @ tracks, private streams do not mix."""
    prompt = (
        f'@bibo Reply with exactly the single word {token_b1} and nothing else. '
        f'@lily Reply with exactly the single word {token_b2} and nothing else.'
    )
    page.goto(f'{BASE}/team', wait_until='domcontentloaded')
    page.get_by_text('Agent Team').first.wait_for(timeout=20_000)
    shot(page, 'b01-team')

    try:
        page.locator('.ant-select').first.click()
        page.locator('.ant-select-item-option').filter(
            has_text='webui:_default').first.click(timeout=4000)
        page.keyboard.press('Escape')
    except PwTimeout:
        pass
    page.wait_for_timeout(300)

    ta = page.locator('[data-testid="team-composer"]')
    if ta.count() == 0:
        ta = page.get_by_placeholder('@me 帮我……')
    ta.click()
    ta.fill(prompt)
    if page.locator('[data-testid="team-send"]').count():
        page.locator('[data-testid="team-send"]').click()
    else:
        click_button(page, '发送')
    shot(page, 'b02-sent')

    page.locator('[data-testid="team-reply-bibo"]').first.wait_for(
        state='visible', timeout=120_000)
    page.locator('[data-testid="team-reply-lily"]').first.wait_for(
        state='visible', timeout=120_000)
    wait_text(page, token_b1, timeout=15_000)
    wait_text(page, token_b2, timeout=15_000)
    shot(page, 'b03-two-cards')

    page.locator('[data-testid="team-reply-bibo"]').first.click()
    page.locator('[data-testid="team-private-stream"]').wait_for(
        state='visible', timeout=20_000)
    stream = page.locator('[data-testid="team-private-stream"]').inner_text()
    if token_b1 not in stream:
        raise AssertionError('C-02/C-03 bibo stream missing TOKEN_B1')
    if token_b2 in stream:
        raise AssertionError('C-03 bibo stream leaked TOKEN_B2')
    shot(page, 'b04-bibo-stream')
    close_private_stream(page)
    page.wait_for_timeout(400)

    page.locator('[data-testid="team-reply-lily"]').first.click()
    page.locator('[data-testid="team-private-stream"]').wait_for(
        state='visible', timeout=20_000)
    stream = page.locator('[data-testid="team-private-stream"]').inner_text()
    if token_b2 not in stream:
        raise AssertionError('C-02/C-03 lily stream missing TOKEN_B2')
    if token_b1 in stream:
        raise AssertionError('C-03 lily stream leaked TOKEN_B1')
    shot(page, 'b05-lily-stream')
    return {'ok': True, 'token_b1': token_b1, 'token_b2': token_b2}


def main() -> int:
    stamp = int(time.time())
    token_a = f'P0A_{stamp}'
    token_b1 = f'P0B1_{stamp}'
    token_b2 = f'P0B2_{stamp}'
    results: dict = {
        'token_a': token_a,
        'token_b1': token_b1,
        'token_b2': token_b2,
        'live': sorted(live_at_names()),
    }
    if 'bibo' not in results['live']:
        results['error'] = '@bibo is not online; cannot run P0 browser cases'
        (OUT / 'results.json').write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 2

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
            results['main'] = case_main_chat(page, token_a)
        except Exception as e:
            dump_fail(page, 'FAIL-main')
            results['main'] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        if 'lily' in results['live']:
            try:
                results['dual'] = case_team_dual(page, token_b1, token_b2)
            except Exception as e:
                dump_fail(page, 'FAIL-dual')
                results['dual'] = {
                    'ok': False,
                    'error': f'{type(e).__name__}: {e}',
                }
        else:
            results['dual'] = {
                'ok': False,
                'skipped': True,
                'error': '@lily is not online; C-02 dual-@ click case skipped',
            }
        context.close()
        browser.close()

    (OUT / 'results.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    main_ok = bool((results.get('main') or {}).get('ok'))
    dual = results.get('dual') or {}
    dual_ok = bool(dual.get('ok') or dual.get('skipped'))
    return 0 if main_ok and dual_ok else 1


if __name__ == '__main__':
    sys.exit(main())
