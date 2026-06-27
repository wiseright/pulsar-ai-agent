"""The agentic loop (Part 1), with every later layer wired in.

`run_agent` is a generator: it *yields* typed events as it works, instead of
printing. A frontend consumes the stream. The loop itself is the same one from
Part 1 — call the model, run the tools it asks for, feed the results back — now
carrying memory in the system prompt (Part 5), compacting when the conversation
grows (Part 4), and gating every tool call through the permission chain (Part 3)
over the tool catalogue (Part 2).
"""

from collections.abc import Iterator

import anthropic

from . import events as ev
from .config import DEFAULT_MODEL, MAX_TOKENS
from .context import compact_if_needed
from .memory import (
    MEMORY_TOOL,
    MEMORY_TOOL_NAME,
    build_system_prompt,
    propose_memory_update_impl,
    _stat_memory,
)
from .permissions import AskFn, check_permission
from .subagents import SPAWN_TOOL_NAME
from .tools import TOOLS, execute_tool


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    """Build a tool_result block to send back to the model."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def run_agent(
    prompt: str,
    *,
    model: str | None = None,
    ask: AskFn | None = None,
    client: "anthropic.Anthropic | None" = None,
    tools: list[dict] | None = None,
    system_extra: str | None = None,
) -> Iterator[ev.Event]:
    """Run the agent on `prompt`, yielding events until it produces a final answer.

    `ask` is the human-escalation callback used by the permission chain; if not
    given, the console prompter is used (so `invoke("run_agent", ...)` works out
    of the box from a terminal).

    Part 6 added two optional knobs so the *same* loop can run a sub-agent:
    `tools` overrides the tool catalogue sent to the API (a sub-agent gets a
    narrower one), and `system_extra` appends a type-specific section to the
    system prompt. Both default to the full agent's behaviour, so existing
    callers are unchanged — a sub-agent IS `run_agent` applied recursively.
    """
    model = model or DEFAULT_MODEL
    client = client or anthropic.Anthropic()
    if ask is None:
        from .frontends.console import console_ask
        ask = console_ask

    # Part 2 tools + the Part 5 memory tool + the Part 6 spawn tool, in one
    # catalogue — unless a caller (a sub-agent) passes a restricted `tools` list.
    from .subagents import SPAWN_TOOL  # local import: subagents.py imports us back
    all_tools = tools if tools is not None else TOOLS + [MEMORY_TOOL, SPAWN_TOOL]

    system_prompt = build_system_prompt()          # Part 5
    if system_extra:                                # Part 6: per-type prompt addition
        system_prompt = f"{system_prompt}\n\n{system_extra}"
    memory_mtime = _stat_memory()
    messages: list[dict] = [{"role": "user", "content": prompt}]

    yield ev.AgentStarted(agent="run_agent", prompt=prompt)

    while True:
        # Part 5: reload memory into the system prompt if the file changed.
        current_mtime = _stat_memory()
        if current_mtime != memory_mtime:
            system_prompt = build_system_prompt()
            if system_extra:                        # Part 6: keep the type-specific section
                system_prompt = f"{system_prompt}\n\n{system_extra}"
            memory_mtime = current_mtime
            yield ev.MemoryReloaded(path="memory changed on disk")

        # Part 4: compact the conversation if it has grown past the budget.
        # Summarisation uses the cheaper SUMMARY_MODEL, not the main model.
        messages, n_compacted = compact_if_needed(client, messages)
        if n_compacted:
            yield ev.Compacted(turns=n_compacted)

        # Part 1: one streamed turn with the model.
        text_parts: list[str] = []
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=all_tools,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    text_parts.append(text)
                    yield ev.AssistantText(text=text)
                final = stream.get_final_message()
        except anthropic.AnthropicError as exc:
            yield ev.AgentError(message=str(exc))
            return

        # Record the assistant turn (the SDK content blocks round-trip as input).
        messages.append({"role": "assistant", "content": final.content})

        if final.stop_reason != "tool_use":
            yield ev.AgentFinished(text="".join(text_parts))
            return

        # The model asked for tools. Permit, execute, and collect the results.
        tool_results: list[dict] = []
        for block in final.content:
            if block.type != "tool_use":
                continue

            tool_input = dict(block.input)
            yield ev.ToolRequested(tool_use_id=block.id, name=block.name, input=tool_input)

            decision = check_permission(block.name, tool_input, ask)   # Part 3
            yield ev.PermissionDecided(name=block.name, decision=decision)

            if decision == "deny":
                # Structured, model-readable refusal — the loop does not crash.
                tool_results.append(
                    _tool_result(block.id, "This action was blocked by the permission system.", True)
                )
                yield ev.ToolExecuted(name=block.name, result="(denied)", is_error=True)
                continue

            # Allowed. Memory writes go to their handler; everything else to the
            # tool dispatcher (Part 2).
            if block.name == MEMORY_TOOL_NAME:                          # Part 5
                result = str(propose_memory_update_impl(**tool_input))
                is_error = False
            elif block.name == SPAWN_TOOL_NAME:                         # Part 6
                # Run a sub-agent to completion INTERNALLY and return only its
                # distilled final answer. Its event stream never reaches the
                # parent — this is the "automatic context compression" point.
                from .subagents import spawn_agent_impl
                result, is_error = spawn_agent_impl(tool_input, model=model, ask=ask, client=client)
            else:
                result, is_error = execute_tool(block.name, tool_input)

            tool_results.append(_tool_result(block.id, result, is_error))
            yield ev.ToolExecuted(name=block.name, result=result, is_error=is_error)

        # Feed the tool results back as the next user turn, and loop.
        messages.append({"role": "user", "content": tool_results})
