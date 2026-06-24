#!/usr/bin/env python3
"""Post-batch repair with iterative "peel the onion" round loop.

After fixing Layer 0 bugs (e.g. missing imports), re-runs affected tasks
to discover Layer 1 bugs that were previously masked, then repairs those too.

Usage:
    # Dry-run: see what would be repaired
    python scripts/run_self_improve_repair.py --dry-run

    # Execute repairs with iterative re-run (default 3 rounds)
    python scripts/run_self_improve_repair.py \
        --adapter remote_docker --min-support 2 --max-patches 3

    # Single-pass (legacy behavior)
    python scripts/run_self_improve_repair.py --max-rounds 1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Load .env if DASHSCOPE_API_KEY not already in environment
if not os.environ.get("DASHSCOPE_API_KEY"):
    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            if _line.startswith("DASHSCOPE_API_KEY="):
                os.environ["DASHSCOPE_API_KEY"] = _line.split("=", 1)[1].strip()
                break

from ms_agent.self_improve.cluster_builder import (
    build_known_clusters_from_root,
    discover_runledger_files,
    iter_ledger_events,
)
from ms_agent.self_improve.executor import FileGuard, RepairExecutor
from ms_agent.self_improve.planner import RepairPlan
from ms_agent.self_improve.repair_agent import RepairAgent
from ms_agent.self_improve.schemas import (
    CapabilityGapSignal,
    EvidenceKind,
    EvidenceRef,
    ImprovementType,
    IncidentSignal,
    RootCauseHypothesis,
    SymptomClass,
)
from ms_agent.self_improve.target_resolver import resolve_repair_targets
from ms_agent.self_improve.verifier import Verifier


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_llm_config(llm: str) -> Dict[str, Any]:
    return {
        "model": llm,
        "service": "dashscope" if "qwen" in llm.lower() else None,
        "dashscope_base_url": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if "qwen" in llm.lower()
            else None
        ),
        "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
    }


# ---------------------------------------------------------------------------
# Ledger scanning & cluster filtering
# ---------------------------------------------------------------------------

def _collect_gap_details(
    ledger_root: Path,
) -> Dict[str, Dict[str, Any]]:
    """Collect per-cluster details (evidence paths, rationales, task_ids) from all ledgers."""
    clusters: Dict[str, Dict[str, Any]] = {}
    for ledger_file in discover_runledger_files(ledger_root):
        for event in iter_ledger_events(ledger_file):
            if event.get("event_type") != "capability_gap_mined":
                continue
            key = event.get("cluster_key", "")
            if not key:
                continue
            bucket = clusters.setdefault(
                key,
                {
                    "support_count": 0,
                    "improvement_types": [],
                    "target_source_paths": [],
                    "evidence_refs": [],
                    "rationales": [],
                    "task_ids": [],
                    "symptom_class": event.get("symptom_class", "unknown"),
                    "root_cause_hypothesis": event.get(
                        "root_cause_hypothesis", "unknown"
                    ),
                },
            )
            bucket["support_count"] += 1
            imp = event.get("improvement_type", "")
            if imp:
                bucket["improvement_types"].append(imp)
            bucket["target_source_paths"] = list(
                set(
                    bucket["target_source_paths"]
                    + (event.get("target_source_paths") or [])
                )
            )
            bucket["evidence_refs"] = list(
                set(
                    bucket["evidence_refs"]
                    + (event.get("evidence_refs") or [])
                )
            )
            rationale = event.get("rationale", "")
            if rationale and rationale not in bucket["rationales"]:
                bucket["rationales"].append(rationale)
            tid = event.get("task_id", "")
            if tid and tid not in bucket["task_ids"]:
                bucket["task_ids"].append(tid)
    return clusters


def _enrich_cluster_targets(
    clusters: Dict[str, Dict[str, Any]],
    *,
    include_symptom_defaults: bool,
) -> None:
    """Fill target_source_paths from evidence stacks and import-site expansion."""
    repo = _repo_root()
    for info in clusters.values():
        info["target_source_paths"] = resolve_repair_targets(
            symptom_class=info.get("symptom_class", "unknown"),
            evidence_refs=info.get("evidence_refs", []),
            ledger_targets=info.get("target_source_paths", []),
            symptom_defaults=_auto_derive_targets,
            repo_root=repo,
            include_symptom_defaults=include_symptom_defaults,
        )


_SYMPTOM_DEFAULT_TARGETS: Dict[str, List[str]] = {
    "tool_repeated_failure": [
        "ms_agent/tools/tool_manager.py",
        "ms_agent/agent/code_agent.py",
    ],
    "stuck_loop": [
        "ms_agent/tools/tool_manager.py",
        "ms_agent/agent/code_agent.py",
    ],
    "artifact_missing": [
        "ms_agent/agent/code_agent.py",
        "ms_agent/tools/code/code_executor.py",
    ],
    "dependency_missing": [
        "ms_agent/tools/code/code_executor.py",
        "ms_agent/agent/code_agent.py",
    ],
    "execution_timeout": [
        "ms_agent/tools/tool_manager.py",
        "ms_agent/agent/code_agent.py",
    ],
    "config_restriction": [
        "ms_agent/benchmark/harbor_terminal_bench_agent.py",
    ],
}
_FALLBACK_TARGETS = ["ms_agent/agent/code_agent.py"]


def _auto_derive_targets(symptom_class: str) -> List[str]:
    """Derive default target files from symptom_class, filtering to files that exist."""
    candidates = _SYMPTOM_DEFAULT_TARGETS.get(symptom_class, _FALLBACK_TARGETS)
    return [p for p in candidates if (_repo_root() / p).is_file()]


def _filter_repairable_gaps(
    clusters: Dict[str, Dict[str, Any]],
    min_support: int,
    allowed_types: set[str] | None = None,
    auto_derive: bool = False,
) -> List[tuple[str, Dict[str, Any]]]:
    """Filter gaps meeting min_support and type constraints, sorted by support desc."""
    if allowed_types is None:
        allowed_types = {ImprovementType.FRAMEWORK_PATCH.value}
    _enrich_cluster_targets(
        clusters,
        include_symptom_defaults=auto_derive,
    )
    eligible = []
    for key, info in clusters.items():
        if info["support_count"] < min_support:
            continue
        if not any(t in allowed_types for t in info["improvement_types"]):
            continue
        targets = info.get("target_source_paths") or []
        if not targets:
            continue
        eligible.append((key, info))
    eligible.sort(key=lambda x: x[1]["support_count"], reverse=True)
    return eligible


# ---------------------------------------------------------------------------
# Synthetic signal / plan builders
# ---------------------------------------------------------------------------

def _build_synthetic_signal(
    cluster_key: str,
    info: Dict[str, Any],
    run_id: str,
) -> IncidentSignal:
    """Build a minimal IncidentSignal from aggregated cluster data."""
    evidence_index = []
    for ref_path in info.get("evidence_refs", []):
        if Path(ref_path).is_file():
            evidence_index.append(EvidenceRef(kind="agent_stdout", path=ref_path))

    return IncidentSignal(
        run_id=run_id,
        iteration=0,
        adapter_name="post_batch_repair",
        task_id=",".join(info.get("task_ids", [])[:5]),
        status="fail",
        exit_code=None,
        reward=0.0,
        incidents=[],
        evidence_index=evidence_index,
        trajectory_analysis=None,
    )


def _build_synthetic_plan(
    cluster_key: str,
    info: Dict[str, Any],
) -> RepairPlan:
    """Build a RepairPlan from aggregated cluster data."""
    rationale = "; ".join(info.get("rationales", [])[:3])
    symptom = info.get("symptom_class", "unknown")
    task_list = ", ".join(info.get("task_ids", [])[:8])
    target_list = ", ".join(info.get("target_source_paths", []))

    if symptom == "config_restriction":
        repair_prompt = (
            f"Agent YAML config generated by _render_agent_yaml() uses overly "
            f"restrictive defaults, causing {info['support_count']} task "
            f"failures: {task_list}. "
            "The method builds a YAML string with string concatenation. "
            "You must ADD config lines to the existing string — do NOT "
            "restructure the method or add early returns. Specifically: "
            "1) In the file_system tool section, after the 'glob' include "
            "line, add '    output_dir: /app\\n' and "
            "'    allow_read_all_files: true\\n'. "
            "2) In the code_executor tool section, after 'shell_executor' "
            "include line, add '    network_enabled: true\\n'. "
            f"Target file: {target_list}, method _render_agent_yaml()."
        )
    else:
        repair_prompt = (
            f"Capability gap: {symptom} / "
            f"{info.get('root_cause_hypothesis', 'unknown')}. "
            f"Observed in {info['support_count']} tasks: {task_list}. "
            f"Rationale: {rationale}. "
            f"Target files (stack order + related sites): {target_list}. "
        )
        if symptom == "dependency_missing":
            repair_prompt += (
                "This is a missing-module / import failure. Fix EVERY bare import "
                "of the missing module across the listed files and any others found "
                "via grep — follow import-chain order (earliest frame first). "
            )
        repair_prompt += (
            "Generate a generalizable framework fix that does NOT hardcode "
            "any task-specific logic."
        )

    return RepairPlan(
        should_repair=True,
        reason=f"Post-batch repair for cluster {cluster_key} "
        f"(support={info['support_count']}, "
        f"tasks={','.join(info.get('task_ids', [])[:5])})",
        suggested_mode="auto",
        target_domains=["framework"],
        target_source_paths=info.get("target_source_paths", []),
        repair_prompt=repair_prompt,
    )


# ---------------------------------------------------------------------------
# Remote verification helpers
# ---------------------------------------------------------------------------

def _run_remote_tasks(
    adapter_type: str,
    remote_host: str,
    remote_repo_dir: str,
    tasks: List[str],
    *,
    work_dir_suffix: str,
    batch_size: int = 8,
) -> Dict[str, float]:
    """Push code to remote and run tasks; return task -> reward."""
    if adapter_type != "remote_docker" or not tasks:
        return {}
    from ms_agent.self_improve.adapters.terminal_bench_remote_docker import (
        TerminalBenchRemoteDockerAdapter,
    )

    adapter = TerminalBenchRemoteDockerAdapter(
        task_name=tasks[0],
        remote_host=remote_host,
        remote_repo_dir=remote_repo_dir,
        eval_batch_size=batch_size,
    )
    adapter.sync_code_to_remote()
    raw = adapter.run_tasks_with_artifacts(tasks, work_dir_suffix=work_dir_suffix)
    return {task_id: reward for task_id, (reward, _) in raw.items()}


def _verify_patch_on_remote(
    adapter_type: str,
    remote_host: str,
    remote_repo_dir: str,
    affected_tasks: List[str],
    regression_tasks: List[str],
    *,
    batch_size: int = 8,
) -> Tuple[bool, List[str], Dict[str, float]]:
    """Verify patch: affected tasks first, then regression controls.

    Returns (passed, failed_task_ids, rewards_by_task).
    """
    rewards: Dict[str, float] = {}
    affected = list(dict.fromkeys(t for t in affected_tasks if t))
    regression = [
        t for t in regression_tasks
        if t and t not in affected
    ]

    if affected:
        print(f"[repair] Verifying affected tasks first: {affected}")
        affected_rewards = _run_remote_tasks(
            adapter_type,
            remote_host,
            remote_repo_dir,
            affected,
            work_dir_suffix="si_verify_affected",
            batch_size=batch_size,
        )
        rewards.update(affected_rewards)
        failed = [t for t in affected if affected_rewards.get(t, 0.0) != 1.0]
        if failed:
            return False, failed, rewards

    if regression:
        print(f"[repair] Running regression controls: {regression}")
        regression_rewards = _run_remote_tasks(
            adapter_type,
            remote_host,
            remote_repo_dir,
            regression,
            work_dir_suffix="si_remote_regression",
            batch_size=batch_size,
        )
        rewards.update(regression_rewards)
        failed = [t for t in regression if regression_rewards.get(t, 0.0) != 1.0]
        if failed:
            return False, failed, rewards

    return True, [], rewards


def _run_regression(
    adapter_type: str,
    remote_host: str,
    remote_repo_dir: str,
    regression_tasks: List[str],
    batch_size: int = 8,
) -> Dict[str, float]:
    """Push code to remote and run regression tasks (legacy helper)."""
    return _run_remote_tasks(
        adapter_type,
        remote_host,
        remote_repo_dir,
        regression_tasks,
        work_dir_suffix="si_remote_regression",
        batch_size=batch_size,
    )


# ---------------------------------------------------------------------------
# Rerun & collect (peel-the-onion pipeline)
# ---------------------------------------------------------------------------

def _rerun_and_collect(
    affected_tasks: List[str],
    remote_host: str,
    remote_repo_dir: str,
    ledger_root: Path,
    round_idx: int,
    config: Dict[str, Any],
    batch_size: int = 8,
) -> int:
    """Re-run tasks on remote Docker, run collect→classify→mine, write new ledger.

    Returns the number of tasks that are still failing (reward < 1.0).
    """
    from ms_agent.self_improve.adapters.terminal_bench_remote_docker import (
        TerminalBenchRemoteDockerAdapter,
    )
    from ms_agent.self_improve.capability_miner import CapabilityGapMiner
    from ms_agent.self_improve.classifier import FailureClassifier
    from ms_agent.self_improve.collector import ArtifactCollector
    from ms_agent.self_improve.ledger import RunLedger
    from ms_agent.self_improve import trajectory_analyzer

    adapter = TerminalBenchRemoteDockerAdapter(
        task_name=affected_tasks[0],
        remote_host=remote_host,
        remote_repo_dir=remote_repo_dir,
        eval_batch_size=batch_size,
    )
    adapter.sync_code_to_remote()

    print(f"[rerun] Running {len(affected_tasks)} tasks on remote Docker...")
    results = adapter.run_tasks_with_artifacts(
        affected_tasks,
        work_dir_suffix=f"si_rerun_round{round_idx}",
    )

    classifier = FailureClassifier()
    miner = CapabilityGapMiner(config)

    still_failing = 0
    for task_id, (reward, trial_dir) in results.items():
        if reward == 1.0:
            print(f"[rerun] {task_id}: reward=1.0 (fixed!)")
            continue
        if not trial_dir:
            print(f"[rerun] {task_id}: reward={reward} (no artifacts)")
            still_failing += 1
            continue

        print(f"[rerun] {task_id}: reward={reward} — running pipeline...")
        still_failing += 1

        collector = ArtifactCollector(trial_dir)
        evidences = collector.collect()

        traj_analysis = None
        for ev in evidences:
            if ev.kind == EvidenceKind.AGENT_STDOUT:
                try:
                    stdout_text = Path(ev.path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    traj_analysis = trajectory_analyzer.analyze(stdout_text)
                except Exception as exc:
                    print(f"[rerun] Trajectory analysis failed for {task_id}: {exc}")
                break

        run_context = {
            "exit_code": None,
            "reward": reward,
            "adapter_name": "terminal_bench_remote_docker",
            "trajectory_analysis": traj_analysis,
        }
        incidents = classifier.classify(evidences, run_context)

        run_id = f"rerun_round{round_idx}_{task_id}"
        signal = IncidentSignal(
            run_id=run_id,
            iteration=0,
            adapter_name="terminal_bench_remote_docker",
            task_id=task_id,
            status="fail",
            exit_code=None,
            reward=reward,
            incidents=incidents,
            evidence_index=evidences,
            trajectory_analysis=traj_analysis,
        )

        gap = miner.mine(signal)

        ledger = RunLedger(str(ledger_root), run_id)
        ledger.record_baseline_result(0, "fail", None, reward)
        primary = signal.primary_incident
        if primary:
            ledger.record_incident_classified(
                0, primary.fingerprint, primary.incident_class.value, primary.confidence,
            )

        incident_fp = primary.fingerprint if primary else "unknown"
        ledger.record_capability_gap_mined(
            iteration=0,
            incident_fingerprint=incident_fp,
            symptom_class=gap.symptom_class.value,
            root_cause_hypothesis=gap.root_cause_hypothesis.value,
            improvement_type=gap.improvement_type.value,
            confidence=gap.confidence,
            cluster_key=gap.cluster_key,
            support_count=gap.support_count,
            min_support_required=gap.min_support_required,
            repair_allowed=gap.repair_allowed,
            rationale=gap.rationale,
            target_source_paths=gap.target_source_paths,
            evidence_refs=gap.evidence_refs,
            task_id=task_id,
        )
        print(
            f"[rerun] {task_id}: gap={gap.symptom_class.value}/"
            f"{gap.root_cause_hypothesis.value} "
            f"cluster={gap.cluster_key}"
        )

    return still_failing


# ---------------------------------------------------------------------------
# Patch application (single round)
# ---------------------------------------------------------------------------

def _apply_patches(
    eligible: List[tuple[str, Dict[str, Any]]],
    max_patches: int,
    repair_agent: RepairAgent,
    executor: RepairExecutor,
    verifier: Verifier,
    adapter_type: str,
    remote_host: str,
    remote_repo_dir: str,
    regression_tasks: List[str],
    report_path: Path,
    batch_size: int = 8,
) -> Tuple[int, List[str]]:
    """Apply patches for eligible clusters. Returns (patches_applied, affected_task_ids)."""
    patches_applied = 0
    affected_task_ids: List[str] = []
    to_repair = eligible[:max_patches]

    for i, (cluster_key, info) in enumerate(to_repair, 1):
        print(f"\n{'=' * 60}")
        print(f"[repair {i}/{len(to_repair)}] {cluster_key}")
        print(f"{'=' * 60}")

        run_id = f"repair_{uuid.uuid4().hex[:8]}"
        signal = _build_synthetic_signal(cluster_key, info, run_id)
        plan = _build_synthetic_plan(cluster_key, info)
        patch_id = f"repair_{cluster_key[:20]}_{i}"

        head_before = _git_head()
        patch = repair_agent.generate_patch(plan, signal, patch_id)
        if not patch:
            print(f"[repair] No patch generated for {cluster_key}")
            _append_report(report_path, cluster_key, info, "no_patch", run_id)
            continue

        print("[repair] Local verification (compileall)...")
        verify_res = verifier.verify_patch(
            ["python -m compileall -q ms_agent/self_improve scripts"],
            {},
            {},
            "generic_python",
        )
        if not verify_res.passed:
            print(f"[repair] Local verification failed: {verify_res.output_log}")
            subprocess.run(
                ["git", "checkout", "--"] + patch.target_files,
                cwd=".", capture_output=True,
            )
            _append_report(report_path, cluster_key, info, "local_verify_failed", run_id)
            continue

        commit_sha = executor.commit_working_changes(
            patch_id, patch.description, patch.target_files,
        )
        if not commit_sha:
            print("[repair] Failed to commit changes.")
            subprocess.run(
                ["git", "checkout", "--"] + patch.target_files,
                cwd=".", capture_output=True,
            )
            _append_report(report_path, cluster_key, info, "commit_failed", run_id)
            continue
        head_after = commit_sha

        if adapter_type == "remote_docker" and (
            info.get("task_ids") or regression_tasks
        ):
            cluster_affected = list(info.get("task_ids", []))
            passed, failed_tasks, verify_rewards = _verify_patch_on_remote(
                adapter_type,
                remote_host,
                remote_repo_dir,
                affected_tasks=cluster_affected,
                regression_tasks=regression_tasks,
                batch_size=batch_size,
            )
            if not passed:
                phase = (
                    "affected_tasks_failed"
                    if any(t in cluster_affected for t in failed_tasks)
                    else "regression_failed"
                )
                print(f"[repair] Remote verification failed ({phase}): {failed_tasks}")
                _revert_commit(head_before, head_after)
                _append_report(
                    report_path, cluster_key, info, phase, run_id,
                    extra={
                        "failed_tasks": failed_tasks,
                        "rewards": verify_rewards,
                    },
                )
                continue
            print("[repair] Remote verification passed (affected + regression).")

        print(f"[repair] Patch applied successfully: {head_after}")
        patches_applied += 1
        for tid in info.get("task_ids", []):
            if tid not in affected_task_ids:
                affected_task_ids.append(tid)
        _append_report(
            report_path, cluster_key, info, "applied", run_id,
            extra={"commit_sha": head_after},
        )

    return patches_applied, affected_task_ids


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=".",
        ).stdout.strip()
    except Exception:
        return ""


def _revert_commit(head_before: str, head_after: str) -> None:
    if not head_before or not head_after or head_before == head_after:
        return
    try:
        print(f"[repair] Reverting commit {head_after}")
        subprocess.run(
            [
                "git", "-c", "user.name=self-improve",
                "-c", "user.email=self-improve@example.invalid",
                "revert", "--no-edit", head_after,
            ],
            check=True,
            cwd=".",
        )
    except Exception as e:
        print(f"[repair] Warning: Failed to revert: {e}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _append_report(
    path: Path,
    cluster_key: str,
    info: Dict[str, Any],
    status: str,
    run_id: str,
    extra: Dict[str, Any] | None = None,
) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "cluster_key": cluster_key,
        "support_count": info["support_count"],
        "task_ids": info.get("task_ids", []),
        "target_source_paths": info.get("target_source_paths", []),
        "status": status,
    }
    if extra:
        row.update(extra)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-batch repair with iterative peel-the-onion round loop"
    )
    parser.add_argument(
        "--ledger-root",
        default="outputs/self_improve_tb21",
        help="Root directory containing run ledger subdirectories",
    )
    parser.add_argument(
        "--min-support",
        type=int,
        default=2,
        help="Minimum support_count for a cluster to be repaired",
    )
    parser.add_argument("--llm", default="qwen-max")
    parser.add_argument(
        "--max-patches",
        type=int,
        default=3,
        help="Maximum number of gaps to repair per round",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum number of repair→rerun rounds (default: 3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Max concurrent Docker containers for rerun (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print repair candidates without executing",
    )
    parser.add_argument(
        "--adapter",
        default="remote_docker",
        choices=["remote_docker", "none"],
        help="Adapter for regression verification (none = local compileall only)",
    )
    parser.add_argument(
        "--remote-host",
        default="root@47.254.25.238",
    )
    parser.add_argument(
        "--remote-repo-dir",
        default="/root/bench_workspace/modelscope-agent-si",
    )
    parser.add_argument(
        "--regression-tasks",
        default="fix-git,build-pmars,polyglot-c-py",
    )
    parser.add_argument(
        "--allowed-types",
        nargs="+",
        default=["framework_patch", "config_patch"],
        help="Improvement types eligible for repair (default: framework_patch, config_patch)",
    )
    parser.add_argument(
        "--auto-derive-targets",
        action="store_true",
        help="Auto-derive target_source_paths from symptom_class when missing",
    )
    parser.add_argument(
        "--report-out",
        default="outputs/self_improve_tb21/repair_report.jsonl",
    )
    args = parser.parse_args()

    ledger_root = Path(args.ledger_root)
    if not ledger_root.is_absolute():
        ledger_root = _repo_root() / ledger_root
    if not ledger_root.is_dir():
        raise SystemExit(f"Ledger root not found: {ledger_root}")

    repair_config = {
        "llm": _build_llm_config(args.llm),
        "scope": {
            "file_write_guard": {
                "include_paths": ["ms_agent/", "scripts/"],
                "exclude_paths": ["bench_local/", "outputs/", ".cache/", ".venv/"],
                "always_allowed_extensions": [".py", ".sh", ".json", ".yaml", ".md"],
                "never_allow_extensions": [".bin", ".exe"],
                "max_file_size_kb": 2048,
            }
        },
    }
    repair_agent = RepairAgent(repair_config)
    executor = RepairExecutor(
        guard=FileGuard(repair_config["scope"]["file_write_guard"]),
        mode="auto",
    )
    verifier = Verifier()
    regression_tasks = [t.strip() for t in args.regression_tasks.split(",") if t.strip()]
    allowed_types = set(args.allowed_types)

    report_path = _repo_root() / args.report_out
    report_path.parent.mkdir(parents=True, exist_ok=True)

    total_patches = 0

    for round_idx in range(1, args.max_rounds + 1):
        print(f"\n{'#' * 60}")
        print(f"# Round {round_idx}/{args.max_rounds}")
        print(f"{'#' * 60}")

        # 1. Scan ledgers → cluster → filter
        print(f"[round {round_idx}] Scanning ledgers in {ledger_root}")
        clusters = _collect_gap_details(ledger_root)
        print(f"[round {round_idx}] Found {len(clusters)} unique capability-gap clusters")

        eligible = _filter_repairable_gaps(
            clusters, args.min_support,
            allowed_types=allowed_types,
            auto_derive=args.auto_derive_targets,
        )
        print(
            f"[round {round_idx}] {len(eligible)} clusters meet min_support={args.min_support} "
            f"and type in {sorted(allowed_types)}"
            f"{' (auto-derive targets enabled)' if args.auto_derive_targets else ''}"
        )

        if not eligible:
            print(f"[round {round_idx}] Nothing to repair. Stopping.")
            break

        for i, (key, info) in enumerate(eligible, 1):
            print(
                f"\n  [{i}] cluster={key} support={info['support_count']} "
                f"symptom={info.get('symptom_class', '?')} "
                f"targets={info.get('target_source_paths', [])}"
            )
            print(f"      tasks: {', '.join(info.get('task_ids', []))}")
            for r in info.get("rationales", [])[:2]:
                print(f"      rationale: {r[:120]}")

        if args.dry_run:
            print(f"\n[round {round_idx}] Dry-run complete. {len(eligible)} candidates found.")
            break

        # 2. Apply patches
        patches_applied, affected_task_ids = _apply_patches(
            eligible=eligible,
            max_patches=args.max_patches,
            repair_agent=repair_agent,
            executor=executor,
            verifier=verifier,
            adapter_type=args.adapter,
            remote_host=args.remote_host,
            remote_repo_dir=args.remote_repo_dir,
            regression_tasks=regression_tasks,
            report_path=report_path,
            batch_size=args.batch_size,
        )
        total_patches += patches_applied

        # 3. If no patches applied this round, stop
        if patches_applied == 0:
            print(f"[round {round_idx}] No patches applied. Stopping.")
            break

        # 4. Rerun affected tasks to discover next-layer bugs
        if args.adapter != "remote_docker" or not affected_task_ids:
            print(f"[round {round_idx}] Skipping rerun (adapter={args.adapter}, "
                  f"affected_tasks={len(affected_task_ids)}).")
            break

        if round_idx >= args.max_rounds:
            print(f"[round {round_idx}] Max rounds reached. Skipping rerun.")
            break

        print(f"\n[round {round_idx}] Re-running {len(affected_task_ids)} affected tasks "
              f"to discover next-layer bugs...")
        still_failing = _rerun_and_collect(
            affected_tasks=affected_task_ids,
            remote_host=args.remote_host,
            remote_repo_dir=args.remote_repo_dir,
            ledger_root=ledger_root,
            round_idx=round_idx,
            config=repair_config,
            batch_size=args.batch_size,
        )

        if still_failing == 0:
            print(f"[round {round_idx}] All affected tasks now pass! Done.")
            break

        print(f"[round {round_idx}] {still_failing} tasks still failing — "
              f"next round will cluster new failures.")

    print(f"\n[repair] All rounds complete. {total_patches} total patches applied.")
    print(f"[repair] Report: {report_path}")


if __name__ == "__main__":
    main()
