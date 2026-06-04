# Copyright (c) ModelScope Contributors. All rights reserved.
import sys
import subprocess

try:
    import omegaconf
except ImportError:
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "omegaconf", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

from .agent.llm_agent import LLMAgent
