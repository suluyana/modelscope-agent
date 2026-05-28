import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ms_agent.self_improve.schemas import (
    CapabilityGapSignal,
    EvidenceRef,
    ImprovementType,
    IncidentClass,
    IncidentSignal,
    RootCauseHypothesis,
    SymptomClass,
    TrajectoryAnalysis,
)


class CapabilityGapMiner:
    """Extracts generalizable capability gaps from failures.

    Uses both textual evidence (exception, trial_log) and structured
    TrajectoryAnalysis when available for richer root-cause hypotheses.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        cfg = config.get("capability", {})
        self.enabled = bool(cfg.get("enabled", True))
        self.min_cluster_size = int(cfg.get("min_cluster_size", 2))
        self.allow_single_case_framework_gap = bool(
            cfg.get("allow_single_case_framework_gap", False)
        )
        self.known_clusters = cfg.get("known_clusters", {}) or {}

    def mine(self, signal: IncidentSignal) -> CapabilityGapSignal:
        if not self.enabled:
            return self._signal(
                signal=signal,
                symptom=SymptomClass.UNKNOWN,
                root_cause=RootCauseHypothesis.UNKNOWN,
                improvement=ImprovementType.NONE,
                confidence=0.0,
                rationale="Capability-gap mining disabled.",
            )

        text = self._combined_evidence(signal.evidence_index)
        primary = signal.primary_incident
        incident_class = primary.incident_class if primary else IncidentClass.UNKNOWN
        traj = signal.trajectory_analysis

        symptom = self._symptom(text, traj)
        root_cause, improvement, confidence, rationale = self._hypothesize(
            incident_class, symptom, text, traj
        )
        cluster_key = self._cluster_key(incident_class, symptom, root_cause, text, traj)
        cluster_cfg = self.known_clusters.get(cluster_key, {})
        support_count = max(1, int(cluster_cfg.get("support_count", 1)))
        min_support = int(
            cluster_cfg.get("min_support_required", self.min_cluster_size)
        )
        target_source_paths = self._safe_source_paths(
            cluster_cfg.get("target_source_paths", [])
        )

        repair_allowed = self._repair_allowed(
            incident_class=incident_class,
            improvement=improvement,
            support_count=support_count,
            min_support=min_support,
            target_source_paths=target_source_paths,
        )

        return self._signal(
            signal=signal,
            symptom=symptom,
            root_cause=root_cause,
            improvement=improvement,
            confidence=confidence,
            rationale=rationale,
            cluster_key=cluster_key,
            support_count=support_count,
            min_support=min_support,
            repair_allowed=repair_allowed,
            target_source_paths=target_source_paths,
        )

    def _repair_allowed(
        self,
        *,
        incident_class: IncidentClass,
        improvement: ImprovementType,
        support_count: int,
        min_support: int,
        target_source_paths: List[str],
    ) -> bool:
        if improvement == ImprovementType.NONE:
            return False
        if incident_class == IncidentClass.FRAMEWORK_ERROR:
            return True
        if improvement not in {
            ImprovementType.FRAMEWORK_PATCH,
            ImprovementType.PROMPT_POLICY_PATCH,
            ImprovementType.TOOLING_ADAPTER_PATCH,
        }:
            return False
        if not target_source_paths:
            return False
        if support_count >= min_support:
            return True
        return self.allow_single_case_framework_gap and support_count == 1

    def _hypothesize(
        self,
        incident_class: IncidentClass,
        symptom: SymptomClass,
        text: str,
        traj: Optional[TrajectoryAnalysis] = None,
    ) -> tuple[RootCauseHypothesis, ImprovementType, float, str]:
        lowered = text.lower()

        # --- Trajectory-derived hypotheses (higher priority) ---
        if traj is not None:
            hyp = self._hypothesize_from_trajectory(incident_class, symptom, traj)
            if hyp is not None:
                return hyp

        # --- Text-based hypotheses (fallback) ---
        if incident_class == IncidentClass.FRAMEWORK_ERROR:
            return (
                RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                ImprovementType.FRAMEWORK_PATCH,
                0.90,
                "Framework-classified failure may be repairable in framework code.",
            )
        if "ms_agent/" in lowered or "scripts/" in lowered:
            if "attributeerror" in lowered or "traceback" in lowered:
                return (
                    RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                    ImprovementType.FRAMEWORK_PATCH,
                    0.82,
                    "Framework stack trace detected in trial evidence.",
                )
        if symptom == SymptomClass.DEPENDENCY_MISSING:
            return (
                RootCauseHypothesis.ENVIRONMENT_DEPENDENCY_GAP,
                ImprovementType.ENVIRONMENT_RECIPE,
                0.85,
                "Missing dependency should be handled as environment or setup recipe.",
            )
        if symptom == SymptomClass.EXECUTION_TIMEOUT:
            return (
                RootCauseHypothesis.TOOLING_ADAPTER_GAP,
                ImprovementType.TOOLING_ADAPTER_PATCH,
                0.65,
                "Timeouts may indicate a reusable tool orchestration or adapter gap.",
            )
        if "tool" in lowered and ("failed" in lowered or "invalid" in lowered):
            return (
                RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                ImprovementType.TOOLING_ADAPTER_PATCH,
                0.70,
                "Tool failure pattern may be a reusable framework capability gap.",
            )
        if symptom == SymptomClass.ARTIFACT_MISSING:
            return (
                RootCauseHypothesis.TASK_IMPLEMENTATION_INCOMPLETE,
                ImprovementType.PROMPT_POLICY_PATCH,
                0.60,
                "Missing task artifact can be a prompt or planning policy gap"
                " only after cross-case support.",
            )
        if incident_class == IncidentClass.TASK_SOLUTION_ERROR:
            return (
                RootCauseHypothesis.TASK_IMPLEMENTATION_INCOMPLETE,
                ImprovementType.OFFLINE_REPORT,
                0.55,
                "Task verification failure lacks enough evidence"
                " for safe framework repair.",
            )
        if incident_class == IncidentClass.INFRA_ERROR:
            return (
                RootCauseHypothesis.ENVIRONMENT_DEPENDENCY_GAP,
                ImprovementType.ENVIRONMENT_RECIPE,
                0.70,
                "Infrastructure failure should produce an environment recipe,"
                " not code patch.",
            )
        return (
            RootCauseHypothesis.UNKNOWN,
            ImprovementType.NONE,
            0.0,
            "No capability-gap hypothesis available.",
        )

    def _hypothesize_from_trajectory(
        self,
        incident_class: IncidentClass,
        symptom: SymptomClass,
        traj: TrajectoryAnalysis,
    ) -> Optional[tuple[RootCauseHypothesis, ImprovementType, float, str]]:
        """Generate hypothesis from trajectory patterns when signals are strong."""

        stuck_loops = [
            p for p in traj.repeated_failure_patterns if "Stuck loop" in p
        ]
        tool_failures = [
            p for p in traj.repeated_failure_patterns if "failed" in p.lower()
        ]
        repeated_cmds = [
            p for p in traj.repeated_failure_patterns if "Command repeated" in p
        ]

        # Stuck loops → agent recovery gap; framework should detect+break loops
        if stuck_loops:
            detail = stuck_loops[0]
            return (
                RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                ImprovementType.PROMPT_POLICY_PATCH,
                0.85,
                f"Agent stuck in loop ({detail}). Framework should detect"
                " repetitive failures and try alternative strategies.",
            )

        # Persistent tool failures → tooling adapter gap
        if tool_failures:
            detail = tool_failures[0]
            total_calls = len(traj.tool_calls)
            failed_calls = sum(1 for tc in traj.tool_calls if not tc.success)
            if total_calls > 0 and failed_calls / total_calls >= 0.4:
                return (
                    RootCauseHypothesis.TOOLING_ADAPTER_GAP,
                    ImprovementType.TOOLING_ADAPTER_PATCH,
                    0.80,
                    f"High tool failure rate ({failed_calls}/{total_calls}):"
                    f" {detail}. Tool adapter may need error handling improvement.",
                )

        # High-turn timeout → agent planning inefficiency
        if traj.final_state == "timeout" and traj.total_turns >= 8:
            return (
                RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                ImprovementType.PROMPT_POLICY_PATCH,
                0.75,
                f"Timeout after {traj.total_turns} turns with"
                f" {len(traj.tool_calls)} tool calls. Agent planning"
                " or task decomposition may be inefficient.",
            )

        # Repeated commands without stuck loop → agent retrying without adaptation
        if repeated_cmds and traj.total_turns >= 5:
            detail = repeated_cmds[0]
            return (
                RootCauseHypothesis.FRAMEWORK_CAPABILITY_GAP,
                ImprovementType.PROMPT_POLICY_PATCH,
                0.70,
                f"Agent repeatedly runs same commands ({detail}) without"
                " adapting approach. Prompt policy should encourage"
                " strategy variation on failure.",
            )

        # Early crash (low turns + errors) → environment issue
        if traj.total_turns <= 2 and len(traj.errors_encountered) >= 2:
            return (
                RootCauseHypothesis.ENVIRONMENT_DEPENDENCY_GAP,
                ImprovementType.ENVIRONMENT_RECIPE,
                0.75,
                f"Crashed at turn {traj.total_turns} with"
                f" {len(traj.errors_encountered)} errors."
                " Likely an environment or setup issue.",
            )

        return None

    def _symptom(
        self,
        text: str,
        traj: Optional[TrajectoryAnalysis] = None,
    ) -> SymptomClass:
        # Trajectory-based symptoms take priority (more precise)
        if traj is not None:
            traj_symptom = self._symptom_from_trajectory(traj)
            if traj_symptom != SymptomClass.UNKNOWN:
                return traj_symptom

        # Text-based fallback
        lowered = text.lower()
        if "no module named" in lowered or "modulenotfounderror" in lowered:
            return SymptomClass.DEPENDENCY_MISSING
        if "execution timed out" in lowered or "timed out" in lowered:
            return SymptomClass.EXECUTION_TIMEOUT
        if "does not exist" in lowered or "filenotfounderror" in lowered:
            return SymptomClass.ARTIFACT_MISSING
        if "assertionerror" in lowered or "failed " in lowered:
            return SymptomClass.TEST_ASSERTION_FAILED
        if "docker" in lowered or "connection refused" in lowered:
            return SymptomClass.ENVIRONMENT_FAILURE
        if "tool schema" in lowered or "invalid tool" in lowered:
            return SymptomClass.TOOL_OR_PROTOCOL_ERROR
        if "attributeerror" in lowered and (
            "ms_agent/" in lowered or "scripts/" in lowered
        ):
            return SymptomClass.TOOL_OR_PROTOCOL_ERROR
        return SymptomClass.UNKNOWN

    def _symptom_from_trajectory(self, traj: TrajectoryAnalysis) -> SymptomClass:
        stuck_loops = any("Stuck loop" in p for p in traj.repeated_failure_patterns)
        if stuck_loops:
            return SymptomClass.STUCK_LOOP

        tool_repeated = any(
            "failed" in p.lower() for p in traj.repeated_failure_patterns
        )
        if tool_repeated:
            return SymptomClass.TOOL_REPEATED_FAILURE

        if traj.final_state == "timeout":
            return SymptomClass.EXECUTION_TIMEOUT

        access_denied = any(
            "access denied" in e.lower() for e in traj.errors_encountered
        )
        if access_denied:
            return SymptomClass.ENVIRONMENT_FAILURE

        return SymptomClass.UNKNOWN

    def _cluster_key(
        self,
        incident_class: IncidentClass,
        symptom: SymptomClass,
        root_cause: RootCauseHypothesis,
        text: str,
        traj: Optional[TrajectoryAnalysis] = None,
    ) -> str:
        tokens = [incident_class.value, symptom.value, root_cause.value]
        if "no module named" in text.lower():
            tokens.append("missing_module")
        if "/app/" in text:
            tokens.append("app_artifact")

        # Trajectory-based cluster refinement
        if traj is not None:
            if any("Stuck loop" in p for p in traj.repeated_failure_patterns):
                tokens.append("stuck_loop")
            failed_tool_names = sorted(
                set(
                    tc.tool_name
                    for tc in traj.tool_calls
                    if not tc.success
                )
            )
            if failed_tool_names:
                tokens.append(f"failed_tools:{'|'.join(failed_tool_names[:3])}")
            if traj.final_state == "timeout" and traj.total_turns >= 8:
                tokens.append("high_turn_timeout")

        raw = "|".join(tokens)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        return f"{symptom.value}:{digest}"

    def _combined_evidence(self, evidence_refs: Iterable[EvidenceRef]) -> str:
        chunks = []
        for ev in evidence_refs:
            try:
                content = Path(ev.path).read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                continue
            chunks.append(content[-4000:])
        return "\n".join(chunks)

    def _safe_source_paths(self, paths: Iterable[str]) -> List[str]:
        safe_paths: list[str] = []
        for path in paths:
            normalized = str(Path(path))
            if normalized.startswith("../") or normalized in {".", ".."}:
                continue
            if not (
                normalized.startswith("ms_agent/")
                or normalized.startswith("scripts/")
            ):
                continue
            if normalized not in safe_paths:
                safe_paths.append(normalized)
        return safe_paths

    def _signal(
        self,
        *,
        signal: IncidentSignal,
        symptom: SymptomClass,
        root_cause: RootCauseHypothesis,
        improvement: ImprovementType,
        confidence: float,
        rationale: str,
        cluster_key: str | None = None,
        support_count: int = 1,
        min_support: int | None = None,
        repair_allowed: bool = False,
        target_source_paths: List[str] | None = None,
    ) -> CapabilityGapSignal:
        return CapabilityGapSignal(
            symptom_class=symptom,
            root_cause_hypothesis=root_cause,
            improvement_type=improvement,
            confidence=confidence,
            cluster_key=cluster_key or f"{symptom.value}:none",
            support_count=support_count,
            min_support_required=min_support or self.min_cluster_size,
            repair_allowed=repair_allowed,
            rationale=rationale,
            target_source_paths=target_source_paths or [],
            evidence_refs=[ev.path for ev in signal.evidence_index],
        )
