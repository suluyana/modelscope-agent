import json
import os
import time
import uuid
from typing import Any, Dict, Optional

class RunLedger:
    def __init__(self, root_dir: str, run_id: str):
        self.run_id = run_id
        root_abs = os.path.abspath(root_dir)
        self.ledger_dir = os.path.abspath(os.path.join(root_abs, run_id))
        if os.path.commonpath([root_abs, self.ledger_dir]) != root_abs:
            raise ValueError(f"Unsafe run_id path outside root_dir: {run_id!r}")
        os.makedirs(self.ledger_dir, exist_ok=True)
        self.ledger_file = os.path.join(self.ledger_dir, "runledger.jsonl")
        self.schema_version = "runledger/v1"

    def _append_event(self, event_type: str, iteration: int, payload: Dict[str, Any]):
        event = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "iteration": iteration,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "ts": time.time(),
        }
        event.update(payload)
        with open(self.ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def record_baseline_result(self, iteration: int, status: str, exit_code: Optional[int], reward: Optional[float]):
        self._append_event("baseline_result", iteration, {
            "status": status,
            "exit_code": exit_code,
            "reward": reward
        })

    def record_incident_classified(self, iteration: int, primary_fingerprint: str, incident_class: str, confidence: float):
        self._append_event("incident_classified", iteration, {
            "incident_fingerprint": primary_fingerprint,
            "class": incident_class,
            "confidence": confidence
        })

    def record_decision_made(
        self,
        iteration: int,
        incident_fingerprint: str,
        decision_class: str,
        decision_confidence: float,
        decision_source: str,
        suggested_mode: str,
        reason: str,
        rule_confidence: Optional[float] = None,
        llm_confidence: Optional[float] = None,
        class_conflict: bool = False,
        confidence_gap: Optional[float] = None,
    ):
        self._append_event("decision_made", iteration, {
            "incident_fingerprint": incident_fingerprint,
            "decision_class": decision_class,
            "decision_confidence": decision_confidence,
            "decision_source": decision_source,
            "suggested_mode": suggested_mode,
            "reason": reason,
            "rule_confidence": rule_confidence,
            "llm_confidence": llm_confidence,
            "class_conflict": class_conflict,
            "confidence_gap": confidence_gap,
        })

    def record_capability_gap_mined(
        self,
        iteration: int,
        incident_fingerprint: str,
        symptom_class: str,
        root_cause_hypothesis: str,
        improvement_type: str,
        confidence: float,
        cluster_key: str,
        support_count: int,
        min_support_required: int,
        repair_allowed: bool,
        rationale: str,
        target_source_paths: Optional[list] = None,
        evidence_refs: Optional[list] = None,
        task_id: Optional[str] = None,
    ):
        self._append_event("capability_gap_mined", iteration, {
            "incident_fingerprint": incident_fingerprint,
            "symptom_class": symptom_class,
            "root_cause_hypothesis": root_cause_hypothesis,
            "improvement_type": improvement_type,
            "confidence": confidence,
            "cluster_key": cluster_key,
            "support_count": support_count,
            "min_support_required": min_support_required,
            "repair_allowed": repair_allowed,
            "rationale": rationale,
            "target_source_paths": target_source_paths or [],
            "evidence_refs": evidence_refs or [],
            "task_id": task_id,
        })

    def record_repair_planned(self, iteration: int, incident_fingerprint: str, target_files: list):
        self._append_event("repair_planned", iteration, {
            "incident_fingerprint": incident_fingerprint,
            "changed_files": target_files
        })

    def record_patch_applied(self, iteration: int, incident_fingerprint: str, patch_id: str, git_sha_before: str, git_sha_after: str):
        self._append_event("patch_applied", iteration, {
            "incident_fingerprint": incident_fingerprint,
            "patch_id": patch_id,
            "git_sha_before": git_sha_before,
            "git_sha_after": git_sha_after
        })

    def record_patch_verified(self, iteration: int, incident_fingerprint: str, passed: bool, exit_code: int, commands: list):
        self._append_event("patch_verified", iteration, {
            "incident_fingerprint": incident_fingerprint,
            "pass": passed,
            "exit_code": exit_code,
            "commands": commands
        })

    def record_rerun_result(self, iteration: int, passed: bool, reward: Optional[float], exit_code: Optional[int]):
        self._append_event("rerun_result", iteration, {
            "pass": passed,
            "reward": reward,
            "exit_code": exit_code
        })

    def record_budget_gate(self, iteration: int, tokens_input: int, tokens_output: int, api_calls: int, cost_usd: float, duration_sec: float):
        self._append_event("budget_gate", iteration, {
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "api_calls": api_calls,
            "cost_usd": cost_usd,
            "duration_sec": duration_sec
        })

    def record_done_evaluation(self, iteration: int, framework_done: bool, task_done: bool, overall_done: bool):
        self._append_event("done_evaluation", iteration, {
            "framework_done": framework_done,
            "task_done": task_done,
            "overall_done": overall_done
        })
    
    def record_file_guard_decision(self, iteration: int, path: str, allowed: bool, reason: str, policy: str):
        self._append_event("file_guard_decision", iteration, {
            "path": path,
            "allowed": allowed,
            "reason": reason,
            "policy": policy
        })

    def record_file_guard_override(self, iteration: int, path: str, approver: str, incident_fingerprint: str):
        self._append_event("file_guard_override", iteration, {
            "path": path,
            "approver": approver,
            "incident_fingerprint": incident_fingerprint
        })
