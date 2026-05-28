import hashlib
import os
from typing import List, Dict, Any, Optional

from ms_agent.self_improve.schemas import IncidentDetail, IncidentClass, IncidentSeverity, EvidenceRef, EvidenceKind

class Rulebook:
    def __init__(self, version: str = "v1"):
        self.version = version
        self.infra_keywords = [
            "docker.sock", "Cannot connect to the Docker daemon", "buildx", "no matching manifest",
            "Connection refused", "Temporary failure in name resolution", "timed out", "timeout",
            "TLS handshake timeout", "Permission denied", "Operation not permitted", 
            "Read-only file system", "ENOSPC", "command not found", "No module named", 
            "pip install failed", "apt-get", "ConnectionError", "DockerException"
        ]
        self.framework_keywords = [
            "tool schema", "invalid tool arguments", "unexpected tool return",
            "max round exceeded", "orchestrator", "adapter",
            "ConfigError", "AgentLoader", "WorkflowLoader"
        ]
        self.task_solution_keywords = [
            "AssertionError", "FAILED ", "FAILURES", "AttributeError", "TypeError", "SyntaxError",
            "IndentationError", "NameError", "KeyError", "IndexError", "ValueError"
        ]
        # P0 scoring params
        self.base_score = 0.65
        self.keyword_score_per_hit = 0.15
        self.keyword_score_cap = 0.45
        self.structural_score = 0.20
        self.conflict_deduction = 0.30

class FailureClassifier:
    def __init__(self, rulebook: Optional[Rulebook] = None):
        self.rulebook = rulebook or Rulebook("v1")

    def _generate_fingerprint(self, error_type: str, stack_top3: str, source_file: str, adapter_name: str) -> str:
        raw = f"{error_type}|{stack_top3}|{source_file}|{adapter_name}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

    def _read_evidence_content(self, evidence: EvidenceRef) -> str:
        if not os.path.exists(evidence.path):
            return ""
        try:
            with open(evidence.path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return ""

    def classify(self, evidence_refs: List[EvidenceRef], run_context: Dict[str, Any]) -> List[IncidentDetail]:
        """
        Classifies the failure based on evidences and context (run_id, adapter_name, exit_code, reward).
        """
        exception_text = ""
        trial_log = ""
        for ev in evidence_refs:
            if ev.kind == EvidenceKind.EXCEPTION or ev.kind == EvidenceKind.TRACEBACK:
                exception_text += self._read_evidence_content(ev) + "\n"
            elif ev.kind == EvidenceKind.TRIAL_LOG:
                trial_log += self._read_evidence_content(ev) + "\n"

        exit_code = run_context.get("exit_code", 0)
        reward = run_context.get("reward")
        adapter_name = run_context.get("adapter_name", "unknown")

        scores = {
            IncidentClass.FRAMEWORK_ERROR: 0.0,
            IncidentClass.INFRA_ERROR: 0.0,
            IncidentClass.TASK_SOLUTION_ERROR: 0.0
        }

        # 1. Base Score calculation
        # Framework base: Stack match (trial.log often contains the only traceback)
        is_framework_stack = (
            "ms_agent/" in exception_text
            or "scripts/" in exception_text
            or "ms_agent/" in trial_log
            or "scripts/" in trial_log
        )
        if is_framework_stack:
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.base_score

        # Infra base: System error
        combined_text = f"{exception_text}\n{trial_log}"
        has_missing_module = "No module named" in combined_text
        has_infra_system_err = (
            any(kw in exception_text for kw in ["ConnectionError", "DockerException", "Timeout"])
            or has_missing_module
        )
        if has_infra_system_err:
            scores[IncidentClass.INFRA_ERROR] += self.rulebook.base_score

        # Task solution base: reward=0 or verifier failed, no exception
        has_test_failure = "AssertionError" in exception_text or "FAILED " in exception_text or "AssertionError" in trial_log or "FAILED " in trial_log
        if (reward == 0.0) or has_test_failure:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.base_score

        # Timeout / infinite loop
        if "Execution timed out" in trial_log or "Execution timed out" in exception_text:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.base_score
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.base_score / 2.0

        # 2. Keyword hits
        infra_hits = sum(1 for kw in self.rulebook.infra_keywords if kw.lower() in exception_text.lower() or kw.lower() in trial_log.lower())
        framework_hits = sum(1 for kw in self.rulebook.framework_keywords if kw.lower() in exception_text.lower() or kw.lower() in trial_log.lower())
        task_solution_hits = sum(1 for kw in getattr(self.rulebook, "task_solution_keywords", []) if kw.lower() in exception_text.lower() or kw.lower() in trial_log.lower())

        scores[IncidentClass.INFRA_ERROR] += min(infra_hits * self.rulebook.keyword_score_per_hit, self.rulebook.keyword_score_cap)
        # framework needs at least stack match or strong keyword to count keywords effectively
        if is_framework_stack or framework_hits > 0:
            scores[IncidentClass.FRAMEWORK_ERROR] += min(framework_hits * self.rulebook.keyword_score_per_hit, self.rulebook.keyword_score_cap)
            
        if task_solution_hits > 0:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += min(task_solution_hits * self.rulebook.keyword_score_per_hit, self.rulebook.keyword_score_cap)


        # 3. Structural support
        if exit_code != 0 and exit_code is not None:
            scores[IncidentClass.INFRA_ERROR] += self.rulebook.structural_score
            scores[IncidentClass.FRAMEWORK_ERROR] += self.rulebook.structural_score

        if reward == 0.0:
            scores[IncidentClass.TASK_SOLUTION_ERROR] += self.rulebook.structural_score

        # 4. Conflict Arbitration
        # Framework vs Infra
        if scores[IncidentClass.FRAMEWORK_ERROR] > 0 and scores[IncidentClass.INFRA_ERROR] > 0:
            if is_framework_stack:
                scores[IncidentClass.INFRA_ERROR] -= self.rulebook.conflict_deduction
            else:
                scores[IncidentClass.FRAMEWORK_ERROR] -= self.rulebook.conflict_deduction

        if has_missing_module:
            scores[IncidentClass.INFRA_ERROR] = max(scores[IncidentClass.INFRA_ERROR], 0.95)
            scores[IncidentClass.FRAMEWORK_ERROR] = min(scores[IncidentClass.FRAMEWORK_ERROR], 0.54)

        # Framework vs Task
        if scores[IncidentClass.TASK_SOLUTION_ERROR] > 0 and scores[IncidentClass.FRAMEWORK_ERROR] > 0:
            scores[IncidentClass.TASK_SOLUTION_ERROR] -= self.rulebook.conflict_deduction

        # 5. Cap scores at 1.0 and determine classes
        incidents = []
        for cls, raw_score in scores.items():
            conf = min(max(raw_score, 0.0), 1.0)
            if conf >= 0.55: # At least low confidence to be considered
                incidents.append((cls, conf))

        if not incidents:
            incidents.append((IncidentClass.UNKNOWN, 0.0))

        # Sort by confidence desc
        incidents.sort(key=lambda x: x[1], reverse=True)

        final_incidents = []
        for rank, (cls, conf) in enumerate(incidents):
            # Extract basic fingerprint features
            error_type = cls.value
            stack_top3 = exception_text.splitlines()[:3]
            stack_str = "|".join(stack_top3) if stack_top3 else "no_stack"
            source_file = "unknown_file"
            
            fingerprint = self._generate_fingerprint(error_type, stack_str, source_file, adapter_name)
            
            severity = IncidentSeverity.HIGH if rank == 0 else IncidentSeverity.MEDIUM
            if cls == IncidentClass.UNKNOWN or conf < 0.55:
                severity = IncidentSeverity.LOW

            summary = f"Detected {cls.value} with confidence {conf:.2f}"
            if cls == IncidentClass.UNKNOWN and exit_code != 0 and not exception_text:
                summary += " (Non-zero exit but no stacktrace. Suggest adding debug logs)"

            final_incidents.append(IncidentDetail(**{
                "incident_id": hashlib.md5(f"{fingerprint}_{rank}".encode()).hexdigest()[:8],
                "fingerprint": fingerprint,
                "class": cls,
                "severity": severity,
                "confidence": conf,
                "summary": summary,
                "rank": rank,
                "evidence_refs": [ev.path for ev in evidence_refs]
            }))

        return final_incidents
