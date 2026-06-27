"""Pulsar — a didactic, runnable coding agent (Parts 1-6).

Public API:
    from pulsar import invoke, run_agent
    for event in invoke("run_agent", "do something"):
        ...
"""

from . import events
from .loop import run_agent
from .registry import AGENTS, invoke

__all__ = ["invoke", "run_agent", "AGENTS", "events"]
