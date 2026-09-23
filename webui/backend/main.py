# Copyright (c) Alibaba, Inc. and its affiliates.
"""
MS-Agent Web UI Backend Server
Provides REST API and WebSocket endpoints for the ms-agent framework.
"""
import os
import sys
import uvicorn
from api import router as api_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from websocket_handler import router as ws_router

# Add ms-agent to path
MS_AGENT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'ms-agent'))
if MS_AGENT_PATH not in sys.path:
    sys.path.insert(0, MS_AGENT_PATH)

app = FastAPI(
    title='MS-Agent Web UI',
    description='Web interface for the MS-Agent framework',
    version='1.0.0')

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Include API and WebSocket routers
app.include_router(api_router, prefix='/api')
app.include_router(ws_router, prefix='/ws')

# Agent Team control plane (same routers as ms-agent-webui). The product UI
# for /team still lives in ms-agent-webui; this exposes the API if this
# server is the one a Team UI is pointed at.
_TEAM_HTTP = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TEAM_HTTP, '..', '..'))
for _p in (_REPO_ROOT, _TEAM_HTTP):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if os.path.isdir(os.path.join(_TEAM_HTTP, 'team')):
    from team import router as team_router
    app.include_router(team_router)


@app.get('/team')
async def team_product_entry():
    """This repo's SPA has no /team. Point people at the product UI."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        '''<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><title>Agent Team</title>
<style>
body{font:14px/1.5 system-ui,sans-serif;max-width:40rem;margin:12vh auto;padding:0 1.5rem;color:#1f1f1f}
code{background:#f4f4f5;padding:.1rem .35rem;border-radius:4px}
a{color:#1677ff}
</style></head><body>
<h1>编排台不在这个 WebUI</h1>
<p>本仓库自带页面没有舰队、看板和配对。用户入口是
<strong>ms-agent-webui</strong> 的 <code>/team</code>。</p>
<p>如果编排台已经指向这台服务，控制面 API 在
<code>/api/v1/team</code>（健康检查 <a href="/health">/health</a>）。</p>
</body></html>''',
        headers={'Cache-Control': 'no-store'},
    )

# Serve static files in production
STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')
if os.path.exists(STATIC_DIR):
    app.mount(
        '/assets',
        StaticFiles(directory=os.path.join(STATIC_DIR, 'assets')),
        name='assets')

    @app.get('/{full_path:path}')
    async def serve_spa(full_path: str):
        """Serve the SPA for all non-API routes"""
        file_path = os.path.join(STATIC_DIR, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, 'index.html'))


@app.get('/health')
async def health_check():
    """Health check endpoint"""
    return {'status': 'healthy', 'service': 'ms-agent-webui'}


def main():
    """Start the server"""
    import argparse
    parser = argparse.ArgumentParser(description='MS-Agent Web UI Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind')
    parser.add_argument('--port', type=int, default=7860, help='Port to bind')
    parser.add_argument(
        '--reload', action='store_true', help='Enable auto-reload')
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print('  MS-Agent Web UI Server')
    print(f"{'='*60}")
    print(f'  Server running at: http://{args.host}:{args.port}')
    print(f'  API documentation: http://{args.host}:{args.port}/docs')
    print(f"{'='*60}\n")

    uvicorn.run('main:app', host=args.host, port=args.port, reload=args.reload)


if __name__ == '__main__':
    main()
