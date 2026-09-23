#!/usr/bin/env python3
"""Browser E2E: EvoX-style mini swarm (6 atomic parts + gated merge).

Source: https://evomap.ai/zh/blog/how-ai-swarms-win-from-26-to-71-percent
Experiment 1 pattern: complete coverage, isolated contexts, programmatic merge.

Steps:
  1. Seed the board via Team HTTP (Lead-decompose artifact).
  2. Open /team in Chromium, click 按板执行.
  3. Wait until p1–p6 complete; merge must stay blocked until then.
  4. Second 按板执行 (or auto-kick) releases merge.
  5. Open a worker rail; Lead follow-up must not dump teammate transcripts.

Usage:
  python scripts/e2e_evox_mini_browser.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evox_mini_pack import MERGE, PARTS, PROJECT_ID, PROJECT_NAME  # noqa: E402

BASE = 'http://127.0.0.1:5173'
API = 'http://127.0.0.1:8000'
OUT = Path('/tmp/evox-mini-browser-e2e')
OUT.mkdir(parents=True, exist_ok=True)


def api(method: str, path: str, body: dict | None = None, timeout: int = 60):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {'raw': raw.decode('utf-8', 'replace')}
        return e.code, parsed


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)


def dump_fail(page, name: str) -> None:
    shot(page, name)
    (OUT / f'{name}.html').write_text(page.content(), encoding='utf-8')
    (OUT / f'{name}.url').write_text(page.url, encoding='utf-8')


def seed_board() -> dict:
    ws = Path('/tmp/evox-mini-ws')
    ws.mkdir(parents=True, exist_ok=True)
    code, proj = api('POST', '/api/v1/team/projects', {
        'project_id': PROJECT_ID,
        'name': PROJECT_NAME,
        'workspace_path': str(ws),
        'default_lead_at': 'ms_agent',
    })
    if code >= 400:
        raise RuntimeError(f'seed project failed {code}: {proj}')
    api('POST', f'/api/v1/team/projects/{PROJECT_ID}/tasks', {
        'task_id': 'evox_root',
        'status': 'pending',
        'prompt': 'EvoX mini: 6 isolated parts + merge. Do not solve here.',
        'trigger_user_id': 'u1',
    })
    for p in PARTS:
        api('POST', f'/api/v1/team/projects/{PROJECT_ID}/tasks', {
            'task_id': p['task_id'],
            'status': 'pending',
            'prompt': p['prompt'],
            'trigger_user_id': 'u1',
            'target_at_name': p['target_at_name'],
            'parent_task_id': 'evox_root',
        })
    api('POST', f'/api/v1/team/projects/{PROJECT_ID}/tasks', {
        'task_id': MERGE['task_id'],
        'status': 'pending',
        'prompt': MERGE['prompt'],
        'trigger_user_id': 'u1',
        'target_at_name': MERGE['target_at_name'],
        'parent_task_id': 'evox_root',
        'blocked_by': MERGE['blocked_by'],
    })
    _, board = api('GET', f'/api/v1/team/projects/{PROJECT_ID}/tasks')
    return board


def board_by_id() -> dict:
    _, payload = api('GET', f'/api/v1/team/projects/{PROJECT_ID}/tasks')
    rows = payload.get('tasks') or []
    return {t['task_id']: t for t in rows}


def wait_started(timeout: float = 90.0) -> dict:
    """At least two parts have left pending (same-@ is serial, so not all)."""
    deadline = time.time() + timeout
    last = {}
    last_kick = 0.0
    while time.time() < deadline:
        last = board_by_id()
        started = [
            p['task_id'] for p in PARTS
            if last.get(p['task_id'], {}).get('status')
            in ('in_progress', 'completed', 'failed')
        ]
        merge_st = last.get(MERGE['task_id'], {}).get('status')
        if len(started) >= 2 and merge_st in ('pending', 'wait_human', None):
            return last
        now = time.time()
        if now - last_kick >= 8:
            api('POST', f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run')
            last_kick = now
        time.sleep(2)
    raise AssertionError(
        'timeout waiting for parallel start; '
        + json.dumps({k: v.get('status') for k, v in last.items()},
                     ensure_ascii=False))


def isolation_ok() -> tuple[bool, str]:
    """Lead timeline must not dump worker part transcripts (idle receipts only)."""
    _, payload = api('GET', f'/api/v1/team/projects/{PROJECT_ID}/timeline')
    msgs = payload.get('messages') or []
    golds = [p['gold'] for p in PARTS]
    leaks = []
    for m in msgs:
        if m.get('sender_type') == 'system':
            continue
        text = m.get('content') or ''
        for g in golds:
            if g and g in text:
                leaks.append(f"{m.get('sender_type')}:{text[:80]}")
                break
    if leaks:
        return False, '; '.join(leaks[:5])
    return True, f'timeline={len(msgs)}'


def wait_parts(status_ok: set[str], timeout: float = 420.0, *, kick: bool = False) -> dict:
    deadline = time.time() + timeout
    last = {}
    last_kick = 0.0
    while time.time() < deadline:
        last = board_by_id()
        parts = [last.get(p['task_id'], {}) for p in PARTS]
        if parts and all(t.get('status') in status_ok for t in parts):
            return last
        now = time.time()
        if kick and now - last_kick >= 8:
            api('POST', f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run')
            last_kick = now
        time.sleep(2)
    raise AssertionError(
        'timeout waiting for parts; '
        + json.dumps({k: v.get('status') for k, v in last.items()},
                     ensure_ascii=False))


def wait_agents_online(names: list[str], timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        code, payload = api('GET', '/api/v1/team/endpoints')
        rows = payload.get('endpoints') or payload.get('agents') or []
        if not isinstance(rows, list):
            rows = []
        last = {
            (r.get('at_name') or r.get('at_name') or '').lstrip('@'): r
            for r in rows if isinstance(r, dict)
        }
        if all((last.get(n) or {}).get('status') == 'online' for n in names):
            return last
        time.sleep(2)
    return last


def main() -> int:
    results: dict = {'project_id': PROJECT_ID, 'source': 'evox-mini'}
    code, probe = api('POST', f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run')
    if code == 404:
        results['error'] = (
            'coordinator/run is 404 — restart ms-agent-webui backend so it '
            'loads modelscope-agent W3/W4 (MS_AGENT_COORDINATOR optional).'
        )
        (OUT / 'results.json').write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 2

    board = seed_board()
    results['seeded'] = len(board.get('tasks') or [])
    online = wait_agents_online(['bibo', 'lily', 'ms_agent'], timeout=90)
    results['agents'] = {
        n: (online.get(n) or {}).get('status')
        for n in ('bibo', 'lily', 'ms_agent', 'alex', 'codex')
    }
    missing = [n for n in ('bibo', 'lily') if (online.get(n) or {}).get('status') != 'online']
    if missing:
        results['error'] = f'agents not online: {missing} ({results["agents"]})'
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1
    first = api('POST', f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run')
    results['first_run'] = first[1]
    dispatched = {d['task_id'] for d in (first[1].get('dispatched') or [])}
    skipped = {
        s['task_id']: s.get('reason') for s in (first[1].get('skipped') or [])
    }
    if MERGE['task_id'] in dispatched:
        results['error'] = 'merge dispatched before parts completed'
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1
    if skipped.get(MERGE['task_id']) != 'blocked':
        results['warn_merge_skip'] = skipped.get(MERGE['task_id'])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1440, 'height': 960},
            locale='zh-CN',
        )
        page = context.new_page()
        page.set_default_timeout(30_000)
        try:
            page.goto(f'{BASE}/team', wait_until='domcontentloaded')
            page.evaluate(
                """([pid]) => {
                  sessionStorage.setItem('msa.team.ui.v2', JSON.stringify({
                    userId: 'u1', projectId: pid, sessionMode: 'auto',
                    threadId: 'evox-mini'
                  }));
                }""",
                [PROJECT_ID],
            )
            page.reload(wait_until='domcontentloaded')
            page.wait_for_timeout(800)
            shot(page, '01-team')
            kanban = page.locator('[data-testid="team-kanban"]')
            kanban.wait_for(state='visible', timeout=20_000)
            shot(page, '02-kanban-seeded')
            run_btn = page.locator('[data-testid="team-run-board"]')
            if run_btn.count():
                run_btn.click()
                page.wait_for_timeout(1500)
            shot(page, '03-after-run')

            board_now = wait_started(90)
            doing = sum(
                1 for p in PARTS
                if board_now.get(p['task_id'], {}).get('status') == 'in_progress'
            )
            results['in_progress_after_run'] = doing
            merge_status = board_now.get(MERGE['task_id'], {}).get('status')
            if merge_status not in ('pending', 'wait_human'):
                # May already have auto-kicked if parts finished extremely fast.
                results['merge_early'] = merge_status
            else:
                results['merge_held'] = merge_status

            page.reload(wait_until='domcontentloaded')
            page.wait_for_timeout(600)
            shot(page, '04-progress')
            card = page.locator('[data-testid="team-task-evox_p2_sumsq"]')
            if card.count():
                card.click()
                page.wait_for_timeout(800)
                shot(page, '05-rail')

            final = wait_parts({'completed', 'failed'}, 600, kick=True)
            results['part_status'] = {
                p['task_id']: final.get(p['task_id'], {}).get('status')
                for p in PARTS
            }
            failed = [
                k for k, v in results['part_status'].items() if v == 'failed'
            ]
            if failed:
                results['error'] = f'parts failed: {failed}'
                dump_fail(page, 'FAIL-parts')
                print(json.dumps(results, ensure_ascii=False, indent=2))
                return 1

            second = api(
                'POST',
                f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run',
            )
            results['second_run'] = second[1]
            deadline = time.time() + 180
            while time.time() < deadline:
                st = board_by_id().get(MERGE['task_id'], {}).get('status')
                if st in ('completed', 'failed', 'in_progress'):
                    results['merge_status'] = st
                    break
                time.sleep(2)
            else:
                results['merge_status'] = board_by_id().get(
                    MERGE['task_id'], {}).get('status')

            page.reload(wait_until='domcontentloaded')
            page.wait_for_timeout(800)
            shot(page, '06-merge')
            if results.get('merge_status') == 'failed':
                results['error'] = 'merge failed'
                dump_fail(page, 'FAIL-merge')
                return 1
            if results.get('merge_status') not in ('completed', 'in_progress'):
                results['error'] = (
                    f'merge not released: {results.get("merge_status")}')
                dump_fail(page, 'FAIL-merge-held')
                return 1

            deadline = time.time() + 180
            while time.time() < deadline:
                if board_by_id().get(MERGE['task_id'], {}).get('status') == 'completed':
                    break
                time.sleep(2)
            results['merge_final'] = board_by_id().get(MERGE['task_id'], {})
            iso, iso_detail = isolation_ok()
            results['isolation'] = {'ok': iso, 'detail': iso_detail}
            shot(page, '07-done')
            results['ok'] = (
                results['merge_final'].get('status') == 'completed' and iso
            )
            if results['ok'] is False and not iso:
                results['error'] = f'isolation leak: {iso_detail}'
        except Exception as e:
            dump_fail(page, 'FAIL')
            results['ok'] = False
            results['error'] = f'{type(e).__name__}: {e}'
        finally:
            context.close()
            browser.close()

    (OUT / 'results.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
