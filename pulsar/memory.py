"""File-based memory across sessions (Part 5).

Memory is plain markdown on disk: a PULSAR.md per scope. It is read into the
system prompt (rebuilt every turn, so edits are picked up), and written only
through `propose_memory_update`, which the permission chain gates on the user.

Two scopes, mirroring the Claude Code convention but with our own file name:
    * project -> <repo root>/PULSAR.md
    * user    -> ~/.claude/PULSAR.md
"""

from pathlib import Path

from .config import PROJECT_MEMORY, USER_MEMORY

MEMORY_TOOL_NAME = "propose_memory_update"

# scope -> file. More specific (project) is loaded last so the model attends to
# it more, and overrides the more general (user) scope.
MEMORY_PATHS: dict[str, Path] = {
    "user": USER_MEMORY,
    "project": PROJECT_MEMORY,
}

BASE_SYSTEM_PROMPT = (
    "You are a helpful coding agent operating in a terminal. You can read and "
    "write files, list directories, and run shell commands, all within the "
    "working directory and subject to the user's permission. Work step by step: "
    "inspect before you change, make the smallest correct edit, and explain what "
    "you did.\n\n"
    "You have a long-term memory made of plain markdown files. When you learn a "
    "durable fact about this project or this user — a convention, a preference, a "
    "known issue — propose adding it with the propose_memory_update tool. Write it "
    "in the user's voice (\"I prefer X\"), as the smallest correct change. The "
    "user approves every memory write."
)


def load_memory() -> str:
    """Read the memory layers (least to most specific) and concatenate them."""
    sections = []
    for label, path in [("User notes", USER_MEMORY), ("Project notes", PROJECT_MEMORY)]:
        if path.exists():
            sections.append(f"## {label} (from {path})\n{path.read_text(encoding='utf-8')}")
    if not sections:
        return ""
    return "# Memory\n\n" + "\n\n".join(sections)


def build_system_prompt() -> str:
    """Assemble the system prompt: base instructions plus memory.

    Called on every turn by the loop, so a mid-session edit to a PULSAR.md is
    reflected on the next turn for free. Memory lives here, in the system prompt,
    never in the mutable message history — so the compactor never evicts it.
    """
    memory = load_memory()
    if not memory:
        return BASE_SYSTEM_PROMPT
    return BASE_SYSTEM_PROMPT + "\n\n" + memory


def _stat_memory() -> float:
    """Latest mtime across the memory files; 0.0 if none exist."""
    return max(
        (p.stat().st_mtime for p in MEMORY_PATHS.values() if p.exists()),
        default=0.0,
    )


MEMORY_TOOL: dict = {
    "name": MEMORY_TOOL_NAME,
    "description": (
        "Propose an addition to long-term memory. Use this when you have learned "
        "something that would help future sessions on this project — a "
        "convention, a preference, a known issue. The user approves the change "
        "before it is written. Write in the user's voice (\"I prefer X\"), and "
        "make the smallest correct change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["project", "user"]},
            "section": {
                "type": "string",
                "description": "The markdown heading to add the note under.",
            },
            "content": {
                "type": "string",
                "description": "The note itself, written in the user's voice.",
            },
        },
        "required": ["scope", "section", "content"],
    },
}


def propose_memory_update_impl(scope: str, section: str, content: str) -> dict:
    """Apply an approved memory update.

    By the time this runs, the permission chain has already approved the call.
    The write is a smallest-correct-change: if the section already exists, the
    note is inserted under it instead of emitting a duplicate heading (a naive
    append would slowly fill the file with repeated "## Conventions" sections).
    """
    if scope not in MEMORY_PATHS:
        return {"status": "error", "message": f"unknown scope: {scope}"}

    path = MEMORY_PATHS[scope]
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    heading = f"## {section}"
    note = content.rstrip() + "\n"

    if heading in existing.splitlines():
        # Insert the note right under the existing heading.
        out, inserted = [], False
        for line in existing.splitlines(keepends=True):
            out.append(line)
            if not inserted and line.strip() == heading:
                out.append(note)
                inserted = True
        updated = "".join(out)
    else:
        # Create the section at the end.
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        updated = f"{existing}{sep}\n{heading}\n{note}"

    path.write_text(updated, encoding="utf-8")
    return {
        "status": "ok",
        "scope": scope,
        "file": str(path),
        "bytes_added": len(note.encode("utf-8")),
    }
