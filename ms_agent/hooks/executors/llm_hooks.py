"""Shared helpers for prompt and agent hook executors."""

from __future__ import annotations

import json

from ms_agent.hooks.events import HookResult
from ms_agent.hooks.response_adapter import ResponseAdapter
from ms_agent.permission.provider import extract_json_value

_ADAPTER = ResponseAdapter()


def add_arguments_to_prompt(prompt: str, arguments: str) -> str:
    return prompt.replace('${ARGUMENTS}', arguments).replace('$ARGUMENTS', arguments)


def fail_closed_result(
    reason: str,
    *,
    fail_closed: bool,
) -> HookResult:
    if fail_closed:
        return HookResult(action='deny', reason=reason, exit_code=-1)
    return HookResult(action='error', reason=reason, exit_code=-1)


def hook_result_from_model_text(
    text: str,
    *,
    event: str | None = None,
    fail_closed: bool = False,
) -> HookResult:
    """Map `{ok, reason}` or a full hook JSON envelope to HookResult.

    ``ok: true`` is ``pass``, not auto-allow. Callers that need skip-ask
    must include ``permissionDecision: allow``.
    """
    parsed = extract_json_value(text)
    if not isinstance(parsed, dict):
        return fail_closed_result(
            'Hook LLM returned invalid JSON', fail_closed=fail_closed)

    if 'ok' in parsed:
        ok = parsed.get('ok')
        if ok is False or ok == 'false':
            return HookResult(
                action='deny',
                reason=str(parsed.get('reason') or 'Hook condition not met'),
            )
        if ok is not True and ok != 'true':
            return fail_closed_result(
                'Hook LLM returned invalid ok field',
                fail_closed=fail_closed,
            )
        perm = parsed.get('permissionDecision') or parsed.get('decision')
        if perm:
            action = ResponseAdapter._map_permission(str(perm))
            if action == 'pass':
                action = ResponseAdapter._map_decision(str(perm))
            return HookResult(
                action=action,
                reason=str(parsed.get('reason') or ''),
                additional_context=str(
                    parsed.get('additional_context')
                    or parsed.get('additionalContext') or ''),
            )
        return HookResult(
            action='pass',
            reason=str(parsed.get('reason') or ''),
            additional_context=str(
                parsed.get('additional_context')
                or parsed.get('additionalContext') or ''),
        )

    return _ADAPTER.parse(
        json.dumps(parsed, ensure_ascii=False),
        event=event,
    )
