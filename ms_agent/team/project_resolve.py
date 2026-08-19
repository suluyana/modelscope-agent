# Copyright (c) ModelScope Contributors. All rights reserved.
"""Project context resolution: thread binding, cards, NL disambiguation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ms_agent.team.errors import NEEDS_DISAMBIGUATION, NEEDS_PROJECT_CARD, PROJECT_REQUIRED, TeamError
from ms_agent.team.models import InboundMessage, OperationKind, TeamProjectMeta, ThreadBinding


@dataclass
class ProjectResolveResult:
    project_id: str | None = None
    needs_card: bool = False
    candidates: list[dict[str, str]] = field(default_factory=list)
    reason: str = ''


class ProjectResolver:
    """Resolve which project an inbound message should run against.

    Priority (requirements §1.4):
    1. Explicit project_id on message
    2. Thread binding
    3. Write ops → interactive card (never guess)
    4. Read ops → default project / unique NL match
    """

    def __init__(
        self,
        projects: Sequence[TeamProjectMeta],
        bindings: Sequence[ThreadBinding] | None = None,
    ) -> None:
        self._projects = {p.project_id: p for p in projects}
        self._by_name = {p.name: p for p in projects}
        self._bindings = {
            (b.chat_id, b.thread_id): b
            for b in (bindings or [])
        }

    def resolve(self, msg: InboundMessage) -> ProjectResolveResult:
        if msg.project_id:
            if msg.project_id not in self._projects and self._projects:
                # Allow unknown ids when project store is external.
                return ProjectResolveResult(project_id=msg.project_id)
            return ProjectResolveResult(project_id=msg.project_id)

        if msg.chat_id and msg.thread_id:
            binding = self._bindings.get((msg.chat_id, msg.thread_id))
            if binding:
                return ProjectResolveResult(project_id=binding.project_id)

        candidates = self._nl_candidates(msg.content)
        if msg.operation_kind == 'write':
            if len(candidates) == 1:
                # Still require explicit confirm for write — return card.
                return ProjectResolveResult(
                    needs_card=True,
                    candidates=candidates,
                    reason='write_requires_confirm',
                )
            if not candidates:
                return ProjectResolveResult(
                    needs_card=True,
                    candidates=[{
                        'project_id': p.project_id,
                        'name': p.name
                    } for p in self._projects.values()],
                    reason='write_project_required',
                )
            return ProjectResolveResult(
                needs_card=True,
                candidates=candidates,
                reason='write_ambiguous',
            )

        # read
        if len(candidates) == 1:
            return ProjectResolveResult(project_id=candidates[0]['project_id'])
        if len(candidates) > 1:
            return ProjectResolveResult(
                needs_card=True,
                candidates=candidates,
                reason='read_ambiguous',
            )
        # Fall back to first member default — still may be None.
        return ProjectResolveResult(
            project_id=None,
            reason='read_no_match',
        )

    def require_project(self, msg: InboundMessage) -> str:
        result = self.resolve(msg)
        if result.project_id:
            return result.project_id
        if result.needs_card:
            raise TeamError(
                NEEDS_PROJECT_CARD if msg.operation_kind == 'write' else
                NEEDS_DISAMBIGUATION,
                'Project context is unclear; please select a project.',
                http_status=409,
                details={
                    'candidates': result.candidates,
                    'reason': result.reason,
                },
            )
        raise TeamError(
            PROJECT_REQUIRED,
            'project_id is required.',
            http_status=400,
        )

    def _nl_candidates(self, content: str) -> list[dict[str, str]]:
        content_l = (content or '').lower()
        hits: list[dict[str, str]] = []
        for p in self._projects.values():
            if p.name and p.name.lower() in content_l:
                hits.append({'project_id': p.project_id, 'name': p.name})
            elif p.project_id.lower() in content_l:
                hits.append({'project_id': p.project_id, 'name': p.name})
        return hits

    @staticmethod
    def infer_operation_kind(content: str,
                             explicit: OperationKind | None = None
                             ) -> OperationKind:
        if explicit:
            return explicit
        write_hints = ('改', '修', '写', '删', '提交', 'push', 'commit', 'fix',
                       'implement', 'delete', 'deploy', '发布')
        text = content or ''
        if any(h in text.lower() for h in write_hints):
            return 'write'
        return 'read'
