"""Remote Docker adapter for Terminal-Bench self-improve.

Runs EvalScope + Docker on a remote server via SSH, syncs code via git
push/pull, and copies result artifacts back to a local directory for analysis.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ms_agent.self_improve.adapters.base import RunAdapter

TASK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_DEFAULT_REMOTE_HOST = "root@47.254.25.238"
_DEFAULT_REMOTE_REPO = "/root/bench_workspace/modelscope-agent-si"
_DEFAULT_BRANCH = "feat/self_improve"
_DEFAULT_TIMEOUT_SEC = 1800
_DEFAULT_REGRESSION_TASKS = [
    "fix-git",
    "build-pmars",
    "hf-model-inference",
    "polyglot-c-py",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class TerminalBenchRemoteDockerAdapter(RunAdapter):
    """Adapter that runs Terminal-Bench tasks on a remote Docker host via SSH."""

    def __init__(
        self,
        task_name: str,
        remote_host: str = _DEFAULT_REMOTE_HOST,
        remote_repo_dir: str = _DEFAULT_REMOTE_REPO,
        local_output_dir: str | None = None,
        branch: str = _DEFAULT_BRANCH,
        timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
        regression_tasks: List[str] | None = None,
    ) -> None:
        if not TASK_NAME_RE.fullmatch(task_name) or task_name in {".", ".."}:
            raise ValueError(f"Invalid task name: {task_name!r}")
        self.task_name = task_name
        self.remote_host = remote_host
        self.remote_repo_dir = remote_repo_dir
        self.branch = branch
        self.timeout_sec = timeout_sec
        self.regression_tasks = regression_tasks or list(_DEFAULT_REGRESSION_TASKS)

        self._local_output_dir = local_output_dir or str(
            _repo_root() / "outputs" / "self_improve_remote" / task_name
        )
        self._trial_dir: str | None = None

    @property
    def name(self) -> str:
        return "terminal_bench_remote_docker"

    @property
    def output_dir(self) -> str:
        if self._trial_dir:
            return self._trial_dir
        return self._local_output_dir

    def _ssh(
        self, cmd: str, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", self.remote_host, cmd],
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout_sec,
        )

    def _scp_from_remote(self, remote_path: str, local_path: str) -> bool:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        result = subprocess.run(
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                f"{self.remote_host}:{remote_path}",
                local_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0

    def sync_code_to_remote(self) -> None:
        """Push local commits and hard-reset remote to match."""
        print(f"[RemoteAdapter] Pushing to origin/{self.branch}...")
        subprocess.run(
            ["git", "push", "origin", self.branch],
            check=True,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
        )
        reset_cmd = (
            f"cd {self.remote_repo_dir} && "
            f"git fetch origin {self.branch} && "
            f"git reset --hard origin/{self.branch}"
        )
        result = self._ssh(reset_cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to sync remote: {result.stderr}"
            )
        print("[RemoteAdapter] Remote synced successfully.")

    def _run_evalscope_remote(
        self, task_names: List[str], work_dir_suffix: str
    ) -> subprocess.CompletedProcess[str]:
        """Run EvalScope on remote for given tasks. Blocks until done."""
        tasks_str = ",".join(task_names)
        batch_size = len(task_names)
        remote_work_dir = (
            f"{self.remote_repo_dir}/outputs/{work_dir_suffix}"
        )
        env_exports = (
            f"export TERMINAL_BENCH_VERSION=2.1 && "
            f"export TERMINAL_BENCH_REGISTRY_PATH="
            f"/root/bench_workspace/datasets/terminal-bench-2.1-registry.json && "
            f"export TERMINAL_BENCH_MODEL=qwen3.6-plus && "
            f"export TERMINAL_BENCH_TASK_NAMES='{tasks_str}' && "
            f"export TERMINAL_BENCH_LIMIT={len(task_names)} && "
            f"export TERMINAL_BENCH_EVAL_BATCH_SIZE={batch_size} && "
            f"export EVALSCOPE_WORK_DIR={remote_work_dir} && "
            f"export EVALSCOPE_NO_TIMESTAMP=true && "
            f"export MS_AGENT_SOURCE_ROOT={self.remote_repo_dir} && "
            f"source /root/.bashrc"
        )
        run_cmd = (
            f"cd {self.remote_repo_dir} && {env_exports} && "
            f"python3 scripts/run_terminal_bench_ms_agent_smoke.py"
        )
        print(
            f"[RemoteAdapter] Running EvalScope on remote: "
            f"tasks={tasks_str} work_dir={remote_work_dir}"
        )
        return self._ssh(run_cmd, timeout=self.timeout_sec)

    def _find_remote_trial_dir(self, work_dir_suffix: str) -> Optional[str]:
        """Find the trial directory on remote matching task_name."""
        remote_work_dir = (
            f"{self.remote_repo_dir}/outputs/{work_dir_suffix}"
        )
        find_cmd = (
            f"find {remote_work_dir}/trials -maxdepth 1 -type d "
            f"-name '{self.task_name}__*' 2>/dev/null | sort -r | head -1"
        )
        result = self._ssh(find_cmd, timeout=30)
        path = result.stdout.strip()
        return path if path else None

    def _download_result(
        self, remote_trial_dir: str, iteration: int
    ) -> Optional[str]:
        """Download result.json from remote trial dir to local."""
        local_trial_dir = os.path.join(
            self._local_output_dir, f"iter_{iteration}"
        )
        os.makedirs(local_trial_dir, exist_ok=True)
        remote_result = f"{remote_trial_dir}/result.json"
        local_result = os.path.join(local_trial_dir, "result.json")

        if self._scp_from_remote(remote_result, local_result):
            self._trial_dir = local_trial_dir
            return local_result
        return None

    def _parse_reward(self, result_json_path: str) -> Optional[float]:
        try:
            with open(result_json_path, encoding="utf-8") as fh:
                data = json.load(fh)
            verifier = data.get("verifier_result") or {}
            rewards = verifier.get("rewards") or {}
            raw = rewards.get("reward")
            if raw is not None:
                return float(raw)
        except Exception:
            pass
        return None

    def run_target(self, iteration: int = 1) -> Tuple[bool, Dict[str, Any]]:
        self.sync_code_to_remote()

        work_dir_suffix = f"si_remote_{self.task_name}"
        result = self._run_evalscope_remote(
            [self.task_name], work_dir_suffix
        )

        if result.returncode != 0:
            print(
                f"[RemoteAdapter] EvalScope exited with code {result.returncode}"
            )
            # Still try to find results (EvalScope may exit non-zero
            # but produce a result.json with reward=0)

        remote_trial = self._find_remote_trial_dir(work_dir_suffix)
        reward: float | None = None

        if remote_trial:
            local_result = self._download_result(remote_trial, iteration)
            if local_result:
                reward = self._parse_reward(local_result)
                print(f"[RemoteAdapter] Task={self.task_name} reward={reward}")
            else:
                print("[RemoteAdapter] Failed to download result.json")
        else:
            print("[RemoteAdapter] No trial directory found on remote")
            # Save subprocess output for debugging
            os.makedirs(self._local_output_dir, exist_ok=True)
            log_path = os.path.join(
                self._local_output_dir,
                f"evalscope_iter{iteration}.log",
            )
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(result.stdout or "")
                if result.stderr:
                    fh.write("\n--- STDERR ---\n")
                    fh.write(result.stderr)

        success = reward is not None and reward == 1.0
        return success, {
            "exit_code": result.returncode,
            "reward": reward,
            "adapter_name": self.name,
            "trial_dir": self._trial_dir,
            "iteration": iteration,
        }

    def run_regression(self, tasks: List[str] | None = None) -> Dict[str, float]:
        """Run regression tasks and return {task_name: reward} mapping."""
        regression = tasks or self.regression_tasks
        if not regression:
            return {}

        self.sync_code_to_remote()
        work_dir_suffix = "si_remote_regression"
        self._run_evalscope_remote(regression, work_dir_suffix)

        results: Dict[str, float] = {}
        for task in regression:
            find_cmd = (
                f"find {self.remote_repo_dir}/outputs/{work_dir_suffix}/trials "
                f"-maxdepth 1 -type d -name '{task}__*' 2>/dev/null "
                f"| sort -r | head -1"
            )
            r = self._ssh(find_cmd, timeout=30)
            trial_path = r.stdout.strip()
            if not trial_path:
                results[task] = 0.0
                continue

            # Read reward directly from remote (no need to download)
            parse_cmd = (
                f"python3 -c \""
                f"import json; "
                f"r=json.load(open('{trial_path}/result.json')); "
                f"vr=r.get('verifier_result',{{}});"
                f"print(vr.get('rewards',{{}}).get('reward', 0.0))\""
            )
            r = self._ssh(parse_cmd, timeout=30)
            try:
                results[task] = float(r.stdout.strip())
            except (ValueError, TypeError):
                results[task] = 0.0

        return results

    def get_context(self) -> Dict[str, str]:
        ctx: Dict[str, str] = {
            "task_name": self.task_name,
            "work_dir": self._local_output_dir,
            "remote_host": self.remote_host,
            "remote_repo_dir": self.remote_repo_dir,
        }
        if self._trial_dir:
            ctx["trial_dir"] = self._trial_dir
        return ctx
