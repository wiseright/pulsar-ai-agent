"""Context management: compaction (Part 4).

When the conversation grows past a token budget, summarise the middle and keep
the original goal plus the most recent turns verbatim. This keeps the agent
competent on long sessions without a bigger context window.
"""

import json

from .config import SUMMARY_MODEL, TOKEN_BUDGET, WINDOW_TURNS


def _content_to_text(content) -> str:
    """Flatten a message's content (str or list of blocks) to text for sizing."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            parts.append(json.dumps(block, default=str))
        else:
            parts.append(str(block))
    return " ".join(parts)


def estimate_tokens(messages: list) -> int:
    """Cheap heuristic: ~4 characters per token. Good enough to trigger compaction."""
    chars = sum(len(_content_to_text(m.get("content", ""))) for m in messages)
    return chars // 4


def summarise_turns(client, turns_to_summarise: list, model: str = SUMMARY_MODEL) -> str:
    """Ask the model to compress a slice of the conversation into a short summary.

    The summary preserves decisions, files touched, and the current task; it
    drops verbose tool output and dead ends. Per Part 4 §7, this runs on a
    cheaper, faster model (`SUMMARY_MODEL`) than the main agent — the summary
    call should cost an order of magnitude less than the turn that triggers it.
    """
    transcript = "\n\n".join(
        f"[{m.get('role', '?')}]\n{_content_to_text(m.get('content', ''))}"
        for m in turns_to_summarise
    )
    prompt = (
        "Summarise the following slice of an agent conversation. Preserve "
        "decisions made, files modified, errors encountered, and the current "
        "task. Drop verbose tool output, failed attempts, and duplication. "
        "Write a tight summary, not a transcript.\n\n" + transcript
    )
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _is_tool_result_turn(message: dict) -> bool:
    """True if `message` is a user turn carrying tool_result blocks.

    Such a turn is only valid right after the assistant turn whose tool_use it
    answers — the API rejects a tool_result that has no matching tool_use.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def compact_if_needed(client, messages: list, summary_model: str = SUMMARY_MODEL) -> tuple[list, int]:
    """Compact the conversation if it exceeds the budget.

    Sliding-window-with-prefix: keep messages[0] (the goal), summarise the
    middle, keep the last WINDOW_TURNS verbatim. Returns (messages, n_compacted),
    where n_compacted is 0 when nothing was done. Summarisation runs on the
    cheaper `summary_model`, independent of the main agent's model.
    """
    if estimate_tokens(messages) < TOKEN_BUDGET:
        return messages, 0

    goal = messages[0]
    # Naive boundary: keep the last WINDOW_TURNS verbatim, summarise the rest.
    split = len(messages) - WINDOW_TURNS
    # ...but never start the tail on a tool_result turn whose matching tool_use
    # is being summarised away — that would leave an orphan the API rejects.
    # Push the boundary forward so each tool_use/tool_result pair stays together.
    while split < len(messages) and _is_tool_result_turn(messages[split]):
        split += 1

    middle = messages[1:split]
    tail = messages[split:]
    if not middle or not tail:
        return messages, 0   # nothing to compact yet (or the window swallowed it all)

    summary_text = summarise_turns(client, middle, model=summary_model)
    summary_message = {
        "role": "user",
        "content": f"[SUMMARY of earlier turns]\n{summary_text}",
    }
    return [goal, summary_message] + tail, len(middle)
