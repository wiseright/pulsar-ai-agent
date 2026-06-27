# Adding a new tool

This guide walks through writing a new tool for Pulsar and wiring it into the
agent end-to-end. It expands the short summary in the
[main README](../README.md#adding-a-new-tool).

By the end you will have built a small `word_count` tool, registered it, given it
a permission rule, optionally exposed it to a sub-agent, and tested it offline.

- [Mental model](#mental-model)
- [How a tool flows through the system](#how-a-tool-flows-through-the-system)
- [Step 1 — Write the tool module](#step-1--write-the-tool-module)
- [Step 2 — Register it in the catalogue](#step-2--register-it-in-the-catalogue)
- [Step 3 — Give it a permission rule](#step-3--give-it-a-permission-rule)
- [Step 4 (optional) — Expose it to a sub-agent](#step-4-optional--expose-it-to-a-sub-agent)
- [Step 5 — Test it offline](#step-5--test-it-offline)
- [Conventions & gotchas](#conventions--gotchas)
- [Checklist](#checklist)

## Mental model

A tool is a `Tool` dataclass (`pulsar/tools/base.py`) — four fields:

```python
@dataclass(frozen=True)
class Tool:
    name: str            # what the model calls
    description: str     # what the model reads to decide when to call it
    input_schema: dict   # JSON Schema for the arguments the model must supply
    fn: Callable[[dict], str]   # the function that actually runs (model never sees it)
```

The first three are sent to the API in the `tools` array (`Tool.definition()`); the
model picks a tool and fills in arguments matching `input_schema`. `fn` runs locally
with those (already JSON-validated) arguments and returns **text**, which becomes the
`tool_result` fed back to the model on the next turn.

Two error channels matter:

- Raise **`ToolError`** for expected failures (bad path, missing file, non-zero exit).
  The dispatcher turns it into a structured `is_error=True` result the model can read
  and recover from — it never crashes the loop.
- Wrap returned text in **`truncate_output`** so a huge result can't blow the context
  window — tool output is re-sent to the model on every later turn.

## How a tool flows through the system

```
ALL_TOOLS (tools/__init__.py)
   ├─► TOOLS = [t.definition() …]      → sent to the API, so the model knows the tool exists
   └─► _BY_NAME = {t.name: t}          → execute_tool() dispatches by name

a turn:
  model emits tool_use ──► check_permission(name, input, ask)   (permissions.py)
                                 │  allow → run it
                                 │  deny  → return an error result, don't run
                                 │  ask   → prompt the human (run_shell, unknown tools…)
                                 ▼
                           execute_tool(name, input)            (tools/__init__.py)
                                 ▼
                           Tool.fn(input) → text ─► tool_result back to the model
```

Adding a tool means touching exactly three places — the module, the catalogue, the
permission rule. **`loop.py` never changes.**

## Step 1 — Write the tool module

Create `pulsar/tools/word_count.py`:

```python
"""A tiny example tool: count the words in a file (Part 2)."""

from .base import Tool, ToolError, truncate_output
from .read_file import _resolve_in_workdir   # reuse the work-dir confinement helper


def _word_count(tool_input: dict) -> str:
    rel = tool_input.get("path", "")
    path = _resolve_in_workdir(rel)           # raises ToolError if it escapes PULSAR_WORKDIR
    if not path.is_file():
        raise ToolError(f"not a file: {rel}")
    n = len(path.read_text(encoding="utf-8", errors="ignore").split())
    return truncate_output(f"{n} words in {rel}")


TOOL = Tool(
    name="word_count",
    description="Count the words in a UTF-8 text file inside the working directory.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, relative to the working dir."},
        },
        "required": ["path"],
    },
    fn=_word_count,
)
```

Notes:

- **Module shape** — every tool module exposes a module-level `TOOL`. The
  implementation function is private (`_word_count`).
- **Confinement** — `_resolve_in_workdir` (defined in `read_file.py`, reused by the
  other file tools) resolves the path under `PULSAR_WORKDIR` and raises `ToolError`
  if it escapes (e.g. `../../etc/passwd`). Any tool that touches the filesystem
  should go through it.
- **Description quality** — the `description` and per-field `description`s are the
  model's only clue about *when* and *how* to call the tool. Treat them as part of
  the prompt: be specific about inputs, units, and limits.
- **Return text, not objects** — `fn` returns a string. Format numbers/structured
  data into readable text the model can parse.

## Step 2 — Register it in the catalogue

Add it to `ALL_TOOLS` in `pulsar/tools/__init__.py`:

```python
from . import list_directory, read_file, run_shell, word_count, write_file

ALL_TOOLS: list[Tool] = [
    read_file.TOOL,
    list_directory.TOOL,
    write_file.TOOL,
    run_shell.TOOL,
    word_count.TOOL,          # ← new
]
```

That single line is enough: `TOOLS` (the API definitions) and `_BY_NAME` (the
dispatcher table) are both derived from `ALL_TOOLS`, so the tool is now both
advertised to the model and runnable by `execute_tool`.

## Step 3 — Give it a permission rule

Tools are gated by a **permission chain** in `pulsar/permissions.py`. The decision
type is `Decision = Literal["allow", "deny", "ask"]`, and `check_permission` walks
the chain in order:

1. **`user_hooks`** — explicit allow/deny lists the user configured (highest priority).
2. **`static_rules`** — the per-tool rules in code (where you add yours).
3. **`semantic_classifier`** — a stub for "let a small model judge ambiguous cases";
   it currently always escalates to `ask`.
4. fallback — anything still unresolved prompts the human (`ask`) on every call.

There is **no allow-by-default**: if you don't add a rule, your tool still works but
asks for confirmation on every single call. Add an explicit branch to `static_rules`:

```python
def static_rules(tool_name: str, tool_input: dict) -> Decision:
    ...
    if tool_name == "word_count":
        return "allow"          # side-effect-free read, confined to the work dir
    ...
```

Choosing the right decision:

| Decision | When | Example in the repo |
|----------|------|---------------------|
| `"allow"` | Side-effect-free / safely scoped operations | `read_file`, `list_directory` |
| `"deny"`  | Statically detectable danger (e.g. path escapes the work dir) | `write_file` with `../` paths |
| `"ask"`   | Anything that mutates the world or runs arbitrary code | `run_shell` always asks |

You can also make the decision depend on `tool_input` (like `write_file`, which
returns `"deny"` for paths that escape the working directory and `"ask"` otherwise).

## Step 4 (optional) — Expose it to a sub-agent

By default sub-agents (`reviewer`, `searcher`) get a **narrow, read-only** tool
catalogue defined in `pulsar/subagents.py`. A new tool is **not** automatically
available to them. To let a worker use it, add its name to that type's catalogue in
the `SUBAGENT_TYPES` registry. Keep workers read-only and never give them
`spawn_agent` (recursion is capped at one level by design).

## Step 5 — Test it offline

Pulsar's test suite runs without a network connection or API key. Tool tests just
call `execute_tool` directly and assert on `(result, is_error)`; permission tests
call `static_rules`. Following the existing patterns in `tests/test_smoke.py`:

```python
from pulsar.tools import execute_tool
from pulsar.permissions import static_rules


def test_word_count_counts_words(workdir):           # `workdir` fixture chdirs into a temp dir
    (workdir / "poem.txt").write_text("one two three")
    result, is_error = execute_tool("word_count", {"path": "poem.txt"})
    assert not is_error
    assert "3 words" in result


def test_word_count_missing_file_is_error(workdir):
    result, is_error = execute_tool("word_count", {"path": "nope.txt"})
    assert is_error


def test_word_count_is_allowed():
    assert static_rules("word_count", {"path": "x"}) == "allow"
```

Run the suite:

```bash
python -m pytest
```

Then try it live (the model decides when to call it):

```bash
python -m pulsar run_agent "how many words are in README.md?"
```

## Conventions & gotchas

- **Always confine filesystem access** to `PULSAR_WORKDIR` via `_resolve_in_workdir`.
  A tool that reads/writes outside it is a sandbox escape.
- **Never let `fn` raise a bare exception** for expected failures — raise `ToolError`
  so the model gets a readable result. (Unexpected exceptions are still caught by the
  dispatcher as a last resort, but the message is less useful.)
- **Always wrap large output** in `truncate_output`. Unbounded output silently
  degrades every subsequent turn.
- **Don't forget the permission rule.** A tool with no `static_rules` branch works
  but nags the user on every call.
- **Keep names stable.** The tool `name` appears in `static_rules`, in sub-agent
  catalogues, and in the model's learned behaviour — renaming means updating all of
  them.
- **`run_shell` is the escape hatch, on purpose.** If you're tempted to add a tool
  that runs arbitrary commands, that already exists and is gated behind `ask`.

## Checklist

- [ ] New module `pulsar/tools/<name>.py` exposing a module-level `TOOL`
- [ ] Filesystem access (if any) goes through `_resolve_in_workdir`
- [ ] Expected failures raise `ToolError`; output wrapped in `truncate_output`
- [ ] Clear `description` + per-field schema descriptions
- [ ] Added to `ALL_TOOLS` in `pulsar/tools/__init__.py`
- [ ] Permission branch added to `static_rules` in `pulsar/permissions.py`
- [ ] (Optional) Added to the relevant sub-agent catalogue in `pulsar/subagents.py`
- [ ] Tests added; `python -m pytest` green
