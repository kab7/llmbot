# Repository structure

Code is the behavioral source of truth. For implementation invariants, also read
[AI_DEVELOPMENT.md](AI_DEVELOPMENT.md).

```text
llmbot/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── bot.py
├── chatgpt_runtime.py
├── config.py
├── schedule_runtime.py
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── setup.sh
├── start.sh
├── Dockerfile
├── docker-compose.yml
├── env.example
├── scripts/
│   ├── chatgpt_auth.py
│   ├── telegram_auth.py
│   ├── deploy_check.py
│   └── scrub_logs.py
├── docs/
│   ├── AI_DEVELOPMENT.md
│   ├── SCHEMAS.md
│   ├── QUICKSTART.md
│   ├── INSTALL.md
│   ├── DEPLOYMENT.md
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
    ├── test_chatgpt_auth.py
    ├── test_chatgpt_runtime.py
    ├── test_deployment_tools.py
    ├── test_config.py
    ├── test_schedule_runtime.py
    ├── test_scrub_logs.py
    └── test_repository_contracts.py
```

Ignored runtime files can appear beside source:

```text
.env
chatgpt_credentials*.json
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
- ChatGPT Responses retry/statistics orchestration;
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
_process_single_chat / _process_combined_folder
get_chat_history
_process_chat_with_openai_result
_call_llm_api_internal
init_scheduler / run_scheduled_summary_job
```

Global process state:

```text
telethon_client
current_context
chatgpt_client
scheduler
application_ref
schedules_lock
```

### `config.py`

Loads `.env` at import time and exposes:

- required Telegram/admin settings;
- path, logging, timeout, and retry settings;
- ChatGPT credential path and selected model;
- `get_config_issues()`;
- `PARSER_PROMPT`;
- `PROCESSOR_PROMPT`.

Integer environment values use `_parse_int_env()`. Invalid integers fall back to
the supplied default; required positive IDs then fail startup validation.

### `chatgpt_runtime.py`

Contains:

- protected credential loading and atomic rotating-token writes;
- serialized OAuth refresh;
- account-specific visible model discovery and `auto` resolution;
- chat-message to Responses input conversion;
- public Responses API HTTP/SSE execution;
- mandatory `response.completed` enforcement and structured errors.

### `schedule_runtime.py`

Contains the schedule persistence/data layer:

- recurrence constants;
- SQLite `schedules` table creation;
- additive `requested_model` and `folder_mode` migrations;
- row conversion;
- next-run calculations;
- record construction;
- full-table load/save;
- recurrence display text.

It does not import Telegram or APScheduler. `bot.py` validates records, serializes
access with an asyncio lock, and registers scheduler jobs.

### `scripts/scrub_logs.py`

Standalone maintenance command for existing text logs. It loads credential
values from `.env`, combines exact-value and recognizable credential-pattern
redaction, rewrites changed files atomically, preserves file ownership/mode when
permitted, and reports only replacement counts.

### `scripts/chatgpt_auth.py`

Interactive local OAuth utility. It implements dynamic client registration,
loopback callback, PKCE/state/nonce, ID-token validation, required ChatGPT-plan
scope validation, and owner-only credential output. The generated file is
runtime state and must never be committed.

## Application flow

`scripts/telegram_auth.py` creates/verifies a user session independently of the
main application. `scripts/deploy_check.py` verifies deployment access without
polling or jobs. Both are included in the Docker image. See
[DEPLOYMENT.md](DEPLOYMENT.md) for replacement-host recovery.

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
  -> per-chat processor LLM calls OR one merged-folder processor LLM call
  -> response validation/cleanup
  -> Telegram HTML/plain output
  -> optional read acknowledgement
```

Folder requests branch after target resolution:

```text
folder_mode=per_chat
  -> load one history
  -> one LLM operation and result
  -> repeat for every dialog

folder_mode=combined
  -> load all histories with original-post permalinks
  -> merge source-labelled blocks
  -> one LLM operation and one cross-folder result
  -> validate that cited Telegram URLs came from the merged history
```

## Persistence

### `.env`

Loaded by `config.py`. `/setmodel` atomically updates `CHATGPT_MODEL` through a
temporary sibling file. OAuth token values never belong in `.env`. Paths are
relative to the process working directory unless configured as absolute.

### ChatGPT OAuth credentials

`CHATGPT_CREDENTIALS_FILE` points to the renewable Sign in with ChatGPT session.
`scripts/chatgpt_auth.py` creates it with owner-only permissions;
`chatgpt_runtime.py` atomically rotates it on refresh. It grants plan usage and
must be protected like the Telethon session.

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
    "period_type": "days" | "hours" | "today" | "yesterday" | "last_messages" | "unread" | None,
    "period_value": int | None,
}
```

It is not persisted.

## Build and packaging

### `setup.sh`

Reuses a healthy supported local `venv` even without a compatible system Python,
or creates/repairs it from an external interpreter. It installs runtime
dependencies, optionally installs dev dependencies, and initializes `.env`
without overwriting it.

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
| `test_bot_llm_api.py` | ChatGPT retry/stats, parser, processor, history, unread, scheduled failure. |
| `test_bot_llm_commands.py` | Runtime commands and top-level user-message orchestration. |
| `test_bot_utils.py` | Guards, formatting, matching, folders, Telethon helpers, scheduler initialization. |
| `test_chatgpt_auth.py` | Stable deployment-host identity and returning-account OAuth validation. |
| `test_chatgpt_runtime.py` | OAuth credentials/refresh, model catalog, payload conversion, and SSE completion. |
| `test_config.py` | Environment parsing and required/optional validation. |
| `test_schedule_runtime.py` | Recurrence calculations and SQLite persistence. |
| `test_scrub_logs.py` | Runtime formatter redaction, HTTP logger levels, historical scrubbing, and idempotency. |
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
