"""list_directory — list entries of a directory in the working directory (Part 2)."""

from .base import Tool, ToolError
from .read_file import _resolve_in_workdir


def _list_directory(inp: dict) -> str:
    rel = inp.get("path", ".")
    path = _resolve_in_workdir(rel)
    if not path.is_dir():
        raise ToolError(f"not a directory: {rel}")
    entries = []
    for child in sorted(path.iterdir()):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
    if not entries:
        return "(empty directory)"
    return "\n".join(entries)


TOOL = Tool(
    name="list_directory",
    description=(
        "List the files and subdirectories of a directory in the working "
        "directory. Use this to discover what exists before reading or editing. "
        "Directories are suffixed with '/'. Returns one entry per line."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Directory path relative to the working directory. "
                    "Defaults to '.' (the working directory itself)."
                ),
            }
        },
        "required": [],
    },
    fn=_list_directory,
)
