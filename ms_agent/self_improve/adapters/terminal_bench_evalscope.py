"""EvalScope + Docker adapter for Terminal-Bench self-improve.

Runs ``scripts/run_terminal_bench_ms_agent_smoke.py`` — the same pipeline used
for the official benchmark — so the execution environment, data format, and
scoring are identical to the baseline.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ms_agent.self_improve.adapters.base import RunAdapter

TASK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_PASSTHROUGH_ENV_KEYS = (
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "DASHSCOPE_API_BASE",
    "OPENAI_BASE_URL",
    "MS_AGENT_SOURCE_ROOT",
    "TERMINAL_BENCH_VERSION",
    "TERMINAL_BENCH_REGISTRY_PATH",
    "TERMINAL_BENCH_MODEL",
    "TERMINAL_BENCH_TIMEOUT_MULTIPLIER",
    "TERMINAL_BENCH_MAX_TURNS",
    "TERMINAL_BENCH_KEEP_DOCKER_IMAGE",
    "TERMINAL_BENCH_FORCE_BUILD",
    "PYTHONPATH",
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _find_trial_dirs(work_dir: str, task_name: str) -> List[Path]:
    """Find all trial directories matching ``{task_name}__*`` under *work_dir*."""
    results: list[Path] = []
    work = Path(work_dir)
    if not work.is_dir():
        return results
    for trial_dir in work.rglob(f"{task_name}__*"):
        if trial_dir.is_dir() and (trial_dir / "result.json").is_file():
            results.append(trial_dir)
    return sorted(results, key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_reward(result_json_path: Path) -> Optional[float]:
    try:
        with result_json_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        verifier = data.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        raw = rewards.get("reward")
        if raw is not None:
            return float(raw)
    except Exception:
        pass
    return None


class TerminalBenchEvalScopeAdapter(RunAdapter):
    """Adapter that delegates to the EvalScope + Docker benchmark runner."""

    def __init__(
        self,
        task_name: str,
        work_dir: str | None = None,
    ) -> None:
        if not TASK_NAME_RE.fullmatch(task_name) or task_name in {".", ".."}:
            raise ValueError(f"Invalid task name: {task_name!r}")
        self.task_name = task_name
        self._work_dir = (
            work_dir
            or os.environ.get("EVALSCOPE_WORK_DIR", "").strip()
            or str(_repo_root() / "outputs" / "terminal_bench_self_improve")
        )
        self._trial_dir: str | None = None

    @property
    def name(self) -> str:
        return "terminal_bench_evalscope"

    @property
    def output_dir(self) -> str:
        if self._trial_dir:
            return self._trial_dir
        return self._work_dir

    def run_target(self, iteration: int = 1) -> Tuple[bool, Dict[str, Any]]:
        env = {k: os.environ[k] for k in _PASSTHROUGH_ENV_KEYS if k in os.environ}
        env["TERMINAL_BENCH_TASK_NAMES"] = self.task_name
        env["TERMINAL_BENCH_LIMIT"] = "1"
        env["EVALSCOPE_WORK_DIR"] = self._work_dir
        env["EVALSCOPE_NO_TIMESTAMP"] = "1"
        env["TERMINAL_BENCH_EVAL_BATCH_SIZE"] = os.environ.get(
            "TERMINAL_BENCH_EVAL_BATCH_SIZE", "1"
        )
        if "MS_AGENT_SOURCE_ROOT" not in env:
            env["MS_AGENT_SOURCE_ROOT"] = str(_repo_root())

        script = str(_repo_root() / "scripts" / "run_terminal_bench_ms_agent_smoke.py")
        cmd = [sys.executable, script]
        timeout_sec = int(
            os.environ.get("TERMINAL_BENCH_EVALSCOPE_TIMEOUT_SEC", "1800")
        )

        print(
            f"[Adapter] Running {self.name} task={self.task_name} "
            f"iter={iteration} timeout={timeout_sec}s"
        )

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_sec,
                env=env,
                cwd=str(_repo_root()),
            )
            output = result.stdout
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            print(
                f"[Adapter] Timeout expired for {self.task_name} after {timeout_sec}s"
            )
            partial = exc.stdout or b""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            output = partial + f"\n[Adapter] Execution timed out after {timeout_sec}s."
            exit_code = -1
        except Exception as exc:
            return False, {
                "exit_code": 1,
                "reward": None,
                "adapter_name": self.name,
                "exception": str(exc),
                "iteration": iteration,
            }

        # Save subprocess output as trial.log in work_dir for debugging
        os.makedirs(self._work_dir, exist_ok=True)
        subprocess_log = os.path.join(
            self._work_dir, f"{self.task_name}_iter{iteration}_subprocess.log"
        )
        with open(subprocess_log, "w", encoding="utf-8") as fh:
            fh.write(output)

        # Discover the trial directory written by EvalScope
        trial_dirs = _find_trial_dirs(self._work_dir, self.task_name)
        reward: float | None = None
        if trial_dirs:
            self._trial_dir = str(trial_dirs[0])
            result_json = trial_dirs[0] / "result.json"
            reward = _parse_reward(result_json)
            print(f"[Adapter] Trial dir found: {self._trial_dir}")
        else:
            print(f"[Adapter] No trial directory found for {self.task_name}")

        success = reward is not None and reward == 1.0

        return success, {
            "exit_code": exit_code,
            "reward": reward,
            "adapter_name": self.name,
            "trial_dir": self._trial_dir,
            "iteration": iteration,
            "subprocess_log": subprocess_log,
        }

    def get_context(self) -> Dict[str, str]:
        ctx: Dict[str, str] = {
            "task_name": self.task_name,
            "work_dir": self._work_dir,
        }
        if self._trial_dir:
            ctx["trial_dir"] = self._trial_dir
        return ctx
