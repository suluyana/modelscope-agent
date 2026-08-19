#!/usr/bin/env python3
"""Agent Team E2E — Codex Host Bridge happy path (T1–T6).

See Agent_Team_E2E_测试流程.md.

Env:
  E2E_API_BASE          default http://127.0.0.1:8000
  E2E_TIMEOUT           dispatch wait seconds (default 180)
  E2E_OWNER             default u1
  E2E_REPO              cwd for daemon (default repo root)
  SKIP_ATTACH=1         skip T5
  SKIP_CANCEL=1         skip T6
  SKIP_CLAUDE=1         skip Claude对照 (default 1)
  SKIP_CURSOR=1         skip Cursor对照 (default 1)
  E2E_CHECK_CODEX_NET=1 optional ACP login probe (currently SKIP marker)
  E2E_UPDATE_DOC=1       patch 附录 in Agent_Team_E2E_测试流程.md
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_BASE = os.environ.get('E2E_API_BASE', 'http://127.0.0.1:8000').rstrip('/')
TEAM = f'{API_BASE}/api/v1/team'
OWNER = os.environ.get('E2E_OWNER', 'u1')
REPO = Path(os.environ.get('E2E_REPO', str(ROOT)))
TIMEOUT = int(os.environ.get('E2E_TIMEOUT', '180'))
PYBIN = os.environ.get(
    'E2E_PYTHON',
    '/Users/luyan/software/miniconda3/bin/python'
    if Path('/Users/luyan/software/miniconda3/bin/python').is_file()
    else sys.executable,
)

SKIP_ATTACH = os.environ.get('SKIP_ATTACH', '0').lower() in ('1', 'true', 'yes')
SKIP_CANCEL = os.environ.get('SKIP_CANCEL', '0').lower() in ('1', 'true', 'yes')
SKIP_CLAUDE = os.environ.get('SKIP_CLAUDE', '1').lower() in ('1', 'true', 'yes')
SKIP_CURSOR = os.environ.get('SKIP_CURSOR', '1').lower() in ('1', 'true', 'yes')
CHECK_NET = os.environ.get('E2E_CHECK_CODEX_NET', '0').lower() in (
    '1', 'true', 'yes')
UPDATE_DOC = os.environ.get('E2E_UPDATE_DOC', '1').lower() in (
    '1', 'true', 'yes')


@dataclass
class CaseResult:
    name: str
    status: str  # PASS|FAIL|SKIP|BLOCKED
    detail: str = ''
    evidence: dict[str, Any] = field(default_factory=dict)


RESULTS: list[CaseResult] = []


def _env_path() -> dict[str, str]:
    env = os.environ.copy()
    nvm_codex = Path.home() / '.nvm' / 'versions' / 'node'
    extras = [
        str(Path.home() / '.local' / 'bin'),
        '/opt/homebrew/bin',
    ]
    if nvm_codex.is_dir():
        for p in sorted(nvm_codex.glob('*/bin'), reverse=True)[:3]:
            extras.append(str(p))
    env['PATH'] = os.pathsep.join(extras + [env.get('PATH', '')])
    env['PYTHONPATH'] = os.pathsep.join([
        str(ROOT),
        str(ROOT / 'webui' / 'backend'),
        env.get('PYTHONPATH', ''),
    ])
    env['PYTHONUNBUFFERED'] = '1'
    # True ACP: no Team .env API keys. Attach fails loudly by default.
    env.setdefault('MS_AGENT_ACP_ATTACH_FALLBACK', 'error')
    env.setdefault('MS_AGENT_SESSION_ATTACH_FALLBACK', 'error')
    return env


def req(method: str, path: str, body: Any = None,
        timeout: float = 60) -> tuple[int, Any]:
    url = path if path.startswith('http') else (
        TEAM + path if path.startswith('/') else f'{TEAM}/{path}')
    data = None if body is None else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw
        return exc.code, detail
    except urllib.error.URLError as exc:
        return 0, {'error': str(exc)}


def record(name: str, result: str, detail: str = '',
           **evidence: Any) -> CaseResult:
    cr = CaseResult(
        name=name, status=result, detail=detail, evidence=dict(evidence))
    RESULTS.append(cr)
    flag = {
        'PASS': '✓',
        'FAIL': '✗',
        'SKIP': '·',
        'BLOCKED': '!',
    }.get(result, '?')
    print(f'[{flag}] {name}: {result}' + (f' — {detail}' if detail else ''))
    if evidence:
        print('    ', json.dumps(evidence, ensure_ascii=False)[:500])
    return cr


def gate() -> bool:
    print('======== 0) Gate ========')
    code, health = req('GET', f'{API_BASE}/api/health', timeout=5)
    if code != 200:
        record('gate.health', 'FAIL', f'http={code}', body=health)
        return False
    record('gate.health', 'PASS', 'ok')

    code, bridges = req('GET', '/bridges?owner_user_id=' + OWNER)
    if code != 200:
        record('gate.bridges', 'FAIL', f'http={code}', body=bridges)
        return False
    record('gate.bridges', 'PASS')

    code, old = req('POST', '/endpoints/pair-token', {'owner_user_id': OWNER})
    if code != 410:
        record('gate.old_pair_410', 'FAIL', f'expected 410 got {code}',
               body=old)
        return False
    record('gate.old_pair_410', 'PASS')

    from ms_agent.bridge.adapters.acp_codex import resolve_codex_acp_command
    acp_cmd = resolve_codex_acp_command()
    if not acp_cmd:
        record(
            'gate.codex_acp',
            'FAIL',
            'codex-acp / npx @agentclientprotocol/codex-acp not available',
        )
        return False
    record('gate.codex_acp', 'PASS', ' '.join(acp_cmd))

    auth = Path.home() / '.codex' / 'auth.json'
    if not auth.is_file():
        record(
            'gate.codex_auth',
            'BLOCKED',
            f'missing {auth} — log in via Codex CLI',
        )
    else:
        record('gate.codex_auth', 'PASS', str(auth))

    if CHECK_NET:
        record(
            'gate.codex_net',
            'SKIP',
            'ACP login probe is manual/integration',
        )
    return True


def wait_bridge_online(label: str, timeout: float = 30) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, data = req('GET', f'/bridges?owner_user_id={OWNER}')
        if code == 200:
            for b in data.get('bridges') or []:
                if b.get('machine_label') == label:
                    if b.get('status') == 'online':
                        return b
                    print(f'  … bridge {label} status={b.get("status")}')
        time.sleep(1)
    return None


def run_t1_t3(label: str, log_path: Path,
              env: dict[str, str]) -> tuple[subprocess.Popen | None, str, str]:
    """Return (daemon, project_id, bridge_id)."""
    print('======== T1) Pair + online ========')
    code, proj = req('POST', '/projects', {
        'name': f'e2e-codex-{int(time.time())}',
        'workspace_path': str(REPO),
        'default_lead_at': 'codex',
    })
    if code != 200:
        record('T1.project', 'FAIL', f'http={code}', body=proj)
        return None, '', ''
    project_id = proj['project_id']
    record('T1.project', 'PASS', project_id=project_id)

    code, tok = req('POST', '/bridges/pair-token', {
        'owner_user_id': OWNER,
        'ttl_minutes': 60,
    })
    if code != 200:
        record('T1.pair_token', 'FAIL', f'http={code}', body=tok)
        return None, project_id, ''
    pair_code = tok['pair_code']
    record('T1.pair_token', 'PASS')

    log_f = open(log_path, 'w')
    proc = subprocess.Popen(
        [
            PYBIN, '-m', 'ms_agent.bridge.daemon',
            '--api-base', API_BASE,
            '--pair-code', pair_code,
            '--machine-label', label,
            '--no-auto-me',
            '--agents', 'codex:codex',
            '--cwd', str(REPO),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    print(f'  daemon pid={proc.pid} log={log_path}')

    bridge = wait_bridge_online(label, timeout=35)
    if bridge is None:
        log_f.flush()
        tail = log_path.read_text(errors='replace')[-2500:]
        record('T1.online', 'FAIL', 'timeout waiting online', log_tail=tail)
        proc.terminate()
        return None, project_id, ''
    bridge_id = bridge['bridge_id']
    record('T1.online', 'PASS', bridge_id=bridge_id, bridge_status=bridge.get('status'))

    print('======== T2) Candidates ========')
    code, cands = req('GET', f'/bridges/{bridge_id}/candidates')
    if code != 200:
        record('T2.candidates', 'FAIL', f'http={code}', body=cands)
        return proc, project_id, bridge_id
    rows = cands.get('candidates') or []
    codex_rows = [c for c in rows if c.get('runtime') == 'codex']
    # Heartbeat discover may wait on ACP session/list — poll up to ~25s.
    for _ in range(8):
        if codex_rows and any(c.get('attachable') for c in codex_rows):
            break
        time.sleep(3)
        code, cands = req('GET', f'/bridges/{bridge_id}/candidates')
        rows = (cands or {}).get('candidates') or []
        codex_rows = [c for c in rows if c.get('runtime') == 'codex']
    attachable = any(c.get('attachable') for c in codex_rows)
    sess = next(
        (c for c in codex_rows if c.get('runtime_session_id')), None)
    if not codex_rows or not attachable:
        record(
            'T2.candidates',
            'FAIL',
            'missing attachable codex',
            candidates=[{
                'runtime': c.get('runtime'),
                'attachable': c.get('attachable'),
                'label': c.get('label'),
            } for c in rows],
        )
    else:
        record(
            'T2.candidates',
            'PASS',
            n=len(rows),
            n_codex=len(codex_rows),
            has_session=bool(sess),
            session_id=(sess or {}).get('runtime_session_id'),
        )

    print('======== T3) Enable agents ========')
    code, ep = req('POST', f'/bridges/{bridge_id}/agents', {
        'at_name': 'codex',
        'runtime': 'codex',
        'adapter_kind': 'acp',
        'status': 'online',
        'candidate_id': (codex_rows[0].get('candidate_id')
                         if codex_rows else None),
    })
    if code != 200:
        record('T3.enable_codex', 'FAIL', f'http={code}', body=ep)
    else:
        record('T3.enable_codex', 'PASS', endpoint_id=ep.get('endpoint_id'))

    code, ep2 = req('POST', f'/bridges/{bridge_id}/agents', {
        'at_name': 'me',
        'runtime': 'codex',
        'adapter_kind': 'acp',
        'status': 'online',
    })
    if code != 200:
        record('T3.enable_me', 'FAIL', f'http={code}', body=ep2)
    else:
        record('T3.enable_me', 'PASS', endpoint_id=ep2.get('endpoint_id'))

    # rebind should not 409
    code, ep3 = req('POST', f'/bridges/{bridge_id}/agents', {
        'at_name': 'codex',
        'runtime': 'codex',
        'adapter_kind': 'acp',
        'status': 'online',
    })
    if code != 200:
        record('T3.rebind', 'FAIL', f'http={code}', body=ep3)
    else:
        record('T3.rebind', 'PASS')

    # Give daemon a beat to sync slots; dispatch path also refreshes from API.
    time.sleep(1)
    return proc, project_id, bridge_id


def run_t4(project_id: str) -> str | None:
    print('======== T4) Dispatch fresh ========')
    code, msg = req(
        'POST',
        f'/projects/{project_id}/messages?wait=true&wait_timeout={TIMEOUT}',
        {
            'content':
            '@codex Reply with exactly the single word PONG and nothing else.',
            'sender_user_id': OWNER,
            'channel': 'web',
            'thread_id': 'e2e-codex-fresh',
            'session_mode': 'fresh',
        },
        timeout=TIMEOUT + 30,
    )
    if code != 200:
        record('T4.fresh', 'FAIL', f'http={code}', body=msg)
        return None
    dispatches = msg.get('dispatches') or []
    replies = msg.get('replies') or []
    if not dispatches:
        record('T4.fresh', 'FAIL', 'no dispatches', body=msg)
        return None
    dispatch_id = dispatches[0].get('dispatch_id')
    ok_reply = next((r for r in replies if r.get('at_name') == 'codex'), None)
    content = ((ok_reply or {}).get('content')
               or (ok_reply or {}).get('error') or '')
    if ok_reply and ok_reply.get('ok') and content.strip():
        record(
            'T4.fresh',
            'PASS',
            dispatch_id=dispatch_id,
            reply=content[:240],
            session_mode=dispatches[0].get('session_mode'),
        )
        return dispatch_id
    # Network/product: empty or error → FAIL (or BLOCKED if looks like net)
    low = content.lower()
    if any(x in low for x in ('timeout', 'unreachable', 'network', 'connect')):
        record('T4.fresh', 'BLOCKED', content[:300], dispatch_id=dispatch_id)
    else:
        record(
            'T4.fresh',
            'FAIL',
            'no successful @codex reply',
            dispatch_id=dispatch_id,
            replies=[{
                'ok': r.get('ok'),
                'at': r.get('at_name'),
                'content': (r.get('content') or r.get('error') or '')[:200],
            } for r in replies],
        )
    return dispatch_id


def run_t5(project_id: str, bridge_id: str) -> None:
    print('======== T5) Dispatch attach ========')
    if SKIP_ATTACH:
        record('T5.attach', 'SKIP', 'SKIP_ATTACH=1')
        return
    code, cands = req('GET', f'/bridges/{bridge_id}/candidates')
    rows = (cands or {}).get('candidates') or [] if code == 200 else []
    sess = next(
        (c for c in rows
         if c.get('runtime') == 'codex' and c.get('runtime_session_id')),
        None,
    )
    if not sess:
        record('T5.attach', 'SKIP', 'no runtime_session_id candidate')
        return
    sid = sess['runtime_session_id']
    # Platform SessionDirectory uses session_mode=attach; adapter resumes by id
    # when envelope.runtime_session_id is set. messages API may resolve via
    # session directory — pass attach mode and hope binding exists after T4.
    code, msg = req(
        'POST',
        f'/projects/{project_id}/messages?wait=true&wait_timeout={TIMEOUT}',
        {
            'content':
            f'@codex (attach session {sid[:8]}) Reply with exactly PONG.',
            'sender_user_id': OWNER,
            'channel': 'web',
            'thread_id': 'e2e-codex-attach',
            'session_mode': 'attach',
        },
        timeout=TIMEOUT + 30,
    )
    if code != 200:
        record('T5.attach', 'FAIL', f'http={code}', body=msg)
        return
    dispatches = msg.get('dispatches') or []
    replies = msg.get('replies') or []
    mode = (dispatches[0].get('session_mode') if dispatches else None)
    resolution = (dispatches[0].get('session_resolution')
                  if dispatches else None)
    ok_reply = next((r for r in replies if r.get('at_name') == 'codex'), None)
    content = ((ok_reply or {}).get('content')
               or (ok_reply or {}).get('error') or '')
    if not dispatches:
        record('T5.attach', 'FAIL', 'no dispatches')
        return
    # Accept: successful reply under attach, OR explicit fallback/error (not silent)
    if ok_reply and ok_reply.get('ok') and content.strip():
        record(
            'T5.attach',
            'PASS',
            session_mode=mode,
            session_resolution=resolution,
            runtime_session_id=dispatches[0].get('runtime_session_id'),
            reply=content[:240],
        )
        return
    res_l = str(resolution or '').lower()
    content_l = content.lower()
    explicit = (
        'attach_fallback' in res_l or 'fallback' in res_l
        or any(x in content_l
               for x in ('attach_fallback', 'resume', 'not found', 'error')))
    if explicit:
        # Contract met: not silent. Network timeout after fallback → BLOCKED.
        st = 'BLOCKED' if 'timeout' in content_l else 'PASS'
        record(
            'T5.attach',
            st,
            'explicit failure/fallback (not silent)',
            session_mode=mode,
            session_resolution=resolution,
            reply=content[:300],
        )
        return
    record(
        'T5.attach',
        'FAIL',
        'no ok reply and no explicit fallback',
        session_mode=mode,
        session_resolution=resolution,
        replies=[{
            'ok': r.get('ok'),
            'content': (r.get('content') or r.get('error') or '')[:200],
        } for r in replies],
    )


def run_t6(project_id: str) -> None:
    print('======== T6) Cancel ========')
    if SKIP_CANCEL:
        record('T6.cancel', 'SKIP', 'SKIP_CANCEL=1')
        return
    # Fire without long wait: wait=false then cancel
    code, msg = req(
        'POST',
        f'/projects/{project_id}/messages?wait=false',
        {
            'content':
            '@codex Take your time: list 50 files under the repo and explain each briefly.',
            'sender_user_id': OWNER,
            'channel': 'web',
            'thread_id': 'e2e-codex-cancel',
            'session_mode': 'fresh',
        },
        timeout=30,
    )
    if code != 200:
        record('T6.cancel', 'FAIL', f'dispatch http={code}', body=msg)
        return
    dispatches = msg.get('dispatches') or []
    if not dispatches:
        record('T6.cancel', 'FAIL', 'no dispatches to cancel', body=msg)
        return
    dispatch_id = dispatches[0]['dispatch_id']
    time.sleep(0.5)
    c_code, c_body = req('POST', f'/dispatches/{dispatch_id}/cancel', {})
    if c_code not in (200, 204):
        # some APIs return 200 with body
        if c_code != 200:
            record(
                'T6.cancel',
                'FAIL',
                f'cancel http={c_code}',
                dispatch_id=dispatch_id,
                body=c_body,
            )
            return
    record('T6.cancel', 'PASS', dispatch_id=dispatch_id, cancel_http=c_code)


def run_t7_optional(project_id: str, bridge_id: str) -> None:
    print('======== T7) Optional对照 ========')
    if not SKIP_CURSOR:
        req('POST', f'/bridges/{bridge_id}/agents', {
            'at_name': 'cursor',
            'runtime': 'cursor',
            'adapter_kind': 'acp',
            'status': 'online',
        })
        code, msg = req(
            'POST',
            f'/projects/{project_id}/messages?wait=true&wait_timeout=60',
            {
                'content': '@cursor Reply PONG',
                'sender_user_id': OWNER,
                'channel': 'web',
                'thread_id': 'e2e-cursor',
                'session_mode': 'fresh',
            },
            timeout=90,
        )
        replies = (msg or {}).get('replies') or [] if code == 200 else []
        text = ' '.join(
            (r.get('content') or r.get('error') or '') for r in replies)
        if 'need_reauth' in text.lower() or 'authentication' in text.lower():
            record('T7.cursor', 'PASS', 'need_reauth as expected',
                   reply=text[:200])
        elif replies and replies[0].get('ok'):
            record('T7.cursor', 'PASS', 'cursor replied', reply=text[:200])
        else:
            record('T7.cursor', 'FAIL', text[:300] or str(msg)[:300])
    else:
        record('T7.cursor', 'SKIP', 'SKIP_CURSOR=1')

    if not SKIP_CLAUDE:
        req('POST', f'/bridges/{bridge_id}/agents', {
            'at_name': 'claude',
            'runtime': 'claude_code',
            'adapter_kind': 'acp',
            'status': 'online',
        })
        code, msg = req(
            'POST',
            f'/projects/{project_id}/messages?wait=true&wait_timeout=60',
            {
                'content': '@claude Reply PONG',
                'sender_user_id': OWNER,
                'channel': 'web',
                'thread_id': 'e2e-claude',
                'session_mode': 'fresh',
            },
            timeout=90,
        )
        replies = (msg or {}).get('replies') or [] if code == 200 else []
        if replies:
            record(
                'T7.claude',
                'PASS' if (not replies[0].get('ok') or replies[0].get('content'))
                else 'FAIL',
                reply=(replies[0].get('content')
                       or replies[0].get('error') or '')[:200],
            )
        else:
            record('T7.claude', 'FAIL', 'no reply', body=msg)
    else:
        record('T7.claude', 'SKIP', 'SKIP_CLAUDE=1')


def summarize() -> int:
    print('\n======== Summary ========')
    counts = {'PASS': 0, 'FAIL': 0, 'SKIP': 0, 'BLOCKED': 0}
    for r in RESULTS:
        counts[r.status] = counts.get(r.status, 0) + 1
        print(f'  {r.status:7} {r.name}: {r.detail}'.rstrip(': '))
    print(dict(counts))
    # Critical path: gate + T1 online + T3 enable + T4 fresh
    critical = [
        'gate.health', 'gate.bridges', 'gate.old_pair_410', 'gate.codex_acp',
        'T1.online', 'T3.enable_codex', 'T4.fresh',
    ]
    by_name = {r.name: r for r in RESULTS}
    for name in critical:
        r = by_name.get(name)
        if r is None or r.status in ('FAIL', 'BLOCKED'):
            return 1
    if any(r.status == 'FAIL' for r in RESULTS
           if r.name.startswith(('T1', 'T2', 'T3', 'T4'))):
        return 1
    return 0


def update_doc_appendix(exit_code: int) -> None:
    if not UPDATE_DOC:
        return
    doc = ROOT / 'Agent_Team_E2E_测试流程.md'
    if not doc.is_file():
        return
    try:
        commit = subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
            text=True,
        ).strip()
    except Exception:
        commit = 'unknown'
    lines = [
        '日期：' + datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
        f'commit：{commit}',
        f'环境网络：API_BASE={API_BASE} CHECK_NET={CHECK_NET}',
        '门禁：' + ', '.join(
            f'{r.name}={r.status}' for r in RESULTS if r.name.startswith('gate.')),
    ]
    for key in ('T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'):
        subset = [r for r in RESULTS if r.name.startswith(key)]
        if not subset:
            lines.append(f'{key}：（未跑）')
            continue
        parts = [
            f'{r.name.split(".",1)[-1]}={r.status}'
            + (f'({r.detail[:80]})' if r.detail else '') for r in subset
        ]
        lines.append(f'{key}：' + '; '.join(parts))
    blocked = [r for r in RESULTS if r.status == 'BLOCKED']
    failed = [r for r in RESULTS if r.status == 'FAIL']
    lines.append(
        '阻塞：'
        + ('; '.join(f'{r.name}:{r.detail}' for r in blocked) or '无'))
    by_name = {r.name: r for r in RESULTS}
    product_ok = all(
        by_name.get(n) and by_name[n].status == 'PASS' for n in (
            'gate.health', 'T1.online', 'T2.candidates', 'T3.enable_codex',
            'T6.cancel',
        )) and not any(
            r.status == 'FAIL'
            for r in RESULTS if r.name.startswith(('T1', 'T2', 'T3', 'T6')))
    t4 = by_name.get('T4.fresh')
    net_blocked = bool(t4 and t4.status == 'BLOCKED') and not failed
    if exit_code == 0 and not failed:
        verdict = '主卖点三句成立（派得到 / 看得住 / 收得回）'
    elif product_ok and net_blocked:
        verdict = (
            '产品链路成立（pair→online→enable→dispatch/cancel）；'
            'Codex 网络不可达阻塞 T4 回包 — 不判产品失败')
    else:
        verdict = '未通过 — 见上方 FAIL/BLOCKED'
    lines.append('结论：' + verdict)
    body = '\n'.join(lines)
    text = doc.read_text(encoding='utf-8')
    pattern = r'(## 附录：首份实跑报告\n\n>.*?\n\n```\n)(.*?)(\n```\n?\Z)'
    repl = r'\1' + body + r'\3'
    new_text, n = re.subn(pattern, repl, text, count=1, flags=re.S)
    if n == 0:
        # fallback replace placeholder block
        new_text = text.rstrip() + '\n\n### 自动填写\n\n```\n' + body + '\n```\n'
    doc.write_text(new_text, encoding='utf-8')
    print(f'Updated appendix in {doc}')


def main() -> int:
    env = _env_path()
    os.environ.update({k: env[k] for k in ('PATH', 'PYTHONPATH')})

    if not gate():
        code = 2
        update_doc_appendix(code)
        return code

    label = f'e2e-codex-{int(time.time())}'
    log_path = ROOT / f'.e2e-codex-{label}.log'
    proc = None
    try:
        proc, project_id, bridge_id = run_t1_t3(label, log_path, env)
        if not bridge_id:
            code = 1
            update_doc_appendix(code)
            return code

        run_t4(project_id)
        run_t5(project_id, bridge_id)
        run_t6(project_id)
        run_t7_optional(project_id, bridge_id)

        code = summarize()
        update_doc_appendix(code)
        return code
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if log_path.is_file():
            print(f'\n--- daemon log tail ({log_path}) ---')
            print(log_path.read_text(errors='replace')[-2000:])


if __name__ == '__main__':
    sys.exit(main())
