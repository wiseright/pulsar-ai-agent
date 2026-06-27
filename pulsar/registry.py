"""Agent registry and the `invoke` entry point.

Agents are invoked by name plus a prompt — `invoke("run_agent", "...")`. This
keeps the call site decoupled from any single agent function and lays the
groundwork for Part 6 (a coordinator that picks among several agents).
"""

from collections.abc import Iterator

from . import events as ev
from .loop import run_agent

# name -> agent function. In Part 6 a sub-agent is just `run_agent` again, run
# with a narrower tool catalogue (see subagents.py) — so there is no separate
# entry: `invoke("run_agent", task, collect=True, tools=..., system_extra=...)`.
AGENTS = {
    "run_agent": run_agent,
}


def invoke(agent_name: str, prompt: str, *, collect: bool = False, **kwargs):
    """Run an agent by name.

    By default returns the event generator (the streaming channel). With
    `collect=True`, drains the stream and returns just the final answer string —
    the convenience form for callers that do not care about intermediate events.
    """
    if agent_name not in AGENTS:
        raise KeyError(f"unknown agent: {agent_name!r}. Known: {sorted(AGENTS)}")

    stream: Iterator[ev.Event] = AGENTS[agent_name](prompt, **kwargs)
    if not collect:
        return stream

    final_text = ""
    for event in stream:
        if isinstance(event, ev.AgentFinished):
            final_text = event.text
    return final_text
