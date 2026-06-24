"""Resolve repair target files from ledger, evidence, and symptom defaults."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, List, Sequence

from ms_agent.self_improve.cluster_builder import FRAMEWORK_PATH_RE, extract_framework_paths

_MISSING_MODULE_RE = re.compile(
    r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


def stack_ordered_framework_paths(
    evidence_refs: Iterable[str],
    repo_root: Path,
    *,
    max_paths: int = 24,
) -> List[str]:
    """Extract framework paths from evidence; innermost traceback frames first."""
    last_index: dict[str, int] = {}
    for ref in evidence_refs:
        path = Path(ref)
        if not path.is_absolute():
            path = repo_root / path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, match in enumerate(FRAMEWORK_PATH_RE.findall(text)):
            norm = match.replace("\\", "/")
            if norm.startswith("../") or norm in {".", ".."}:
                continue
            last_index[norm] = idx

    ordered = sorted(last_index.keys(), key=lambda p: last_index[p], reverse=True)
    return [
        p for p in ordered
        if (repo_root / p).is_file()
    ][:max_paths]


def missing_modules_from_evidence(evidence_refs: Iterable[str]) -> List[str]:
    """Extract missing Python module names from trial evidence text."""
    modules: List[str] = []
    for ref in evidence_refs:
        path = Path(ref)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _MISSING_MODULE_RE.findall(text):
            if match not in modules:
                modules.append(match)
    return modules


def files_with_bare_import_of(
    module: str,
    repo_root: Path,
    *,
    search_roots: Sequence[str] = ("ms_agent", "scripts"),
) -> List[str]:
    """Find source files with a top-level import of *module* (no install fallback)."""
    if not module or module.startswith("."):
        return []

    pattern = re.compile(
        rf"^(?:from\s+{re.escape(module)}\s+import|import\s+{re.escape(module)}\b)"
    )
    hits: List[str] = []
    for root_name in search_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in sorted(root.rglob("*.py")):
            if "self_improve" in py_file.parts:
                continue
            rel = py_file.relative_to(repo_root).as_posix()
            try:
                lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                if "install_package" in line:
                    continue
                if pattern.match(line):
                    if rel not in hits:
                        hits.append(rel)
                    break
    return hits


def expand_dependency_import_targets(
    paths: List[str],
    evidence_refs: Iterable[str],
    repo_root: Path,
) -> List[str]:
    """Append all bare-import sites for modules mentioned in missing-module errors."""
    merged = list(paths)
    for module in missing_modules_from_evidence(evidence_refs):
        for path in files_with_bare_import_of(module, repo_root):
            if path not in merged:
                merged.append(path)
    return merged


def resolve_repair_targets(
    *,
    symptom_class: str,
    evidence_refs: Iterable[str],
    ledger_targets: Iterable[str],
    symptom_defaults: Callable[[str], List[str]],
    repo_root: Path,
    include_symptom_defaults: bool = True,
    max_paths: int = 16,
) -> List[str]:
    """Merge target paths: evidence stack frames first, then ledger, defaults, import expansion."""
    refs = list(evidence_refs)
    paths: List[str] = []

    for path in stack_ordered_framework_paths(refs, repo_root):
        if path not in paths:
            paths.append(path)

    for path in extract_framework_paths(refs, repo_root, max_paths=24):
        if path not in paths and (repo_root / path).is_file():
            paths.append(path)

    for path in ledger_targets:
        norm = str(path).replace("\\", "/")
        if norm and norm not in paths and (repo_root / norm).is_file():
            paths.append(norm)

    if include_symptom_defaults:
        for path in symptom_defaults(symptom_class):
            if path not in paths:
                paths.append(path)

    if symptom_class == "dependency_missing":
        paths = expand_dependency_import_targets(paths, refs, repo_root)

    return paths[:max_paths]
