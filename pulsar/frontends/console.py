"""Console frontend: render the event stream, and prompt for permissions.

This is the first (and, until the post-series TUI, only) consumer of the event
stream. It also supplies the human-escalation callback `console_ask` that the
permission chain calls when a tool needs the user's say-so.
"""

import sys

from .. import events as ev
from ..permissions import Decision
from ..registry import invoke

# Tool calls the user approved "for the session" so we stop re-asking.
_session_allow: set[tuple] = set()


def _key(tool_name: str, tool_input: dict) -> tuple:
    return (tool_name, tuple(sorted((k, str(v)) for k, v in tool_input.items())))


def console_ask(tool_name: str, tool_input: dict) -> Decision:
    """The human layer of the permission chain (Part 3), on the console."""
    key = _key(tool_name, tool_input)
    if key in _session_allow:
        return "allow"

    print("\n[permission] The agent wants to use:", file=sys.stderr)
    print(f"  tool:  {tool_name}", file=sys.stderr)
    print(f"  input: {tool_input}", file=sys.stderr)
    try:
        choice = input("  allow once / session / deny? [o/s/d] ").strip().lower()
    except EOFError:
        return "deny"

    if choice in ("o", "once", "allow", "a", "allow once"):
        return "allow"
    if choice in ("s", "session"):
        _session_allow.add(key)
        return "allow"
    return "deny"


# ANSI dim/colour helpers (kept minimal; degrade gracefully if not a TTY).
def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m"


def run_console(agent_name: str, prompt: str) -> str:
    """Drive an agent and print its event stream. Returns the final answer."""
    final_text = ""
    for event in invoke(agent_name, prompt, ask=console_ask):
        if isinstance(event, ev.AgentStarted):
            print(_dim(f"[agent: {event.agent}] {event.prompt}"))
        elif isinstance(event, ev.AssistantText):
            print(event.text, end="", flush=True)
        elif isinstance(event, ev.ToolRequested):
            print(_dim(f"\n[tool→] {event.name}({event.input})"))
        elif isinstance(event, ev.PermissionDecided):
            if event.decision == "deny":
                print(_dim(f"[permission] {event.name}: denied"))
        elif isinstance(event, ev.ToolExecuted):
            tag = "error" if event.is_error else "ok"
            preview = event.result if len(event.result) < 500 else event.result[:500] + " …"
            print(_dim(f"[tool← {tag}] {preview}"))
        elif isinstance(event, ev.Compacted):
            print(_dim(f"[context] compacted {event.turns} turns into a summary"))
        elif isinstance(event, ev.MemoryReloaded):
            print(_dim(f"[memory] reloaded ({event.path})"))
        elif isinstance(event, ev.AgentFinished):
            final_text = event.text
            print()  # newline after the streamed answer
        elif isinstance(event, ev.AgentError):
            print(_dim(f"\n[error] {event.message}"), file=sys.stderr)
    return final_text
