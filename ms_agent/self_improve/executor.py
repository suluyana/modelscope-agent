import os
import subprocess
from typing import List, Dict, Any, Tuple
from ms_agent.self_improve.schemas import GuardDecision, RepairPatch

class FileGuard:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {
            "include_paths": ["ms_agent/", "scripts/"],
            "exclude_paths": ["bench_local/", "outputs/", ".cache/", ".venv/"],
            "always_allowed_extensions": [".py", ".sh", ".json", ".md", ".yaml", ".yml", ".toml"],
            "always_allowed_filenames": ["Dockerfile", "Makefile", ".gitignore"],
            "never_allow_extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz", ".7z", ".bin", ".so", ".dylib", ".exe"],
            "max_file_size_kb": 2048,
            "allow_rename": False,
            "allow_delete": False,
        }

    def _is_path_allowed(self, path: str) -> bool:
        included = any(path.startswith(inc) for inc in self.config["include_paths"])
        excluded = any(path.startswith(exc) for exc in self.config["exclude_paths"])
        return included and not excluded

    def evaluate(self, path: str, operation: str, is_text: bool, size_kb: float, approval_mode: str) -> GuardDecision:
        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1]

        # 1. Hard Deny
        if not self._is_path_allowed(path):
            return GuardDecision(allowed=False, reason="Path not in include_paths or is in exclude_paths", policy_applied="hard_deny")
        if not is_text:
            return GuardDecision(allowed=False, reason="Binary files are not allowed", policy_applied="hard_deny")
        if ext in self.config.get("never_allow_extensions", []):
            return GuardDecision(allowed=False, reason="Extension is in never_allow_extensions", policy_applied="hard_deny")
        if size_kb > self.config.get("max_file_size_kb", 2048):
            return GuardDecision(allowed=False, reason="File size exceeds limit", policy_applied="hard_deny")
        if operation == "delete" and not self.config.get("allow_delete", False):
            return GuardDecision(allowed=False, reason="Delete operation is disabled", policy_applied="hard_deny")
        if operation == "rename" and not self.config.get("allow_rename", False):
            return GuardDecision(allowed=False, reason="Rename operation is disabled", policy_applied="hard_deny")

        # 2. Direct Allow
        is_allowed_ext = ext in self.config.get("always_allowed_extensions", [])
        is_allowed_file = filename in self.config.get("always_allowed_filenames", [])
        
        if operation == "modify" and (is_allowed_ext or is_allowed_file):
            return GuardDecision(allowed=True, reason="Direct allow for safe extension/filename modification", policy_applied="direct_allow")

        # 3. Controlled Create
        if operation == "create" and (is_allowed_ext or is_allowed_file):
            if approval_mode == "assist":
                return GuardDecision(allowed=False, reason="Controlled create requires human approval in assist mode", policy_applied="controlled_create")
            return GuardDecision(allowed=True, reason="Controlled create allowed", policy_applied="controlled_create")

        # 4. Adaptive Allow (For existing text files not in allowlist)
        if operation == "modify":
            if approval_mode == "assist":
                return GuardDecision(allowed=False, reason="Adaptive allow requires human approval in assist mode", policy_applied="adaptive_allow")
            return GuardDecision(allowed=True, reason="Adaptive allow for modification", policy_applied="adaptive_allow")

        return GuardDecision(allowed=False, reason="Default deny", policy_applied="default_deny")

class RepairExecutor:
    def __init__(self, guard: FileGuard, mode: str = "assist"):
        self.guard = guard
        self.mode = mode
        self.last_error = ""

    def _fail(self, message: str) -> bool:
        self.last_error = message
        print(f"[Executor] {message}")
        return False

    def _normalize_patch_path(self, path: str):
        path = path.strip().strip('"')
        if path in ("", "/dev/null"):
            return None
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        path = os.path.normpath(path)
        if os.path.isabs(path) or path == "." or path.startswith(".."):
            raise ValueError(f"Unsafe patch path: {path!r}")
        return path

    def _assert_regular_workspace_path(self, path: str) -> bool:
        current = "."
        for part in os.path.normpath(path).split(os.sep)[:-1]:
            current = os.path.join(current, part)
            if os.path.exists(current) and os.path.realpath(current) != os.path.abspath(current):
                self.last_error = f"Refusing to patch path under symlinked directory: {path}"
                print(f"[Executor] {self.last_error}")
                return False
        if os.path.exists(path) and os.path.realpath(path) != os.path.abspath(path):
            self.last_error = f"Refusing to patch symlinked path: {path}"
            print(f"[Executor] {self.last_error}")
            return False
        return True

    def _diff_target_paths(self, diff_content: str) -> List[str]:
        return sorted({path for path, _operation in self._git_diff_target_operations(diff_content)})

    def _git_diff_target_operations(self, diff_content: str) -> List[Tuple[str, str]]:
        parsed = subprocess.run(
            ["git", "apply", "--numstat", "--summary"],
            input=diff_content,
            text=True,
            capture_output=True,
        )
        if parsed.returncode != 0:
            raise ValueError(f"git could not parse unified diff: {parsed.stderr.strip()}")

        create_paths = set()
        delete_paths = set()
        unsupported_operation = None
        numstat_paths = []

        for line in parsed.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("create mode "):
                if " 120000 " in f" {stripped} ":
                    raise ValueError("symlink creation in unified diff is not allowed")
                create_paths.add(self._normalize_patch_path(stripped.split(maxsplit=3)[3]))
            elif stripped.startswith("delete mode "):
                delete_paths.add(self._normalize_patch_path(stripped.split(maxsplit=3)[3]))
            elif stripped.startswith("rename ") or stripped.startswith("copy "):
                unsupported_operation = stripped.split(maxsplit=1)[0]
            else:
                parts = line.split("\t")
                if len(parts) >= 3:
                    numstat_paths.append(self._normalize_patch_path(parts[-1]))

        if unsupported_operation:
            # Rename/copy diffs are intentionally not supported by the auto patcher.
            return [(path, "rename") for path in sorted(path for path in numstat_paths if path)]

        operations = []
        for path in numstat_paths:
            if not path:
                continue
            if path in delete_paths:
                operation = "delete"
            elif path in create_paths:
                operation = "create"
            else:
                operation = "modify"
            operations.append((path, operation))

        deduped = []
        seen = set()
        for item in operations:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _looks_like_unified_diff(self, diff_content: str) -> bool:
        return (
            "diff --git " in diff_content
            or ("\n--- " in f"\n{diff_content}" and "\n+++ " in f"\n{diff_content}" and "\n@@ " in f"\n{diff_content}")
        )

    def _evaluate_operations(self, operations: List[Tuple[str, str]], patch: RepairPatch, human_approval_callback=None) -> bool:
        for target, operation in operations:
            if not self._assert_regular_workspace_path(target):
                return False
            size_kb = 0
            if os.path.exists(target):
                size_kb = os.path.getsize(target) / 1024.0

            decision = self.guard.evaluate(
                path=target,
                operation=operation,
                is_text=True,
                size_kb=size_kb,
                approval_mode=self.mode,
            )

            if not decision.allowed:
                if decision.policy_applied == "hard_deny":
                    print(f"[Executor] Patch denied by guard: {target}: {decision.reason}")
                    return False
                if self.mode == "assist" and human_approval_callback:
                    approved = human_approval_callback(patch, decision)
                    if approved:
                        continue
                    print(f"[Executor] Patch rejected by human: {target}: {decision.reason}")
                    return False
                print(f"[Executor] Patch denied by guard: {target}: {decision.reason}")
                return False
        return True

    def _apply_unified_diff(self, patch: RepairPatch, human_approval_callback=None) -> bool:
        if "GIT binary patch" in patch.diff_content:
            return self._fail("Binary git patches are not allowed.")

        try:
            operations = self._git_diff_target_operations(patch.diff_content)
        except ValueError as exc:
            return self._fail(f"Unified diff path validation failed: {exc}")

        if not operations:
            return self._fail("Unified diff did not contain any target paths.")
        if not self._evaluate_operations(operations, patch, human_approval_callback):
            return False

        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn"],
            input=patch.diff_content,
            text=True,
            capture_output=True,
        )
        if check.returncode != 0:
            return self._fail(f"Unified diff check failed: {check.stderr.strip()}")

        apply = subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            input=patch.diff_content,
            text=True,
            capture_output=True,
        )
        if apply.returncode != 0:
            return self._fail(f"Unified diff apply failed: {apply.stderr.strip()}")

        targets = sorted({path for path, _operation in operations})
        print(f"[Executor] Applied unified diff for {', '.join(targets)}.")
        return self._commit_patch(patch, targets)

    def _commit_patch(self, patch: RepairPatch, targets: List[str]) -> bool:
        try:
            repo_cwd = "."
            subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, cwd=repo_cwd)

            for target in targets:
                subprocess.run(["git", "add", os.path.abspath(target)], check=True, cwd=repo_cwd)

            commit_msg = f"fix(self-improve): auto repair {patch.patch_id}\n\n{patch.description}"
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=self-improve",
                    "-c",
                    "user.email=self-improve@example.invalid",
                    "commit",
                    "-m",
                    commit_msg,
                ],
                check=True,
                cwd=repo_cwd,
            )
            print(f"[Executor] Created git commit for patch {patch.patch_id}")

            sha_proc = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, cwd=repo_cwd, text=True)
            new_sha = sha_proc.stdout.strip()
            print(f"[Executor] New commit SHA: {new_sha}")
        except Exception as e:
            print(f"[Executor] Warning: Failed to create git commit (or not a git repo): {e}")

        print(f"[Executor] Successfully applied patch {patch.patch_id}")
        return True

    def apply_patch(self, patch: RepairPatch, human_approval_callback=None) -> bool:
        """
        Validates the patch against the file guard and applies it if allowed.
        In assist mode, human_approval_callback should be provided.
        """
        self.last_error = ""
        if not patch.file_patches and self._looks_like_unified_diff(patch.diff_content):
            return self._apply_unified_diff(patch, human_approval_callback)

        # Collect unique target paths
        try:
            targets = {self._normalize_patch_path(path) for path in patch.target_files}
            file_patch_paths = [self._normalize_patch_path(fp.path) for fp in patch.file_patches]
        except ValueError as exc:
            return self._fail(f"Patch path validation failed: {exc}")
        targets = {path for path in targets if path}
        targets.update(path for path in file_patch_paths if path)

        for target in targets:
            if not self._assert_regular_workspace_path(target):
                return False
            # Check file size if exists
            size_kb = 0
            if os.path.exists(target):
                size_kb = os.path.getsize(target) / 1024.0

            decision = self.guard.evaluate(
                path=target,
                operation="modify" if os.path.exists(target) else "create",
                is_text=True,
                size_kb=size_kb,
                approval_mode=self.mode
            )
            
            if not decision.allowed:
                if decision.policy_applied == "hard_deny":
                    print(f"[Executor] Patch denied by guard: {decision.reason}")
                    return False
                if self.mode == "assist" and human_approval_callback:
                    approved = human_approval_callback(patch, decision)
                    if not approved:
                        print(f"[Executor] Patch rejected by human: {decision.reason}")
                        return False
                else:
                    print(f"[Executor] Patch denied by guard: {decision.reason}")
                    return False
        
        # Apply the file_patches
        planned_contents = {}
        for fp, path in zip(patch.file_patches, file_patch_paths):
            if not path or not os.path.exists(path):
                return self._fail(
                    f"Warning: target file {fp.path} does not exist. Cannot patch."
                )

            if path in planned_contents:
                content = planned_contents[path]
            else:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

            if fp.search_text not in content:
                return self._fail(f"Failed to find search_text in {path}. Patch failed.")
            if content.count(fp.search_text) != 1:
                return self._fail(f"search_text in {path} is not unique. Patch failed.")

            new_content = content.replace(fp.search_text, fp.replace_text)
            planned_contents[path] = new_content

        patched_paths = []
        for path, new_content in planned_contents.items():
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[Executor] Patched {path} successfully.")
            patched_paths.append(path)

        return self._commit_patch(patch, patched_paths)
