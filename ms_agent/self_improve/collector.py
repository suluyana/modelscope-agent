"""Artifact collector for self-improve.

Collects execution artifacts from trial directories and standardizes them as
EvidenceRef objects.  Supports both the file-based layout produced by
``fast_local`` and the richer ``result.json`` written by EvalScope + Harbor.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from ms_agent.self_improve.schemas import EvidenceKind, EvidenceRef


class ArtifactCollector:

    def __init__(self, trial_dir: str) -> None:
        self.trial_dir = trial_dir
        self._evalscope_meta: Dict[str, Any] = {}

    @property
    def evalscope_meta(self) -> Dict[str, Any]:
        return self._evalscope_meta

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_digest(self, filepath: str) -> Optional[str]:
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except Exception:
            return None

    def _write_extracted(self, rel_path: str, content: str) -> str:
        full = os.path.join(self.trial_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return full

    # ------------------------------------------------------------------
    # EvalScope result.json extraction
    # ------------------------------------------------------------------

    def _try_evalscope_result_json(self) -> List[EvidenceRef]:
        """Parse EvalScope ``result.json`` and extract embedded evidence."""
        result_path = os.path.join(self.trial_dir, "result.json")
        if not os.path.isfile(result_path):
            return []

        try:
            with open(result_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []

        agent_result = data.get("agent_result")
        if not isinstance(agent_result, dict):
            return []

        evidences: List[EvidenceRef] = []

        # --- agent stdout ---
        metadata = agent_result.get("metadata") or {}
        stdout_text = metadata.get("stdout") or ""
        if stdout_text:
            path = self._write_extracted("agent/agent_stdout.txt", stdout_text)
            evidences.append(
                EvidenceRef(
                    path=path,
                    kind=EvidenceKind.AGENT_STDOUT,
                    digest=self._get_digest(path),
                )
            )

        stderr_text = metadata.get("stderr") or ""
        if stderr_text:
            path = self._write_extracted("agent/agent_stderr.txt", stderr_text)
            evidences.append(
                EvidenceRef(
                    path=path,
                    kind=EvidenceKind.TRIAL_LOG,
                    digest=self._get_digest(path),
                )
            )

        # --- verifier output ---
        verifier = data.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        if rewards:
            path = self._write_extracted(
                "verifier/rewards_extracted.json",
                json.dumps(rewards, ensure_ascii=False, indent=2),
            )
            evidences.append(
                EvidenceRef(
                    path=path,
                    kind=EvidenceKind.VERIFIER_OUTPUT,
                    digest=self._get_digest(path),
                )
            )

        # --- exception info ---
        exception_info = data.get("exception_info")
        if exception_info:
            if isinstance(exception_info, dict):
                exc_text = json.dumps(exception_info, ensure_ascii=False, indent=2)
            else:
                exc_text = str(exception_info)
            path = self._write_extracted("exception_extracted.txt", exc_text)
            evidences.append(
                EvidenceRef(
                    path=path,
                    kind=EvidenceKind.EXCEPTION,
                    digest=self._get_digest(path),
                )
            )

        # --- capture metadata for downstream consumers ---
        self._evalscope_meta = {
            "trial_name": data.get("trial_name", ""),
            "started_at": data.get("started_at", ""),
            "finished_at": data.get("finished_at", ""),
            "reward": rewards.get("reward"),
            "return_code": metadata.get("return_code"),
        }

        # The result.json itself is also useful as SUMMARY evidence
        evidences.append(
            EvidenceRef(
                path=result_path,
                kind=EvidenceKind.SUMMARY,
                digest=self._get_digest(result_path),
            )
        )

        return evidences

    # ------------------------------------------------------------------
    # File-based collection (fast_local fallback)
    # ------------------------------------------------------------------

    def _collect_file_based(self) -> List[EvidenceRef]:
        evidences: List[EvidenceRef] = []

        expected_files = {
            "exception.txt": EvidenceKind.EXCEPTION,
            "trial.log": EvidenceKind.TRIAL_LOG,
            "verifier/test-stdout.txt": EvidenceKind.VERIFIER_OUTPUT,
            "agent/trajectory.json": EvidenceKind.TRAJECTORY,
            "result.json": EvidenceKind.SUMMARY,
        }

        for rel_path, kind in expected_files.items():
            full_path = os.path.join(self.trial_dir, rel_path)
            if os.path.exists(full_path):
                evidences.append(
                    EvidenceRef(
                        path=full_path,
                        kind=kind,
                        digest=self._get_digest(full_path),
                    )
                )

        traceback_path = os.path.join(self.trial_dir, "traceback.txt")
        if os.path.exists(traceback_path):
            evidences.append(
                EvidenceRef(
                    path=traceback_path,
                    kind=EvidenceKind.TRACEBACK,
                    digest=self._get_digest(traceback_path),
                )
            )

        memory_dir = os.path.join(self.trial_dir, ".memory")
        if os.path.isdir(memory_dir):
            for fname in os.listdir(memory_dir):
                if fname.endswith(".json"):
                    full_path = os.path.join(memory_dir, fname)
                    evidences.append(
                        EvidenceRef(
                            path=full_path,
                            kind=EvidenceKind.TRAJECTORY,
                            digest=self._get_digest(full_path),
                        )
                    )

        return evidences

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def collect(self) -> List[EvidenceRef]:
        """Collect all available evidence from the trial directory.

        Tries EvalScope ``result.json`` first (richer data).  Falls back to
        the file-based layout for ``fast_local`` or when the result.json
        does not contain ``agent_result``.
        """
        evalscope_evidence = self._try_evalscope_result_json()
        if evalscope_evidence:
            # Still pick up any extra files not covered by result.json
            file_evidence = self._collect_file_based()
            seen_paths = {e.path for e in evalscope_evidence}
            for ev in file_evidence:
                if ev.path not in seen_paths:
                    evalscope_evidence.append(ev)
            return evalscope_evidence

        return self._collect_file_based()
