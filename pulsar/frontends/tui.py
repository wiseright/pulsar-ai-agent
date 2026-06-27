"""Textual TUI frontend.

A second consumer of the agent's event stream — proof that the core is truly
UI-agnostic. Nothing in `loop.py` / `events.py` changed to add this; the TUI
just renders the same events the console frontend prints, and supplies its own
human-escalation callback (a modal dialog instead of an input() prompt).

Design note — sync/async bridge:
    The agent loop is a *synchronous* generator and the permission `ask` callback
    blocks. Textual runs an *async* event loop. So we run the loop in a worker
    THREAD and marshal everything back to the UI thread with `call_from_thread`:
      * each event  -> call_from_thread(self._handle_event, event)   (mount/update widgets)
      * a permission -> call_from_thread(self._ask_modal, ...)        (await a modal, return choice)
"""

from __future__ import annotations

import functools

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from .. import events as ev
from ..config import DEFAULT_MODEL, WORKING_DIR
from ..permissions import Decision
from ..registry import invoke

# ASCII pulsar emblem (a neutron star with sweeping radiation beams) above the
# wordmark (figlet font: ansi_shadow), shown once at startup as a Claude
# Code-style welcome banner. Raw string so the beams' backslashes are literal.
_LOGO = r"""
                    \   |   /
                     \  |  /
             ·  ──────( ◉ )──────  ·
                     /  |  \
                    /   |   \

██████╗ ██╗   ██╗██╗     ███████╗ █████╗ ██████╗
██╔══██╗██║   ██║██║     ██╔════╝██╔══██╗██╔══██╗
██████╔╝██║   ██║██║     ███████╗███████║██████╔╝
██╔═══╝ ██║   ██║██║     ╚════██║██╔══██║██╔══██╗
██║     ╚██████╔╝███████╗███████║██║  ██║██║  ██║
╚═╝      ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
"""
_ACCENT = "#d97757"   # warm terracotta accent


def _short(value, limit: int = 300) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + " …"


class PermissionModal(ModalScreen[str]):
    """Asks the user to authorise a tool call. Dismisses with 'once'|'session'|'deny'."""

    def __init__(self, tool_name: str, tool_input: dict) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._tool_input = tool_input

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("Permission required", id="dialog-title")
            yield Static(Text(f"tool:  {self._tool_name}"))
            yield Static(Text(f"input: {_short(self._tool_input)}"))
            yield Button("Allow once", variant="primary", id="once")
            yield Button("Allow for session", variant="success", id="session")
            yield Button("Deny", variant="error", id="deny")

    @on(Button.Pressed)
    def _chosen(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")


class AgentTUI(App[None]):
    """Interactive terminal UI: type a prompt, watch the agent work."""

    CSS = """
    #log { padding: 1 2; }
    .banner { margin-bottom: 1; }
    .user     { color: $accent; text-style: bold; margin-top: 1; }
    .assistant{ margin-top: 1; }
    .tool     { color: $warning; }
    .toolok   { color: $success; }
    .toolerr  { color: $error; }
    .deny     { color: $error; text-style: bold; }
    .sys      { color: $text-muted; text-style: italic; }
    #prompt   { dock: bottom; }
    #dialog   { padding: 1 2; width: 70%; height: auto; background: $panel; border: round $primary; }
    #dialog-title { text-style: bold; margin-bottom: 1; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, agent_name: str = "run_agent") -> None:
        super().__init__()
        self._agent_name = agent_name
        self._busy = False
        self._session_allow: set[tuple] = set()
        self._current_assistant: Static | None = None
        self._assistant_buf = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="log")
        yield Input(placeholder="Type a prompt and press Enter…", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Pulsar"
        self.sub_title = self._agent_name
        self.query_one("#log", VerticalScroll).mount(
            Static(self._welcome_banner(), classes="banner")
        )
        self.query_one("#prompt", Input).focus()

    def _welcome_banner(self) -> Group:
        """The startup logo + a small info box (model, workdir, how to quit)."""
        logo = Text(_LOGO, style=f"bold {_ACCENT}")
        tagline = Text("Pulsar - a didactic coding agent, built across Parts 1-6", style="italic dim")
        info = Text()
        info.append("model    ", style="dim"); info.append(f"{DEFAULT_MODEL}\n")
        info.append("agent    ", style="dim"); info.append(f"{self._agent_name}\n")
        info.append("workdir  ", style="dim"); info.append(f"{WORKING_DIR}\n")
        info.append("quit     ", style="dim"); info.append("Ctrl+C")
        box = Panel(info, border_style="dim", expand=False, padding=(0, 1))
        return Group(logo, tagline, Text(""), box)

    # --- user input -> start a run in a worker thread ------------------------
    @on(Input.Submitted, "#prompt")
    def _submit(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._busy:
            return
        inp = self.query_one("#prompt", Input)
        inp.value = ""
        inp.disabled = True
        self._busy = True
        self._mount(Text(f"you › {prompt}", style="bold"), "user")
        self.run_worker(
            functools.partial(self._worker, prompt),
            thread=True,
            exclusive=True,
            name="agent-run",
        )

    def _worker(self, prompt: str) -> None:
        """Runs in a thread: drive the agent, marshal events to the UI thread."""
        try:
            for event in invoke(self._agent_name, prompt, ask=self._threaded_ask):
                self.call_from_thread(self._handle_event, event)
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            self.call_from_thread(self._handle_event, ev.AgentError(message=str(exc)))

    # --- permission escalation: thread -> UI modal -> back -------------------
    def _threaded_ask(self, tool_name: str, tool_input: dict) -> Decision:
        return self.call_from_thread(self._ask_modal, tool_name, tool_input)

    async def _ask_modal(self, tool_name: str, tool_input: dict) -> Decision:
        key = (tool_name, tuple(sorted((k, str(v)) for k, v in tool_input.items())))
        if key in self._session_allow:
            return "allow"
        choice = await self.push_screen_wait(PermissionModal(tool_name, tool_input))
        if choice == "session":
            self._session_allow.add(key)
            return "allow"
        if choice == "once":
            return "allow"
        return "deny"

    # --- render the event stream (runs on the UI thread) ---------------------
    async def _handle_event(self, event: ev.Event) -> None:
        log = self.query_one("#log", VerticalScroll)

        if isinstance(event, ev.AssistantText):
            if self._current_assistant is None:
                self._assistant_buf = ""
                self._current_assistant = Static("", classes="assistant")
                await log.mount(self._current_assistant)
            self._assistant_buf += event.text
            self._current_assistant.update(Text(self._assistant_buf))
            log.scroll_end(animate=False)
            return

        # Any non-text event ends the current assistant bubble.
        self._current_assistant = None

        if isinstance(event, ev.ToolRequested):
            self._mount(Text(f"⚙ {event.name}({_short(event.input)})"), "tool")
        elif isinstance(event, ev.PermissionDecided):
            if event.decision == "deny":
                self._mount(Text(f"⛔ {event.name}: denied"), "deny")
        elif isinstance(event, ev.ToolExecuted):
            self._mount(Text(f"← {_short(event.result)}"), "toolerr" if event.is_error else "toolok")
        elif isinstance(event, ev.Compacted):
            self._mount(Text(f"… compacted {event.turns} turns into a summary"), "sys")
        elif isinstance(event, ev.MemoryReloaded):
            self._mount(Text(f"… memory reloaded ({event.path})"), "sys")
        elif isinstance(event, ev.AgentError):
            self._mount(Text(f"error: {event.message}"), "deny")
            self._finish()
        elif isinstance(event, ev.AgentFinished):
            self._finish()

        log.scroll_end(animate=False)

    def _mount(self, renderable, css_class: str) -> None:
        self.query_one("#log", VerticalScroll).mount(Static(renderable, classes=css_class))

    def _finish(self) -> None:
        self._busy = False
        inp = self.query_one("#prompt", Input)
        inp.disabled = False
        inp.focus()


def run_tui(agent_name: str = "run_agent") -> None:
    AgentTUI(agent_name=agent_name).run()
