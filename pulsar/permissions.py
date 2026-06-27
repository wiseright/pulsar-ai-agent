"""The permission system (Part 3).

A chain of responsibility maps (tool_name, tool_input) to a decision:

    user hooks  ->  static rules  ->  semantic classifier  ->  ask the human

The first layer with a definite opinion (allow/deny) wins; "ask" falls through
to the next layer; the human is the final arbiter. The human prompt itself is
injected by the frontend (`ask` callback), so this module stays UI-agnostic.

Every tool has an explicit static rule — there is no "allow by default". The
default for anything unrecognised is to ask.
"""

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from .config import PROJECT_ROOT

Decision = Literal["allow", "deny", "ask"]

# A callback the human-escalation layer uses to ask the user. Provided by the
# frontend; takes (tool_name, tool_input) and returns "allow" or "deny".
AskFn = Callable[[str, dict], Decision]

# Shell patterns that are never acceptable, regardless of context. Deny rules are
# written defensively: anything that even smells like one of these is denied.
_SHELL_DENY_SUBSTRINGS = (
    "rm -rf /",
    "rm -rf ~",
    "sudo ",
    "mkfs",
    ":(){",          # fork bomb
    "> /dev/sda",
    "dd if=",
)
_SHELL_DENY_PAIRS = (
    ("curl ", "| sh"),
    ("curl ", "|sh"),
    ("wget ", "| sh"),
)


# ---------------------------------------------------------------------------
# Layer 1: static rules — one explicit branch per tool.
# ---------------------------------------------------------------------------
def static_rules(tool_name: str, tool_input: dict) -> Decision:
    """Pure function from (tool, input) to allow/deny/ask. Cheap and total."""
    if tool_name in ("read_file", "list_directory"):
        # Side-effect-free and confined to the working directory by the tool.
        return "allow"

    if tool_name == "write_file":
        path = str(tool_input.get("path", ""))
        if os.path.isabs(path) or ".." in Path(path).parts:
            return "deny"            # escaping the working directory
        return "allow"               # inside the working directory

    if tool_name == "run_shell":
        cmd = str(tool_input.get("command", ""))
        if any(bad in cmd for bad in _SHELL_DENY_SUBSTRINGS):
            return "deny"
        if any(a in cmd and b in cmd for a, b in _SHELL_DENY_PAIRS):
            return "deny"
        return "ask"                 # never auto-allowed — the user authorises

    if tool_name == "propose_memory_update":
        # Writing long-term memory is privileged: always confirm (Part 5).
        return "ask"

    if tool_name == "spawn_agent":
        # Part 6: spawning a sub-agent is itself allowed. The article's §7 frames
        # this as "sub-agents are gated inside themselves" — the worker's OWN
        # read_file/list_directory calls still go through check_permission, so the
        # gating happens inside the worker, not at the spawn point.
        # Per Part 6 §3: Claude Code gives each worker its own permission
        # context (default accept-edits) and lets it write. Our workers
        # are read-only (their catalogue is read_file + list_directory only), which
        # makes `allow` the safe, didactic choice here.
        return "allow"

    return "ask"                     # safe default for anything unrecognised


# ---------------------------------------------------------------------------
# Layer 0 (runs first): user/project hooks — declarative overrides.
# ---------------------------------------------------------------------------
def _load_hooks() -> dict:
    """Read optional `permissions.toml` from the project root.

    Shape:
        [allow]
        run_shell = ["pnpm test", "git status"]
        [deny]
        read_file = ["secrets.env"]
    Returns {} if absent or malformed.
    """
    path = PROJECT_ROOT / "permissions.toml"
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def user_hooks(tool_name: str, tool_input: dict) -> Decision | None:
    """User-authored overrides. Returns None to mean "no opinion".

    Hooks run before the built-in static rules: a command the user pre-approved
    is allowed without escalation; a path the user blocked is denied even if the
    defaults would allow it.
    """
    hooks = _load_hooks()
    # The "value" we match against is command for run_shell, else path.
    value = tool_input.get("command") if tool_name == "run_shell" else tool_input.get("path")
    if value is None:
        return None
    deny = hooks.get("deny", {}).get(tool_name, [])
    if any(pat in value for pat in deny):
        return "deny"
    allow = hooks.get("allow", {}).get(tool_name, [])
    if any(pat in value for pat in allow):
        return "allow"
    return None


# ---------------------------------------------------------------------------
# Layer 3: semantic classifier (stub).
# ---------------------------------------------------------------------------
def semantic_classifier(tool_name: str, tool_input: dict) -> Decision:
    """Where a small model would judge ambiguous cases. We always escalate."""
    return "ask"


# ---------------------------------------------------------------------------
# The chain.
# ---------------------------------------------------------------------------
def check_permission(tool_name: str, tool_input: dict, ask: AskFn) -> Decision:
    """Walk the chain and return a final allow/deny.

    `ask` is the human-escalation callback (supplied by the frontend). It is only
    invoked if no earlier layer reaches a definite decision.
    """
    hook = user_hooks(tool_name, tool_input)
    if hook is not None:
        return hook

    rule = static_rules(tool_name, tool_input)
    if rule != "ask":
        return rule

    classifier = semantic_classifier(tool_name, tool_input)
    if classifier != "ask":
        return classifier

    return ask(tool_name, tool_input)
