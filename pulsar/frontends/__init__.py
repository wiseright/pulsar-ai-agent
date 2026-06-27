"""Frontends consume the agent's event stream.

Two implementations, both built on the same event stream (the core does not
change between them):
  * console.py — a plain, line-based console frontend
  * tui.py     — an interactive Textual TUI
"""
