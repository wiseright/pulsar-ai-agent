"""Central configuration: model, paths, and budgets.

Everything that the rest of the package needs to know about *where things live*
and *which knobs are tunable* is gathered here, so no other module has to read
the environment directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load ANTHROPIC_API_KEY (and optional overrides) from a local .env if present.
load_dotenv()

# --- Model (Part 1/5) ---------------------------------------------------------
# Default to Sonnet: cheap enough to run the didactic agent often.
DEFAULT_MODEL = os.environ.get("PULSAR_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 4096

# Summarisation runs on a cheaper, faster model than the main agent (Part 4 §7:
# "Use a cheaper model" — the summary call should cost an order of magnitude
# less than the turn that triggers it). Override with PULSAR_SUMMARY_MODEL.
SUMMARY_MODEL = os.environ.get("PULSAR_SUMMARY_MODEL", "claude-haiku-4-5-20251001")

# --- Filesystem scope for the file tools (Part 2/3) --------------------------
# The file tools (read/list/write) are confined to this directory. By default
# it is the current working directory from which the agent is launched.
WORKING_DIR = Path(os.environ.get("PULSAR_WORKDIR", Path.cwd())).resolve()

# Tool outputs are prompt material (Part 2 §4) and the main context expense
# (Part 4 §6): truncate large outputs at the tool layer, with a clear marker,
# so a single huge file read can never blow the window on every later turn.
MAX_OUTPUT_BYTES = 200_000

# --- Memory locations (Part 5) -----------------------------------------------
# We mirror the Claude Code convention but use our own file name: a PULSAR.md
# per scope (so Pulsar never reads or writes Claude Code's CLAUDE.md files).
#   * project memory lives inside this package's project root (the repo root)
#   * user memory lives in ~/.claude/PULSAR.md
# Claude Code also has enterprise/policy and an auto-extracted memory directory;
# we leave those out for clarity.
_PACKAGE_DIR = Path(__file__).resolve().parent          # .../pulsar/pulsar
PROJECT_ROOT = _PACKAGE_DIR.parent                       # .../pulsar (repo root)
PROJECT_MEMORY = PROJECT_ROOT / "PULSAR.md"             # project scope
USER_MEMORY = Path.home() / ".claude" / "PULSAR.md"    # user scope

# --- Context management budget (Part 4) --------------------------------------
TOKEN_BUDGET = 30_000   # trigger compaction above this estimated token count
WINDOW_TURNS = 8        # keep this many recent turns verbatim when compacting
