import inspect
from typing import Any, Dict

from ms_agent.self_improve.schemas import EvidenceKind, IncidentSignal
from ms_agent.self_improve.ledger import RunLedger
from ms_agent.self_improve.collector import ArtifactCollector
from ms_agent.self_improve.classifier import FailureClassifier
from ms_agent.self_improve.capability_miner import CapabilityGapMiner
from ms_agent.self_improve import trajectory_analyzer
from ms_agent.self_improve.decision_engine import DecisionEngine
from ms_agent.self_improve.planner import RepairPlanner
from ms_agent.self_improve.executor import RepairExecutor, FileGuard
from ms_agent.self_improve.verifier import Verifier
from ms_agent.self_improve.adapters.base import RunAdapter
from ms_agent.self_improve.repair_agent import RepairAgent

def human_approval_callback(patch, guard_decision):
    print("\n[Human Approval Required]")
    print(f"Patch ID: {patch.patch_id}")
    print(f"Description: {patch.description}")
    print(f"Target files: {patch.target_files}")
    print(f"Guard Decision: {guard_decision.reason}")
    if patch.file_patches:
        print("\n--- Patch Preview ---")
        for fp in patch.file_patches:
            print(f"File: {fp.path}")
            print(f"- {fp.search_text}")
            print(f"+ {fp.replace_text}")
        print("---------------------\n")
    
    # Prompt the user
    try:
        response = input("Approve this patch? [y/N]: ").strip().lower()
        if response in ["y", "yes"]:
            return True
        else:
            return False
    except EOFError:
        print("[Human Approval] Input stream closed. Denying patch.")
        return False

class SelfImproveOrchestrator:
    def __init__(self, run_id: str, adapter: RunAdapter, config: Dict[str, Any]):
        self.run_id = run_id
        self.adapter = adapter
        self.config = config
        
        self.mode = config.get("mode", "assist")
        self.max_iterations = config.get("loop", {}).get("max_iterations", 5)
        
        # Initialize components
        self.ledger = RunLedger(config.get("logging", {}).get("root_dir", "outputs/self_improve"), self.run_id)
        self.classifier = FailureClassifier()
        self.capability_miner = CapabilityGapMiner(config)
        self.decision_engine = DecisionEngine(config=config, run_mode=self.mode)
        self.planner = RepairPlanner(mode=self.mode)
        self.executor = RepairExecutor(guard=FileGuard(config.get("scope", {}).get("file_write_guard")), mode=self.mode)
        self.verifier = Verifier()
        self.repair_agent = RepairAgent(config)
        self.failed_attempts_by_fingerprint = {}

    def run_loop(self):
        print(f"[Orchestrator] Starting self-improve loop (run_id: {self.run_id}, mode: {self.mode})")
        try:
            supports_iteration = "iteration" in inspect.signature(
                self.adapter.run_target
            ).parameters
        except (TypeError, ValueError):
            supports_iteration = False
        
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n[Orchestrator] --- Iteration {iteration} ---")
            
            # 1. RUN_BASELINE
            if supports_iteration:
                success, run_context = self.adapter.run_target(iteration=iteration)
            else:
                success, run_context = self.adapter.run_target()
            self.ledger.record_baseline_result(iteration, "success" if success else "fail", run_context.get("exit_code"), run_context.get("reward"))
            
            if success:
                print("[Orchestrator] Baseline run succeeded. No repair needed.")
                break

            # 2. COLLECT_ARTIFACTS
            collector = ArtifactCollector(self.adapter.output_dir)
            evidences = collector.collect()

            # 2.5 TRAJECTORY ANALYSIS
            traj_analysis = None
            for ev in evidences:
                if ev.kind == EvidenceKind.AGENT_STDOUT:
                    try:
                        from pathlib import Path
                        stdout_text = Path(ev.path).read_text(encoding="utf-8", errors="replace")
                        traj_analysis = trajectory_analyzer.analyze(stdout_text)
                        print(
                            f"[Orchestrator] Trajectory: {traj_analysis.total_turns} turns, "
                            f"{len(traj_analysis.tool_calls)} tool calls, "
                            f"state={traj_analysis.final_state}, "
                            f"patterns={len(traj_analysis.repeated_failure_patterns)}"
                        )
                    except Exception as e:
                        print(f"[Orchestrator] Trajectory analysis failed: {e}")
                    break
            run_context = {**run_context, "trajectory_analysis": traj_analysis}

            # 3. CLASSIFY_FAILURE
            incidents = self.classifier.classify(evidences, run_context)
            adapter_context = self.adapter.get_context()
            signal = IncidentSignal(
                run_id=self.run_id,
                iteration=iteration,
                adapter_name=self.adapter.name,
                task_id=adapter_context.get("task_name"),
                status="fail",
                exit_code=run_context.get("exit_code"),
                reward=run_context.get("reward"),
                incidents=incidents,
                evidence_index=evidences,
                trajectory_analysis=traj_analysis,
            )
            
            primary = signal.primary_incident
            if primary:
                self.ledger.record_incident_classified(iteration, primary.fingerprint, primary.incident_class.value, primary.confidence)
                print(f"[Orchestrator] Classified as: {primary.incident_class.value} (confidence: {primary.confidence:.2f})")

            capability_gap = self.capability_miner.mine(signal)

            # 4. DECIDE_ACTION (rule_only / hybrid)
            decision = self.decision_engine.decide(signal)
            incident_fingerprint = decision.incident_fingerprint
            if not incident_fingerprint and primary:
                incident_fingerprint = primary.fingerprint
            if not incident_fingerprint:
                incident_fingerprint = "unknown"

            self.ledger.record_capability_gap_mined(
                iteration=iteration,
                incident_fingerprint=incident_fingerprint,
                symptom_class=capability_gap.symptom_class.value,
                root_cause_hypothesis=capability_gap.root_cause_hypothesis.value,
                improvement_type=capability_gap.improvement_type.value,
                confidence=capability_gap.confidence,
                cluster_key=capability_gap.cluster_key,
                support_count=capability_gap.support_count,
                min_support_required=capability_gap.min_support_required,
                repair_allowed=capability_gap.repair_allowed,
                rationale=capability_gap.rationale,
                target_source_paths=capability_gap.target_source_paths,
                evidence_refs=capability_gap.evidence_refs,
                task_id=signal.task_id,
            )
            print(
                "[Orchestrator] Capability gap: "
                f"{capability_gap.symptom_class.value}/"
                f"{capability_gap.root_cause_hypothesis.value} "
                f"improvement={capability_gap.improvement_type.value} "
                f"support={capability_gap.support_count}/"
                f"{capability_gap.min_support_required} "
                f"repair_allowed={capability_gap.repair_allowed}"
            )

            self.ledger.record_decision_made(
                iteration=iteration,
                incident_fingerprint=incident_fingerprint,
                decision_class=decision.decision_class.value,
                decision_confidence=decision.decision_confidence,
                decision_source=decision.decision_source.value,
                suggested_mode=decision.suggested_mode,
                reason=decision.reason,
                rule_confidence=decision.rule_confidence,
                llm_confidence=decision.llm_confidence,
                class_conflict=decision.class_conflict,
                confidence_gap=decision.confidence_gap,
            )
            print(
                "[Orchestrator] Decision: "
                f"{decision.decision_class.value} ({decision.decision_source.value}, "
                f"conf={decision.decision_confidence:.2f}, mode={decision.suggested_mode})"
            )
            
            # 5. PLAN_REPAIR
            plan = self.planner.plan(
                signal,
                decision=decision,
                capability_gap=capability_gap,
            )
            self.ledger.record_repair_planned(iteration, incident_fingerprint, plan.target_domains)
            
            if not plan.should_repair:
                print(f"[Orchestrator] Repair skipped: {plan.reason}")
                break # Or ask human, but break for now
                
            # 6. SELECT_EVAL_DOMAINS (skipped for minimal P0)
            
            # 7. EXECUTE_REPAIR
            effective_mode = plan.suggested_mode
            if effective_mode not in ("observe", "assist", "auto", "ask_human"):
                effective_mode = self.mode
            print(f"[Orchestrator] Execute repair mode: {effective_mode}")

            if effective_mode in ("observe", "ask_human"):
                if effective_mode == "ask_human":
                    print("[Orchestrator] Human escalation requested. Stopping execution.")
                else:
                    print("[Orchestrator] Observe mode active. Stopping execution.")
                break
            self.executor.mode = effective_mode
                
            patch_id = f"patch_{iteration}"
            failed_attempts = self.failed_attempts_by_fingerprint.get(incident_fingerprint, [])
            patch = self.repair_agent.generate_patch(plan, signal, patch_id, failed_attempts=failed_attempts)
            if not patch:
                print("[Orchestrator] Repair agent failed to generate a patch.")
                break
            patch.incident_fingerprint = incident_fingerprint

            import subprocess
            head_before = None
            tracked_dirty_before = False
            try:
                head_before = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=".").stdout.strip()
            except Exception:
                pass
            try:
                tracked_dirty_before = (
                    subprocess.run(["git", "diff", "--quiet"], cwd=".").returncode != 0
                    or subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=".").returncode != 0
                )
            except Exception:
                tracked_dirty_before = False

            applied = self.executor.apply_patch(patch, human_approval_callback)
            if not applied:
                print("[Orchestrator] Patch was not applied.")
                if incident_fingerprint not in self.failed_attempts_by_fingerprint:
                    self.failed_attempts_by_fingerprint[incident_fingerprint] = []
                self.failed_attempts_by_fingerprint[incident_fingerprint].append({
                    "patch_id": patch.patch_id,
                    "patch_content": patch.model_dump() if hasattr(patch, "model_dump") else patch.dict(),
                    "verification_log": self.executor.last_error or "Patch was not applied.",
                })
                continue

            head_after = None
            try:
                head_after = (
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True,
                        text=True,
                        cwd=".",
                    )
                    .stdout.strip()
                )
            except Exception:
                pass

            self.ledger.record_patch_applied(
                iteration,
                incident_fingerprint,
                patch.patch_id,
                head_before or "",
                head_after or "",
            )

            # 8. VERIFY_PATCH 
            print("[Orchestrator] Verifying patch...")
            patch_cmds = self.config.get("verify", {}).get("patch_commands", ["pytest tests -q"])
            if not patch_cmds:
                print("[Orchestrator] No verification commands specified. Skipping explicit verify step.")
                verify_passed = True
            else:
                adapter_ctx = self.adapter.get_context()
                verify_res = self.verifier.verify_patch(patch_cmds, adapter_ctx, {}, "generic_python")
                
                self.ledger.record_patch_verified(iteration, incident_fingerprint, verify_res.passed, verify_res.exit_code, verify_res.commands_run)
                verify_passed = verify_res.passed
                
                if not verify_passed:
                    print(f"[Orchestrator] Verification failed: {verify_res.output_log}")
                    # Record failure
                    if incident_fingerprint not in self.failed_attempts_by_fingerprint:
                        self.failed_attempts_by_fingerprint[incident_fingerprint] = []
                    self.failed_attempts_by_fingerprint[incident_fingerprint].append({
                        "patch_id": patch.patch_id,
                        "patch_content": patch.model_dump() if hasattr(patch, "model_dump") else patch.dict(),
                        "verification_log": verify_res.output_log
                    })
                    
                    # Revert broken commit if it was created
                    try:
                        head_after = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=".").stdout.strip()
                        if tracked_dirty_before:
                            print(
                                "[Orchestrator] Skip rollback because tracked files were dirty before patch."
                            )
                        elif head_before and head_after != head_before:
                            print(f"[Orchestrator] Reverting broken commit {head_after}")
                            subprocess.run(
                                [
                                    "git",
                                    "-c",
                                    "user.name=self-improve",
                                    "-c",
                                    "user.email=self-improve@example.invalid",
                                    "revert",
                                    "--no-edit",
                                    head_after,
                                ],
                                check=True,
                                cwd=".",
                            )
                    except Exception as e:
                        print(f"[Orchestrator] Warning: Failed to revert broken commit: {e}")
                else:
                    print("[Orchestrator] Verification passed.")

            # 9. CHECKPOINT_CHANGESET & 10. RERUN_TARGET
            print("[Orchestrator] Proceeding to next iteration to evaluate the repaired system.")
            
        print("[Orchestrator] Loop finished.")
