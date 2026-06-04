# Copyright (c) ModelScope Contributors. All rights reserved.
import sys
import subprocess
import importlib.util

if not importlib.util.find_spec('omegaconf'):
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', 'omegaconf'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        print(f'Failed to install omegaconf: {e.stderr.decode()}')
        raise ImportError('Failed to install omegaconf') from None
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
