"""Command-line entry point.

Console (one-shot):
    python -m pulsar <agent_name> "<prompt>"
    python -m pulsar run_agent "Read pyproject.toml and list the dependencies"

Interactive TUI:
    python -m pulsar --tui [<agent_name>]
"""

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Interactive TUI mode: `--tui [agent_name]`
    if argv and argv[0] == "--tui":
        agent_name = argv[1] if len(argv) > 1 else "run_agent"
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
