#!/usr/bin/env python3
# Copyright (c) ModelScope Contributors. All rights reserved.
"""Smoke-run Terminal-Bench with Harbor agent_import_path -> MsAgentTerminalBenchAgent.

Requires sibling ``../evalscope`` on ``PYTHONPATH`` (PyPI evalscope may lack
``agent_import_path`` / ``dataset_version`` extra_params).

Typical local run::

    pip install 'harbor==0.1.28'
    python scripts/run_terminal_bench_ms_agent_smoke.py

TB 2.1 (local registry)::

    export TERMINAL_BENCH_VERSION=2.1
    export TERMINAL_BENCH_REGISTRY_PATH=datasets/terminal-bench-2.1-registry.json
    export TERMINAL_BENCH_MODEL=qwen3.6-plus
    python scripts/run_terminal_bench_ms_agent_smoke.py

Optional: ``TERMINAL_BENCH_TASK_NAMES=fix-git`` to pin the smoke task.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _evalscope_src() -> Path:
    return _repo_root().parent / 'evalscope'


def _bootstrap_evalscope() -> None:
    evalscope_src = _evalscope_src()
    if not (evalscope_src / 'evalscope').is_dir():
        raise SystemExit(
            f'EvalScope repo not found at {evalscope_src}. '
            'Clone it next to modelscope-agent or set PYTHONPATH.'
        )
    sys.path.insert(0, str(evalscope_src))
    import evalscope.models.model_apis  # noqa: F401
    import evalscope.evaluator  # noqa: F401
    from evalscope.filters import extraction as _extraction  # noqa: F401
    from evalscope.filters import selection as _selection  # noqa: F401
    from evalscope.metrics import metric as _metric  # noqa: F401
    import evalscope.benchmarks.terminal_bench.terminal_bench_adapter  # noqa: F401


def main() -> None:
    repo_root = _repo_root()
    _bootstrap_evalscope()

    from dotenv import load_dotenv

    from evalscope import TaskConfig, run_task
    from evalscope.constants import EvalType

    load_dotenv(repo_root / '.env', override=False)

    os.environ.setdefault('MS_AGENT_SOURCE_ROOT', str(repo_root))

    api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise SystemExit(
            'Set DASHSCOPE_API_KEY or OPENAI_API_KEY in .env (or export in shell).'
        )

    if os.getenv('DASHSCOPE_API_KEY'):
        api_base = os.getenv(
            'DASHSCOPE_API_BASE',
            'https://dashscope.aliyuncs.com/compatible-mode/v1',
        )
        default_model = 'qwen3.6-plus'
    else:
        api_base = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        default_model = 'gpt-4.1-mini'

    model = os.getenv('TERMINAL_BENCH_MODEL', default_model)

    tb_extra = {
        'environment_type': 'docker',
        'agent_import_path': (
            'ms_agent.benchmark.harbor_terminal_bench_agent:'
            'MsAgentTerminalBenchAgent'
        ),
        'timeout_multiplier': float(
            os.getenv('TERMINAL_BENCH_TIMEOUT_MULTIPLIER', '10')
        ),
        'max_turns': int(os.getenv('TERMINAL_BENCH_MAX_TURNS', '80')),
    }

    _keep_raw = os.getenv('TERMINAL_BENCH_KEEP_DOCKER_IMAGE', '1').strip().lower()
    if _keep_raw in ('0', 'false', 'no'):
        tb_extra['environment_delete'] = True
    else:
        tb_extra['environment_delete'] = False

    _darwin_arm = platform.system() == 'Darwin' and platform.machine() == 'arm64'
    _force_raw = os.getenv('TERMINAL_BENCH_FORCE_BUILD', '').strip().lower()
    if _force_raw in ('1', 'true', 'yes'):
        tb_extra['environment_force_build'] = True
    elif _force_raw in ('0', 'false', 'no'):
        tb_extra['environment_force_build'] = False
    elif _darwin_arm:
        tb_extra['environment_force_build'] = True

    _tn = os.getenv('TERMINAL_BENCH_TASK_NAMES', '').strip()
    if _tn:
        tb_extra['task_names'] = [p.strip() for p in _tn.split(',') if p.strip()]
    elif _darwin_arm:
        tb_extra['task_names'] = ['adaptive-rejection-sampler']
    _ex = os.getenv('TERMINAL_BENCH_EXCLUDE_TASK_NAMES', '').strip()
    if _ex:
        tb_extra['exclude_task_names'] = [
            p.strip() for p in _ex.split(',') if p.strip()
        ]

    _tb_ver = os.getenv('TERMINAL_BENCH_VERSION', '').strip()
    if _tb_ver:
        tb_extra['dataset_version'] = _tb_ver
    _reg = os.getenv('TERMINAL_BENCH_REGISTRY_PATH', '').strip()
    if _reg:
        reg_path = Path(_reg)
        if not reg_path.is_absolute():
            reg_path = repo_root / reg_path
        tb_extra['registry_path'] = str(reg_path.resolve())

    work_root = os.getenv(
        'EVALSCOPE_WORK_DIR',
        str(repo_root / 'outputs' / 'terminal_bench_ms_agent_smoke'),
    )
    no_ts = os.getenv('EVALSCOPE_NO_TIMESTAMP', '').strip().lower() in (
        '1',
        'true',
        'yes',
    )

    task_cfg = TaskConfig(
        model=model,
        model_id=f'ms-agent__{model}'.replace('/', '__'),
        api_url=api_base,
        api_key=api_key,
        eval_type=EvalType.OPENAI_API,
        datasets=['terminal_bench_v2'],
        dataset_args={
            'terminal_bench_v2': {
                'extra_params': tb_extra,
            },
        },
        eval_batch_size=int(os.getenv('TERMINAL_BENCH_EVAL_BATCH_SIZE', '1')),
        limit=int(os.getenv('TERMINAL_BENCH_LIMIT', '1')),
        work_dir=work_root,
        no_timestamp=no_ts,
        generation_config={
            'temperature': 0.2,
        },
    )
    run_task(task_cfg=task_cfg)


if __name__ == '__main__':
    main()
