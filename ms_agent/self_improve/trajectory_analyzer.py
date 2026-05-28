"""Trajectory analyzer for self-improve.

Parses the raw agent stdout captured by EvalScope and extracts structured
signals: tool calls, shell commands, errors, and behavioral patterns that
the classifier and capability_miner can use for root-cause analysis.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import List

from ms_agent.self_improve.schemas import ToolCallRecord, TrajectoryAnalysis

_PREFIX_RE = re.compile(r"^\[(?:INFO|WARNING|ERROR):ms_agent\]\s*")
_TOOL_CALLING_RE = re.compile(r"\[tool_calling\]:")
_USAGE_RE = re.compile(
    r"\[usage\]\s*prompt_tokens:\s*(\d+),\s*completion_tokens:\s*(\d+)"
)
_AGENT_TASK_BEGIN_RE = re.compile(r"\[Agent-\w+\]\s*Agent\s+\S+\s+task beginning")
_AGENT_TASK_FINISH_RE = re.compile(r"\[Agent-\w+\]\s*Agent\s+\S+\s+task finished")
_TIMEOUT_RE = re.compile(r"(?:AgentTimeoutError|timed?\s*out|timeout)", re.IGNORECASE)
_ERROR_LINE_RE = re.compile(
    r"(?:Error|Exception|Traceback|FAILED|error:|fatal:)", re.IGNORECASE
)
_ACCESS_DENIED_RE = re.compile(r"Access denied:", re.IGNORECASE)
_SHELL_RETURN_CODE_RE = re.compile(r'"return_code":\s*(\d+)')


def _strip_prefix(line: str) -> str:
    return _PREFIX_RE.sub("", line)


def _try_parse_json_block(lines: List[str], start: int) -> tuple[dict | None, int]:
    """Try to parse a JSON object starting at *start*, spanning multiple lines."""
    depth = 0
    buf: list[str] = []
    for i in range(start, len(lines)):
        stripped = _strip_prefix(lines[i]).strip()
        # skip agent prefix like "[Agent-default] "
        agent_match = re.match(r"\[Agent-\w+\]\s*", stripped)
        if agent_match:
            stripped = stripped[agent_match.end():]
        buf.append(stripped)
        depth += stripped.count("{") - stripped.count("}")
        depth += stripped.count("[") - stripped.count("]")
        if depth <= 0 and buf:
            text = "\n".join(buf)
            try:
                return json.loads(text), i
            except json.JSONDecodeError:
                return None, i
    return None, len(lines) - 1


def _summarize(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def analyze(stdout: str) -> TrajectoryAnalysis:
    lines = stdout.splitlines()
    tool_calls: list[ToolCallRecord] = []
    shell_commands: list[str] = []
    errors: list[str] = []
    turn_count = 0
    call_order = 0
    has_timeout = False
    has_finish = False

    i = 0
    while i < len(lines):
        line = lines[i]
        clean = _strip_prefix(line).strip()

        # Count turns by [usage] markers
        if _USAGE_RE.search(clean):
            turn_count += 1

        # Detect timeout
        if _TIMEOUT_RE.search(clean):
            has_timeout = True

        # Detect task finished
        if _AGENT_TASK_FINISH_RE.search(clean):
            has_finish = True

        # Detect errors
        if _ERROR_LINE_RE.search(clean) and not _USAGE_RE.search(clean):
            err_text = _strip_prefix(line).strip()
            agent_match = re.match(r"\[Agent-\w+\]\s*", err_text)
            if agent_match:
                err_text = err_text[agent_match.end():]
            if len(err_text) > 10:
                errors.append(_summarize(err_text, 300))

        # Detect access denied
        if _ACCESS_DENIED_RE.search(clean):
            err_text = _strip_prefix(line).strip()
            errors.append(_summarize(err_text, 300))

        # Parse tool calls
        if _TOOL_CALLING_RE.search(clean):
            j = i + 1
            while j < len(lines):
                next_clean = _strip_prefix(lines[j]).strip()
                agent_match = re.match(r"\[Agent-\w+\]\s*", next_clean)
                if agent_match:
                    next_clean = next_clean[agent_match.end():]
                if next_clean.startswith("{"):
                    parsed, end_idx = _try_parse_json_block(lines, j)
                    if parsed and "tool_name" in parsed:
                        tool_name = parsed.get("tool_name", "")
                        args = parsed.get("arguments", {})
                        args_summary = ""
                        if isinstance(args, dict):
                            if "command" in args:
                                cmd = args["command"]
                                shell_commands.append(cmd)
                                args_summary = _summarize(cmd, 200)
                            elif "paths" in args:
                                args_summary = str(args["paths"])
                            else:
                                args_summary = _summarize(
                                    json.dumps(args, ensure_ascii=False), 200
                                )

                        # Look ahead for the result JSON
                        result_summary = ""
                        success = True
                        for k in range(end_idx + 1, min(end_idx + 50, len(lines))):
                            rclean = _strip_prefix(lines[k]).strip()
                            rm = re.match(r"\[Agent-\w+\]\s*", rclean)
                            if rm:
                                rclean = rclean[rm.end():]
                            if rclean.startswith("{"):
                                rp, rend = _try_parse_json_block(lines, k)
                                if rp is not None:
                                    if "success" in rp:
                                        success = bool(rp.get("success", True))
                                        out = rp.get("output", "")
                                        err = rp.get("error")
                                        result_summary = _summarize(
                                            str(err or out), 200
                                        )
                                    elif isinstance(rp, dict) and any(
                                        "Access denied" in str(v) for v in rp.values()
                                    ):
                                        success = False
                                        result_summary = "Access denied"
                                    break
                            if _TOOL_CALLING_RE.search(rclean):
                                break
                            if _USAGE_RE.search(rclean):
                                break

                        tool_calls.append(
                            ToolCallRecord(
                                tool_name=tool_name,
                                arguments_summary=args_summary,
                                result_summary=result_summary,
                                success=success,
                                order=call_order,
                            )
                        )
                        call_order += 1
                        j = end_idx + 1
                        continue
                    else:
                        j = end_idx + 1
                        continue
                elif next_clean == "" or next_clean.startswith("["):
                    if not _TOOL_CALLING_RE.search(next_clean):
                        break
                j += 1
            i = j
            continue

        i += 1

    # Determine final state
    if has_timeout:
        final_state = "timeout"
    elif errors and not has_finish:
        final_state = "error"
    elif has_finish:
        final_state = "completed"
    else:
        final_state = "unknown"

    # Detect repeated failure patterns
    repeated_patterns: list[str] = []
    if shell_commands:
        cmd_counts = Counter(shell_commands)
        for cmd, count in cmd_counts.items():
            if count >= 3:
                repeated_patterns.append(
                    f"Command repeated {count}x: {_summarize(cmd, 100)}"
                )

    failed_tools = [tc for tc in tool_calls if not tc.success]
    if len(failed_tools) >= 3:
        failed_names = Counter(tc.tool_name for tc in failed_tools)
        for tname, cnt in failed_names.items():
            if cnt >= 3:
                repeated_patterns.append(f"Tool {tname} failed {cnt}x")

    # Check for stuck loops (same tool+args repeated)
    if len(tool_calls) >= 4:
        tc_keys = [
            f"{tc.tool_name}:{tc.arguments_summary}" for tc in tool_calls
        ]
        key_counts = Counter(tc_keys)
        for key, cnt in key_counts.items():
            if cnt >= 3:
                tname = key.split(":")[0]
                repeated_patterns.append(
                    f"Stuck loop: {tname} called {cnt}x with same args"
                )

    unique_tools = sorted(set(tc.tool_name for tc in tool_calls))

    # Deduplicate errors
    seen_errors: set[str] = set()
    deduped_errors: list[str] = []
    for err in errors:
        key = err[:80]
        if key not in seen_errors:
            seen_errors.add(key)
            deduped_errors.append(err)

    return TrajectoryAnalysis(
        tool_calls=tool_calls,
        shell_commands=shell_commands,
        errors_encountered=deduped_errors[:50],
        final_state=final_state,
        total_turns=turn_count,
        unique_tools_used=unique_tools,
        repeated_failure_patterns=repeated_patterns,
    )
