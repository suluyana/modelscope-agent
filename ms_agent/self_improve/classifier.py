from __future__ import annotations
import hashlib
import os
from typing import Any, Dict, List, Optional

from ms_agent.self_improve.schemas import (
    EvidenceKind,
    EvidenceRef,
    IncidentClass,
    IncidentDetail,
    IncidentSeverity,
    TrajectoryAnalysis,
)


class Rulebook:
    def __init__(self, version: str = "v1") -> None:
        self.version = version
        self.infra_keywords = [
            "docker.sock", "Cannot connect to the Docker daemon", "buildx",
            "no matching manifest", "Connection refused",
            "Temporary failure in name resolution", "timed out", "timeout",
            "TLS handshake timeout", "Permission denied",
            "Operation not permitted", "Read-only file system", "ENOSPC",
            "command not found", "No module named", "pip install failed",
            "apt-get", "ConnectionError", "DockerException",
        ]
        self.framework_keywords = [
            "tool schema", "invalid tool arguments", "unexpected tool return",
            "max round exceeded", "orchestrator", "adapter",
            "ConfigError", "AgentLoader", "WorkflowLoader",
        ]
        self.task_solution_keywords = [
            "AssertionError", "FAILED ", "FAILURES", "AttributeError",
            "TypeError", "SyntaxError", "IndentationError", "NameError",
            "KeyError", "IndexError", "ValueError",
        ]
        self.base_score = 0.65
        self.keyword_score_per_hit = 0.15
        self.keyword_score_cap = 0.45
        self.structural_score = 0.20
        self.conflict_deduction = 0.30
        self.trajectory_score_per_signal = 0.20
        self.trajectory_score_cap = 0.40


class FailureClassifier:
    def __init__(self, rulebook: Optional[Rulebook] = None) -> None:
        self.rulebook = rulebook or Rulebook("v1")

    def _generate_fingerprint(
        self,
        error_type: str,
        stack_top3: str,
        source_file: str,
        adapter_name: str,
    ) -> str:
        raw = f"{error_type}|{stack_top3}|{source_file}|{adapter_name}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _read_evidence_content(self, evidence: EvidenceRef) -> str:
        if not os.path.exists(evidence.path):
            return ""
        try:
            with open(evidence.path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def classify(
        self,
        evidence_refs: List[EvidenceRef],
        run_context: Dict[str, Any],
    ) -> List[IncidentDetail]:
        exception_text = ""
        trial_log = ""
        agent_stdout = ""
        for ev in evidence_refs:
            if ev.kind in (EvidenceKind.EXCEPTION, EvidenceKind.TRACEBACK):
                exception_text += self._read_evidence_content(ev) + "\n"
            elif ev.kind == EvidenceKind.TRIAL_LOG:
                trial_log += self._read_evidence_content(ev) + "\n"
            elif ev.kind == EvidenceKind.AGENT_STDOUT:
                agent_stdout += self._read_evidence_content(ev) + "\n"

        exit_code = run_context.get("exit_code", 0)
        reward = run_context.get("reward")
        adapter_name = run_context.get("adapter_name", "unknown")
        traj: Optional[TrajectoryAnalysis] = run_context.get("trajectory_analysis")

        scores: Dict[IncidentClass, float] = {
            IncidentClass.FRAMEWORK_ERROR: 0.0,
            IncidentClass.INFRA_ERROR: 0.0,
            IncidentClass.TASK_SOLUTION_ERROR: 0.0,
        }

        combined_text = f"{exception_text}\n{trial_log}"
        # Include a tail of agent_stdout for keyword matching
        stdout_tail = agent_stdout[-8000:] if agent_stdout else ""
        search_text = f"{combined_text}\n{stdout_tail}"

        # ---- 1. Base Score ----
        is_framework_stack = (
            "ms_agent/" in exception_text
            or "scripts/" in exception_text
            or "ms_agent/" in trial_log
            or "scripts/" in trial_log
        )
        if is_framework_stack:
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.base_score

        has_missing_module = "No module named" in search_text
        has_infra_system_err = (
            any(
                kw in exception_text
                for kw in ["ConnectionError", "DockerException", "Timeout"]
            )
            or has_missing_module
        )
        if has_infra_system_err:
            scores[IncidentClass.INFRA_ERROR] += self.rulebook.base_score

        has_test_failure = any(
            kw in search_text for kw in ["AssertionError", "FAILED "]
        )
        if reward == 0.0 or has_test_failure:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.base_score

        if "Execution timed out" in search_text:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.base_score
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.base_score / 2.0

        # ---- 2. Keyword hits ----
        search_lower = search_text.lower()
        infra_hits = sum(
            1 for kw in self.rulebook.infra_keywords if kw.lower() in search_lower
        )
        framework_hits = sum(
            1 for kw in self.rulebook.framework_keywords if kw.lower() in search_lower
        )
        task_solution_hits = sum(
            1
            for kw in self.rulebook.task_solution_keywords
            if kw.lower() in search_lower
        )

        scores[IncidentClass.INFRA_ERROR] += min(
            infra_hits * self.rulebook.keyword_score_per_hit,
            self.rulebook.keyword_score_cap,
        )
        if is_framework_stack or framework_hits > 0:
            scores[IncidentClass.FRAMEWORK_ERROR] += min(
                framework_hits * self.rulebook.keyword_score_per_hit,
                self.rulebook.keyword_score_cap,
            )
        if task_solution_hits > 0:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += min(
                task_solution_hits * self.rulebook.keyword_score_per_hit,
                self.rulebook.keyword_score_cap,
            )

        # ---- 3. Structural support ----
        if exit_code is not None and exit_code != 0:
            scores[IncidentClass.INFRA_ERROR] += self.rulebook.structural_score
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.structural_score
        if reward == 0.0:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.structural_score

        # ---- 4. Trajectory-based scoring ----
        scores = self._apply_trajectory_signals(scores, traj)

        # ---- 5. Conflict Arbitration ----
        if (
            scores[IncidentClass.FRAMEWORK_ERROR] > 0
            and scores[IncidentClass.INFRA_ERROR] > 0
        ):
            if is_framework_stack:
                scores[IncidentClass.INFRA_ERROR] -= self.rulebook.conflict_deduction
            else:
                scores[IncidentClass.FRAMEWORK_ERROR] -= self.rulebook.conflict_deduction

        if has_missing_module:
            scores[IncidentClass.INFRA_ERROR] = max(
                scores[IncidentClass.INFRA_ERROR], 0.95
            )
            scores[IncidentClass.FRAMEWORK_ERROR] = min(
                scores[IncidentClass.FRAMEWORK_ERROR], 0.54
            )

        if (
            scores[IncidentClass.TASK_SOLUTION_ERROR] > 0
            and scores[IncidentClass.FRAMEWORK_ERROR] > 0
        ):
            scores[IncidentClass.TASK_SOLUTION_ERROR] -= (
                self.rulebook.conflict_deduction
            )

        # ---- 6. Build incidents ----
        incidents: list[tuple[IncidentClass, float]] = []
        for cls, raw_score in scores.items():
            conf = min(max(raw_score, 0.0), 1.0)
            if conf >= 0.55:
                incidents.append((cls, conf))

        if not incidents:
            incidents.append((IncidentClass.UNKNOWN, 0.0))

        incidents.sort(key=lambda x: x[1], reverse=True)

        final_incidents: list[IncidentDetail] = []
        for rank, (cls, conf) in enumerate(incidents):
            error_type = cls.value
            stack_top3 = exception_text.splitlines()[:3]
            stack_str = "|".join(stack_top3) if stack_top3 else "no_stack"
            source_file = "unknown_file"

            fingerprint = self._generate_fingerprint(
                error_type, stack_str, source_file, adapter_name
            )
            severity = IncidentSeverity.HIGH if rank == 0 else IncidentSeverity.MEDIUM
            if cls == IncidentClass.UNKNOWN or conf < 0.55:
                severity = IncidentSeverity.LOW

            summary = f"Detected {cls.value} with confidence {conf:.2f}"
            if cls == IncidentClass.UNKNOWN and exit_code != 0 and not exception_text:
                summary += " (Non-zero exit but no stacktrace. Suggest adding debug logs)"

            # Enrich summary with trajectory insights
            summary = self._enrich_summary(summary, cls, traj)

            final_incidents.append(
                IncidentDetail(
                    **{
                        "incident_id": hashlib.md5(
                            f"{fingerprint}_{rank}".encode()
                        ).hexdigest()[:8],
                        "fingerprint": fingerprint,
                        "class": cls,
                        "severity": severity,
                        "confidence": conf,
                        "summary": summary,
                        "rank": rank,
                        "evidence_refs": [ev.path for ev in evidence_refs],
                    }
                )
            )

        return final_incidents

    def _apply_trajectory_signals(
        self,
        scores: Dict[IncidentClass, float],
        traj: Optional[TrajectoryAnalysis],
    ) -> Dict[IncidentClass, float]:
        if traj is None:
            return scores

        rb = self.rulebook
        updated = dict(scores)

        # Stuck loops and repeated failure patterns → framework gap
        stuck_loop_count = sum(
            1 for p in traj.repeated_failure_patterns if "Stuck loop" in p
        )
        tool_fail_count = sum(
            1 for p in traj.repeated_failure_patterns if "failed" in p.lower()
        )
        repeated_cmd_count = sum(
            1 for p in traj.repeated_failure_patterns if "Command repeated" in p
        )

        if stuck_loop_count > 0:
            updated[IncidentClass.FRAMEWORK_ERROR] += min(
                stuck_loop_count * rb.trajectory_score_per_signal,
                rb.trajectory_score_cap,
            )

        if tool_fail_count > 0:
            updated[IncidentClass.FRAMEWORK_ERROR] += min(
                tool_fail_count * rb.trajectory_score_per_signal,
                rb.trajectory_score_cap,
            )

        if repeated_cmd_count > 0:
            updated[IncidentClass.FRAMEWORK_ERROR] += min(
                repeated_cmd_count * rb.trajectory_score_per_signal * 0.5,
                rb.trajectory_score_cap,
            )

        # Access denied errors → infra
        access_denied = sum(
            1 for e in traj.errors_encountered if "access denied" in e.lower()
        )
        if access_denied > 0:
            updated[IncidentClass.INFRA_ERROR] += rb.trajectory_score_per_signal

        # Timeout from trajectory (more reliable than keyword matching)
        if traj.final_state == "timeout":
            high_turn_timeout = traj.total_turns >= 8
            if high_turn_timeout:
                # Agent ran many turns but still timed out → strategy inefficiency
                updated[IncidentClass.FRAMEWORK_ERROR] += rb.trajectory_score_per_signal
            else:
                # Few turns before timeout → single operation hung
                updated[IncidentClass.TASK_SOLUTION_ERROR] += (
                    rb.trajectory_score_per_signal
                )

        # Low turn count + errors → likely env crash
        if traj.total_turns <= 2 and len(traj.errors_encountered) > 0:
            updated[IncidentClass.INFRA_ERROR] += rb.trajectory_score_per_signal

        # High tool failure rate → framework error handling gap
        total_calls = len(traj.tool_calls)
        failed_calls = sum(1 for tc in traj.tool_calls if not tc.success)
        if total_calls >= 5 and failed_calls / total_calls >= 0.5:
            updated[IncidentClass.FRAMEWORK_ERROR] += rb.trajectory_score_per_signal

        return updated

    def _enrich_summary(
        self,
        summary: str,
        cls: IncidentClass,
        traj: Optional[TrajectoryAnalysis],
    ) -> str:
        if traj is None:
            return summary

        parts = [summary]

        if cls == IncidentClass.FRAMEWORK_ERROR:
            stuck = [p for p in traj.repeated_failure_patterns if "Stuck loop" in p]
            if stuck:
                parts.append(f"[trajectory: {stuck[0]}]")
            elif traj.final_state == "timeout" and traj.total_turns >= 8:
                parts.append(
                    f"[trajectory: timeout after {traj.total_turns} turns"
                    f" — agent strategy inefficiency]"
                )
            fail_patterns = [
                p for p in traj.repeated_failure_patterns if "failed" in p.lower()
            ]
            if fail_patterns:
                parts.append(f"[trajectory: {fail_patterns[0]}]")

        elif cls == IncidentClass.INFRA_ERROR:
            if traj.total_turns <= 2 and traj.errors_encountered:
                parts.append(
                    f"[trajectory: crashed at turn {traj.total_turns}]"
                )

        return " ".join(parts)
