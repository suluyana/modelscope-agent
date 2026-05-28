import os
import re
import subprocess
import sys
from typing import Dict, Any, Tuple
from ms_agent.self_improve.adapters.base import RunAdapter

TASK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _clean_policy_for_iteration(iteration: int) -> bool:
    """TERMINAL_BENCH_FAST_LOCAL_CLEAN: always | first | never (default: always)."""
    policy = os.environ.get("TERMINAL_BENCH_FAST_LOCAL_CLEAN", "always").strip().lower()
    if policy == "never":
        return False
    if policy == "first":
        return iteration <= 1
    return True


class TerminalBenchFastLocalAdapter(RunAdapter):
    def __init__(self, task_name: str, output_dir_base: str | None = None):
        if not TASK_NAME_RE.fullmatch(task_name) or task_name in {".", ".."}:
            raise ValueError(f"Invalid task name: {task_name!r}")
        self.task_name = task_name
        if output_dir_base is None:
            output_dir_base = os.environ.get("BENCH_LOCAL_ROOT", "bench_local").strip() or "bench_local"
        self._output_dir = os.path.join(output_dir_base, task_name, "app")

    @property
    def name(self) -> str:
        return "terminal_bench_fast_local"

    @property
    def output_dir(self) -> str:
        return self._output_dir

    def run_target(self, iteration: int = 1) -> Tuple[bool, Dict[str, Any]]:
        cmd = [
            sys.executable,
            "scripts/run_terminal_bench_unified.py",
            "fast_local",
            "--mode",
            "full",
            "--task-name",
            self.task_name,
        ]
        use_clean = _clean_policy_for_iteration(iteration)
        if use_clean:
            cmd.append("--clean")
            no_seed = os.environ.get("TERMINAL_BENCH_FAST_LOCAL_NO_SEED", "").strip().lower()
            if no_seed in ("1", "true", "yes"):
                cmd.append("--no-seed-app-from-image")

        timeout_sec = int(os.environ.get("TERMINAL_BENCH_FAST_LOCAL_TIMEOUT_SEC", "3600"))
        clean_tag = "clean" if use_clean else "no-clean"
        print(
            f"[Adapter] Running {self.name} task={self.task_name} "
            f"iter={iteration} {clean_tag} timeout={timeout_sec}s"
        )
        try:
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout_sec,
                    cwd=os.environ.get("MS_AGENT_SOURCE_ROOT", os.getcwd()),
                )
                output = result.stdout
                exit_code = result.returncode
            except subprocess.TimeoutExpired as e:
                print(f"[Adapter] Timeout expired for {self.task_name} after {timeout_sec}s")
                partial = e.stdout or b""
                if isinstance(partial, bytes):
                    partial = partial.decode("utf-8", errors="replace")
                output = partial + f"\n[Adapter] Execution timed out after {timeout_sec}s."
                exit_code = -1
            
            # Save the captured output to trial.log so ArtifactCollector can find it
            os.makedirs(self.output_dir, exist_ok=True)
            trial_log_path = os.path.join(self.output_dir, "trial.log")
            with open(trial_log_path, "w", encoding="utf-8") as f:
                f.write(output)
                
            reward = None
            
            result_json = os.path.join(self.output_dir, "result.json")
            if os.path.exists(result_json):
                import json
                try:
                    with open(result_json, "r") as f:
                        data = json.load(f)
                        reward = data.get("reward")
                except Exception:
                    pass

            success = exit_code == 0
            
            return success, {
                "exit_code": exit_code,
                "reward": reward,
                "adapter_name": self.name,
                "clean": use_clean,
                "iteration": iteration,
            }
        except Exception as e:
            return False, {
                "exit_code": 1,
                "reward": None,
                "adapter_name": self.name,
                "exception": str(e),
                "clean": use_clean,
                "iteration": iteration,
            }

    def get_context(self) -> Dict[str, str]:
        return {"task_name": self.task_name}
