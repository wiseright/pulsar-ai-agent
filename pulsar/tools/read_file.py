"""read_file — read a text file from the working directory (Part 2)."""

from pathlib import Path

from ..config import WORKING_DIR
from .base import Tool, ToolError, truncate_output


def _resolve_in_workdir(rel_path: str) -> Path:
    """Resolve `rel_path` against WORKING_DIR and refuse anything that escapes it.

    This is the first line of defence; the permission layer (Part 3) is the
    second. Defence in depth: the tool itself never trusts the model's path.
    """
    path = (WORKING_DIR / rel_path).resolve()
    if path != WORKING_DIR and WORKING_DIR not in path.parents:
        raise ToolError(f"path escapes the working directory: {rel_path}")
    return path


def _read_file(inp: dict) -> str:
    path = _resolve_in_workdir(inp["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {inp['path']}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"not a UTF-8 text file: {inp['path']}") from exc
    # Truncate at the tool layer (Part 2 §4 / Part 4 §6) so a huge file cannot
    # blow the context window on every later turn.
    return truncate_output(
        content,
        hint="Use run_shell with grep/head/tail to read specific sections.",
    )


TOOL = Tool(
    name="read_file",
    description=(
        "Read the contents of a text file from the working directory. Use this "
        "when you need to inspect a file's actual contents — for example before "
        "editing, summarising, or answering a question about a specific file. "
        "Returns the file content as a string, truncated past 200KB with a clear "
        "marker (use grep/head/tail via run_shell for larger files). Fails if the "
        "file does not exist or is not a UTF-8 text file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the file, relative to the working directory. "
                    "Must not contain '..' or absolute path components."
                ),
            }
        },
        "required": ["path"],
    },
    fn=_read_file,
)
