# AI Development Guide

This is the canonical repository guide for coding agents. Read it before changing
runtime behavior. `README.md` and the other files in `docs/` are primarily
user-facing and may omit implementation details.

## 1. System purpose and boundaries

The project is a single-admin Telegram bot that analyzes Telegram history with an
OpenAI-compatible chat-completions API.

There are two separate Telegram clients:

- `python-telegram-bot` receives bot commands and sends bot replies.
- Telethon runs as the configured Telegram user and reads dialogs, folders,
  unread state, and message history.

Every registered bot command and the free-text handler is wrapped with
`@admin_only`. `ADMIN_USER_ID` is therefore a security boundary, not merely a UI
preference.

The process is intentionally stateful:

- the last resolved target and period live in the in-memory `current_context`;
- Telethon authentication lives in the session file;
- periodic jobs live in SQLite;
- runtime LLM changes are persisted to `.env`;
- application and full LLM traffic logs are written to rotating files.

The application has no multi-user state model and no database for conversation
context.

## 2. Source map

| Path | Responsibility |
| --- | --- |
| `bot.py` | Composition root and most application logic: Telegram handlers, Telethon access, intent guards, history selection, LLM calls, output formatting, and APScheduler integration. |
| `config.py` | Import-time `.env` loading, typed environment parsing, defaults, validation, parser prompt, and processor prompt. |
| `llm_runtime.py` | Mutable primary/fallback LLM settings, URL normalization, model-list normalization, candidate construction, token masking, and Yandex helpers. |
| `schedule_runtime.py` | SQLite schema and serialization plus recurrence calculations. It has no Telegram or APScheduler dependency. |
| `tests/test_bot_llm_api.py` | LLM retry/fallback, parser, processor, history, unread, and scheduled-job behavior. |
| `tests/test_bot_llm_commands.py` | Bot command handlers and top-level message orchestration. |
| `tests/test_bot_utils.py` | Intent guards, formatting, folders, Telethon helpers, scheduler initialization, and other utilities. |
| `tests/test_config.py` | Environment loading and configuration validation. |
| `tests/test_llm_runtime.py` | Runtime LLM configuration contract. |
| `tests/test_schedule_runtime.py` | Recurrence and SQLite persistence contract. |
| `tests/test_repository_contracts.py` | Code-derived parser/schedule schemas, SQLite columns, command docs, env inventory, and Docker module inventory. |
| `env.example` | Canonical list of supported environment variables. |
| `Dockerfile`, `docker-compose.yml` | Production container layout. |
| `docs/schemas/*.json` | Derived machine-readable contracts; tests compare them with code and SQLite. |

Runtime files are not source files:

- `.env`
- `telethon_session.session` (or the path derived from `SESSION_NAME`)
- `schedules.db` (or `SCHEDULES_FILE`)
- `bot.log`
- `llm_traffic.log`

Treat all of them as sensitive. In particular, the Telethon session grants access
to the Telegram account, and `llm_traffic.log` contains full prompts, chat
history sent to providers, and provider responses.

## 3. Startup and shutdown

`asyncio.run(main())` owns the process lifecycle:

1. `config.get_config_issues()` validates required Telegram/admin settings.
   Missing LLM tokens are only a warning because `/settoken` can configure one
   after startup.
2. `init_telethon_client()` connects the user session. Connection and
   authorization checks have explicit startup timeouts.
3. A `python-telegram-bot` `Application` is created and all handlers are
   registered.
4. The application is initialized, started, and polling begins.
5. `init_scheduler()` starts APScheduler and restores persisted schedules.
6. Shutdown stops polling, the bot application, the scheduler, and Telethon.

Configuration is read at import time. Tests that change configuration environment
variables reload the `config` module. A production `.env` change made outside the
bot generally requires a restart; `/seturl`, `/setmodel`, and `/settoken` update
both in-memory settings and `.env`.

## 4. Free-text request pipeline

`process_user_message()` is the main orchestration function:

1. Reject processing if neither primary nor fallback has a token.
2. Parse the original text with `parse_command_with_gpt()`.
3. Validate types and normalize the JSON with `validate_command_payload()`.
4. Apply deterministic intent guards to the original text.
5. Fill an omitted target and period from `current_context`.
6. Send a visible “recognized command” diagnostic to the admin.
7. If recurrence was explicitly requested, persist a schedule and do not run the
   analysis immediately.
8. Resolve either one chat or every chat matching a Telegram folder.
9. Save the successfully resolved target and period to `current_context`.
10. Load and analyze each chat sequentially.
11. Optionally mark each processed chat as read.
12. Send per-chat results and aggregate LLM statistics.

Do not bypass the deterministic intent guards when extending parsing. They exist
because an LLM parser may return structurally valid but invented destructive or
surprising intent.

### Parser payload

`config.PARSER_PROMPT` asks for exactly these semantic fields:

| Field | Values / meaning |
| --- | --- |
| `target_type` | `chat`, `folder`, or `null` |
| `target_name` | Explicit target name or `null` |
| `period_type` | `days`, `hours`, `today`, `last_messages`, `unread`, or `null` |
| `period_value` | Positive integer only for `days`, `hours`, and `last_messages` |
| `mark_as_read` | Boolean; must be backed by explicit source text |
| `query` | Full user intent used for downstream analysis |
| `requested_model` | Optional model identifier for this analysis only |
| `recurrence_type` | `daily`, `weekly`, `monthly`, `interval_days`, or `null` |
| `interval_days` | Positive integer only for `interval_days` |
| `time` | Local `HH:MM` for a recurring request |

`validate_command_payload()` adds internal `time_missing`. It is not part of the
prompted JSON contract.

### Deterministic guards

The original user text has authority over parsed output:

- explicit “chat” versus “folder” wording corrects `target_type`;
- `mark_as_read=true` is discarded without an explicit mark-as-read phrase;
- `period_type=unread` is discarded without explicit unread wording;
- obvious textual periods such as today, yesterday, a week, N hours/days, and
  last N messages override a conflicting parsed period;
- parsed recurrence is discarded unless the original text contains an explicit
  recurrence phrase;
- a recurrence phrase that the parser failed to understand produces an error
  instead of silently running once.

Because mark-as-read changes external Telegram state, any new wording support must
be added deliberately to the guard and tested.

## 5. Target and history semantics

### Chat and folder resolution

Chat and folder lookup share `find_best_match()`:

- matching is case-insensitive and ignores emoji;
- exact matches score `1.0`;
- substring matches score `0.9`;
- otherwise `SequenceMatcher` provides fuzzy similarity;
- a result is accepted at similarity `>= 0.5`.

Folder membership is reproduced from Telegram `DialogFilter` fields. It combines
explicit/pinned peers with dynamic `bots`, `contacts`, `non_contacts`, `groups`,
and `broadcasts` rules, then applies explicit exclusion and the
`exclude_read`, `exclude_muted`, and `exclude_archived` flags.

Folder requests operate on all matched dialogs sequentially. There is no
concurrency limit because there is currently no concurrent per-folder execution.

### Period selection

`get_chat_history()` returns `(formatted_history, first_message_id)`:

- `days`: messages since current UTC time minus N days;
- `hours`: messages since current UTC time minus N hours;
- `today`: messages since local midnight converted to UTC;
- `last_messages`: latest N messages;
- `unread`: at most the unread count/default limit and, when available, only
  message IDs above `read_inbox_max_id`;
- `None`: latest `DEFAULT_MESSAGES_LIMIT` messages.

Output is chronological even when Telethon returns limited history newest-first.
Only messages with `message.text` are included. Media-only messages and other
non-text events are ignored. Timestamps embedded in the prompt are converted to
the host/container local timezone.

For unread mode, a dialog with a known zero unread count is skipped before
history loading. If Telegram reports unread messages but the text-only selection
is empty, an explicit mark-as-read request can still acknowledge the chat.

The returned first message ID is used to construct a `t.me` link when Telegram
supports one.

## 6. LLM runtime contract

`LLMRuntimeConfig` owns mutable primary and fallback settings. A comma-separated
model value creates an ordered model list. Exact duplicate
`(url, model, token)` candidates are removed.

At the standalone class level, passing `fallback_token=None` enables dynamic
inheritance when the primary token changes. The application config normally
passes a string: an absent `FALLBACK_LLM_TOKEN` copies the primary token value at
startup, while an explicitly empty variable produces an empty fallback token.
Neither application path dynamically follows later `/settoken primary` changes.

The normal call order in `_call_llm_api_internal()` is:

1. run all primary models in order;
2. repeat the complete primary list for up to `LLM_MAX_RETRIES` rounds;
3. only after primary is exhausted, do the same for fallback.

HTTP errors, invalid JSON, unexpected response shape, empty content, and
validator rejection move to the next candidate. A `429` also moves to the next
candidate. Free models have separate primary/fallback pacing and growing 429
backoff, configured by the `*_FREE_MODEL_*` variables.

Candidate stats count requests, 429s, successes, validator rejections, and other
technical errors. They are aggregated across a folder operation and shown to the
admin.

### One-request model override

When `requested_model` is present:

- it is used only for the analysis call, not the parser call;
- the primary endpoint/token is preferred, or fallback credentials are used when
  primary has no token;
- only that model is attempted;
- it gets three attempts;
- configured model fallback is intentionally disabled.

### Provider differences

All providers must expose an OpenAI-style chat-completions response with
`choices[0].message.content`.

For `ai.api.cloud.yandex.net`, `_build_llm_headers()` uses
`Authorization: Api-Key` and derives `x-folder-id` from a model URI shaped like
`gpt://<folder_id>/<model>`. Other providers use bearer authorization.

`/limits` derives an OpenRouter-style `.../key` endpoint. It is intentionally not
supported for Yandex Cloud.

### Summary quality checks

The processor prompt requires Russian, grounded output. A response validator
rejects suspicious HTML/code artifacts, unexpected scripts, excessive mixed
scripts, boilerplate, and dates not supported by the supplied history.
`_cleanup_summary_text()` then removes known presentation artifacts.

The complete payload and response are logged to `llm_traffic.log`. Never add
tokens to that log, but assume the log already contains private chat data.

## 7. Scheduling contract

Natural-language recurrence creates a persistent record and schedules a one-shot
APScheduler `DateTrigger`. After each execution, the next one-shot job is
calculated and registered.

Supported recurrence:

- `daily`;
- `weekly`, anchored to the local weekday on which it was created;
- `monthly`, anchored to the local day of month on which it was created and
  clamped to the final day of shorter months;
- `interval_days`.

Times use the process local timezone. Docker Compose currently sets
`TZ=Europe/Moscow`.

SQLite table `schedules` stores:

`id`, `created_at`, `last_run`, `next_run`, `chat_id`, `target_type`,
`target_name`, `period_type`, `period_value`, `query`, `requested_model`,
`mark_as_read`, `recurrence_type`, `time`, `interval_days`, `weekday`, and
`day_of_month`.

Important behavior:

- `_ensure_schema()` currently performs only the known `requested_model`
  additive migration;
- all load/modify/save operations in `bot.py` use `schedules_lock`;
- persistence rewrites the full table in one SQLite transaction;
- stale `next_run` values are recomputed at startup rather than executed as
  missed work;
- successful jobs set `last_run` and calculate the next recurrence;
- uncaught job-level failures retain the record and retry after 300 seconds;
- per-chat failures inside a multi-chat job are counted as skipped and the job
  still advances to its normal next recurrence;
- invalid records are removed and the admin is notified when possible;
- `/schedules` lists records and `/delschedule <id>` removes both persistence and
  the in-process job.

When adding a schedule field, update the schema, `_SCHEDULE_COLUMNS`,
row conversion, save tuples/SQL, record construction, validation, display, and
tests together. Add an explicit migration for existing databases.

## 8. Output and state mutation

LLM markdown-like output is converted to Telegram-supported HTML. Supported
transformations are headings, bold, inline code, and HTTP(S) links. If Telegram
rejects HTML, the bot retries with plain text. Long messages are split below
Telegram's 4096-character limit.

Mark-as-read happens after `_process_chat_with_openai_result()` returns, except
for the documented unread/text-empty case. That helper converts LLM failures into
visible `❌` answer text instead of raising, so the current flow can acknowledge a
chat even when analysis failed. Preserve or deliberately change this behavior
with explicit tests; do not assume “mark after successful analysis” is currently
enforced. A Telegram acknowledgement failure is logged but does not change the
analysis result.

`current_context` is updated only after target resolution succeeds. It contains:

```python
{
    "target_type": "chat" | "folder",
    "target_name": str,
    "period_type": "days" | "hours" | "today" | "last_messages" | "unread" | None,
    "period_value": int | None,
}
```

Context is global, intentionally single-admin, and lost at restart.

## 9. Configuration and paths

Use `env.example` as the variable inventory. Important path behavior:

- `.env` updates always target `Path(".env")`, relative to the process working
  directory;
- `SESSION_NAME`, `SCHEDULES_FILE`, `LOG_FILE_PATH`, and
  `LLM_TRAFFIC_LOG_PATH` may be absolute or working-directory-relative;
- Docker sets all mutable paths to `/data` and bind-mounts
  `/data/srv/data/llmbot` from the host;
- deployment-specific server details live in root `AGENTS.md`.

The supported Python range is 3.11-3.13. The Docker image uses Python 3.12.

`setup.sh` validates and repairs `venv`, supports `--dev` and `--recreate`, and
accepts `PYTHON_BIN`/`VENV_DIR` overrides. `start.sh` refuses to launch an
unsupported or incomplete environment.

## 10. Tests and verification

Install both runtime and development requirements, then run:

```bash
python -m pytest
```

`pyproject.toml` enables terminal missing-line coverage and enforces 50% across
all four runtime modules: `bot`, `config`, `llm_runtime`, and
`schedule_runtime`.

Last verified on 2026-07-20 with Python 3.12.13:

- 118 tests passed;
- total configured coverage: 72.53%;
- `bot.py`: 69%;
- `config.py`: 100%;
- `llm_runtime.py`: 100%;
- `schedule_runtime.py`: 100%.

Update this snapshot only from a complete suite invocation.

Tests are heavily mocked and do not prove live Telegram, provider, Docker, or
deployment connectivity. For behavior changes, run the smallest relevant test
file first with `--no-cov`, then run the full suite with coverage before handoff.

## 11. Change checklists

### Add or change a parser field

Update all of:

1. `config.PARSER_PROMPT` schema, rules, and examples;
2. `validate_command_payload()`;
3. deterministic intent guards when the field can cause external state changes;
4. `process_user_message()` extraction and flow;
5. schedule persistence if recurring jobs need the field;
6. `docs/schemas/parser-command.schema.json`;
7. parser, utility, repository-contract, orchestration, and scheduled-job tests;
8. this guide and user-facing command examples when applicable.

### Add a period type

Update `ALLOWED_PERIOD_TYPES`, parser prompt, validation, context resolution,
`format_period_text()`, `get_chat_history()`, both JSON schemas,
recognized-command output, and unit/orchestration/repository-contract tests.

### Add a bot command

Create an async handler, wrap it with `@admin_only`, register it in `main()`, add
it to `/help`, and test authorization plus success/error paths.

### Change LLM retry behavior

Preserve the distinctions between:

- models within one scope;
- retry rounds;
- primary and fallback scopes;
- free and paid pacing;
- transport/API failures and validator rejections;
- configured candidates and one-request model overrides.

The tests in `tests/test_bot_llm_api.py` encode these distinctions explicitly.

### Change folder behavior

Test explicit include/pinned peers, dynamic inclusion flags, exclusions, unread,
muted, archived, legacy Telethon filter results, timeouts, and error wrapping.

## 12. Known constraints

- `bot.py` is a large monolith; avoid creating new cross-module import cycles.
- Folder chats are processed sequentially and large folders can take a long time.
- Entire selected histories are sent in one LLM request; there is no token-aware
  chunking or map/reduce summarization.
- Only text messages are analyzed.
- In-memory context is global and cannot safely support multiple admins.
- Runtime `.env` writes assume a single process and a writable working directory.
- Schedule persistence rewrites all records and is designed for a small local
  schedule set, not concurrent multi-process access.
- Unit tests mock external services; production behavior also depends on Telegram
  session health, provider compatibility, rate limits, and local timezone.
