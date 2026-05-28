import json
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, Iterable, List

from ms_agent.llm.llm import LLM
from ms_agent.llm.utils import Message
from omegaconf import OmegaConf

from ms_agent.self_improve.schemas import RepairPatch
from ms_agent.self_improve.planner import RepairPlan
from ms_agent.self_improve.schemas import IncidentSignal

class RepairAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Instantiate LLM from config if possible
        llm_config = OmegaConf.create({"llm": config.get("llm", {"model": "qwen-max"})})
        try:
            self.llm = LLM.from_config(llm_config)
        except Exception as e:
            print(f"[RepairAgent] Failed to init LLM, will use fallback. Error: {e}")
            self.llm = None

    def _looks_like_unified_diff(self, diff_content: str) -> bool:
        return (
            "diff --git " in diff_content
            or ("\n--- " in f"\n{diff_content}" and "\n+++ " in f"\n{diff_content}" and "\n@@ " in f"\n{diff_content}")
        )

    def _paths_from_unified_diff(self, diff_content: str) -> List[str]:
        paths = []
        patterns = (
            re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE),
            re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE),
        )
        for pattern in patterns:
            for match in pattern.finditer(diff_content):
                candidates = match.groups()
                for candidate in candidates:
                    if candidate == "/dev/null":
                        continue
                    norm = os.path.normpath(candidate)
                    if norm.startswith("../") or norm in (".", ".."):
                        continue
                    if norm not in paths:
                        paths.append(norm)
        return paths

    def _extract_json(self, reply_text: str) -> Optional[dict]:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply_text, re.DOTALL)
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

    def _candidate_paths_from_evidence(self, signal: Optional[IncidentSignal]) -> List[str]:
        if signal is None:
            return []

        paths = []
        root = Path.cwd().resolve()
        pattern = re.compile(r"\b(?:ms_agent|scripts)/[A-Za-z0-9_./-]+\.py\b")
        for ev in signal.evidence_index:
            try:
                with open(ev.path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            for match in pattern.findall(content):
                norm = os.path.normpath(match)
                if not (norm.startswith("ms_agent/") or norm.startswith("scripts/")):
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
        return paths[:5]

    def _source_context(self, paths: Iterable[str]) -> str:
        sections = []
        for path in paths:
            if not (path.startswith("ms_agent/") or path.startswith("scripts/")):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            if len(content) > 5000:
                content = content[:2500] + "\n# ... file truncated ...\n" + content[-2500:]
            sections.append(f"--- SOURCE FILE {path} ---\n{content}\n")
        if not sections:
            return (
                "--- SOURCE FILE CONTEXT ---\n"
                "No framework source file was confidently identified in the evidence. "
                "Return an empty patch unless the evidence contains enough exact existing source text.\n"
            )
        return "\n".join(sections)

    def _configured_source_paths(self, plan: Optional[RepairPlan]) -> List[str]:
        paths = []
        root = Path.cwd().resolve()
        for path in getattr(plan, "target_source_paths", []) or []:
            norm = os.path.normpath(path)
            if not (norm.startswith("ms_agent/") or norm.startswith("scripts/")):
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

    def _candidate_source_paths(
        self,
        signal: Optional[IncidentSignal],
        plan: Optional[RepairPlan] = None,
    ) -> List[str]:
        paths = self._candidate_paths_from_evidence(signal)
        for path in self._configured_source_paths(plan):
            if path not in paths:
                paths.append(path)
        if not paths:
            print(
                "[RepairAgent] No candidate framework source file found in evidence. "
                "Skipping patch generation."
            )
        return paths

    def _build_prompt(
        self,
        plan: Optional[RepairPlan],
        signal: Optional[IncidentSignal],
        failed_attempts: Optional[list] = None,
    ) -> str:
        prompt = (
            "You are an expert Python developer fixing the ms_agent framework.\n"
            f"Failure Plan: {plan.repair_prompt if plan else ''}\n"
            f"Reason: {plan.reason if plan else ''}\n\n"
            "Evidence Context:\n"
        )

        if signal is not None:
            for ev in signal.evidence_index:
                try:
                    with open(ev.path, "r", encoding="utf-8") as f:
                        content = f.read()
                        if len(content) > 2000:
                            content = content[-2000:]
                        prompt += f"--- {ev.kind.value} from {ev.path} ---\n{content}\n\n"
                except Exception:
                    pass

        candidate_paths = self._candidate_source_paths(signal, plan)
        prompt += self._source_context(candidate_paths)

        if failed_attempts:
            prompt += "--- PREVIOUS FAILED ATTEMPTS ---\n"
            prompt += (
                "You have already tried the following patches which failed. "
                "Do NOT generate the exact same patch again.\n\n"
            )
            for attempt in failed_attempts:
                prompt += f"Attempt Patch ID: {attempt.get('patch_id')}\n"
                prompt += f"Generated Patch:\n{json.dumps(attempt.get('patch_content', {}), indent=2)}\n"
                prompt += f"Verification Error:\n{attempt.get('verification_log', 'Unknown error')}\n\n"

        prompt += (
            "Please output a patch to fix this error. Format your response strictly as JSON:\n"
            "```json\n"
            "{\n"
            '  "target_files": ["relative/path.py"],\n'
            '  "diff_content": "empty string unless you are absolutely sure you can produce a complete valid unified diff",\n'
            '  "description": "What this patch does",\n'
            '  "file_patches": [\n'
            '    {\n'
            '      "path": "relative/path.py",\n'
            '      "search_text": "exact existing text copied from SOURCE FILE CONTEXT",\n'
            '      "replace_text": "replacement text"\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n"
            "Rules:\n"
            "- Prefer file_patches with diff_content=\"\". This is the default and safest patch format.\n"
            "- search_text must be copied exactly from SOURCE FILE CONTEXT and must be unique in that file.\n"
            "- Use unified diff only if you can produce a complete git-apply-compatible diff with exact line counts.\n"
            "- Only patch files listed in SOURCE FILE CONTEXT.\n"
            "- Do not patch benchmark task files, outputs, logs, or absolute paths.\n"
            "- If there is no safe framework fix, return target_files=[], diff_content=\"\", file_patches=[].\n"
        )
        return prompt

    def _to_repair_patch(
        self,
        parsed: dict,
        patch_id: str,
        signal: IncidentSignal,
        allowed_paths: Iterable[str],
    ) -> Optional[RepairPatch]:
        target_files = parsed.get("target_files", [])
        diff_content = parsed.get("diff_content", "")
        file_patches = parsed.get("file_patches", [])
        if not file_patches and not self._looks_like_unified_diff(diff_content):
            print("[RepairAgent] LLM returned no applicable patch content.")
            return None

        allowed = set(allowed_paths)
        requested_paths = set()
        if isinstance(target_files, list):
            requested_paths.update(str(path) for path in target_files)
        if isinstance(file_patches, list):
            requested_paths.update(str(fp.get("path", "")) for fp in file_patches if isinstance(fp, dict))
        if self._looks_like_unified_diff(diff_content):
            diff_paths = self._paths_from_unified_diff(diff_content)
            if not diff_paths:
                print("[RepairAgent] Unified diff did not expose target paths.")
                return None
            requested_paths.update(diff_paths)
        disallowed = sorted(path for path in requested_paths if path and path not in allowed)
        if disallowed:
            print(
                "[RepairAgent] LLM patch touched files outside SOURCE FILE CONTEXT: "
                + ", ".join(disallowed)
            )
            return None

        primary = signal.primary_incident
        return RepairPatch(
            patch_id=patch_id,
            incident_fingerprint=primary.fingerprint if primary else "unknown",
            target_files=target_files,
            diff_content=diff_content,
            description=parsed.get("description", "Generated by RepairAgent"),
            file_patches=file_patches,
        )

    def generate_patch(self, plan: RepairPlan, signal: IncidentSignal, patch_id: str, failed_attempts: list = None) -> Optional[RepairPatch]:
        if not self.llm:
            print("[RepairAgent] No LLM available, returning empty patch.")
            return None

        candidate_paths = self._candidate_source_paths(signal, plan)
        if not candidate_paths:
            return None

        prompt = self._build_prompt(plan, signal, failed_attempts)

        messages = [
            Message(role="system", content="You are a self-healing agent modifying a python framework."),
            Message(role="user", content=prompt)
        ]

        try:
            print("[RepairAgent] Asking LLM to generate patch...")
            # Some LLMs return a generator if stream is True, ms_agent LLM wraps this differently
            # For simplicity, we just grab text.
            response = self.llm.generate(messages, stream=False)
            
            # The response object can be a string or structured message depending on the LLM implementation
            # Let's extract text
            reply_text = ""
            if isinstance(response, str):
                reply_text = response
            elif hasattr(response, "content"):
                reply_text = response.content
            elif hasattr(response, "__aiter__"):
                # if it's an async generator, this will fail in sync context, but P0 skeleton might not handle it perfectly yet.
                pass 
                
            print(f"[RepairAgent] LLM replied: {reply_text[:100]}...")
            
            parsed = self._extract_json(reply_text)
            if parsed:
                return self._to_repair_patch(parsed, patch_id, signal, candidate_paths)
            else:
                print("[RepairAgent] Could not parse JSON from LLM response.")
        except Exception as e:
            print(f"[RepairAgent] LLM generation failed: {e}")

        return None
