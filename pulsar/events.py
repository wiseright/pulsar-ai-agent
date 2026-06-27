"""Typed events emitted by the agent loop (Part 1).

The core of the agent is UI-agnostic: instead of printing, `run_agent` *yields*
these events. Today a console frontend consumes them; later a Python TUI can
consume the very same stream without the core changing a line.

Each event is a small immutable record. `kind` gives a stable string tag, handy
for a future TUI (or NDJSON bridge) that wants to switch on the event type.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    """Base class for all events. `kind` is set by each subclass."""

    kind: str = "event"


@dataclass(frozen=True)
class AgentStarted(Event):
    agent: str = ""
    prompt: str = ""
    kind: str = "agent_started"


@dataclass(frozen=True)
class AssistantText(Event):
    """A chunk of streamed assistant text (a token or a few)."""

    text: str = ""
    kind: str = "assistant_text"


@dataclass(frozen=True)
class ToolRequested(Event):
    """The model asked to call a tool; not yet permitted or executed."""

    tool_use_id: str = ""
    name: str = ""
    input: dict | None = None
    kind: str = "tool_requested"


@dataclass(frozen=True)
class PermissionDecided(Event):
    """Outcome of the permission chain for a tool call (Part 3)."""

    name: str = ""
    decision: str = ""   # "allow" | "deny"
    kind: str = "permission_decided"


@dataclass(frozen=True)
class ToolExecuted(Event):
    name: str = ""
    result: str = ""
    is_error: bool = False
    kind: str = "tool_executed"


@dataclass(frozen=True)
class Compacted(Event):
    """The context compactor summarised some turns (Part 4)."""

    turns: int = 0
    kind: str = "compacted"


@dataclass(frozen=True)
class MemoryReloaded(Event):
    """Memory changed on disk and was reloaded into the system prompt (Part 5)."""

    path: str = ""
    kind: str = "memory_reloaded"


@dataclass(frozen=True)
class AgentFinished(Event):
    """The model produced a final answer; the loop is done."""

    text: str = ""
    kind: str = "agent_finished"


@dataclass(frozen=True)
class AgentError(Event):
    message: str = ""
    kind: str = "agent_error"
