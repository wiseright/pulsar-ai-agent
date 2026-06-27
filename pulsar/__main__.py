"""Command-line entry point.

Console (one-shot):
    python -m pulsar <agent_name> "<prompt>"
    python -m pulsar run_agent "Read pyproject.toml and list the dependencies"

Interactive TUI:
    python -m pulsar --tui [<agent_name>]
"""

import sys


def _has_api_key() -> bool:
    """True if an Anthropic API key is available.

    Importing config triggers `load_dotenv()`, so a key set in a local `.env`
    counts here just as an exported environment variable does.
    """
    import os

    from . import config  # noqa: F401 — imported for its load_dotenv() side effect

    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _api_key_error() -> int:
    """Print a friendly message when the API key is missing; return exit code 2."""
    print("error: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
    print("The agent needs it to call Claude. Set it via a local .env file:", file=sys.stderr)
    print("    cp .env.example .env      # then edit .env and set your key", file=sys.stderr)
    print("or export it for this shell:", file=sys.stderr)
    print("    export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
    print("(The offline test suite does not need a key.)", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Interactive TUI mode: `--tui [agent_name]`
    if argv and argv[0] == "--tui":
        agent_name = argv[1] if len(argv) > 1 else "run_agent"
        if not _has_api_key():
            return _api_key_error()
        try:
            from .frontends.tui import run_tui
        except ImportError:
            print("error: the TUI needs Textual. Install it with: pip install textual", file=sys.stderr)
            return 2
        run_tui(agent_name)
        return 0

    if len(argv) < 2:
        print('usage: python -m pulsar <agent_name> "<prompt>"', file=sys.stderr)
        print('       python -m pulsar --tui [<agent_name>]', file=sys.stderr)
        print('example: python -m pulsar run_agent "summarise README.md"', file=sys.stderr)
        return 2

    agent_name = argv[0]
    prompt = " ".join(argv[1:])

    if not _has_api_key():
        return _api_key_error()

    # Imported here so `--help`-style misuse doesn't require the anthropic SDK.
    from .frontends.console import run_console

    try:
        run_console(agent_name, prompt)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
