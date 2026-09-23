#!/usr/bin/env python3
"""Browser E2E: Lead decomposes → Coordinator dispatch → DAG gate → collect.

Does NOT pre-seed tasks. User message must contain no at-signs so Ingress
routes to Lead. Merge must use dispatch_result_read (not glob / re-solve).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:5173'
API = 'http://127.0.0.1:8000'
PROJECT_ID = f'proj_lead_os_{int(time.time())}'
OUT = Path('/tmp/lead-dag-browser-e2e')
OUT.mkdir(parents=True, exist_ok=True)
DOC = ROOT / 'Agent_Team_编排台_功能与架构.md'

# Ingress treats at-signs as worker dispatch. These strings must stay clean.
MEGA = f"""不要用 at 符号点名任何人，也不要自己把 extract 做完。

这是一条大任务：写一页内部对照备忘，比较「编排台任务板 + Coordinator」和「主 Agent 调度 sub-agent 再让 LLM 汇总」。

硬性流程：
1. 用 task_board_write 把工作拆到派工上下文里的 project_id。
2. 写两个可并行的 extract：target_at_name 分别填 bibo 和 lily（不要加 at 符号）。
3. 再写一个汇合任务：blocked_by 必须是那两个 extract 的 task_id；target_at_name 填 ms_agent。
4. 每个 prompt 必须自洽（Worker 看不到本条消息）。extract 只准读这个绝对路径：{DOC}
5. 新任务 status 一律 pending。拆完即停，不要替 Worker 做题。
6. 汇合 prompt 必须写明：只用 dispatch_result_read 收集两份终态，禁止 glob/find/ls，禁止重读原文档。

bibo 的 extract 交出三条要点：谁负责拆解、Coordinator 是不是 LLM、Lead 的 prompt 默认有没有 Worker 正文。
lily 的 extract 交出：完结回执写什么、不写什么。    
"""

FOLLOW = (
    '不要重做子任务。调用 task_board_read，再对每个 completed 子任务 '
    'dispatch_result_read。输出一张 Markdown 表：列=要点 / bibo / lily，'
    '单元格必须逐字引用 Worker 终态，禁止自己重写。禁止 glob/find/ls。'
)

assert '@' not in MEGA, 'MEGA must not contain at-signs (Ingress would skip Lead)'
assert '@' not in FOLLOW, 'FOLLOW must not contain at-signs'


def api(method: str, path: str, body: dict | None = None, timeout: int = 90):
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


def tasks() -> list[dict]:
    _, payload = api('GET', f'/api/v1/team/projects/{PROJECT_ID}/tasks')
    return payload.get('tasks') or []


def by_id() -> dict:
    return {t['task_id']: t for t in tasks()}


def extracts_and_merges(rows: list[dict] | None = None):
    rows = rows if rows is not None else tasks()
    extracts = [
        t for t in rows
        if (t.get('target_at_name') or '') in ('bibo', 'lily')
        and len(t.get('prompt') or '') >= 40
    ]
    merges = [t for t in rows if t.get('blocked_by')]
    return extracts, merges


def wait_lead_wrote(timeout: float = 240.0) -> tuple[list[dict], list[dict]]:
    deadline = time.time() + timeout
    last_ex, last_mg = [], []
    while time.time() < deadline:
        last_ex, last_mg = extracts_and_merges()
        names = {(t.get('target_at_name') or '') for t in last_ex}
        if 'bibo' in names and 'lily' in names and last_mg:
            return last_ex, last_mg
        time.sleep(3)
    return last_ex, last_mg


def tool_names(dispatch_id: str) -> list[str]:
    p = (
        Path.home() / '.ms_agent' / 'team' / 'dispatch_logs'
        / f'{dispatch_id}.jsonl'
    )
    if not p.exists():
        return []
    names = []
    for line in p.read_text(encoding='utf-8').splitlines():
        ev = json.loads(line)
        pl = ev.get('payload') or {}
        if ev.get('type') == 'team.stream' and pl.get('type') == 'tool_call':
            names.append(pl.get('name') or '')
    return names


def final_text(dispatch_id: str) -> str:
    from ms_agent.team.context import final_text_from_dispatch_events
    p = (
        Path.home() / '.ms_agent' / 'team' / 'dispatch_logs'
        / f'{dispatch_id}.jsonl'
    )
    if not p.exists():
        return ''
    events = [
        json.loads(line)
        for line in p.read_text(encoding='utf-8').splitlines()
    ]
    return final_text_from_dispatch_events(events)


def snippet_aligned(worker_text: str, collected: str) -> bool:
    """ACP streams insert newlines mid-word; merge may quote rather than paste.

    Check that the merge kept the worker's facts, not that every glyph matches.
    """
    def compact(s: str) -> str:
        return ''.join((s or '').split())

    worker = compact(worker_text)
    collected_c = compact(collected)
    worker_cjk = ''.join(
        ch for ch in worker if '\u4e00' <= ch <= '\u9fff')
    collected_cjk = ''.join(
        ch for ch in collected_c if '\u4e00' <= ch <= '\u9fff')
    if not worker_cjk or not collected_cjk:
        return False
    checks: list[bool] = []
    if '拆解' in worker_cjk:
        checks.append(
            '拆解' in collected_cjk and (
                'Lead' in collected_c or 'LLM' in collected_c))
    if 'Coordinator' in worker:
        checks.append(
            'Coordinator' in collected_c and (
                '不是' in collected_cjk or '非' in collected_cjk
                or '确定' in collected_cjk))
    if '回执' in worker_cjk:
        checks.append('回执' in collected_cjk)
    if '正文' in worker_cjk:
        checks.append('正文' in collected_cjk or 'prompt' in collected_c.lower())
    if checks:
        return all(checks)
    n = 4
    body = worker_cjk[len(worker_cjk) // 4:] or worker_cjk
    grams = {body[i:i + n] for i in range(0, max(len(body) - n, 0) + 1)}
    hits = sum(1 for g in grams if g in collected_cjk)
    return bool(grams) and (hits >= 6 or hits / len(grams) >= 0.1)


def wait_workers_live(timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _, eps = api('GET', '/api/v1/team/endpoints')
        items = eps.get('endpoints') or []
        last = {
            e.get('at_name'): e.get('status')
            for e in items
            if e.get('at_name') in ('bibo', 'lily', 'ms_agent')
        }
        if last.get('bibo') == 'online' and last.get('lily') == 'online':
            _, bridges = api('GET', '/api/v1/team/bridges')
            live = any(
                b.get('bridge_id') == 'br-6bf48140'
                and b.get('status') == 'online'
                for b in (bridges.get('bridges') or [])
            )
            if live:
                return last
        time.sleep(2)
    return last


def ui_reachable() -> bool:
    try:
        urllib.request.urlopen(BASE + '/team', timeout=2)
        return True
    except Exception:
        return False


def send_message(content: str, timeout: int = 250):
    return api(
        'POST',
        f'/api/v1/team/projects/{PROJECT_ID}/messages'
        f'?wait=true&wait_timeout={min(timeout, 300)}',
        {
            'content': content,
            'sender_user_id': 'u1',
            'channel': 'web',
            'thread_id': 'lead-oneshot',
            'session_mode': 'fresh',
        },
        timeout=timeout + 5,
    )


def run_board():
    return api(
        'POST', f'/api/v1/team/projects/{PROJECT_ID}/coordinator/run')


def record_board(results: dict) -> tuple[list[dict], list[dict]]:
    extracts, merges = extracts_and_merges()
    results['board_after_lead'] = [
        {
            'task_id': t.get('task_id'),
            'at': t.get('target_at_name'),
            'blocked_by': t.get('blocked_by'),
            'status': t.get('status'),
            'prompt_len': len(t.get('prompt') or ''),
        }
        for t in tasks()
    ]
    results['checks']['lead_wrote_board'] = (
        len(extracts) >= 2
        and {t.get('target_at_name') for t in extracts} >= {'bibo', 'lily'}
    )
    results['checks']['has_blocked_by'] = len(merges) >= 1
    held = any(
        t.get('status') in ('pending', 'wait_human') for t in merges)
    incomplete = [
        t for t in extracts
        if t.get('status') not in ('completed', 'failed')
    ]
    results['checks']['merge_held_while_parts_run'] = (
        held if incomplete else True)
    return extracts, merges


def wait_dag(results: dict) -> dict | None:
    deadline = time.time() + 420
    merge_row = None
    while time.time() < deadline:
        run_board()
        extracts, merges = extracts_and_merges()
        if extracts and all(t.get('status') == 'completed' for t in extracts):
            if merges and all(
                    t.get('status') in ('completed', 'in_progress', 'failed')
                    for t in merges):
                results['checks']['dag_released'] = True
                return merges[0]
        time.sleep(4)
    results['checks']['dag_released'] = False
    results['error'] = 'timeout waiting DAG release'
    results['status_timeout'] = {
        t.get('task_id'): {
            'status': t.get('status'),
            'at': t.get('target_at_name'),
            'blocked_by': t.get('blocked_by'),
        }
        for t in tasks()
    }
    return None


def wait_merge_read(results: dict, merge_row: dict | None) -> tuple[bool, str | None]:
    deadline = time.time() + 180
    merge_did = None
    while time.time() < deadline:
        extracts, merges = extracts_and_merges()
        merge_row = merges[0] if merges else merge_row
        merge_did = (merge_row or {}).get('last_dispatch_id')
        names = tool_names(merge_did or '')
        if names:
            results['merge_tools'] = names
        status = (merge_row or {}).get('status')
        if status == 'completed':
            used = any('dispatch_result_read' in n for n in names)
            return used, merge_did
        if status == 'failed':
            return False, merge_did
        time.sleep(3)
    used = any(
        'dispatch_result_read' in n
        for n in (results.get('merge_tools') or [])
    )
    return used, merge_did


def finish_align(results: dict, merge_did: str | None) -> None:
    extract_text = {
        t['target_at_name']: final_text(t.get('last_dispatch_id') or '')
        for t in extracts_and_merges()[0]
        if t.get('status') == 'completed'
    }
    collected = final_text(merge_did or '')
    results['extracts'] = {k: v[:400] for k, v in extract_text.items()}
    results['merge_final'] = collected[:800]
    aligned = bool(extract_text) and all(
        snippet_aligned(text, collected)
        for text in extract_text.values()
    )
    results['checks']['lead_aligned_to_workers'] = aligned
    if not aligned:
        results['align_fail'] = [
            name for name, text in extract_text.items()
            if not snippet_aligned(text, collected)
        ]
    results['ok'] = all(
        results['checks'].get(k) for k in (
            'lead_wrote_board',
            'has_blocked_by',
            'merge_held_while_parts_run',
            'dag_released',
            'lead_used_dispatch_result_read',
            'lead_aligned_to_workers',
        ))


def drive_after_send(results: dict, *, send_follow) -> int:
    extracts, merges = wait_lead_wrote(240)
    record_board(results)
    if not results['checks']['lead_wrote_board']:
        results['error'] = (
            'Lead did not task_board_write assigned extract subtasks')
        return 1
    if not results['checks']['has_blocked_by']:
        results['error'] = 'Lead did not write a blocked_by merge task'
        return 1
    merge_row = wait_dag(results)
    if merge_row is None:
        return 1
    used_read, merge_did = wait_merge_read(results, merge_row)
    if not used_read:
        send_follow()
        deadline = time.time() + 180
        while time.time() < deadline:
            _, tl = api(
                'GET', f'/api/v1/team/projects/{PROJECT_ID}/timeline')
            results['timeline_n'] = len(tl.get('messages') or [])
            for t in tasks():
                if t.get('target_at_name') == 'ms_agent' and t.get(
                        'last_dispatch_id'):
                    names = tool_names(t['last_dispatch_id'])
                    if any('dispatch_result_read' in n for n in names):
                        used_read = True
                        merge_did = t['last_dispatch_id']
                        results['lead_tools'] = names
                        break
            if used_read:
                break
            time.sleep(3)
    results['checks']['lead_used_dispatch_result_read'] = used_read
    finish_align(results, merge_did)
    return 0 if results.get('ok') else 1


def main() -> int:
    results: dict = {'project_id': PROJECT_ID, 'checks': {}}
    code, proj = api('POST', '/api/v1/team/projects', {
        'project_id': PROJECT_ID,
        'name': 'lead-oneshot-decompose',
        'workspace_path': str(ROOT),
        'default_lead_at': 'ms_agent',
    })
    if code >= 400:
        results['error'] = f'project {code}: {proj}'
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1

    def dump_results(code: int) -> int:
        (OUT / 'results.json').write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return code

    if not ui_reachable():
        results['mode'] = 'api'
        live = wait_workers_live(90)
        results['workers'] = live
        if live.get('bibo') != 'online' or live.get('lily') != 'online':
            results['error'] = f'workers not online: {live}'
            results['ok'] = False
            return dump_results(1)
        code, body = send_message(MEGA, timeout=250)
        results['lead_send'] = {
            'http': code,
            'targets': [
                d.get('target_at_name')
                for d in (body.get('dispatches') or [])
            ],
        }
        if code >= 400:
            results['error'] = f'lead send {code}: {body}'
            results['ok'] = False
            return dump_results(1)
        rc = drive_after_send(
            results, send_follow=lambda: send_message(FOLLOW, timeout=180))
        if not results.get('ok') and not results.get('error'):
            results['error'] = 'checks failed'
        return dump_results(rc)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1440, 'height': 960}, locale='zh-CN')
        page = context.new_page()
        page.set_default_timeout(360_000)
        try:
            results['mode'] = 'browser'
            page.goto(f'{BASE}/team', wait_until='domcontentloaded')
            page.evaluate(
                """([pid]) => {
                  sessionStorage.setItem('msa.team.ui.v2', JSON.stringify({
                    userId: 'u1', projectId: pid, sessionMode: 'fresh',
                    threadId: 'lead-oneshot', message: ''
                  }));
                }""",
                [PROJECT_ID],
            )
            page.reload(wait_until='domcontentloaded')
            page.wait_for_timeout(1200)
            page.locator('[data-testid="team-composer"]').fill(MEGA)
            shot(page, '01-prompt')
            page.locator('[data-testid="team-send"]').click()
            shot(page, '02-sent')

            def follow():
                page.locator('[data-testid="team-composer"]').fill(FOLLOW)
                page.locator('[data-testid="team-send"]').click()
                page.wait_for_timeout(3000)
                shot(page, '05-followup')

            rc = drive_after_send(results, send_follow=follow)
            shot(page, '06-done')
            if rc:
                dump_fail(page, 'FAIL')
        except Exception as e:
            dump_fail(page, 'FAIL')
            results['ok'] = False
            results['error'] = f'{type(e).__name__}: {e}'
            rc = 1
        finally:
            context.close()
            browser.close()
        return dump_results(rc)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
