import re
import shlex
import subprocess
from typing import List, Dict
from ms_agent.self_improve.schemas import VerificationResult

class TemplateVarResolver:
    def __init__(self, config_defaults: Dict[str, str] = None):
        self.config_defaults = config_defaults or {"related_tests_expr": ""}

    def resolve(self, template: str, adapter_context: Dict[str, str], runtime_args: Dict[str, str], profile: str) -> str:
        # Check required variables
        required_vars = {"generic_python": ["config", "query"], "terminal_bench_v2": ["task_name"]}
        required = required_vars.get(profile, [])
        
        merged_context = {}
        # Order: config_defaults -> runtime_args -> adapter_context
        merged_context.update(self.config_defaults)
        merged_context.update(runtime_args)
        merged_context.update(adapter_context)

        referenced_vars = set(re.findall(r'\$\{([^}]+)\}', template))
        for req in required:
            if req not in referenced_vars:
                continue
            if req not in merged_context:
                raise ValueError(f"Missing required template variable: {req}")

        # Basic substitution ${var}
        def repl(match):
            var_name = match.group(1)
            val = merged_context.get(var_name, "")
            return shlex.quote(str(val))

        resolved = re.sub(r'\$\{([^}]+)\}', repl, template)
        # Normalize spaces
        resolved = re.sub(r'\s+', ' ', resolved).strip()
        return resolved

class Verifier:
    def __init__(self, resolver: TemplateVarResolver = None):
        self.resolver = resolver or TemplateVarResolver()

    def run_commands(self, commands: List[str], cwd: str = ".") -> VerificationResult:
        all_output = ""
        cmds_run = []
        for cmd in commands:
            if not cmd.strip():
                continue
            cmds_run.append(cmd)
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=cwd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, check=True
                )
                all_output += f"$ {cmd}\n{result.stdout}\n"
            except subprocess.CalledProcessError as e:
                all_output += f"$ {cmd}\n{e.stdout}\n"
                return VerificationResult(
                    passed=False,
                    exit_code=e.returncode,
                    output_log=all_output,
                    commands_run=cmds_run
                )
        
        return VerificationResult(
            passed=True,
            exit_code=0,
            output_log=all_output,
            commands_run=cmds_run
        )

    def verify_patch(self, patch_commands_templates: List[str], adapter_context: Dict[str, str], runtime_args: Dict[str, str], profile: str) -> VerificationResult:
        resolved_cmds = []
        for tpl in patch_commands_templates:
            try:
                resolved_cmds.append(self.resolver.resolve(tpl, adapter_context, runtime_args, profile))
            except ValueError as e:
                return VerificationResult(passed=False, exit_code=-1, output_log=str(e), commands_run=[])
                
        return self.run_commands(resolved_cmds)
