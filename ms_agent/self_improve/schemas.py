"""
Self-Improve Schemas
"""
import enum
from typing import List, Optional
from pydantic import BaseModel, Field

class IncidentClass(str, enum.Enum):
    INFRA_ERROR = "infra_error"
    FRAMEWORK_ERROR = "framework_error"
    TASK_SOLUTION_ERROR = "task_solution_error"
    UNKNOWN = "unknown"


class SymptomClass(str, enum.Enum):
    ARTIFACT_MISSING = "artifact_missing"
    TEST_ASSERTION_FAILED = "test_assertion_failed"
    DEPENDENCY_MISSING = "dependency_missing"
    EXECUTION_TIMEOUT = "execution_timeout"
    TOOL_OR_PROTOCOL_ERROR = "tool_or_protocol_error"
    ENVIRONMENT_FAILURE = "environment_failure"
    UNKNOWN = "unknown"


class RootCauseHypothesis(str, enum.Enum):
    TASK_IMPLEMENTATION_INCOMPLETE = "task_implementation_incomplete"
    FRAMEWORK_CAPABILITY_GAP = "framework_capability_gap"
    ENVIRONMENT_DEPENDENCY_GAP = "environment_dependency_gap"
    TOOLING_ADAPTER_GAP = "tooling_adapter_gap"
    UNKNOWN = "unknown"


class ImprovementType(str, enum.Enum):
    FRAMEWORK_PATCH = "framework_patch"
    PROMPT_POLICY_PATCH = "prompt_policy_patch"
    TOOLING_ADAPTER_PATCH = "tooling_adapter_patch"
    ENVIRONMENT_RECIPE = "environment_recipe"
    OFFLINE_REPORT = "offline_report"
    NONE = "none"

class IncidentSeverity(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class EvidenceKind(str, enum.Enum):
    EXCEPTION = "exception"
    TRACEBACK = "traceback"
    TRIAL_LOG = "trial_log"
    TRAJECTORY = "trajectory"
    VERIFIER_OUTPUT = "verifier_output"
    SUMMARY = "summary"
    AGENT_STDOUT = "agent_stdout"

class EvidenceRef(BaseModel):
    path: str
    kind: EvidenceKind
    offset: Optional[int] = None
    digest: Optional[str] = None

class IncidentDetail(BaseModel):
    incident_id: str
    fingerprint: str
    incident_class: IncidentClass = Field(alias="class")
    severity: IncidentSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    rank: int
    evidence_refs: List[str] = Field(default_factory=list) # List of paths referencing evidence_index

class IncidentSignal(BaseModel):
    """Incident Signal Contract v1"""
    schema_version: str = "incident/v1"
    run_id: str
    iteration: int
    adapter_name: str
    task_id: Optional[str] = None
    
    status: str  # success, fail, partial
    exit_code: Optional[int] = None
    reward: Optional[float] = None
    
    incidents: List[IncidentDetail] = Field(default_factory=list)
    evidence_index: List[EvidenceRef] = Field(default_factory=list)

    @property
    def primary_incident(self) -> Optional[IncidentDetail]:
        if not self.incidents:
            return None
        # Return the incident with rank 0, or just the first one if sorted
        return sorted(self.incidents, key=lambda x: x.rank)[0]


class DecisionSource(str, enum.Enum):
    RULE_ONLY = "rule_only"
    RULE_DIRECT = "rule_direct"
    LLM_ARBITER = "llm_arbiter"
    MERGED = "merged"


class DecisionResult(BaseModel):
    decision_class: IncidentClass
    decision_confidence: float = Field(ge=0.0, le=1.0)
    decision_source: DecisionSource
    suggested_mode: str  # observe, assist, auto, ask_human
    reason: str
    incident_fingerprint: Optional[str] = None
    rule_confidence: Optional[float] = None
    llm_confidence: Optional[float] = None
    class_conflict: bool = False
    confidence_gap: Optional[float] = None


class CapabilityGapSignal(BaseModel):
    symptom_class: SymptomClass
    root_cause_hypothesis: RootCauseHypothesis
    improvement_type: ImprovementType
    confidence: float = Field(ge=0.0, le=1.0)
    cluster_key: str
    support_count: int = Field(ge=1)
    min_support_required: int = Field(ge=1)
    repair_allowed: bool
    rationale: str
    target_source_paths: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)

class GuardDecision(BaseModel):
    allowed: bool
    reason: str
    policy_applied: str

class FilePatch(BaseModel):
    path: str
    search_text: str
    replace_text: str

class RepairPatch(BaseModel):
    patch_id: str
    incident_fingerprint: str
    target_files: List[str]
    diff_content: str
    description: str
    file_patches: List[FilePatch] = Field(default_factory=list)

class VerificationResult(BaseModel):
    passed: bool
    exit_code: int
    output_log: str
    commands_run: List[str]


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments_summary: str
    result_summary: str
    success: bool
    order: int


class TrajectoryAnalysis(BaseModel):
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    shell_commands: List[str] = Field(default_factory=list)
    errors_encountered: List[str] = Field(default_factory=list)
    final_state: str = "unknown"
    total_turns: int = 0
    unique_tools_used: List[str] = Field(default_factory=list)
    repeated_failure_patterns: List[str] = Field(default_factory=list)
