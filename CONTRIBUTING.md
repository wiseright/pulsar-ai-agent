# Contributing to Pulsar

Thanks for your interest! Pulsar is a **didactic** project: it accompanies the
*"Anatomy of an Agentic Loop"* series and is meant to be **read** as much as run.
Contributions that keep it clear and faithful to the articles are very welcome.

## Guiding principle

Readability over cleverness. Each module maps to one Part of the series and keeps
the article's function names. When in doubt, prefer the explanation that teaches.

## Getting set up

```bash
git clone <your-fork-url> pulsar
cd pulsar
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[test]"
```

Pulsar targets **Python ≥ 3.11**.

## Before opening a pull request

- Run the test suite — it is fully offline (no API key, no network):

  ```bash
  python -m pytest -q
  ```

- Add or update tests for any behaviour you change. The suite uses a scripted
  fake Anthropic client, so new tests should stay offline and deterministic.
- Keep comments and docstrings in the same explanatory style as the surrounding
  code, and update the README if you change user-facing behaviour.
- Never commit secrets or a virtual environment. `.env`, `.venv/`, and `env/`
  are git-ignored on purpose.

## Reporting issues

Please include your OS, Python version, and the exact command and output. For
bugs in the agent loop, the event stream around the failure is especially useful.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
