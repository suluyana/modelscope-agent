import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ms_agent.self_improve.schemas import (
    CapabilityGapSignal,
    EvidenceRef,
    ImprovementType,
    IncidentClass,
    IncidentSignal,
    RootCauseHypothesis,
    SymptomClass,
)


class CapabilityGapMiner:
    """Extracts generalizable capability gaps from non-framework failures."""

    def __init__(self, config: Dict[str, Any]):
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

        symptom = self._symptom(text)
        root_cause, improvement, confidence, rationale = self._hypothesize(
            incident_class, symptom, text
        )
        cluster_key = self._cluster_key(incident_class, symptom, root_cause, text)
        cluster_cfg = self.known_clusters.get(cluster_key, {})
        support_count = max(1, int(cluster_cfg.get("support_count", 1)))
        min_support = int(cluster_cfg.get("min_support_required", self.min_cluster_size))
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
    ) -> tuple[RootCauseHypothesis, ImprovementType, float, str]:
        lowered = text.lower()
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
                "Missing task artifact can be a prompt or planning policy gap only after cross-case support.",
            )
        if incident_class == IncidentClass.TASK_SOLUTION_ERROR:
            return (
                RootCauseHypothesis.TASK_IMPLEMENTATION_INCOMPLETE,
                ImprovementType.OFFLINE_REPORT,
                0.55,
                "Task verification failure lacks enough evidence for safe framework repair.",
            )
        if incident_class == IncidentClass.INFRA_ERROR:
            return (
                RootCauseHypothesis.ENVIRONMENT_DEPENDENCY_GAP,
                ImprovementType.ENVIRONMENT_RECIPE,
                0.70,
                "Infrastructure failure should produce an environment recipe, not code patch.",
            )
        return (
            RootCauseHypothesis.UNKNOWN,
            ImprovementType.NONE,
            0.0,
            "No capability-gap hypothesis available.",
        )

    def _symptom(self, text: str) -> SymptomClass:
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

    def _cluster_key(
        self,
        incident_class: IncidentClass,
        symptom: SymptomClass,
        root_cause: RootCauseHypothesis,
        text: str,
    ) -> str:
        tokens = [incident_class.value, symptom.value, root_cause.value]
        if "no module named" in text.lower():
            tokens.append("missing_module")
        if "/app/" in text:
            tokens.append("app_artifact")
        raw = "|".join(tokens)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        return f"{symptom.value}:{digest}"

    def _combined_evidence(self, evidence_refs: Iterable[EvidenceRef]) -> str:
        chunks = []
        for ev in evidence_refs:
            try:
                content = Path(ev.path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            chunks.append(content[-4000:])
        return "\n".join(chunks)

    def _safe_source_paths(self, paths: Iterable[str]) -> List[str]:
        safe_paths = []
        for path in paths:
            normalized = str(Path(path))
            if normalized.startswith("../") or normalized in {".", ".."}:
                continue
            if not (normalized.startswith("ms_agent/") or normalized.startswith("scripts/")):
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
