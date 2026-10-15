"""Settings and inbox over PermissionLedger."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.envelope import EnvelopeRoute

router = APIRouter(
    prefix="/api/permissions",
    tags=["permissions"],
    route_class=EnvelopeRoute,
)


class RuleCreate(BaseModel):
    pattern: str
    scope: Literal["project", "global"] = "project"
    project_id: Optional[str] = None


class RuleDelete(BaseModel):
    pattern: str
    project_id: Optional[str] = None


class PreviewBody(BaseModel):
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    project_id: Optional[str] = None


class AskResolve(BaseModel):
    action: Literal["allow_once", "allow_always", "deny"] = "allow_once"
    pattern: Optional[str] = None
    session_id: Optional[str] = None


def _reload_rules() -> None:
    """Live agents freeze permission memory at build time."""
    from app.backends.ms_agent.runtime import registry

    registry.mark_all_stale()


@router.get("/rules")
def list_rules(project_id: Optional[str] = None, scope: Optional[str] = None):
    from app.backends.ms_agent.permission_store import ledger_for_project

    ledger = ledger_for_project(project_id)
    narrowed = scope if scope in ("project", "global") else None
    entries = ledger.list_rules(scope=narrowed)
    return {
        "rules": [ledger.serialize_rule(e) for e in entries],
        "agent_provider_available": True,
        "approver_id": "permission-approver",
    }


@router.post("/rules")
def add_rule(body: RuleCreate):
    from ms_agent.permission.pattern_guard import UnsafePersistPattern

    from app.backends.ms_agent.permission_store import ledger_for_project

    ledger = ledger_for_project(body.project_id)
    if body.scope == "project" and ledger.memory._project_file is None:
        raise HTTPException(400, "project_id required for project-scoped rules")
    try:
        ledger.add_rule(body.pattern, scope=body.scope)
    except UnsafePersistPattern as exc:
        raise HTTPException(400, str(exc)) from exc
    _reload_rules()
    return {"ok": True, "pattern": body.pattern, "scope": body.scope}


@router.delete("/rules")
def delete_rule(body: RuleDelete):
    from app.backends.ms_agent.permission_store import ledger_for_project

    ledger = ledger_for_project(body.project_id)
    removed = ledger.revoke_rule(body.pattern)
    _reload_rules()
    return {"ok": True, "removed": removed}


@router.post("/preview")
def preview_match(body: PreviewBody):
    from app.backends.ms_agent.permission_store import ledger_for_project

    return ledger_for_project(body.project_id).preview_match(
        body.tool_name, body.tool_args)


@router.get("/asks")
def list_asks(project_id: Optional[str] = None, state: Optional[str] = None):
    from app.backends.ms_agent.permission_store import (
        ledger_for_project,
        serialize_ask,
    )

    ledger = ledger_for_project(project_id)
    rows = ledger.list_asks(state=state, project_id=project_id)
    return {"asks": [serialize_ask(r) for r in rows]}


@router.post("/asks/{request_id}/resolve")
def resolve_ask(request_id: str, body: AskResolve):
    from ms_agent.permission.handler import PermissionAction, PermissionResponse
    from ms_agent.permission.pattern_guard import (
        UnsafePersistPattern,
        validate_persist_pattern,
    )

    from app.backends.ms_agent.permission_store import asks_store, ledger_for
    from app.backends.ms_agent.runtime import registry

    if body.session_id and registry.resolve_permission(
            body.session_id, request_id, body.action):
        return {"resolved": True, "via": "live"}

    store = asks_store()
    request = store.get(request_id)
    if request is None:
        raise HTTPException(404, "ask not found")
    ledger = ledger_for()
    if body.action == "deny":
        try:
            ledger.decide_ask(
                request,
                PermissionResponse(action=PermissionAction.DENY),
            )
        except Exception:  # noqa: BLE001
            try:
                store.queue_resume(request_id, decision="deny")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(409, str(exc)) from exc
        return {"resolved": True, "via": "store"}

    pattern = body.pattern or ""
    if body.action == "allow_always":
        try:
            pattern = validate_persist_pattern(pattern or request.tool_name)
        except UnsafePersistPattern as exc:
            raise HTTPException(400, str(exc)) from exc
        ledger.add_rule(pattern, scope="project")
        _reload_rules()
    try:
        ledger.queue_resume(
            request_id,
            decision="allow_once",
            pattern=pattern,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(409, str(exc)) from exc
    return {"resolved": True, "via": "continuation"}
