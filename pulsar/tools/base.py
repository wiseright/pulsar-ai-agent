"""The shape of a tool (Part 2).

Mechanically a tool is three things the model sees — a name, a description, and
an input schema — plus one thing it does not see: the function that actually
runs. `definition()` returns exactly the dict we send to the API in the `tools`
array.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ..config import MAX_OUTPUT_BYTES


class ToolError(Exception):
    """Raised by a tool implementation when a call fails.

    The loop turns this into a structured, model-readable tool_result with
    `is_error=True`, rather than crashing — exactly the Part 3 contract.
    """


def truncate_output(text: str, *, limit: int = MAX_OUTPUT_BYTES, hint: str = "") -> str:
    """Cap a tool's output at `limit` bytes, with a clear marker (Part 2 §4).

    Tool output is prompt material: a 10MB file returned in full would be
    re-sent to the model on every later turn and blow the context window
    (Part 4 §6). We keep the first `limit` bytes and append an explicit notice
    telling the model what happened and how to narrow its next call — so the
    truncation is legible to the model, not silent data loss.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    kept = encoded[:limit].decode("utf-8", errors="ignore")
    omitted = len(encoded) - limit
    marker = f"\n\n[output truncated: {omitted} more bytes omitted of {len(encoded)} total."
    if hint:
        marker += f" {hint}"
    marker += "]"
    return kept + marker


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], str]   # takes the validated input, returns text output

    def definition(self) -> dict:
        """The JSON the model receives. Only name/description/schema — never fn."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
