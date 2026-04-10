# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

from abc import ABC, abstractmethod

from ms_agent.trajectory.collector import TrajectoryCollector


class BaseAdapter(ABC):
    """Attach framework hooks to a TrajectoryCollector."""

    @abstractmethod
    def attach(self, collector: TrajectoryCollector) -> None:
        """Subscribe to host events and forward to collector."""

    @abstractmethod
    def detach(self) -> None:
        """Unsubscribe and restore host state."""
