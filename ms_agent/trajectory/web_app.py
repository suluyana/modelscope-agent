# Copyright (c) ModelScope Contributors. All rights reserved.
"""Browser dashboard (stdlib ``http.server`` only). Neon dark UI, polls JSON tail.

Run::

  python -m ms_agent.trajectory serve 8765 /path/to/output_dir

Open http://127.0.0.1:8765/
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse


def _pick_jsonl(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        traj = path / 'trajectories'
        if traj.is_dir():
            files = sorted(
                traj.glob('*.jsonl'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                return files[0]
        files = sorted(
            path.glob('*.jsonl'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            return files[0]
    raise SystemExit(f'No trajectory jsonl found under {path}')


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Trajectory Live</title>
<style>
  :root { --bg:#0d1117; --fg:#e6edf3; --acc:#58a6ff; --ok:#3fb950; --bd:#30363d; }
  body { font-family: ui-sans-serif, system-ui; background: var(--bg); color: var(--fg);
         margin:0; min-height:100vh; }
  h1 { text-align:center; color: var(--acc); font-weight:600; letter-spacing:.04em;
       text-shadow:0 0 24px rgba(88,166,255,.35); padding:1rem; }
  #meta { text-align:center; color:#8b949e; font-size:.85rem; margin-bottom:.5rem; }
  table { width:96%; margin:0 auto; border-collapse:collapse; font-size:.8rem; }
  th { text-align:left; color:#79c0ff; border-bottom:2px solid var(--ok); padding:.5rem; }
  td { border-bottom:1px solid var(--bd); padding:.45rem .5rem; vertical-align:top; }
  tr:hover { background:#161b22; }
  .kind { color:#d2a8ff; font-weight:600; }
  .tool { color:#ffa657; }
  .ts { color:#8b949e; white-space:nowrap; }
  .glow { box-shadow:0 0 40px rgba(35,134,54,.12); border-radius:8px; overflow:hidden;
          border:1px solid #238636; margin:1rem auto; max-width:1200px; }
</style>
</head>
<body>
<h1>Trajectory Live</h1>
<div id="meta"></div>
<div class="glow"><table><thead><tr>
  <th class="ts">time</th><th>kind</th><th>tool</th><th>detail</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<script>
const rows = document.getElementById('rows');
const meta = document.getElementById('meta');
let offset = 0;
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;');
}
async function poll() {
  const r = await fetch('/api/tail?offset=' + offset);
  const j = await r.json();
  offset = j.next_offset;
  meta.textContent = j.file + ' · +' + j.added + ' new · total lines ' + j.total_rows;
  for (const ev of j.events) {
    const tr = document.createElement('tr');
    if (ev._row) {
      const [ts, kind, tool, det] = ev._row;
      tr.innerHTML = '<td class="ts">' + esc(ts) + '</td><td class="kind">' + esc(kind)
        + '</td><td class="tool">' + esc(tool) + '</td><td>' + esc(det) + '</td>';
    } else {
      tr.innerHTML = '<td colspan="4"><pre style="margin:0;white-space:pre-wrap;">'
        + esc(JSON.stringify(ev)) + '</pre></td>';
    }
    rows.appendChild(tr);
  }
  while (rows.children.length > 400) rows.removeChild(rows.firstChild);
  window.scrollTo(0, document.body.scrollHeight);
}
setInterval(poll, 400);
poll();
</script>
</body>
</html>"""


def _row_from_obj(obj: Dict[str, Any]) -> list[str]:
    if obj.get('type') == 'header':
        return ['—', 'header', '—', f"run {obj.get('run_id', '')}"]
    if obj.get('type') == 'footer':
        return ['—', 'footer', '—', f"n={obj.get('total_events')}"]
    if 'kind' in obj:
        ts = str(obj.get('ts', ''))[:19]
        kind = str(obj.get('kind', ''))
        tool = str(obj.get('tool_name') or '—')
        data = obj.get('data') or {}
        hook = data.get('hook_event_name', '')
        fw = data.get('framework', '')
        det = hook or fw or json.dumps(data, ensure_ascii=False)[:120]
        return [ts, kind, tool, det]
    return ['?', '?', '?', json.dumps(obj, ensure_ascii=False)[:80]]


def run_web(host: str, port: int, watch: Path) -> None:
    jsonl_path = _pick_jsonl(watch).resolve()

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(_PAGE.encode('utf-8'))
                return
            if parsed.path == '/api/tail':
                qs = parse_qs(parsed.query or '')
                try:
                    off = int(qs.get('offset', ['0'])[0])
                except ValueError:
                    off = 0
                events_out: list[dict] = []
                added = 0
                try:
                    with open(jsonl_path, 'r', encoding='utf-8') as f:
                        f.seek(off)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(obj, dict):
                                events_out.append({
                                    '_row': _row_from_obj(obj),
                                })
                                added += 1
                        next_off = f.tell()
                    total_rows = sum(
                        1
                        for _ in open(
                            jsonl_path, 'r', encoding='utf-8', errors='ignore'
                        )
                    )
                except OSError:
                    next_off = off
                    total_rows = 0
                body = json.dumps(
                    {
                        'file': str(jsonl_path),
                        'events': events_out,
                        'added': added,
                        'next_offset': next_off,
                        'total_rows': total_rows,
                    },
                    ensure_ascii=False,
                ).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    server = HTTPServer((host, port), H)
    print(f'Trajectory web UI  http://{host}:{port}/  watching\n  {jsonl_path}')
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description='Trajectory browser UI (stdlib)')
    p.add_argument('port', type=int, nargs='?', default=8765)
    p.add_argument(
        'path',
        nargs='?',
        default='.',
        help='output directory or jsonl',
    )
    p.add_argument('--host', default='127.0.0.1')
    args = p.parse_args(argv)
    run_web(args.host, args.port, Path(args.path))
