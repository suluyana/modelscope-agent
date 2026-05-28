from typing import List, Optional
from pydantic import BaseModel, Field
from ms_agent.self_improve.schemas import (
    CapabilityGapSignal,
    DecisionResult,
    ImprovementType,
    IncidentClass,
    IncidentDetail,
    IncidentSignal,
)

class RepairPlan(BaseModel):
    should_repair: bool
    reason: str
    suggested_mode: str  # observe, assist, auto
    target_domains: List[str] = Field(default_factory=list)
    target_source_paths: List[str] = Field(default_factory=list)
    repair_prompt: str = ""

class RepairPlanner:
    def __init__(self, mode: str = "assist"):
        self.mode = mode

    def plan(
        self,
        signal: IncidentSignal,
        decision: Optional[DecisionResult] = None,
        capability_gap: Optional[CapabilityGapSignal] = None,
    ) -> RepairPlan:
        if decision is not None:
            primary = signal.primary_incident
            fingerprint = decision.incident_fingerprint or (
                primary.fingerprint if primary else "unknown"
            )
            cls = decision.decision_class
            conf = decision.decision_confidence
            mode = decision.suggested_mode

            if mode == "ask_human":
                return RepairPlan(
                    should_repair=False,
                    reason=f"Decision requires human escalation: {decision.reason}",
                    suggested_mode="ask_human",
                    repair_prompt=decision.reason,
                )

            if cls == IncidentClass.INFRA_ERROR:
                capability_plan = self._plan_capability_gap(capability_gap)
                if capability_plan:
                    return capability_plan
                return RepairPlan(
                    should_repair=False,
                    reason="Infrastructure error detected. Recommend environment check or component substitution.",
                    suggested_mode="observe",
                    repair_prompt=decision.reason,
                )

            if cls == IncidentClass.TASK_SOLUTION_ERROR:
                capability_plan = self._plan_capability_gap(capability_gap)
                if capability_plan:
                    return capability_plan
                return RepairPlan(
                    should_repair=False,
                    reason="Task solution error detected. Skipping framework repair.",
                    suggested_mode="observe",
                    repair_prompt=decision.reason,
                )

            if cls == IncidentClass.FRAMEWORK_ERROR:
                should_repair = mode in ("assist", "auto")
                return RepairPlan(
                    should_repair=should_repair,
                    reason=f"{decision.reason} (confidence={conf:.2f})",
                    suggested_mode=mode if should_repair else "observe",
                    target_domains=["ms_agent/", "scripts/"],
                    target_source_paths=(
                        capability_gap.target_source_paths if capability_gap else []
                    ),
                    repair_prompt=f"Fix framework error fingerprint={fingerprint}.",
                )

            return RepairPlan(
                should_repair=False,
                reason=f"Decision blocked repair: {decision.reason}",
                suggested_mode="observe",
                repair_prompt=decision.reason,
            )

        primary: Optional[IncidentDetail] = signal.primary_incident
        
        if not primary:
            return RepairPlan(
                should_repair=False,
                reason="No primary incident found.",
                suggested_mode="observe"
            )

        if primary.incident_class == IncidentClass.UNKNOWN:
            # Check if this is a silent crash without a stacktrace
            if "No stacktrace" in primary.summary or "no stacktrace" in primary.summary.lower():
                return RepairPlan(
                    should_repair=False,
                    reason=f"Silent crash detected. {primary.summary}",
                    suggested_mode="observe",
                    repair_prompt="The execution failed with a non-zero exit code, but no stack trace or explicit error was logged. Please add debug logging (e.g. `logger.debug` or `print`) around the suspected failure points to capture more context for the next run."
                )
            return RepairPlan(
                should_repair=False,
                reason=f"Unknown incident class: {primary.incident_class}",
                suggested_mode="observe",
            )

        if primary.confidence < 0.75:
            capability_plan = self._plan_capability_gap(capability_gap)
            if capability_plan:
                return capability_plan
            # Low confidence -> degrade to assist if in auto
            mode = "assist" if self.mode == "auto" else self.mode
            return RepairPlan(
                should_repair=True if mode == "assist" else False,
                reason=f"Low confidence ({primary.confidence:.2f}). Degraded to assist.",
                suggested_mode=mode,
                repair_prompt=f"Please investigate the low confidence {primary.incident_class.value}."
            )

        if primary.incident_class == IncidentClass.INFRA_ERROR:
            capability_plan = self._plan_capability_gap(capability_gap)
            if capability_plan:
                return capability_plan
            # P0: only output suggestions for infra
            # Handle component substitution hint
            return RepairPlan(
                should_repair=False,
                reason="Infrastructure error detected. Recommend environment check or component substitution.",
                suggested_mode="observe",
                repair_prompt="Check network, docker daemon, or consider component substitution (e.g., Jina -> Tavily) if it's a persistent block."
            )
            
        if primary.incident_class == IncidentClass.TASK_SOLUTION_ERROR:
            capability_plan = self._plan_capability_gap(capability_gap)
            if capability_plan:
                return capability_plan
            return RepairPlan(
                should_repair=False,
                reason="Task solution error detected. Skipping framework repair.",
                suggested_mode="observe"
            )

        if primary.incident_class == IncidentClass.FRAMEWORK_ERROR:
            return RepairPlan(
                should_repair=True,
                reason="Framework error detected. Initiating repair.",
                suggested_mode=self.mode,
                target_domains=["ms_agent/", "scripts/"],
                repair_prompt=f"Fix the framework error identified by fingerprint {primary.fingerprint}."
            )
            
        return RepairPlan(
            should_repair=False,
            reason=f"Unknown incident class: {primary.incident_class}",
            suggested_mode="observe"
        )

    def _plan_capability_gap(
        self, capability_gap: Optional[CapabilityGapSignal]
    ) -> Optional[RepairPlan]:
        if not capability_gap or not capability_gap.repair_allowed:
            return None
        if capability_gap.improvement_type not in {
            ImprovementType.FRAMEWORK_PATCH,
            ImprovementType.PROMPT_POLICY_PATCH,
            ImprovementType.TOOLING_ADAPTER_PATCH,
        }:
            return None
        mode = self.mode if self.mode in ("assist", "auto") else "observe"
        should_repair = mode in ("assist", "auto")
        return RepairPlan(
            should_repair=should_repair,
            reason=(
                "Cross-case capability gap repair allowed: "
                f"{capability_gap.cluster_key} "
                f"support={capability_gap.support_count}/"
                f"{capability_gap.min_support_required}; "
                f"{capability_gap.rationale}"
            ),
            suggested_mode=mode if should_repair else "observe",
            target_domains=["ms_agent/", "scripts/"],
            target_source_paths=capability_gap.target_source_paths,
            repair_prompt=(
                "Fix a generalized self-improve capability gap. "
                f"symptom={capability_gap.symptom_class.value}; "
                f"root_cause={capability_gap.root_cause_hypothesis.value}; "
                f"improvement_type={capability_gap.improvement_type.value}; "
                "Do not hard-code any benchmark task name, expected answer, or /app artifact."
            ),
        )
