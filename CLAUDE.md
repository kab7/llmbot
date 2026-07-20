# CLAUDE.md

Read [`docs/AI_DEVELOPMENT.md`](docs/AI_DEVELOPMENT.md) before changing this
repository. It is the canonical architecture, invariant, persistence, testing,
and extension guide for coding agents. Python code is the behavioral source of
truth; schemas and documentation are derived contracts.

Quick verification:

```bash
./setup.sh --dev
venv/bin/python -m pytest
```

Use Python 3.11-3.13. The production Docker image uses Python 3.12.

Important safety boundaries:

- every Telegram handler must remain admin-only;
- mark-as-read and recurrence require explicit source-text intent;
- `.env`, Telethon sessions, schedule data, and logs are runtime secrets/state;
- `llm_traffic.log` contains full chat history sent to LLM providers;
- parser/schedule changes must update `docs/schemas/*.json` and repository
  contract tests;
- deployment coordinates and commands are in `AGENTS.md`.
