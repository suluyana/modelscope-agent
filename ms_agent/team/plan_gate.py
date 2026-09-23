"""C-15 Plan gate: Worker produces a read-only plan before any code change.

Default off (``TeamProjectMeta.plan_gate``). When on, a task whose
``plan_status`` is not ``approved`` is dispatched in SDK ``plan`` mode
(reads + task-board only). Completing that pass parks the row as
``wait_human`` / ``plan_status=ready`` until a human approves.
"""

from __future__ import annotations

from ms_agent.team.models import TeamProjectMeta, TeamTask

PLAN_GATE_PREFIX = (
    '[plan_gate] Produce a read-only plan first. Do not modify files and do '
    'not run write or network shell commands. Write the plan with '
    'task_board_write.result_summary, then stop. A human must approve '
    'before any code change.\n\n'
)

_PLAN_PENDING = frozenset({'', 'required', 'ready'})


def project_wants_plan(project: TeamProjectMeta | None) -> bool:
    return bool(project is not None and getattr(project, 'plan_gate', False))


def task_needs_plan_pass(task: TeamTask | None, project: TeamProjectMeta | None) -> bool:
    if not project_wants_plan(project) or task is None:
        return False
    return (task.plan_status or '') != 'approved'


def apply_plan_prefix(prompt: str) -> str:
    text = prompt or ''
    if text.startswith('[plan_gate]'):
        return text
    return PLAN_GATE_PREFIX + text
