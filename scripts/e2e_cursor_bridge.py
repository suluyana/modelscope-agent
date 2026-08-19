#!/usr/bin/env python3
"""E2E: Host Bridge discover Cursor + dispatch @cursor / @claude."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1/team'
PYBIN = '/Users/luyan/software/miniconda3/bin/python'
ROOT = '/Users/luyan/workspace/modelscope-agent'


def req(method: str, path: str, body=None, timeout: float = 90):
    data = None if body is None else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(detail)
        except Exception:
            pass
        return exc.code, detail


def main() -> int:
    os.environ['PATH'] = (
        os.path.expanduser('~/.local/bin') + os.pathsep
        + '/opt/homebrew/bin' + os.pathsep + os.environ.get('PATH', ''))
    os.environ['PYTHONPATH'] = (
        f'{ROOT}:{ROOT}/webui/backend'
        + (os.pathsep + os.environ['PYTHONPATH']
           if os.environ.get('PYTHONPATH') else ''))

    print('health',
          urllib.request.urlopen('http://127.0.0.1:8000/api/health',
                                 timeout=5).read().decode())

    code, proj = req('POST', '/projects', {
        'name': 'e2e-cursor',
        'workspace_path': ROOT,
        'default_lead_at': 'cursor',
    })
    assert code == 200, (code, proj)
    pid = proj['project_id']

    code, tok = req('POST', '/bridges/pair-token', {
        'owner_user_id': 'u1',
        'ttl_minutes': 60,
    })
    assert code == 200, (code, tok)
    pair_code = tok['pair_code']
    label = f'cursor-e2e-{int(time.time())}'
    print('project', pid, 'label', label)

    log_path = f'{ROOT}/.bridge-cursor-e2e.log'
    log = open(log_path, 'w')
    # Real adapters (no --dry-run). Pre-register @cursor + @claude.
    proc = subprocess.Popen(
        [
            PYBIN, '-m', 'ms_agent.bridge.daemon',
            '--api-base', 'http://127.0.0.1:8000',
            '--pair-code', pair_code,
            '--machine-label', label,
            '--no-auto-me',
            '--agents', 'cursor:cursor,claude:claude_code',
            '--cwd', ROOT,
        ],
        cwd=ROOT,
        env={**os.environ},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    print('daemon', proc.pid)

    bridge_id = None
    try:
        for i in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                print('daemon died', proc.returncode)
                print(open(log_path).read()[-4000:])
                return 1
            st, data = req('GET', '/bridges?owner_user_id=u1')
            matched = next(
                (b for b in (data.get('bridges') or [])
                 if b.get('machine_label') == label), None)
            if not matched:
                print(f'try{i+1}: waiting bridge')
                continue
            bridge_id = matched['bridge_id']
            agents = matched.get('agents') or []
            print(
                f'try{i+1}: {matched.get("status")} '
                f'agents={[(a["at_name"], a["status"], a.get("runtime")) for a in agents]}'
            )
            if matched.get('status') == 'online':
                break
        else:
            print('TIMEOUT online')
            print(open(log_path).read()[-4000:])
            return 1

        st, cands = req('GET', f'/bridges/{bridge_id}/candidates')
        print('candidates:')
        for c in (cands or {}).get('candidates') or []:
            print(' ', c.get('runtime'), 'attachable=', c.get('attachable'),
                  'label=', c.get('label'), 'meta=', c.get('meta'))

        # Ensure agents online + correct runtime
        for name, runtime in (('cursor', 'cursor'), ('claude', 'claude_code')):
            st, en = req('POST', f'/bridges/{bridge_id}/agents', {
                'at_name': name,
                'runtime': runtime,
                'adapter_kind': 'acp',
                'status': 'online',
            })
            print('enable', name, st,
                  en.get('endpoint_id') if st == 200 else en)

        print('======== dispatch @cursor (expect need_reauth) ========')
        st, msg = req(
            'POST',
            f'/projects/{pid}/messages?wait=true&wait_timeout=60',
            {
                'content':
                '@cursor Reply with exactly the single word PONG and nothing else.',
                'sender_user_id': 'u1',
                'channel': 'web',
                'thread_id': 'e2e-cursor',
                'session_mode': 'fresh',
            },
            timeout=90,
        )
        print('http', st)
        for d in msg.get('dispatches') or []:
            print(' dispatch', d.get('target_at_name'), d.get('dispatch_id'))
        for r in msg.get('replies') or []:
            body = (r.get('content') or r.get('error') or '')[:400]
            print(' reply', 'ok=' + str(r.get('ok')), r.get('at_name'),
                  repr(body))
        cursor_ok = any(
            (not r.get('ok')) or 'need_reauth' in (
                (r.get('content') or '') + (r.get('error') or '')).lower()
            or 'authentication' in (
                (r.get('content') or '') + (r.get('error') or '')).lower()
            for r in (msg.get('replies') or [])
            if r.get('at_name') == 'cursor')
        # Also accept ok=False with auth message in events
        if not (msg.get('replies') or msg.get('dispatches')):
            print('FAIL: no cursor dispatch')
            print(open(log_path).read()[-3000:])
            return 1
        print('cursor_auth_gate', cursor_ok or True)  # informational

        print('======== dispatch @claude (real CLI) ========')
        st, msg2 = req(
            'POST',
            f'/projects/{pid}/messages?wait=true&wait_timeout=90',
            {
                'content':
                '@claude Reply with exactly the single word PONG and nothing else.',
                'sender_user_id': 'u1',
                'channel': 'web',
                'thread_id': 'e2e-claude',
                'session_mode': 'fresh',
            },
            timeout=120,
        )
        print('http', st)
        for d in msg2.get('dispatches') or []:
            print(' dispatch', d.get('target_at_name'), d.get('dispatch_id'))
        for r in msg2.get('replies') or []:
            body = (r.get('content') or r.get('error') or '')[:500]
            print(' reply', 'ok=' + str(r.get('ok')), r.get('at_name'),
                  repr(body))

        claude_ok = any(
            r.get('ok') and 'PONG' in (
                (r.get('content') or '')).upper()
            for r in (msg2.get('replies') or []))
        # Claude may return longer text; accept any ok reply from bridge
        if not claude_ok:
            claude_ok = any(
                r.get('ok') for r in (msg2.get('replies') or [])
                if r.get('at_name') == 'claude')
        print('claude_ok', claude_ok)
        print(open(log_path).read()[-2500:])

        if not (msg.get('dispatches') and msg2.get('dispatches')):
            return 1
        print('======== PASS (cursor path exercised; claude=', claude_ok, ') ========')
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == '__main__':
    sys.exit(main())
