"""RepairAgent: generates framework patches via multi-turn tool-use conversation.

Uses LLMAgent + FileSystemTool so the LLM can autonomously grep, read, and
edit source files — no hand-rolled file I/O or truncation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ms_agent.self_improve.planner import RepairPlan
from ms_agent.self_improve.schemas import IncidentSignal, RepairPatch

_SAFE_PREFIXES = ("ms_agent/", "scripts/")
_FORBIDDEN_PREFIXES = ("ms_agent/self_improve/",)


class RepairAgent:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Config / prompt construction
    # ------------------------------------------------------------------

    def _build_agent_config(self) -> Any:
        """Construct a minimal DictConfig for a sub-LLMAgent.

        Pattern follows ``agent_delegate.py:_build_agent_config`` (L223).
        """
        from omegaconf import OmegaConf

        return OmegaConf.create({
            "llm": self.config.get("llm", {"model": "qwen-max"}),
            "output_dir": str(Path.cwd()),
            "trust_remote_code": True,
            "tools": {
                "file_system": {
                    "allow_read_all_files": True,
                    "include": ["read_file", "grep", "glob", "edit_file"],
                },
            },
            "max_chat_round": 15,
            "save_history": False,
            "enable_snapshots": False,
            "callbacks": [],
            "prompt": {"system": self._repair_system_prompt()},
        })

    @staticmethod
    def _repair_system_prompt() -> str:
        return (
            "You are an expert Python developer fixing the ms_agent framework.\n"
            "\n"
            "## Workflow\n"
            "1. Use `grep` to search for relevant patterns or error messages in the codebase.\n"
            "2. Use `read_file` to read the full context of target files.\n"
            "3. Use `edit_file` to make precise, minimal changes.\n"
            "4. After editing, use `read_file` again to verify your changes look correct.\n"
            "\n"
            "## CRITICAL: edit_file usage\n"
            "The `read_file` tool returns content with line number prefixes like `123\\tcode here`.\n"
            "When using `edit_file`, the `old_string` and `new_string` must contain ONLY the actual\n"
            "file content — do NOT include line number prefixes. For example:\n"
            "  - read_file shows: `197\\t        self._init_lock = None`\n"
            "  - edit_file old_string should be: `        self._init_lock = None`\n"
            "Strip ALL line number prefixes (digits + tab) from old_string and new_string.\n"
            "\n"
            "## Safety constraints\n"
            "- Only modify files under `ms_agent/` or `scripts/`.\n"
            "- NEVER modify files under `ms_agent/self_improve/`.\n"
            "- Do NOT modify benchmark task files, outputs, logs, or test files.\n"
            "\n"
            "## Patch principles\n"
            "- Make the smallest possible change that fixes the root cause.\n"
            "- Do NOT hardcode any task-specific logic — only generalizable framework fixes.\n"
            "- Preserve existing code style and conventions.\n"
            "- Each edit_file call must use exact existing text for the search string.\n"
            "\n"
            "## When done\n"
            "After completing all edits, summarize what you changed in a short message.\n"
            "If you determine there is no safe framework fix, say so explicitly and make no edits.\n"
        )

    def _build_repair_prompt(
        self,
        plan: Optional[RepairPlan],
        signal: Optional[IncidentSignal],
        failed_attempts: Optional[list] = None,
    ) -> str:
        parts: list[str] = []

        parts.append(
            f"## Failure description\n"
            f"{plan.repair_prompt if plan else 'No description available.'}\n"
            f"Reason: {plan.reason if plan else 'Unknown'}\n"
        )

        target_paths = self._configured_source_paths(plan)
        if target_paths:
            parts.append(
                "## Target files to investigate\n"
                + "\n".join(f"- `{p}`" for p in target_paths)
                + "\n\nUse `read_file` and `grep` to explore these files. "
                "Do NOT guess file contents — always read first.\n"
            )

        if signal is not None:
            evidence_parts: list[str] = []
            for ev in signal.evidence_index[:4]:
                try:
                    raw = Path(ev.path).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                tail = raw[-2000:] if len(raw) > 2000 else raw
                evidence_parts.append(
                    f"--- {ev.kind.value} from {Path(ev.path).name} ---\n{tail}"
                )
            if evidence_parts:
                parts.append(
                    "## Evidence snippets (tail)\n"
                    + "\n\n".join(evidence_parts)
                    + "\n\nThese are truncated tails. Use `read_file` with the "
                    "evidence paths above if you need more context.\n"
                )

        if failed_attempts:
            parts.append("## Previous failed attempts\n")
            parts.append(
                "The following patches were already tried and failed. "
                "Do NOT repeat the same approach.\n\n"
            )
            for attempt in failed_attempts:
                parts.append(f"- Patch ID: {attempt.get('patch_id')}")
                parts.append(
                    f"  Error: {attempt.get('verification_log', 'Unknown')}\n"
                )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Source path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _configured_source_paths(plan: Optional[RepairPlan]) -> List[str]:
        paths: list[str] = []
        root = Path.cwd().resolve()
        for path in getattr(plan, "target_source_paths", []) or []:
            norm = os.path.normpath(path)
            if not norm.startswith(_SAFE_PREFIXES):
                continue
            candidate = (root / norm).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if norm not in paths:
                paths.append(norm)
        return paths

    # ------------------------------------------------------------------
    # Git diff extraction — only captures changes made DURING the session
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_dirty_files() -> set[str]:
        """Return set of files with uncommitted changes (vs HEAD)."""
        try:
            out = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, cwd=".",
            ).stdout.strip()
            staged = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                capture_output=True, text=True, cwd=".",
            ).stdout.strip()
        except Exception:
            return set()
        result = set()
        if out:
            result.update(out.splitlines())
        if staged:
            result.update(staged.splitlines())
        return result

    @staticmethod
    def _extract_changes_from_git(
        patch_id: str,
        pre_existing: set[str],
    ) -> Optional[RepairPatch]:
        """Build a RepairPatch from working-tree changes made since snapshot."""
        try:
            diff_names = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, cwd=".",
            ).stdout.strip()
        except Exception:
            return None

        if not diff_names:
            return None

        all_changed = diff_names.splitlines()
        newly_changed = [f for f in all_changed if f not in pre_existing]

        target_files = [
            f for f in newly_changed
            if f.startswith(_SAFE_PREFIXES)
            and not f.startswith(_FORBIDDEN_PREFIXES)
        ]
        if not target_files:
            print("[RepairAgent] No new allowed file changes detected.")
            return None

        disallowed = [
            f for f in newly_changed
            if not f.startswith(_SAFE_PREFIXES)
            or f.startswith(_FORBIDDEN_PREFIXES)
        ]
        if disallowed:
            print(
                "[RepairAgent] Reverting edits to disallowed files: "
                + ", ".join(disallowed)
            )
            subprocess.run(
                ["git", "checkout", "--"] + disallowed,
                cwd=".", capture_output=True,
            )

        try:
            diff_content = subprocess.run(
                ["git", "diff", "HEAD", "--"] + target_files,
                capture_output=True, text=True, cwd=".",
            ).stdout
        except Exception:
            diff_content = ""

        return RepairPatch(
            patch_id=patch_id,
            incident_fingerprint="tool_use_repair",
            target_files=target_files,
            diff_content=diff_content,
            description=f"Generated by RepairAgent (tool-use) for {patch_id}",
            file_patches=[],
        )

    # ------------------------------------------------------------------
    # Core: run LLMAgent with FileSystemTool
    # ------------------------------------------------------------------

    async def _run_repair_agent(
        self,
        plan: RepairPlan,
        signal: IncidentSignal,
        patch_id: str,
        failed_attempts: Optional[list] = None,
    ) -> Optional[RepairPatch]:
        from ms_agent.agent.llm_agent import LLMAgent

        pre_existing = self._snapshot_dirty_files()

        config = self._build_agent_config()
        agent = LLMAgent(config=config, tag=f"repair-{patch_id}")

        prompt = self._build_repair_prompt(plan, signal, failed_attempts)

        old_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            print(f"[RepairAgent] Starting tool-use repair for {patch_id}...")
            await agent.run(prompt)
        except Exception as e:
            print(f"[RepairAgent] Agent run failed: {e}")
        finally:
            sys.stdout = old_stdout
            try:
                if agent.tool_manager:
                    await agent.cleanup_tools()
            except Exception:
                pass

        return self._extract_changes_from_git(patch_id, pre_existing)

    def generate_patch(
        self,
        plan: RepairPlan,
        signal: IncidentSignal,
        patch_id: str,
        failed_attempts: Optional[list] = None,
    ) -> Optional[RepairPatch]:
        """Public sync interface — internally runs async LLMAgent."""
        print("[RepairAgent] Asking LLM to generate patch...")
        try:
            return asyncio.run(
                self._run_repair_agent(plan, signal, patch_id, failed_attempts)
            )
        except Exception as e:
            print(f"[RepairAgent] Patch generation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Legacy helpers (kept for backward compatibility)
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_unified_diff(diff_content: str) -> bool:
        return (
            "diff --git " in diff_content
            or (
                "\n--- " in f"\n{diff_content}"
                and "\n+++ " in f"\n{diff_content}"
                and "\n@@ " in f"\n{diff_content}"
            )
        )

    @staticmethod
    def _paths_from_unified_diff(diff_content: str) -> List[str]:
        paths: list[str] = []
        patterns = (
            re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE),
            re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE),
        )
        for pattern in patterns:
            for match in pattern.finditer(diff_content):
                for candidate in match.groups():
                    if candidate == "/dev/null":
                        continue
                    norm = os.path.normpath(candidate)
                    if norm.startswith("../") or norm in (".", ".."):
                        continue
                    if norm not in paths:
                        paths.append(norm)
        return paths

    @staticmethod
    def _extract_json(reply_text: str) -> Optional[dict]:
        fenced = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", reply_text, re.DOTALL
        )
        candidates = [fenced.group(1)] if fenced else []
        start = reply_text.find("{")
        if start != -1:
            candidates.append(reply_text[start:])

        decoder = json.JSONDecoder()
        for candidate in candidates:
            try:
                parsed, _ = decoder.raw_decode(candidate.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
