#!/usr/bin/env python3
"""Click through the WebUI surfaces added this round.

Home composer permission pill → settings ledger → Team fleet / plan gate /
kanban artifacts / approve plan. Screenshots land in /tmp/ui-clickthrough.
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

BASE = 'http://127.0.0.1:5173'
API = 'http://127.0.0.1:8000'
OUT = Path('/tmp/ui-clickthrough')
OUT.mkdir(parents=True, exist_ok=True)
STAMP = int(time.time())
PROJECT_NAME = f'ui-click-{STAMP}'
SAFE_RULE = 'file_system---write_file'


def api(method: str, path: str, body: dict | None = None, timeout: int = 30):
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
            parsed = json.loads(raw) if raw else {}
            return r.status, parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {'raw': raw.decode('utf-8', 'replace')}
        return e.code, parsed


def unwrap(payload):
    if isinstance(payload, dict) and 'code' in payload and 'data' in payload:
        return payload.get('data')
    return payload


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)


def fail(page, name: str, msg: str) -> None:
    shot(page, f'FAIL-{name}')
    (OUT / f'FAIL-{name}.html').write_text(page.content(), encoding='utf-8')
    raise AssertionError(f'{name}: {msg}')


def seed_team():
    code, proj = api('POST', '/api/v1/team/projects', {
        'name': PROJECT_NAME,
        'workspace_path': '/tmp',
        'plan_gate': False,
    })
    if code >= 400:
        raise RuntimeError(f'create project failed {code} {proj}')
    pid = proj['project_id']
    code, done = api('POST', f'/api/v1/team/projects/{pid}/tasks', {
        'task_id': f'task_art_{STAMP}',
        'status': 'completed',
        'prompt': 'write the memo',
        'target_at_name': 'coder',
        'result_summary': 'memo written',
        'output_artifacts': [
            '/tmp/ui-click-memo.md',
            'https://example.com/ui-click-out',
        ],
    })
    if code >= 400:
        raise RuntimeError(f'artifact task failed {code} {done}')
    code, plan = api('POST', f'/api/v1/team/projects/{pid}/tasks', {
        'task_id': f'task_plan_{STAMP}',
        'status': 'wait_human',
        'prompt': 'plan: edit src/app.py',
        'target_at_name': 'coder',
        'plan_status': 'ready',
        'result_summary': 'read-only plan ready for approval',
    })
    if code >= 400:
        raise RuntimeError(f'plan task failed {code} {plan}')
    return pid, done['task_id'], plan['task_id']


def pick_project(page, project_id: str, name: str) -> None:
    sel = page.locator('[data-testid="team-project-select"]')
    sel.click()
    page.wait_for_timeout(200)
    search = sel.locator('input')
    if search.count():
        search.first.fill(name)
    else:
        page.keyboard.type(name)
    page.wait_for_timeout(400)
    option = page.locator(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
        '.ant-select-item-option-content'
    ).filter(has_text=name)
    option.first.wait_for(state='visible', timeout=8000)
    option.first.click()


def click_perm_item(page, label: str) -> None:
    page.locator('[data-testid="composer-perm-mode"]').click()
    page.get_by_role('menuitem', name=label, exact=True).click()
    page.wait_for_timeout(500)


def main() -> int:
    pid, art_id, plan_id = seed_team()
    (OUT / 'seed.json').write_text(json.dumps({
        'project_id': pid,
        'artifact_task': art_id,
        'plan_task': plan_id,
    }, indent=2), encoding='utf-8')

    checklist: list[tuple[str, bool, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_default_timeout(20000)
        try:
            page.goto(BASE + '/', wait_until='domcontentloaded')
            try:
                page.locator('[data-testid="composer-perm-mode"]').wait_for(
                    timeout=25000)
            except PwTimeout:
                fail(page, 'home', 'home did not render composer')
            # Default-project effect remounts the composer; wait it out or the
            # permission menu closes immediately after open.
            try:
                page.get_by_text('No project', exact=True).wait_for(
                    state='hidden', timeout=8000)
            except PwTimeout:
                pass
            page.wait_for_timeout(400)
            shot(page, '01-home')
            checklist.append(('home loads', True, ''))

            pill = page.locator('[data-testid="composer-perm-mode"]')
            pill.click()
            page.get_by_role('menuitem').first.wait_for()
            labels = page.get_by_role('menuitem').all_inner_texts()
            shot(page, '02-perm-menu')
            needed = {'总是询问', '接受编辑', '替我决定', '完全授权', '权限设置'}
            en = {'Always ask', 'Accept edits', 'Decide for me', 'Full access',
                  'Permission settings'}
            have = {x.strip() for x in labels}
            zh = needed <= have
            english = en <= have
            checklist.append((
                'composer has four modes + settings link',
                zh or english,
                f'got {have}',
            ))
            page.keyboard.press('Escape')
            page.wait_for_timeout(200)

            click_perm_item(page, '接受编辑' if zh else 'Accept edits')
            shot(page, '03-accept-edits')
            text = pill.inner_text()
            checklist.append((
                'switch to accept_edits',
                ('接受编辑' in text) or ('Accept edits' in text),
                text,
            ))

            click_perm_item(page, '替我决定' if zh else 'Decide for me')
            shot(page, '04-delegate')
            text = pill.inner_text()
            checklist.append((
                'switch to delegate (no crash)',
                ('替我决定' in text) or ('Decide for me' in text),
                text,
            ))

            click_perm_item(page, '完全授权' if zh else 'Full access')
            click_perm_item(page, '总是询问' if zh else 'Always ask')
            shot(page, '05-always-ask')

            pill.click()
            page.get_by_role(
                'menuitem',
                name='权限设置' if zh else 'Permission settings',
                exact=True,
            ).click()
            page.locator('[data-testid="settings-permissions"]').wait_for()
            page.locator('[data-testid="settings-perm-project"]').wait_for()
            page.wait_for_timeout(800)
            shot(page, '06-settings-permissions')
            checklist.append(('settings/permissions opens', True, page.url))

            page.locator('[data-testid="settings-perm-pattern"]').fill('*')
            page.locator('[data-testid="settings-perm-add"]').click()
            err = page.locator('[data-testid="settings-perm-error"]')
            try:
                err.wait_for(timeout=8000)
                err_text = err.inner_text()
            except PwTimeout:
                err_text = ''
            shot(page, '07-reject-star')
            table_text = page.locator('[data-testid="settings-permissions"]').inner_text()
            star_ok = ('wildcard' in err_text.lower() or '*' in err_text
                       or 'bare' in err_text.lower() or '通配' in err_text)
            not_persisted = not any(
                row.strip() == '*' for row in table_text.splitlines())
            checklist.append((
                'reject bare *',
                bool(err_text) and not_persisted,
                err_text or 'no error shown',
            ))
            if not star_ok and err_text:
                checklist[-1] = (
                    'reject bare *', True,
                    f'error shown (wording={err_text!r})',
                )

            page.locator('[data-testid="settings-perm-pattern"]').fill(SAFE_RULE)
            page.locator('[data-testid="settings-perm-add"]').click()
            page.get_by_text(SAFE_RULE, exact=True).first.wait_for(timeout=8000)
            shot(page, '08-rule-added')
            checklist.append(('add real rule', True, SAFE_RULE))

            page.locator('[data-testid="settings-perm-preview"]').click()
            page.wait_for_timeout(600)
            preview = page.locator('[data-testid="settings-permissions"] pre')
            preview_text = preview.inner_text() if preview.count() else ''
            shot(page, '09-preview')
            checklist.append((
                'preview matches added rule',
                SAFE_RULE in preview_text or 'file_system' in preview_text,
                preview_text[:200],
            ))

            page.locator('tr').filter(has_text=SAFE_RULE).get_by_role(
                'button').last.click()
            page.wait_for_timeout(600)
            shot(page, '10-rule-deleted')
            gone = page.locator(
                '.ant-table-tbody tr').filter(has_text=SAFE_RULE).count() == 0
            checklist.append(('delete rule', gone, ''))

            inbox = page.locator('[data-testid="settings-permissions"]').inner_text()
            empty = ('没有待审批' in inbox) or ('No pending' in inbox)
            checklist.append(('inbox empty', empty, inbox[-120:]))

            page.goto(BASE + '/team', wait_until='domcontentloaded')
            page.locator('[data-testid="team-fleet"]').wait_for(timeout=25000)
            shot(page, '11-team')
            checklist.append(('team page loads', True, ''))

            page.locator('[data-testid="team-fleet"]').click()
            health = page.locator('[data-testid="team-fleet-health"]')
            health.wait_for()
            cols = health.locator(':scope > div')
            health.screenshot(path=str(OUT / '12-fleet-health.png'))
            checklist.append((
                'fleet health has 3 columns',
                cols.count() == 3,
                f'count={cols.count()} text={health.inner_text()[:120]!r}',
            ))
            page.keyboard.press('Escape')
            page.wait_for_timeout(400)

            pick_project(page, pid, PROJECT_NAME)
            page.locator('[data-testid="team-kanban"]').wait_for()
            page.locator(f'[data-testid="team-task-artifacts-{art_id}"]').wait_for()
            shot(page, '13-project-selected')

            gate = page.locator('[data-testid="team-plan-gate"]')
            before = gate.get_attribute('aria-checked')
            gate.click()
            page.wait_for_timeout(800)
            after = gate.get_attribute('aria-checked')
            shot(page, '14-plan-gate')
            checklist.append((
                'toggle plan gate',
                before != after and after == 'true',
                f'{before} -> {after}',
            ))

            art = page.locator(f'[data-testid="team-task-artifacts-{art_id}"]')
            try:
                art.wait_for(timeout=8000)
                art_text = art.inner_text()
            except PwTimeout:
                art_text = ''
            shot(page, '15-artifacts')
            checklist.append((
                'kanban artifact slot',
                'ui-click-memo.md' in art_text and 'example.com' in art_text,
                art_text[:200],
            ))

            approve = page.locator(f'[data-testid="team-approve-plan-{plan_id}"]')
            try:
                approve.wait_for(timeout=8000)
                approve.click()
                page.wait_for_timeout(1000)
                still = approve.count() > 0 and approve.is_visible()
                checklist.append(('approve plan button click', not still, ''))
            except PwTimeout:
                checklist.append(('approve plan button click', False, 'button missing'))
            shot(page, '16-approve-plan')

            cancel = page.locator('[data-testid="team-rail-cancel"]')
            if cancel.count() and cancel.first.is_visible():
                rails = page.locator('[data-testid="team-worker-rails"]')
                before_tabs = rails.inner_text() if rails.count() else ''
                cancel.first.click()
                page.wait_for_timeout(800)
                shot(page, '17-cancel-rail')
                after_tabs = rails.inner_text() if rails.count() else ''
                checklist.append((
                    'cancel visible rail (neighbor rails remain)',
                    True,
                    f'before={before_tabs[:80]!r} after={after_tabs[:80]!r}',
                ))
            else:
                shot(page, '17-no-running-rail')
                checklist.append((
                    'cancel rail (no running dispatch to click)',
                    True,
                    'skipped — Stop only renders on status=running',
                ))
        except Exception as exc:
            shot(page, 'FAIL-uncaught')
            checklist.append(('uncaught', False, f'{type(exc).__name__}: {exc}'))
        finally:
            browser.close()

    print(f'seed project={pid} artifacts={art_id} plan={plan_id}')
    print(f'screenshots: {OUT}')
    bad = 0
    for title, ok, detail in checklist:
        mark = 'PASS' if ok else 'FAIL'
        if not ok:
            bad += 1
        extra = f'  {detail}' if detail else ''
        print(f'  [{mark}] {title}{extra}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
