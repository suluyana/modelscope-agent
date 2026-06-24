"""Build cross-case capability cluster registry from RunLedger events."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ms_agent.self_improve.schemas import ImprovementType

FRAMEWORK_PATH_RE = re.compile(r"\b(?:ms_agent|scripts)/[A-Za-z0-9_./-]+\.py\b")

REPAIRABLE_IMPROVEMENT_TYPES = {
    ImprovementType.FRAMEWORK_PATCH.value,
    ImprovementType.PROMPT_POLICY_PATCH.value,
    ImprovementType.TOOLING_ADAPTER_PATCH.value,
}


def iter_ledger_events(ledger_path: Path | str) -> Iterable[Dict[str, Any]]:
    with Path(ledger_path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event


def discover_runledger_files(root: Path) -> List[Path]:
    if root.is_file() and root.name == "runledger.jsonl":
        return [root]
    return sorted(root.rglob("runledger.jsonl"))


def extract_framework_paths(
    evidence_paths: Iterable[str],
    repo_root: Optional[Path] = None,
    *,
    max_paths: int = 8,
) -> List[str]:
    paths: List[str] = []
    root = repo_root or Path.cwd()
    for evidence_path in evidence_paths:
        candidate = Path(evidence_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for match in FRAMEWORK_PATH_RE.findall(text):
            norm = match.replace("\\", "/")
            if norm.startswith("../") or norm in {".", ".."}:
                continue
            if norm not in paths:
                paths.append(norm)
    return paths[:max_paths]


def build_known_clusters(
    ledger_paths: Iterable[Path | str],
    *,
    min_cluster_size: int = 2,
    repo_root: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Aggregate capability_gap_mined events into known_clusters config."""
    grouped: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "events": [],
            "tasks": set(),
            "evidence_paths": set(),
            "improvement_types": [],
        }
    )

    for ledger_path in ledger_paths:
        for event in iter_ledger_events(ledger_path):
            if event.get("event_type") != "capability_gap_mined":
                continue
            cluster_key = str(event.get("cluster_key", "")).strip()
            if not cluster_key:
                continue
            bucket = grouped[cluster_key]
            bucket["events"].append(event)
            task_id = event.get("task_id")
            if task_id:
                bucket["tasks"].add(str(task_id))
            for path in event.get("evidence_refs") or []:
                bucket["evidence_paths"].add(str(path))
            improvement = event.get("improvement_type")
            if improvement:
                bucket["improvement_types"].append(str(improvement))

    known_clusters: Dict[str, Dict[str, Any]] = {}
    for cluster_key, bucket in grouped.items():
        support_count = len(bucket["events"])
        if support_count < min_cluster_size:
            continue
        improvement_types = bucket["improvement_types"]
        if improvement_types and not any(
            t in REPAIRABLE_IMPROVEMENT_TYPES for t in improvement_types
        ):
            continue

        target_source_paths = extract_framework_paths(
            bucket["evidence_paths"],
            repo_root=repo_root,
        )
        if not target_source_paths:
            continue

        known_clusters[cluster_key] = {
            "support_count": support_count,
            "min_support_required": min_cluster_size,
            "target_source_paths": target_source_paths,
            "task_ids": sorted(bucket["tasks"]),
            "improvement_types": sorted(set(improvement_types)),
        }

    return known_clusters


def build_known_clusters_from_root(
    root: Path,
    *,
    min_cluster_size: int = 2,
    repo_root: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    ledger_files = discover_runledger_files(root)
    return build_known_clusters(
        ledger_files,
        min_cluster_size=min_cluster_size,
        repo_root=repo_root,
    )
