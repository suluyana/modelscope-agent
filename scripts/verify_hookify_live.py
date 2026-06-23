#!/usr/bin/env python3
"""Live verification: installed hookify plugin blocks dangerous shell commands."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from omegaconf import OmegaConf

from ms_agent.config.resolver import ConfigResolver
from ms_agent.hooks.factory import build_hook_runtime
from ms_agent.plugins.runtime import PluginRuntime


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--project',
        default=os.environ.get('HOOKIFY_E2E_PROJECT', os.getcwd()),
        help='Project workspace containing .claude hookify rules',
    )
    parser.add_argument(
        '--config',
        default=str(Path(__file__).resolve().parent / 'hookify_e2e' / 'agent.yaml'),
        help='Agent config with hooks.enabled_sources including plugin',
    )
    args = parser.parse_args()

    project_path = str(Path(args.project).resolve())
    cfg = OmegaConf.load(args.config)
    cfg.local_dir = project_path
    cfg = ConfigResolver().resolve(cfg, project_path=project_path)

    runtime = PluginRuntime(
        global_root=Path(os.environ.get('MS_AGENT_HOME', '~/.ms_agent')).expanduser(),
    )
    runtime.start_sync(project_path, 'hookify-live-verify', config=cfg)
    hook_runtime = build_hook_runtime(
        cfg,
        session_id='hookify-live-verify',
        plugin_hook_registries=runtime.load_result.hook_registries,
    )

    if hook_runtime.is_empty:
        print('FAIL: hook runtime is empty — is hookify installed and enabled?')
        return 1

    pretooluse = hook_runtime.registry.get_handlers('PreToolUse')
    if not pretooluse:
        print('FAIL: no PreToolUse handlers registered')
        return 1
    print(f'OK: {len(pretooluse)} PreToolUse handler(s) from plugins')

    safe_result, _ = await hook_runtime.run_pre_tool_use(
        'code_executor---shell_executor',
        {'command': 'echo hello'},
    )
    print(f'Safe command action: {safe_result.action}')

    block_result, attachments = await hook_runtime.run_pre_tool_use(
        'code_executor---shell_executor',
        {'command': 'rm -rf /tmp/important'},
    )
    print(f'Dangerous command action: {block_result.action}')
    if block_result.reason:
        print(f'Reason: {block_result.reason[:200]}')
    if attachments:
        print(f'Additional context: {attachments[0].content[:200]}')

    if block_result.action != 'deny':
        print('FAIL: hookify did not block dangerous rm -rf command')
        return 1

    print('PASS: hookify PreToolUse hook blocked dangerous rm -rf')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
