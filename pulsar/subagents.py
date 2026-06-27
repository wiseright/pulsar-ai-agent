"""Sub-agents (Part 6).

The core idea of Part 6: a *sub-agent is the loop applied recursively*. We do
not write a second, smaller agent — we call `run_agent` again with a narrower
tool catalogue and a type-specific system prompt, run it to completion, and hand
the parent only the distilled final answer. The sub-agent's whole event stream
(its reads, its thinking, its tool calls) lives and dies inside the call; the
parent's conversation grows by exactly one tool_result block. That is the
"automatic context compression" the article describes — no Part 4 summariser
needed, because the worker's context never enters the parent's window.

Design choices (faithful to the article AND to how Claude Code does it,
as discussed in Part 6):

* **One spawn tool parameterised by type**, not N `spawn_*` tools. Claude Code
  exposes a single `Agent`/`Task` tool with a `subagent_type` parameter
  (`AgentTool.tsx:82-102`); adding a worker is then *data* (a new entry in the
  registry below), not a new tool in the catalogue.
* **A small registry of sub-agent types.** Each `SubAgentType` is a spec: a
  name, a system-prompt addition, and a restricted tool catalogue. We ship two
  read-only workers — `reviewer` (code review -> structured findings) and
  `searcher` (focused search -> synthesised answer).
* **One level of recursion only**, like Claude Code (the Agent tool is excluded
  from a worker's pool, `constants/tools.ts`). The workers' catalogues here
  contain only read_file + list_directory — never `spawn_agent` — so a
  sub-agent cannot spawn further sub-agents.
"""

from dataclasses import dataclass, field

from .tools import ALL_TOOLS

# The single spawn tool's name, kept here so both the catalogue and the loop's
# dispatcher refer to one source of truth (mirrors MEMORY_TOOL_NAME in Part 5).
SPAWN_TOOL_NAME = "spawn_agent"

# Read-only worker tool pool: side-effect-free tools only. Build the definitions
# from the Part 2 catalogue by name, so a worker can never receive write_file,
# run_shell, the memory tool, or spawn_agent itself.
_READ_ONLY_NAMES = ("read_file", "list_directory")
_READ_ONLY_TOOLS: list[dict] = [
    tool.definition() for tool in ALL_TOOLS if tool.name in _READ_ONLY_NAMES
]


@dataclass(frozen=True)
class SubAgentType:
    """The spec for one kind of sub-agent.

    Attributes:
        name: The `subagent_type` value the parent selects.
        description: One-line summary the model reads to pick a type.
        system_extra: A section appended to the worker's system prompt, shaping
            how it works and what its final answer should look like.
        tools: The restricted tool catalogue the worker runs with. Never
            includes spawn_agent — that is what caps recursion at one level.
    """

    name: str
    description: str
    system_extra: str
    tools: list[dict] = field(default_factory=list)


# --- The registry of sub-agent types ----------------------------------------
# Two read-only workers. Adding a third worker is data: append an entry here.
SUBAGENT_TYPES: dict[str, SubAgentType] = {
    "reviewer": SubAgentType(
        name="reviewer",
        description="Read-only code reviewer: reads the given file(s) and returns structured findings.",
        system_extra=(
            "You are a focused code REVIEWER sub-agent. You are read-only: you may "
            "only read files and list directories. Review the code for correctness, "
            "clarity, and risk. Return a short, structured set of findings — group "
            "them as Strengths and Issues, each as a terse bullet with a file:line "
            "reference where possible. Do not propose to edit files; just report. "
            "Your final message is the ONLY thing the caller sees, so make it "
            "self-contained and distilled."
        ),
        tools=_READ_ONLY_TOOLS,
    ),
    "searcher": SubAgentType(
        name="searcher",
        description="Read-only searcher: explores the codebase and returns a synthesised answer to a focused question.",
        system_extra=(
            "You are a focused SEARCHER sub-agent. You are read-only: you may only "
            "read files and list directories. Locate where in the codebase the "
            "answer to the task lives, then synthesise a single concise answer that "
            "cites the relevant file paths. Do not dump whole files; distil. Your "
            "final message is the ONLY thing the caller sees, so make it "
            "self-contained."
        ),
        tools=_READ_ONLY_TOOLS,
    ),
}


def _type_enum() -> list[str]:
    """Return the known sub-agent type names, for the tool's input schema enum."""
    return list(SUBAGENT_TYPES)


# The single spawn tool definition the model sees. Parameterised by type — one
# tool, a registry of types — not one tool per worker.
SPAWN_TOOL: dict = {
    "name": SPAWN_TOOL_NAME,
    "description": (
        "Spawn a read-only sub-agent to handle a focused, self-contained task and "
        "return only its distilled final answer. Use this to delegate work that "
        "would otherwise flood your own context (a code review, a codebase search). "
        "The sub-agent runs in its own context and you see only its result. "
        "Available types: "
        + "; ".join(f"{t.name} — {t.description}" for t in SUBAGENT_TYPES.values())
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "enum": _type_enum(),
                "description": "Which kind of sub-agent to run.",
            },
            "task": {
                "type": "string",
                "description": "The self-contained task for the sub-agent (it does not see your conversation).",
            },
        },
        "required": ["subagent_type", "task"],
    },
}


def spawn_agent_impl(tool_input: dict, *, model=None, ask=None, client=None) -> tuple[str, bool]:
    """Run a sub-agent to completion and return only its distilled final answer.

    This is the Part 6 handler the loop dispatches `spawn_agent` to. It looks up
    the requested type in the registry, then runs `run_agent` recursively with
    that type's restricted tool catalogue and system-prompt addition, collecting
    ONLY the final answer. The sub-agent's internal event stream never leaves
    this function, so it cannot leak into the parent's stream.

    Args:
        tool_input: The tool call input, expecting `subagent_type` and `task`.
        model: Model id to run the sub-agent with (inherited from the parent).
        ask: The human-escalation callback; the worker's own read_file/list
            calls still pass through `check_permission`, so the sub-agent is
            "gated inside itself" (see the permission note in permissions.py).
        client: The Anthropic client to reuse for the sub-agent's turns.

    Returns:
        A `(result_text, is_error)` tuple, matching `execute_tool`'s contract so
        the loop can feed it straight back as a tool_result.
    """
    # Lazy import: subagents.py is imported by loop.py at module load, so we
    # must NOT import run_agent at module top — that would be a circular import.
    from .registry import invoke

    subagent_type = str(tool_input.get("subagent_type", ""))
    task = str(tool_input.get("task", ""))

    spec = SUBAGENT_TYPES.get(subagent_type)
    if spec is None:
        known = ", ".join(sorted(SUBAGENT_TYPES))
        return (f"unknown subagent_type: {subagent_type!r}. Known: {known}", True)
    if not task.strip():
        return ("spawn_agent requires a non-empty 'task'.", True)

    # A sub-agent IS run_agent applied recursively, with a narrower catalogue and
    # a type-specific prompt. `collect=True` drains the worker's event stream and
    # returns only the final answer — the parent never sees the internals.
    try:
        final = invoke(
            "run_agent",
            task,
            collect=True,
            model=model,
            ask=ask,
            client=client,
            tools=spec.tools,           # restricted: read-only, no spawn_agent
            system_extra=spec.system_extra,
        )
    except Exception as exc:  # noqa: BLE001 - never crash the parent loop
        return (f"sub-agent ({subagent_type}) failed: {exc}", True)

    return (final or "(the sub-agent produced no final answer)", False)
