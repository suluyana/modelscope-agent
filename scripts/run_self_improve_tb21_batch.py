#!/usr/bin/env python3
"""Batch self-improve evaluation on Terminal-Bench 2.1 (fast_local).

Supports both sequential and parallel execution. For parallel auto/assist mode,
use workspace isolation to avoid concurrent code-write conflicts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ms_agent.self_improve.adapters.terminal_bench_fast_local import (
    TerminalBenchFastLocalAdapter,
)
from ms_agent.self_improve.adapters.terminal_bench_evalscope import (
    TerminalBenchEvalScopeAdapter,
)
from ms_agent.self_improve.adapters.terminal_bench_remote_docker import (
    TerminalBenchRemoteDockerAdapter,
)
from ms_agent.self_improve.cluster_builder import build_known_clusters_from_root
from ms_agent.self_improve.orchestrator import SelfImproveOrchestrator

PILOT_TASKS = [
    "fix-git",
    "bn-fit-modify",
    "distribution-search",
    "break-filter-js-from-html",
    "filter-js-from-html",
]

WORKSPACE_DIRS = ("ms_agent", "scripts", "requirements")
WORKSPACE_FILES = (
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "ruff.toml",
    "README.md",
    "README_ZH.md",
)
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bench_root() -> Path:
    raw = os.environ.get("BENCH_LOCAL_ROOT", "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = _repo_root() / p
        return p.resolve()
    for candidate in ("bench_local_v21", "bench_local"):
        p = (_repo_root() / candidate).resolve()
        if p.is_dir():
            return p
    return (_repo_root() / "bench_local").resolve()


def _discover_tasks() -> list[str]:
    root = _bench_root()
    if not root.is_dir():
        raise SystemExit(f"BENCH_LOCAL_ROOT not found: {root}")
    names: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        if path.name.startswith("_"):
            continue
        if (path / "instruction.md").is_file() and (
            path / "tests" / "test_outputs.py"
        ).is_file():
            names.append(path.name)
    return names


def _validate_task_names(task_names: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for task_name in task_names:
        if not TASK_NAME_RE.fullmatch(task_name):
            raise SystemExit(f"Invalid task name: {task_name!r}")
        if task_name in seen:
            continue
        seen.add(task_name)
        selected.append(task_name)
    return selected


def _load_capability_clusters(path: str) -> dict:
    if not path:
        return {}
    cluster_path = Path(path)
    if not cluster_path.is_absolute():
        cluster_path = _repo_root() / cluster_path
    with cluster_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("--capability-clusters-json must contain a JSON object")
    return data


def _resolve_capability_clusters(args) -> dict:
    clusters: dict = {}
    if args.auto_build_capability_clusters:
        ledger_root = Path(args.capability_ledger_root)
        if not ledger_root.is_absolute():
            ledger_root = _repo_root() / ledger_root
        clusters = build_known_clusters_from_root(
            ledger_root,
            min_cluster_size=args.capability_min_cluster_size,
            repo_root=_repo_root(),
        )
        print(
            f"[batch] auto-built {len(clusters)} capability clusters from {ledger_root}"
        )
    explicit = _load_capability_clusters(args.capability_clusters_json)
    if explicit:
        clusters.update(explicit)
    return clusters


def _si_config(
    mode: str,
    llm: str,
    max_iterations: int,
    decision_mode: str,
    rule_high_confidence: float,
    llm_min_confidence: float,
    disagreement_delta: float,
    capability_min_cluster_size: int,
    allow_single_case_capability_repair: bool,
    known_capability_clusters: dict,
) -> dict:
    return {
        "mode": mode,
        "llm": {
            "model": llm,
            "service": "dashscope" if "qwen" in llm.lower() else None,
            "dashscope_base_url": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
                if "qwen" in llm.lower()
                else None
            ),
            "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        },
        "loop": {"max_iterations": max_iterations},
        "logging": {"root_dir": "outputs/self_improve_tb21"},
        "capability": {
            "enabled": True,
            "min_cluster_size": capability_min_cluster_size,
            "allow_single_case_framework_gap": allow_single_case_capability_repair,
            "known_clusters": known_capability_clusters,
        },
        "decision": {
            "mode": decision_mode,
            "classifier": {
                "min_confidence": 0.75,
                "low_confidence_floor": 0.55,
            },
            "thresholds": {
                "rule_high_confidence": rule_high_confidence,
                "llm_min_confidence": llm_min_confidence,
                "disagreement_delta": disagreement_delta,
            },
            "fallback": {
                "on_low_confidence": "switch_to_assist",
                "on_rule_llm_disagreement": "ask_human",
            },
        },
        "scope": {
            "file_write_guard": {
                "include_paths": ["ms_agent/", "scripts/"],
                "exclude_paths": [
                    "bench_local/",
                    "bench_local_v21/",
                    "outputs/",
                    ".cache/",
                    ".venv/",
                ],
                "always_allowed_extensions": [".py", ".sh", ".json", ".yaml", ".md"],
                "never_allow_extensions": [".bin", ".exe"],
                "max_file_size_kb": 2048,
            }
        },
        "verify": {"patch_commands": ["python -m compileall -q ms_agent/self_improve scripts"]},
    }


def _copy_workspace(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".git",
        ".DS_Store",
        "._*",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "outputs",
        "bench_local",
        "bench_local_v21",
        "datasets",
        "terminal_bench_2.0_claude_code_qwen3.6plus",
        "terminal_bench_2.1_claude_code_qwen3.6plus",
        "terminal_bench_2.1_ms_agent_qwen3.6plus",
        ".venv",
        ".venv*",
        ".cache",
        ".colima",
        ".xdg-cache",
        "projects",
        "node_modules",
        "output",
        "output_video*",
        "build",
        "dist",
        "tmp",
        "logs",
    )
    dst.mkdir(parents=True, exist_ok=False)
    for dirname in WORKSPACE_DIRS:
        source_dir = src / dirname
        if source_dir.is_dir():
            shutil.copytree(
                source_dir,
                dst / dirname,
                ignore=ignore,
                ignore_dangling_symlinks=True,
            )
    for filename in WORKSPACE_FILES:
        source_file = src / filename
        if source_file.is_file():
            shutil.copy2(source_file, dst / filename)
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    subprocess.run(["git", "add", "."], cwd=dst, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=self-improve",
            "-c",
            "user.email=self-improve@example.invalid",
            "commit",
            "-q",
            "-m",
            "chore: initialize isolated workspace",
        ],
        cwd=dst,
        check=True,
    )


def _build_adapter(
    adapter_type: str,
    task_name: str,
    bench_base: str,
    evalscope_work_dir: str | None = None,
    remote_host: str | None = None,
    remote_repo_dir: str | None = None,
    regression_tasks: list[str] | None = None,
) -> TerminalBenchFastLocalAdapter | TerminalBenchEvalScopeAdapter | TerminalBenchRemoteDockerAdapter:
    if adapter_type == "remote_docker":
        return TerminalBenchRemoteDockerAdapter(
            task_name,
            remote_host=remote_host or "root@47.254.25.238",
            remote_repo_dir=remote_repo_dir or "/root/bench_workspace/modelscope-agent-si",
            regression_tasks=regression_tasks,
        )
    if adapter_type == "evalscope":
        work_dir = evalscope_work_dir
        if not work_dir:
            work_dir = str(
                _repo_root() / "outputs" / "terminal_bench_self_improve" / task_name
            )
        return TerminalBenchEvalScopeAdapter(task_name, work_dir=work_dir)
    return TerminalBenchFastLocalAdapter(task_name, output_dir_base=bench_base)


def _run_task_sequential(task_name: str, args: argparse.Namespace, bench_base: str) -> dict:
    run_id = f"tb21_{task_name}_{uuid.uuid4().hex[:8]}"
    config = _si_config(
        args.mode,
        args.llm,
        args.max_iterations,
        args.decision_mode,
        args.rule_high_confidence,
        args.llm_min_confidence,
        args.disagreement_delta,
        args.capability_min_cluster_size,
        args.allow_single_case_capability_repair,
        getattr(args, "resolved_capability_clusters", {})
        or _load_capability_clusters(args.capability_clusters_json),
    )
    regression = [t.strip() for t in args.regression_tasks.split(",") if t.strip()]
    adapter = _build_adapter(
        args.adapter,
        task_name,
        bench_base,
        args.evalscope_work_dir,
        remote_host=args.remote_host,
        remote_repo_dir=args.remote_repo_dir,
        regression_tasks=regression,
    )
    orchestrator = SelfImproveOrchestrator(run_id, adapter, config)
    try:
        orchestrator.run_loop()
        return {
            "task_name": task_name,
            "run_id": run_id,
            "status": "completed",
            "error": None,
        }
    except Exception as exc:
        return {
            "task_name": task_name,
            "run_id": run_id,
            "status": "error",
            "error": str(exc),
        }


def _run_task_parallel_subprocess(
    task_name: str,
    args: argparse.Namespace,
    bench_base: str,
    workspace_root: Path,
    log_dir: Path,
) -> dict:
    run_id = f"tb21_{task_name}_{uuid.uuid4().hex[:8]}"
    workspace = _repo_root()
    created_workspace = None

    if args.workspace_isolation == "copy":
        created_workspace = workspace_root / f"{task_name}_{uuid.uuid4().hex[:8]}"
        _copy_workspace(_repo_root(), created_workspace)
        workspace = created_workspace

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_name}_{run_id}.log"
    ledger_root = (_repo_root() / "outputs/self_improve_tb21").resolve()

    cmd = [
        sys.executable,
        "scripts/run_self_improve_tb.py",
        "--task-name",
        task_name,
        "--mode",
        args.mode,
        "--llm",
        args.llm,
        "--decision-mode",
        args.decision_mode,
        "--adapter",
        args.adapter,
        "--rule-high-confidence",
        str(args.rule_high_confidence),
        "--llm-min-confidence",
        str(args.llm_min_confidence),
        "--disagreement-delta",
        str(args.disagreement_delta),
        "--capability-min-cluster-size",
        str(args.capability_min_cluster_size),
        "--max-iterations",
        str(args.max_iterations),
        "--logging-root",
        str(ledger_root),
    ]
    if args.adapter == "evalscope":
        es_work_dir = args.evalscope_work_dir or str(
            _repo_root() / "outputs" / "terminal_bench_self_improve" / task_name
        )
        cmd.extend(["--work-dir", es_work_dir])
    if args.allow_single_case_capability_repair:
        cmd.append("--allow-single-case-capability-repair")
    cluster_json_path = getattr(args, "runtime_capability_clusters_json", "")
    if not cluster_json_path:
        cluster_json_path = args.capability_clusters_json
    if cluster_json_path:
        cluster_json = Path(cluster_json_path)
        if not cluster_json.is_absolute():
            cluster_json = _repo_root() / cluster_json
        cmd.extend(["--capability-clusters-json", str(cluster_json)])

    env = os.environ.copy()
    env["BENCH_LOCAL_ROOT"] = bench_base
    env["PYTHONPATH"] = f"{workspace}:{env.get('PYTHONPATH', '')}"
    env["MS_AGENT_SOURCE_ROOT"] = str(workspace)

    try:
        with log_path.open("w", encoding="utf-8") as f:
            proc = subprocess.run(
                cmd,
                cwd=str(workspace),
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        status = "completed" if proc.returncode == 0 else "error"
        err = None if proc.returncode == 0 else f"subprocess exit_code={proc.returncode}"
        return {
            "task_name": task_name,
            "run_id": run_id,
            "status": status,
            "error": err,
            "log_path": str(log_path),
            "workspace": str(workspace),
        }
    finally:
        if created_workspace and args.cleanup_workspaces:
            shutil.rmtree(created_workspace, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch self-improve on Terminal-Bench 2.1 fast_local"
    )
    parser.add_argument(
        "--mode",
        default="observe",
        choices=["observe", "assist", "auto"],
    )
    parser.add_argument("--llm", default="qwen-max")
    parser.add_argument(
        "--decision-mode",
        default="rule_only",
        choices=["rule_only", "hybrid"],
    )
    parser.add_argument("--rule-high-confidence", type=float, default=0.90)
    parser.add_argument("--llm-min-confidence", type=float, default=0.70)
    parser.add_argument("--disagreement-delta", type=float, default=0.25)
    parser.add_argument("--capability-min-cluster-size", type=int, default=2)
    parser.add_argument("--allow-single-case-capability-repair", action="store_true")
    parser.add_argument("--capability-clusters-json", default="")
    parser.add_argument(
        "--auto-build-capability-clusters",
        action="store_true",
        help="Build known_clusters from prior runledger.jsonl files before running tasks",
    )
    parser.add_argument(
        "--capability-ledger-root",
        default="outputs/self_improve_tb21",
        help="Ledger root searched when --auto-build-capability-clusters is enabled",
    )
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="Max tasks (0=all)")
    parser.add_argument(
        "--tasks",
        default="",
        help="Comma-separated task names (overrides --pilot/--limit)",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help=f"Run pilot set: {', '.join(PILOT_TASKS)}",
    )
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument(
        "--summary-out",
        default="outputs/self_improve_tb21/batch_summary.jsonl",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="Skip tasks already recorded as completed in summary-out",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=1,
        help="Number of tasks to run concurrently",
    )
    parser.add_argument(
        "--workspace-isolation",
        choices=["none", "copy"],
        default="none",
        help="Workspace isolation strategy for parallel runs",
    )
    parser.add_argument(
        "--workspace-root",
        default="outputs/self_improve_tb21/workspaces",
        help="Workspace root directory for isolation",
    )
    parser.add_argument(
        "--cleanup-workspaces",
        action="store_true",
        help="Remove copied workspaces after each task",
    )
    parser.add_argument(
        "--adapter",
        default="fast_local",
        choices=["fast_local", "evalscope", "remote_docker"],
        help="Execution adapter: fast_local (default), evalscope (local Docker), or remote_docker (SSH+Docker)",
    )
    parser.add_argument(
        "--evalscope-work-dir",
        default=None,
        help="EvalScope work directory (only used with --adapter evalscope)",
    )
    parser.add_argument(
        "--remote-host",
        default="root@47.254.25.238",
        help="Remote SSH host (only used with --adapter remote_docker)",
    )
    parser.add_argument(
        "--remote-repo-dir",
        default="/root/bench_workspace/modelscope-agent-si",
        help="Remote repo directory (only used with --adapter remote_docker)",
    )
    parser.add_argument(
        "--regression-tasks",
        default="fix-git,build-pmars,hf-model-inference,polyglot-c-py",
        help="Comma-separated regression tasks for remote verification",
    )
    args = parser.parse_args()

    all_tasks = _discover_tasks()
    if args.list_tasks:
        print(f"BENCH_LOCAL_ROOT={_bench_root()}")
        print(f"Tasks ({len(all_tasks)}):")
        for name in all_tasks:
            print(f"  - {name}")
        return

    if args.tasks.strip():
        selected = _validate_task_names(
            [t.strip() for t in args.tasks.split(",") if t.strip()]
        )
    elif args.pilot:
        selected = _validate_task_names([t for t in PILOT_TASKS if t in all_tasks])
        missing = [t for t in PILOT_TASKS if t not in all_tasks]
        if missing:
            print(f"[warn] pilot tasks not in bench root: {missing}", file=sys.stderr)
    else:
        selected = _validate_task_names(all_tasks)
        if args.limit > 0:
            selected = selected[: args.limit]

    if not selected:
        raise SystemExit("No tasks selected.")

    summary_path = _repo_root() / args.summary_out
    args.resolved_capability_clusters = _resolve_capability_clusters(args)
    args.runtime_capability_clusters_json = args.capability_clusters_json
    if args.resolved_capability_clusters and not args.capability_clusters_json:
        runtime_clusters_path = summary_path.parent / "capability_clusters.runtime.json"
        runtime_clusters_path.parent.mkdir(parents=True, exist_ok=True)
        with runtime_clusters_path.open("w", encoding="utf-8") as handle:
            json.dump(args.resolved_capability_clusters, handle, ensure_ascii=False, indent=2)
        args.runtime_capability_clusters_json = str(runtime_clusters_path)
    if args.skip_completed and summary_path.is_file():
        done: set[str] = set()
        with summary_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("status") == "completed"
                    and row.get("mode") == args.mode
                    and row.get("decision_mode") == args.decision_mode
                    and row.get("bench_root") == str(_bench_root())
                    and row.get("capability_min_cluster_size")
                    == args.capability_min_cluster_size
                    and row.get("allow_single_case_capability_repair")
                    == args.allow_single_case_capability_repair
                ):
                    done.add(row.get("task_name", ""))
        before = len(selected)
        selected = [t for t in selected if t not in done]
        print(
            f"[batch] skip-completed: {before - len(selected)} skipped, {len(selected)} remaining"
        )

    if not selected:
        print("All selected tasks already completed.")
        return

    if args.parallelism > 1 and args.mode in ("assist", "auto") and args.workspace_isolation == "none":
        raise SystemExit(
            "Parallel assist/auto requires --workspace-isolation copy to avoid concurrent code-write conflicts."
        )

    bench_base = str(_bench_root())
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_root = (_repo_root() / args.workspace_root).resolve()
    log_dir = (summary_path.parent / "parallel_logs").resolve()

    print(f"Worktree eval: {_repo_root()}")
    print(f"BENCH_LOCAL_ROOT={bench_base}")
    print(
        f"Mode={args.mode} decision_mode={args.decision_mode} "
        f"tasks={len(selected)} parallelism={args.parallelism}"
    )

    results: list[dict] = []
    if args.parallelism <= 1:
        for i, task_name in enumerate(selected, 1):
            print(f"\n{'=' * 60}\n[{i}/{len(selected)}] {task_name}\n{'=' * 60}")
            row = _run_task_sequential(task_name, args, bench_base)
            row.update(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "mode": args.mode,
                    "decision_mode": args.decision_mode,
                    "bench_root": bench_base,
                }
            )
            results.append(row)
            with summary_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        workspace_root.mkdir(parents=True, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallelism) as ex:
            future_map = {
                ex.submit(
                    _run_task_parallel_subprocess,
                    task_name,
                    args,
                    bench_base,
                    workspace_root,
                    log_dir,
                ): task_name
                for task_name in selected
            }
            done_cnt = 0
            for fut in concurrent.futures.as_completed(future_map):
                done_cnt += 1
                task_name = future_map[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    row = {
                        "task_name": task_name,
                        "run_id": f"tb21_{task_name}_{uuid.uuid4().hex[:8]}",
                        "status": "error",
                        "error": str(exc),
                    }
                row.update(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "mode": args.mode,
                        "decision_mode": args.decision_mode,
                        "bench_root": bench_base,
                        "capability_min_cluster_size": args.capability_min_cluster_size,
                        "allow_single_case_capability_repair": (
                            args.allow_single_case_capability_repair
                        ),
                    }
                )
                print(
                    f"[parallel {done_cnt}/{len(selected)}] {task_name} -> {row['status']}"
                )
                results.append(row)
                with summary_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nBatch done. Summary appended to {summary_path}")
    ok = sum(1 for r in results if r["status"] == "completed")
    print(f"Completed {ok}/{len(results)} orchestrator runs.")


if __name__ == "__main__":
    main()
