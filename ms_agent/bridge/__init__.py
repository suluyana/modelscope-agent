# Copyright (c) ModelScope Contributors. All rights reserved.
"""Local agent-bridge: connect personal Agent CLIs to the MS-Agent platform."""
from ms_agent.bridge.daemon import BridgeDaemon, main
from ms_agent.bridge.discovery import discover_runtimes
from ms_agent.bridge.pair import pair_bridge_with_platform

__all__ = [
    'BridgeDaemon',
    'discover_runtimes',
    'main',
    'pair_bridge_with_platform',
]
