# Copyright (c) ModelScope Contributors. All rights reserved.
"""OpenClaw-family adapter (Tier B stub, one module for multiple products).

Products to verify in Tier C: openclaw, arkclaw, copaw, qclaw, nanobot.

Design: shared stub now; if event schemas diverge, use ``data["variant"]`` or
split into per-product adapters.

Checklist per product:
- Entry binary / extension API for hooks
- Whether events match a common schema
- Tool and async task lifecycle naming

Emit with ``data["framework"]`` e.g. ``"openclaw"`` and optional ``variant``.

**Shipped integration:** copy ``contrib/openclaw-trajectory-hook/`` into
``~/.openclaw/hooks/`` (see ``contrib/README.md``).
"""
from __future__ import annotations

from ms_agent.trajectory.adapters.base import BaseAdapter
from ms_agent.trajectory.collector import TrajectoryCollector


class OpenClawFamilyAdapter(BaseAdapter):
    def attach(self, collector: TrajectoryCollector) -> None:
        raise NotImplementedError(
            'OpenClawFamilyAdapter.attach is Tier C; wire host plugin or sidecar'
        )

    def detach(self) -> None:
        pass
