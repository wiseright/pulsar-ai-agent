"""run_shell — execute a shell command (Part 2).

This tool really runs commands on the user's machine. It is the reason the
permission chain (Part 3) exists: a static rule forces every run_shell call to
be authorised by the user (it is never auto-allowed), and a deny-list blocks a
handful of patterns outright. See `permissions.py`.
"""

import subprocess

from ..config import WORKING_DIR
from .base import Tool, ToolError, truncate_output

_TIMEOUT_SECONDS = 60


def _run_shell(inp: dict) -> str:
    command = inp["command"]
    try:
        completed = subprocess.run(
            command,
            shell=True,                 # run through the platform shell
            cwd=str(WORKING_DIR),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {_TIMEOUT_SECONDS}s") from exc

    out = completed.stdout or ""
    err = completed.stderr or ""
    body = out
    if err:
        body += ("\n" if body else "") + f"[stderr]\n{err}"
    body += f"\n[exit code: {completed.returncode}]"
    # Truncate at the tool layer (Part 2 §4 / Part 4 §6): a command that prints
    # hundreds of KB of logs must not blow the context window on every later turn.
    body = truncate_output(body, hint="Re-run piping through grep/head/tail to narrow the output.")
    if completed.returncode != 0:
        # Non-zero exit is reported as a (non-fatal) error result so the model
        # can read it and decide what to do, rather than the loop crashing.
        raise ToolError(body)
    return body


TOOL = Tool(
    name="run_shell",
    description=(
        "Execute a shell command in the working directory and return its "
        "combined stdout/stderr and exit code. Use this for builds, tests, git, "
        "and other command-line tasks. The user must authorise every call. "
        "Prefer the dedicated read_file/list_directory/write_file tools for file "
        "operations when they fit."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            }
        },
        "required": ["command"],
    },
    fn=_run_shell,
)
