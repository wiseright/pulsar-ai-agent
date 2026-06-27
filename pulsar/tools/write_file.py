"""write_file — create or overwrite a text file in the working directory (Part 2)."""

from .base import Tool, ToolError
from .read_file import _resolve_in_workdir


def _write_file(inp: dict) -> str:
    rel = inp["path"]
    path = _resolve_in_workdir(rel)   # refuses paths that escape WORKING_DIR
    content = inp["content"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"could not write {rel}: {exc}") from exc
    return f"wrote {len(content.encode('utf-8'))} bytes to {rel}"


TOOL = Tool(
    name="write_file",
    description=(
        "Create or overwrite a text file in the working directory with the "
        "given content. Overwrites any existing file at that path. Use this to "
        "save changes after you have decided what the file should contain. "
        "Returns the number of bytes written."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to write, relative to the working directory. "
                    "Must not contain '..' or absolute path components."
                ),
            },
            "content": {
                "type": "string",
                "description": "The full text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
    fn=_write_file,
)
