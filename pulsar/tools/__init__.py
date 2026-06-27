"""The tool catalogue and dispatcher (Part 2).

`TOOLS` is the list of definitions sent to the API. `execute_tool` is the
dispatcher the loop calls once a tool use has been permitted: it looks the tool
up by name, runs it, and turns any ToolError into a structured error result.
"""

from . import list_directory, read_file, run_shell, write_file
from .base import Tool, ToolError

# Every tool the agent can call. Add a tool here and it is automatically part of
# the catalogue, the dispatcher, and (via permissions.py) the permission rules.
ALL_TOOLS: list[Tool] = [
    read_file.TOOL,
    list_directory.TOOL,
    write_file.TOOL,
    run_shell.TOOL,
]

# The definitions the model sees, sent in the API `tools` array.
TOOLS: list[dict] = [tool.definition() for tool in ALL_TOOLS]

_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def execute_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run a tool by name.

    Returns (result_text, is_error). A missing tool or a ToolError both come
    back as an error result the model can read, never as an exception that
    breaks the loop.
    """
    tool = _BY_NAME.get(name)
    if tool is None:
        return (f"unknown tool: {name}", True)
    try:
        return (tool.fn(tool_input), False)
    except ToolError as exc:
        return (str(exc), True)
    except Exception as exc:  # noqa: BLE001 - defensive: never crash the loop
        return (f"unexpected error in {name}: {exc}", True)
