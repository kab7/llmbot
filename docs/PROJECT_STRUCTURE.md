# Repository structure

Code is the behavioral source of truth. For implementation invariants, also read
[AI_DEVELOPMENT.md](AI_DEVELOPMENT.md).

```text
llmbot/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── bot.py
├── config.py
├── llm_runtime.py
├── schedule_runtime.py
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── setup.sh
├── start.sh
├── Dockerfile
├── docker-compose.yml
├── env.example
├── docs/
│   ├── AI_DEVELOPMENT.md
│   ├── SCHEMAS.md
│   ├── QUICKSTART.md
│   ├── INSTALL.md
│   ├── EXAMPLES.md
│   ├── FAQ.md
│   ├── PROJECT_STRUCTURE.md
│   ├── CHANGELOG.md
│   └── schemas/
│       ├── parser-command.schema.json
│       └── schedule-record.schema.json
└── tests/
    ├── test_bot_llm_api.py
    ├── test_bot_llm_commands.py
    ├── test_bot_utils.py
    ├── test_config.py
    ├── test_llm_runtime.py
    ├── test_schedule_runtime.py
    └── test_repository_contracts.py
```

Ignored runtime files can appear beside source:

```text
.env
venv/
*.session
schedules.db*
bot.log*
llm_traffic.log*
.coverage
htmlcov/
```

## Runtime modules

### `bot.py`

Composition root and main application module. It contains:

- rotating log setup;
- admin authorization decorator;
- parser normalization and deterministic intent guards;
- primary/fallback LLM HTTP execution;
- free-model pacing and 429 backoff;
- summary validation and cleanup;
- Telethon initialization and reconnect logic;
- chat and folder lookup;
- Telegram folder-filter reproduction;
- history and unread selection;
- read acknowledgement;
- output formatting and Telegram chunking;
- bot command handlers;
- natural-language request orchestration;
- APScheduler integration;
- startup and shutdown lifecycle.

Key orchestration functions:

```text
main
process_user_message
parse_command_with_gpt
validate_command_payload
_resolve_single_chat / _resolve_folder_chats
_process_single_chat
get_chat_history
_process_chat_with_openai_result
_call_llm_api_internal
init_scheduler / run_scheduled_summary_job
```

Global process state:

```text
telethon_client
current_context
llm_runtime
scheduler
application_ref
schedules_lock
```

### `config.py`

Loads `.env` at import time and exposes:

- required Telegram/admin settings;
- path, logging, timeout, retry, and pacing settings;
- primary/fallback LLM defaults;
- `get_config_issues()`;
- `PARSER_PROMPT`;
- `PROCESSOR_PROMPT`.

Integer environment values use `_parse_int_env()`. Invalid integers fall back to
the supplied default; required positive IDs then fail startup validation.

### `llm_runtime.py`

Contains:

- `LLMSettings`;
- URL validation and chat-completions normalization;
- comma-separated model normalization;
- primary/fallback mutable settings;
- inherited or independent fallback tokens;
- candidate ordering and deduplication;
- token masking;
- Yandex host and folder-ID helpers.

This module does not perform HTTP calls.

### `schedule_runtime.py`

Contains the schedule persistence/data layer:

- recurrence constants;
- SQLite `schedules` table creation;
- additive `requested_model` migration;
- row conversion;
- next-run calculations;
- record construction;
- full-table load/save;
- recurrence display text.

It does not import Telegram or APScheduler. `bot.py` validates records, serializes
access with an asyncio lock, and registers scheduler jobs.

## Application flow

```text
Telegram Bot API
  -> @admin_only
  -> process_user_message
  -> parser LLM
  -> validate_command_payload
  -> deterministic source-text guards
  -> context resolution
  -> schedule persistence OR target resolution
  -> Telethon history selection
  -> processor LLM
  -> response validation/cleanup
  -> Telegram HTML/plain output
  -> optional read acknowledgement
```

Folder requests branch from target resolution into a sequential per-chat loop.

## Persistence

### `.env`

Loaded by `config.py`. Runtime LLM commands atomically replace this file through
a temporary sibling file. Paths are relative to the process working directory
unless configured as absolute.

### Telethon session

`SESSION_NAME` is passed to `TelegramClient`. The resulting session is sensitive
account authentication material.

### SQLite schedules

`SCHEDULES_FILE` points to a SQLite database. Canonical columns and types are
documented in
[`schemas/schedule-record.schema.json`](schemas/schedule-record.schema.json).

### Context

`current_context` is an in-memory dictionary:

```python
{
    "target_type": "chat" | "folder",
    "target_name": str,
    "period_type": "days" | "hours" | "today" | "last_messages" | "unread" | None,
    "period_value": int | None,
}
```

It is not persisted.

## Build and packaging

### `setup.sh`

Creates or repairs the local `venv`, installs runtime dependencies, optionally
installs dev dependencies, and initializes `.env` without overwriting it.

### `start.sh`

Validates the local Python version and runtime imports, then uses `exec` to run
`bot.py`.

### Docker

The Dockerfile installs runtime dependencies, copies all four runtime modules,
and compiles them before setting the command. Compose mounts mutable state under
`/data`.

No Python package/wheel is built; the project runs directly from source files.

## Tests

| File | Contract |
| --- | --- |
| `test_bot_llm_api.py` | HTTP candidates, fallback, pacing, parser, processor, history, unread, scheduled failure. |
| `test_bot_llm_commands.py` | Runtime commands and top-level user-message orchestration. |
| `test_bot_utils.py` | Guards, formatting, matching, folders, Telethon helpers, scheduler initialization. |
| `test_config.py` | Environment parsing and required/optional validation. |
| `test_llm_runtime.py` | Mutable model settings and provider helpers. |
| `test_schedule_runtime.py` | Recurrence calculations and SQLite persistence. |
| `test_repository_contracts.py` | Code-to-schema, command documentation, env inventory, SQLite columns, Docker module inventory. |

`pyproject.toml` configures pytest and coverage for every runtime Python module.

## Extension boundaries

- Parser changes must update prompt, validator, guards, JSON schema, schedules
  when relevant, docs, and tests.
- Schedule-field changes must update SQLite migration, column ordering,
  serialization, validation, JSON schema, display, and tests.
- New bot commands must remain `@admin_only`, be registered in `main`, documented,
  and added to the repository contract test.
- New runtime modules must be copied by Docker and added to coverage source.
