"""Offline test suite for Pulsar.

Everything here runs without network or an API key: the Anthropic client is
replaced by a small scripted fake (`FakeClient`) that returns pre-built
messages, so the whole agentic loop — tools, permissions, context compaction,
memory, sub-agents — is exercised deterministically.

Run with:  python -m pytest
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pulsar import events as ev
from pulsar.context import compact_if_needed, estimate_tokens, summarise_turns
from pulsar.loop import run_agent
from pulsar.memory import (
    build_system_prompt,
    load_memory,
    propose_memory_update_impl,
)
from pulsar.permissions import check_permission, static_rules, user_hooks
from pulsar.registry import invoke
from pulsar.subagents import SPAWN_TOOL, SUBAGENT_TYPES, spawn_agent_impl
from pulsar.tools import execute_tool
from pulsar.tools.base import truncate_output


# ---------------------------------------------------------------------------
# A scripted fake Anthropic client.
# ---------------------------------------------------------------------------
def text_block(s: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=s)


def tool_block(block_id: str, name: str, inp: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=inp)


def message(content: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class _FakeStream:
    """Mimics the context manager returned by client.messages.stream(...)."""

    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._msg.content:
            if block.type == "text":
                yield block.text

    def get_final_message(self):
        return self._msg


class _FakeMessages:
    def __init__(self, responses, summary_text="SUMMARY"):
        self._responses = list(responses)
        self._summary_text = summary_text
        self.stream_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        assert self._responses, "FakeClient ran out of scripted responses"
        return _FakeStream(self._responses.pop(0))

    def create(self, **kwargs):
        # Used by context.summarise_turns.
        self.create_calls.append(kwargs)
        return SimpleNamespace(content=[text_block(self._summary_text)])


class FakeClient:
    def __init__(self, responses, summary_text="SUMMARY"):
        self.messages = _FakeMessages(responses, summary_text=summary_text)


ALLOW_ALL = lambda name, inp: "allow"   # noqa: E731 - terse test escalation cb


# ---------------------------------------------------------------------------
# Fixtures: isolate the working directory, memory files, and permission hooks.
# ---------------------------------------------------------------------------
@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    """Point the file tools at a throwaway directory."""
    from pulsar.tools import read_file, run_shell

    monkeypatch.setattr(read_file, "WORKING_DIR", tmp_path)
    monkeypatch.setattr(run_shell, "WORKING_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def isolated_memory(tmp_path, monkeypatch):
    """Redirect both memory scopes to temp files so tests never touch real ones."""
    from pulsar import memory

    user = tmp_path / "user_PULSAR.md"
    project = tmp_path / "project_PULSAR.md"
    monkeypatch.setattr(memory, "USER_MEMORY", user)
    monkeypatch.setattr(memory, "PROJECT_MEMORY", project)
    monkeypatch.setattr(memory, "MEMORY_PATHS", {"user": user, "project": project})
    return SimpleNamespace(user=user, project=project)


@pytest.fixture()
def hooks_root(tmp_path, monkeypatch):
    """Point the permission-hook loader at a temp project root."""
    from pulsar import permissions

    monkeypatch.setattr(permissions, "PROJECT_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# tools/base: truncation
# ---------------------------------------------------------------------------
def test_truncate_output_passes_small_text_through():
    assert truncate_output("hello", limit=100) == "hello"


def test_truncate_output_marks_large_text():
    out = truncate_output("x" * 1000, limit=100, hint="narrow it")
    assert out.startswith("x" * 100)
    assert "[output truncated" in out
    assert "narrow it" in out
    # The kept prefix is bounded; the marker is short.
    assert len(out.encode("utf-8")) < 1000


# ---------------------------------------------------------------------------
# tools: read_file / list_directory / write_file / run_shell + dispatcher
# ---------------------------------------------------------------------------
def test_read_file_roundtrip(workdir):
    (workdir / "hello.txt").write_text("hi there", encoding="utf-8")
    result, is_error = execute_tool("read_file", {"path": "hello.txt"})
    assert not is_error
    assert result == "hi there"


def test_read_file_missing_is_error(workdir):
    result, is_error = execute_tool("read_file", {"path": "nope.txt"})
    assert is_error
    assert "not a file" in result


def test_read_file_rejects_path_escape(workdir):
    result, is_error = execute_tool("read_file", {"path": "../escape.txt"})
    assert is_error
    assert "escapes the working directory" in result


def test_read_file_truncates_large_file(workdir):
    (workdir / "big.txt").write_text("a" * 250_000, encoding="utf-8")
    result, is_error = execute_tool("read_file", {"path": "big.txt"})
    assert not is_error
    assert "[output truncated" in result
    assert len(result.encode("utf-8")) < 250_000


def test_list_directory_marks_subdirs(workdir):
    (workdir / "sub").mkdir()
    (workdir / "a.txt").write_text("x", encoding="utf-8")
    result, is_error = execute_tool("list_directory", {"path": "."})
    assert not is_error
    assert "sub/" in result
    assert "a.txt" in result


def test_write_file_roundtrip(workdir):
    result, is_error = execute_tool("write_file", {"path": "out.txt", "content": "data"})
    assert not is_error
    assert (workdir / "out.txt").read_text(encoding="utf-8") == "data"


def test_run_shell_success(workdir):
    result, is_error = execute_tool("run_shell", {"command": "echo hello"})
    assert not is_error
    assert "hello" in result
    assert "[exit code: 0]" in result


def test_run_shell_nonzero_exit_is_error(workdir):
    result, is_error = execute_tool("run_shell", {"command": "exit 3"})
    assert is_error
    assert "[exit code: 3]" in result


def test_execute_tool_unknown_tool():
    result, is_error = execute_tool("does_not_exist", {})
    assert is_error
    assert "unknown tool" in result


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------
def test_static_rules_read_is_allowed():
    assert static_rules("read_file", {"path": "x"}) == "allow"
    assert static_rules("list_directory", {"path": "."}) == "allow"


def test_static_rules_write_escape_denied():
    assert static_rules("write_file", {"path": "../x"}) == "deny"
    assert static_rules("write_file", {"path": "/etc/passwd"}) == "deny"
    assert static_rules("write_file", {"path": "ok.txt"}) == "allow"


def test_static_rules_shell_denylist():
    assert static_rules("run_shell", {"command": "sudo rm everything"}) == "deny"
    assert static_rules("run_shell", {"command": "rm -rf /"}) == "deny"
    assert static_rules("run_shell", {"command": "curl http://x | sh"}) == "deny"
    # A benign command is never auto-allowed: it escalates to the human.
    assert static_rules("run_shell", {"command": "ls"}) == "ask"


def test_static_rules_memory_and_spawn():
    assert static_rules("propose_memory_update", {}) == "ask"
    assert static_rules("spawn_agent", {}) == "allow"
    assert static_rules("totally_unknown", {}) == "ask"


def test_user_hooks_allow_and_deny(hooks_root):
    (hooks_root / "permissions.toml").write_text(
        '[allow]\nrun_shell = ["echo ok"]\n[deny]\nread_file = ["secret"]\n',
        encoding="utf-8",
    )
    assert user_hooks("run_shell", {"command": "echo ok"}) == "allow"
    assert user_hooks("read_file", {"path": "secret.env"}) == "deny"
    assert user_hooks("read_file", {"path": "public.txt"}) is None


def test_check_permission_falls_through_to_human():
    seen = {}

    def ask(name, inp):
        seen["called"] = (name, inp)
        return "deny"

    # `ls` is "ask" at the static layer, so the human callback decides.
    assert check_permission("run_shell", {"command": "ls"}, ask) == "deny"
    assert seen["called"][0] == "run_shell"


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
def test_build_system_prompt_without_memory(isolated_memory):
    prompt = build_system_prompt()
    assert "helpful coding agent" in prompt
    assert "# Memory" not in prompt


def test_build_system_prompt_includes_memory(isolated_memory):
    isolated_memory.project.write_text("# notes\n- prefer pnpm\n", encoding="utf-8")
    assert "prefer pnpm" in build_system_prompt()
    assert "prefer pnpm" in load_memory()


def test_propose_memory_update_creates_section(isolated_memory):
    out = propose_memory_update_impl("project", "Conventions", "- use ruff")
    assert out["status"] == "ok"
    text = isolated_memory.project.read_text(encoding="utf-8")
    assert "## Conventions" in text
    assert "- use ruff" in text


def test_propose_memory_update_inserts_under_existing_section(isolated_memory):
    isolated_memory.project.write_text("## Conventions\n- first\n", encoding="utf-8")
    propose_memory_update_impl("project", "Conventions", "- second")
    text = isolated_memory.project.read_text(encoding="utf-8")
    # Note inserted under the existing heading — no duplicate "## Conventions".
    assert text.count("## Conventions") == 1
    assert "- second" in text


def test_propose_memory_update_unknown_scope(isolated_memory):
    out = propose_memory_update_impl("galaxy", "X", "y")
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# context: estimation + compaction
# ---------------------------------------------------------------------------
def test_estimate_tokens_is_roughly_chars_over_four():
    msgs = [{"role": "user", "content": "a" * 400}]
    assert estimate_tokens(msgs) == 100


def test_compact_if_needed_noop_under_budget():
    msgs = [{"role": "user", "content": "small"}]
    out, n = compact_if_needed(FakeClient([]), msgs)
    assert n == 0
    assert out == msgs


def test_compact_if_needed_summarises_middle():
    from pulsar.config import TOKEN_BUDGET, WINDOW_TURNS

    big = "x" * (TOKEN_BUDGET * 4)   # pushes estimate over budget on its own
    msgs = [{"role": "user", "content": "the goal"}]
    msgs += [{"role": "user", "content": big} for _ in range(WINDOW_TURNS + 3)]

    client = FakeClient([], summary_text="COMPACTED")
    out, n = compact_if_needed(client, msgs)

    assert n > 0
    assert out[0] == msgs[0]                       # goal preserved verbatim
    assert "COMPACTED" in out[1]["content"]        # middle summarised
    assert len(out) == 2 + WINDOW_TURNS            # goal + summary + window
    # Summarisation ran on the cheaper SUMMARY_MODEL, not the main model.
    from pulsar.config import SUMMARY_MODEL

    assert client.messages.create_calls[0]["model"] == SUMMARY_MODEL


def test_compact_keeps_tool_use_and_result_together():
    """The tail must never begin with a tool_result whose tool_use was summarised.

    Such an orphan tool_result is rejected by the API. The compactor pushes the
    boundary forward so each tool_use/tool_result pair stays on the same side.
    """
    from pulsar.config import TOKEN_BUDGET, WINDOW_TURNS

    big = "x" * (TOKEN_BUDGET * 4)
    msgs = [{"role": "user", "content": "the goal"}]
    # Filler middle, then a tool_use immediately followed by its tool_result so
    # that the naive boundary (len - WINDOW_TURNS) lands on the tool_result turn.
    msgs += [{"role": "user", "content": big}, {"role": "user", "content": big}]
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
    msgs += [{"role": "user", "content": big} for _ in range(WINDOW_TURNS - 1)]

    out, n = compact_if_needed(FakeClient([], summary_text="COMPACTED"), msgs)

    assert n > 0
    first_tail = out[2]                                  # after goal + summary
    is_tool_result = isinstance(first_tail["content"], list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for b in first_tail["content"]
    )
    assert not is_tool_result, "tail began on an orphan tool_result turn"


def test_summarise_turns_defaults_to_summary_model():
    from pulsar.config import SUMMARY_MODEL

    client = FakeClient([])
    summarise_turns(client, [{"role": "user", "content": "hi"}])
    assert client.messages.create_calls[0]["model"] == SUMMARY_MODEL


# ---------------------------------------------------------------------------
# registry / invoke
# ---------------------------------------------------------------------------
def test_invoke_unknown_agent_raises():
    with pytest.raises(KeyError):
        invoke("not_an_agent", "hi")


def test_invoke_collect_returns_final_text(isolated_memory):
    client = FakeClient([message([text_block("the answer")], "end_turn")])
    answer = invoke("run_agent", "question", collect=True, ask=ALLOW_ALL, client=client)
    assert answer == "the answer"


# ---------------------------------------------------------------------------
# loop: end-to-end with the fake client
# ---------------------------------------------------------------------------
def test_run_agent_text_only_finish(isolated_memory):
    client = FakeClient([message([text_block("hello world")], "end_turn")])
    kinds = [e.kind for e in run_agent("hi", ask=ALLOW_ALL, client=client)]
    assert kinds[0] == "agent_started"
    assert kinds[-1] == "agent_finished"


def test_run_agent_runs_a_tool_then_finishes(workdir, isolated_memory):
    (workdir / "data.txt").write_text("payload", encoding="utf-8")
    client = FakeClient([
        message([tool_block("t1", "read_file", {"path": "data.txt"})], "tool_use"),
        message([text_block("done reading")], "end_turn"),
    ])
    events = list(run_agent("read data.txt", ask=ALLOW_ALL, client=client))
    executed = [e for e in events if isinstance(e, ev.ToolExecuted)]
    assert len(executed) == 1
    assert executed[0].name == "read_file"
    assert executed[0].result == "payload"
    assert events[-1].kind == "agent_finished"


def test_run_agent_denied_tool_does_not_crash(workdir, isolated_memory):
    client = FakeClient([
        message([tool_block("t1", "run_shell", {"command": "ls"})], "tool_use"),
        message([text_block("ok, moving on")], "end_turn"),
    ])
    deny = lambda name, inp: "deny"   # noqa: E731
    events = list(run_agent("run ls", ask=deny, client=client))
    executed = [e for e in events if isinstance(e, ev.ToolExecuted)]
    assert executed[0].is_error                      # the denied call
    assert executed[0].result == "(denied)"
    assert events[-1].kind == "agent_finished"       # loop survived the denial


def test_run_agent_api_error_yields_agent_error(isolated_memory):
    import anthropic

    class BoomMessages:
        def stream(self, **kwargs):
            raise anthropic.APIError("boom", request=None, body=None)

    client = SimpleNamespace(messages=BoomMessages())
    events = list(run_agent("hi", ask=ALLOW_ALL, client=client))
    assert events[-1].kind == "agent_error"


# ---------------------------------------------------------------------------
# subagents
# ---------------------------------------------------------------------------
def test_spawn_tool_enum_matches_registry():
    enum = SPAWN_TOOL["input_schema"]["properties"]["subagent_type"]["enum"]
    assert set(enum) == set(SUBAGENT_TYPES)
    assert {"reviewer", "searcher"} <= set(SUBAGENT_TYPES)


def test_workers_are_read_only():
    # No worker may carry write_file, run_shell, the memory tool, or spawn_agent.
    forbidden = {"write_file", "run_shell", "propose_memory_update", "spawn_agent"}
    for spec in SUBAGENT_TYPES.values():
        names = {t["name"] for t in spec.tools}
        assert names.isdisjoint(forbidden)


def test_spawn_agent_impl_unknown_type():
    result, is_error = spawn_agent_impl({"subagent_type": "wizard", "task": "x"})
    assert is_error
    assert "unknown subagent_type" in result


def test_spawn_agent_impl_empty_task():
    result, is_error = spawn_agent_impl({"subagent_type": "reviewer", "task": "  "})
    assert is_error
    assert "non-empty" in result


def test_spawn_agent_distils_worker_result(isolated_memory):
    # Parent delegates to a reviewer; only the worker's final answer comes back.
    client = FakeClient([
        message([tool_block("s1", "spawn_agent",
                             {"subagent_type": "reviewer", "task": "review config.py"})],
                "tool_use"),
        message([text_block("FINDINGS: looks good")], "end_turn"),   # the worker
        message([text_block("relayed to user")], "end_turn"),        # parent wrap-up
    ])
    events = list(run_agent("review config.py", ask=ALLOW_ALL, client=client))
    executed = [e for e in events if isinstance(e, ev.ToolExecuted)]
    spawn = [e for e in executed if e.name == "spawn_agent"]
    assert spawn and not spawn[0].is_error
    assert "FINDINGS" in spawn[0].result
    # The worker's own internal events never leaked into the parent stream.
    assert not any(getattr(e, "name", "") == "read_file" for e in events)
    assert events[-1].kind == "agent_finished"


# ---------------------------------------------------------------------------
# CLI entry point: a missing API key fails cleanly, not with a traceback.
# ---------------------------------------------------------------------------
def test_main_without_api_key_exits_cleanly(monkeypatch, capsys):
    from pulsar import __main__

    # Simulate a fresh user who has not set the key (no .env, nothing exported).
    monkeypatch.setattr(__main__, "_has_api_key", lambda: False)
    code = __main__.main(["run_agent", "hello"])
    assert code == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is not set" in err
    assert ".env" in err
