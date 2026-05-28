import uuid
import os
import argparse
import re
import json

from ms_agent.self_improve.orchestrator import SelfImproveOrchestrator


def _build_adapter(adapter_type: str, task_name: str, work_dir: str | None):
    if adapter_type == "evalscope":
        from ms_agent.self_improve.adapters.terminal_bench_evalscope import (
            TerminalBenchEvalScopeAdapter,
        )
        return TerminalBenchEvalScopeAdapter(task_name, work_dir=work_dir)

    from ms_agent.self_improve.adapters.terminal_bench_fast_local import (
        TerminalBenchFastLocalAdapter,
    )
    return TerminalBenchFastLocalAdapter(task_name)


def main():
    parser = argparse.ArgumentParser(description="Run Self-Improve loop on TerminalBench")
    parser.add_argument("--task-name", required=True, type=str)
    parser.add_argument("--mode", default="assist", type=str, choices=["observe", "assist", "auto"])
    parser.add_argument("--llm", default="qwen-max", type=str)
    parser.add_argument(
        "--decision-mode",
        default="rule_only",
        type=str,
        choices=["rule_only", "hybrid"],
    )
    parser.add_argument(
        "--adapter",
        default="fast_local",
        choices=["fast_local", "evalscope"],
        help="Execution adapter: fast_local (local subprocess) or evalscope (Docker via EvalScope)",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="EvalScope work directory (only used with --adapter evalscope)",
    )
    parser.add_argument("--rule-high-confidence", type=float, default=0.90)
    parser.add_argument("--llm-min-confidence", type=float, default=0.70)
    parser.add_argument("--disagreement-delta", type=float, default=0.25)
    parser.add_argument("--capability-min-cluster-size", type=int, default=2)
    parser.add_argument("--allow-single-case-capability-repair", action="store_true")
    parser.add_argument("--capability-clusters-json", default="")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--logging-root", type=str, default="outputs/self_improve")
    args = parser.parse_args()

    safe_task_name = re.sub(r"[^A-Za-z0-9._-]+", "_", args.task_name).strip("._-")
    if not safe_task_name:
        safe_task_name = "task"
    run_id = f"tb_{safe_task_name}_{uuid.uuid4().hex[:8]}"
    output_dir = args.logging_root
    os.makedirs(output_dir, exist_ok=True)
    known_clusters = {}
    if args.capability_clusters_json:
        with open(args.capability_clusters_json, encoding="utf-8") as f:
            known_clusters = json.load(f)

    config = {
        "mode": args.mode,
        "llm": {
            "model": args.llm,
            "service": "dashscope" if "qwen" in args.llm.lower() else None,
            "dashscope_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1" if "qwen" in args.llm.lower() else None,
            "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", "")
        },
        "loop": {"max_iterations": args.max_iterations},
        "logging": {"root_dir": output_dir},
        "capability": {
            "enabled": True,
            "min_cluster_size": args.capability_min_cluster_size,
            "allow_single_case_framework_gap": args.allow_single_case_capability_repair,
            "known_clusters": known_clusters,
        },
        "decision": {
            "mode": args.decision_mode,
            "classifier": {
                "min_confidence": 0.75,
                "low_confidence_floor": 0.55,
            },
            "thresholds": {
                "rule_high_confidence": args.rule_high_confidence,
                "llm_min_confidence": args.llm_min_confidence,
                "disagreement_delta": args.disagreement_delta,
            },
            "fallback": {
                "on_low_confidence": "switch_to_assist",
                "on_rule_llm_disagreement": "ask_human",
            },
        },
        "scope": {
            "file_write_guard": {
                "include_paths": ["ms_agent/", "scripts/"],
                "exclude_paths": ["bench_local/", "outputs/", ".cache/", ".venv/"],
                "always_allowed_extensions": [".py", ".sh", ".json", ".yaml", ".md"],
                "never_allow_extensions": [".bin", ".exe"],
                "max_file_size_kb": 2048,
            }
        },
        "verify": {
            "patch_commands": [
                "python -m compileall -q ms_agent/self_improve scripts"
            ]
        }
    }

    adapter = _build_adapter(args.adapter, args.task_name, args.work_dir)
    orchestrator = SelfImproveOrchestrator(run_id, adapter, config)

    orchestrator.run_loop()

if __name__ == "__main__":
    main()
