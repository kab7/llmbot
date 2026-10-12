# AI Development Guide

This is the canonical repository guide for coding agents. Read it before changing
runtime behavior. `README.md` and the other files in `docs/` are primarily
user-facing and may omit implementation details.

## 1. System purpose and boundaries

The project is a single-admin Telegram bot that analyzes Telegram history with
ChatGPT Plus/Pro plan usage through the official Sign in with ChatGPT OAuth flow
and the streaming Responses API.

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
- the selected ChatGPT model is persisted to `.env`;
- renewable ChatGPT OAuth credentials live in a separate owner-only JSON file;
- application and full LLM traffic logs are written to rotating files.

The application has no multi-user state model and no database for conversation
context.

## 2. Source map

| Path | Responsibility |
| --- | --- |
| `bot.py` | Composition root and most application logic: Telegram handlers, Telethon access, intent guards, history selection, LLM calls, output formatting, and APScheduler integration. |
| `chatgpt_runtime.py` | ChatGPT OAuth credential loading/refresh, account-specific model discovery, Responses request conversion, SSE parsing, and terminal-event enforcement. |
| `config.py` | Import-time `.env` loading, typed environment parsing, defaults, validation, parser prompt, and processor prompt. |
| `schedule_runtime.py` | SQLite schema and serialization plus recurrence calculations. It has no Telegram or APScheduler dependency. |
| `scripts/scrub_logs.py` | Standalone in-place credential redaction for existing text logs; loads secret values from a dotenv file without printing them. |
| `scripts/telegram_auth.py` | Isolated interactive user-session setup; hidden code/2FA input, no bot polling or scheduler startup. |
| `scripts/deploy_check.py` | Non-polling preflight for config, Telethon identity, Bot API getMe, OAuth/model catalog, and optional completed inference. |
| `tests/test_bot_llm_api.py` | ChatGPT retries, parser, processor, history, unread, and scheduled-job behavior. |
| `tests/test_chatgpt_auth.py` | Stable deployment-host identity and returning-account validation for OAuth setup. |
| `tests/test_chatgpt_runtime.py` | OAuth credential, refresh, model-list, payload, and SSE contracts. |
| `tests/test_deployment_tools.py` | Isolated session setup, cleanup on failure, non-interactive rejection, and preflight auth/model checks. |
| `tests/test_bot_llm_commands.py` | Bot command handlers and top-level message orchestration. |
| `tests/test_bot_utils.py` | Intent guards, formatting, folders, Telethon helpers, scheduler initialization, and other utilities. |
| `tests/test_config.py` | Environment loading and configuration validation. |
| `tests/test_schedule_runtime.py` | Recurrence and SQLite persistence contract. |
| `tests/test_repository_contracts.py` | Code-derived parser/schedule schemas, SQLite columns, command docs, env inventory, and Docker module inventory. |
| `env.example` | Canonical list of supported environment variables. |
| `Dockerfile`, `docker-compose.yml` | Production container layout. |
| `docs/schemas/*.json` | Derived machine-readable contracts; tests compare them with code and SQLite. |

Runtime files are not source files:

- `.env`
- `chatgpt_credentials.json` (or `CHATGPT_CREDENTIALS_FILE`)
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
   A missing ChatGPT credential file is a warning; analysis remains unavailable
   until the file is generated and the process is restarted.
2. `init_telethon_client()` connects the user session. Connection and
   authorization checks have explicit startup timeouts.
3. A `python-telegram-bot` `Application` is created and all handlers are
   registered.
4. The application is initialized, started, and polling begins.
5. `init_scheduler()` starts APScheduler and restores persisted schedules.
6. Shutdown stops polling, the bot application, the scheduler, and Telethon.

Configuration is read at import time. Tests that change configuration environment
variables reload the `config` module. A production `.env` change made outside the
bot generally requires a restart. `/setmodel` updates the in-memory selection and
`CHATGPT_MODEL` in `.env`; OAuth credentials are never accepted through Telegram.

## 4. Free-text request pipeline

`process_user_message()` is the main orchestration function:

1. Reject processing if a valid ChatGPT OAuth credential record is unavailable.
2. Parse the original text with `parse_command_with_gpt()`.
3. Validate types and normalize the JSON with `validate_command_payload()`.
4. Apply deterministic intent guards to the original text.
5. Fill an omitted target and period from `current_context`.
6. Send a visible “recognized command” diagnostic to the admin.
7. If recurrence was explicitly requested, persist a schedule and do not run the
   analysis immediately.
8. Resolve either one chat or every chat matching a Telegram folder.
9. Save the successfully resolved target and period to `current_context`.
10. For `per_chat`, load and analyze each chat sequentially. For `combined`,
    load all histories sequentially, merge source-labelled blocks, and run one
    LLM operation over the full folder context.
11. Optionally mark each processed chat as read.
12. Send the per-chat or one combined result and aggregate LLM statistics.

Do not bypass the deterministic intent guards when extending parsing. They exist
because an LLM parser may return structurally valid but invented destructive or
surprising intent.

### Parser payload

`config.PARSER_PROMPT` asks for exactly these semantic fields:

| Field | Values / meaning |
| --- | --- |
| `target_type` | `chat`, `folder`, or `null` |
| `target_name` | Explicit target name or `null` |
| `folder_mode` | `per_chat`, `combined`, or `null`; only meaningful for folders |
| `period_type` | `days`, `hours`, `today`, `yesterday`, `last_messages`, `unread`, or `null` |
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
- explicit common/top-N/cross-folder search wording selects `combined`;
  otherwise folder requests remain `per_chat`, and explicit “each separately”
  wording forces `per_chat`;
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

Folder history loading is sequential. There are two processing modes:

- `per_chat` is the backward-compatible default and makes one LLM call/result per
  matched dialog;
- `combined` appends a source header plus the selected history from every
  non-empty dialog, then makes one LLM call/result over that merged context.

`combined` history requests `include_message_links=True`, so every linkable
message line includes its original permalink. The processor is instructed to
cite relevant links and deduplicate repeated events. Its response validator
requires at least one URL copied from the history when linkable URLs exist and
rejects Telegram URLs absent from the history. The first condition uses only
canonical `[Оригинал]` permalinks. The anti-hallucination condition compares
against every literal URL in the supplied history, including links embedded in
message text and source headers. URL sets intentionally ignore duplicates.

On both sides of the source blocks, combined history includes an authoritative
manifest with source indexes, names, and loaded text-message counts. A
high-precision validator rejects a leading meta-claim that only one source is
present or that all other sources are absent when the manifest contains more
than one. It does not require every source to appear in the result.

On the first repairable citation or source-presence rejection, the mutable
request conversation is extended with the rejected assistant answer and a
focused correction instruction. Citation repair copies exact URLs from the
`Оригинал` markers. Source-presence repair includes the authoritative manifest
and explicitly allows selecting only relevant sources. Later candidates and
retry rounds therefore repair the answer instead of receiving an identical
prompt. Only one validation-repair turn is appended.

Combined folder analysis passes
`COMBINED_LLM_REQUEST_TIMEOUT_SECONDS` (default `90`) to the HTTP layer. The
effective value cannot be lower than `LLM_REQUEST_TIMEOUT_SECONDS`. All other
parser and analysis calls keep the ordinary timeout (default `20`).

### Period selection

`get_chat_history()` returns `(formatted_history, first_message_id)`:

- `days`: messages since current UTC time minus N days;
- `hours`: messages since current UTC time minus N hours;
- `today`: messages since local midnight converted to UTC;
- `yesterday`: messages from the previous local midnight up to the current local
  midnight, both converted to UTC;
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
supports one. Public username entities use `t.me/<username>/<message_id>`.
Private channels and supergroups use `t.me/c/<channel_id>/<message_id>`. Legacy
private `Chat` entities and private user dialogs have no usable message
permalink.

## 6. ChatGPT subscription runtime contract

The repository has exactly one LLM route: ChatGPT plan usage. There is no
provider URL setting, API-key setting, primary/fallback scope, or provider
fallback. The endpoint constants in `chatgpt_runtime.py` are fixed to the public
OpenAI API and authorization hosts documented for Sign in with ChatGPT.

### Credential lifecycle

`scripts/chatgpt_auth.py` performs first-time dynamic client registration and
OAuth authorization locally:

1. generate fresh state, nonce, and PKCE verifier/challenge;
2. start a loopback callback at `127.0.0.1`;
3. authorize `dynamic_agent_client` with the stable `ext_agent_host_id`;
4. exchange the returned code using the issued `client_id`;
5. validate the ID-token signature, issuer, audience, expiry, and nonce;
6. on reauthorization, require the validated account subject to match the saved
   account;
7. require `chatgpt.tokens.use.direct`;
8. atomically write the credential record with Unix mode `0600`.

The credential JSON includes access, refresh, and retained ID tokens. Never log,
print, commit, paste, or store those token values in `.env`. For a remote VM,
complete the loopback flow on a local computer, keep the generated stable host
ID assigned to that deployment, and transfer the entire protected file over
SSH. Docker points `CHATGPT_CREDENTIALS_FILE` at
`/data/chatgpt_credentials.json`.
Credential JSON files are excluded from both Git and the Docker build context.

`ChatGPTSubscriptionClient.get_access_token()` refreshes within 90 seconds of
expiry. Refresh uses the issued client ID, rotating refresh token, and
`resource=https://api.openai.com/v1`, omitting `scope`. Refreshes are
serialized; the replacement access token, refresh token, expiry, scopes, and
timestamp are saved together atomically.

### Models

`CHATGPT_MODEL=auto` is the source default. Auto mode calls `GET /v1/models`,
keeps entries with `visibility == "list"`, preserves server order, and caches the
first slug for the process lifetime. `/models` shows the account-specific list.
`/setmodel <slug|auto>` changes the process value and persists
`CHATGPT_MODEL`. A natural-language `requested_model` is an exact slug used for
the analysis call only; no provider aliases are recognized.

### Responses request

`messages_to_responses_payload()` moves system/developer content into
`instructions` and keeps user/assistant turns in the `input` array. Every
request sets exactly the required subscription fields:

- `store: false`;
- `stream: true`;
- a model available to the signed-in account.

Do not add preview-unsupported fields such as `max_output_tokens`,
`temperature`, `top_p`, `metadata`, or `previous_response_id`. Each request
contains its complete required history.

`parse_responses_sse()` accumulates `response.output_text.delta`, handles
structured failed/incomplete events, and accepts success only after
`response.completed`. A stream ending without that terminal event is a
technical failure. A 401 triggers one forced OAuth refresh. Subscription usage
limit errors can arrive inside a 200 SSE stream and are classified as rate
limits.

`_call_llm_api_internal()` retries the selected model up to
`LLM_MAX_RETRIES`. It keeps separate request, rate-limit, success, validator
rejection, and technical-error counters. A first incomplete response appends the
partial assistant text when present plus one instruction to rewrite the whole
answer more compactly; it never delivers partial output. A per-call timeout
override is used for large combined-folder requests.

### Summary quality and logging

The processor prompt requires Russian, grounded output. A response validator
rejects suspicious artifacts and unsupported dates. Combined-folder calls also
reject missing/invented source links and false claims that a supplied
multi-source history contains only one source. The first repairable rejection
adds a focused correction turn.

The complete prompt and result are logged to `llm_traffic.log`; therefore that
file remains sensitive even though authorization/header/query forms are
redacted and HTTP-client loggers stay at `WARNING`. OAuth tokens are used only
inside `chatgpt_runtime.py` request headers and are never written to traffic
logs. Historical text logs can be processed with `scripts/scrub_logs.py`.

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
`target_name`, `folder_mode`, `period_type`, `period_value`, `query`,
`requested_model`, `mark_as_read`, `recurrence_type`, `time`, `interval_days`,
`weekday`, and `day_of_month`.

Important behavior:

- `_ensure_schema()` performs the known `requested_model` and `folder_mode`
  additive migrations and clears model overrides left by the removed provider
  runtimes without deleting the schedules;
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
Telegram's 4096-character limit. Analysis results use
`_split_summary_chunks()`: top-level numbered items, bullets, and headings are
treated as indivisible semantic blocks and packed into messages up to 3500
characters. Unnumbered results fall back to paragraph blocks. Raw fixed-length
splitting is used only when one semantic block is itself too large for a
message. Non-summary administrative output continues to use
`_split_text_chunks()`.

Mark-as-read happens only when `_process_chat_with_openai_result()` returns
`ok=True`, except for the documented unread/text-empty case. The helper converts
LLM failures into visible `❌` answer text with `ok=False`; per-chat and combined
flows must preserve unread state in that case. A Telegram acknowledgement
failure is logged but does not change the analysis result.

`current_context` is updated only after target resolution succeeds. It contains:

```python
{
    "target_type": "chat" | "folder",
    "target_name": str,
    "period_type": "days" | "hours" | "today" | "yesterday" | "last_messages" | "unread" | None,
    "period_value": int | None,
}
```

Context is global, intentionally single-admin, and lost at restart.

## 9. Configuration and paths

Use `env.example` as the variable inventory. Important path behavior:

- `.env` updates always target `Path(".env")`, relative to the process working
  directory;
- `CHATGPT_CREDENTIALS_FILE` points to the protected renewable OAuth session;
- `CHATGPT_MODEL` is `auto` or one exact slug returned by `/models`;
- `SESSION_NAME`, `SCHEDULES_FILE`, `LOG_FILE_PATH`, and
  `LLM_TRAFFIC_LOG_PATH` may be absolute or working-directory-relative;
- Docker sets all mutable paths to `/data` and bind-mounts
  `/data/srv/data/llmbot` from the host;
- deployment-specific server details live in root `AGENTS.md`.

The supported Python range is 3.11-3.13. The Docker image uses Python 3.12.

Fresh-host deployment is described in `docs/DEPLOYMENT.md`. Docker includes
`scripts/telegram_auth.py` and `scripts/deploy_check.py`. Run both with the main
service stopped and the same Compose mounts/environment. The login utility is
independent of `bot.py`'s short startup timeout and never starts summaries. The
preflight utility does not import `bot.py`, polls no updates, reads no history,
and executes no schedules. It may rotate OAuth credentials; avoid concurrent
credential consumers during migration.

`setup.sh` validates and repairs `venv`, reuses a healthy supported venv
interpreter when no compatible system Python is on `PATH`, supports `--dev` and
`--recreate`, and accepts `PYTHON_BIN`/`VENV_DIR` overrides. Forced recreation
still requires an external supported interpreter. `start.sh` refuses to launch
an unsupported or incomplete environment.

## 10. Tests and verification

Install both runtime and development requirements, then run:

```bash
python -m pytest
```

`pyproject.toml` enables terminal missing-line coverage and enforces 50% across
`bot`, `chatgpt_runtime`, `config`, and `schedule_runtime`.

Do not hand-edit a verification snapshot here. Report the complete suite result
from the current change in the handoff.

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

Preserve the distinctions between transport failures, OAuth failures,
subscription usage limits, incomplete streams, validator rejections, retry
rounds, and one-request exact-model overrides. Never accept a stream without
`response.completed`.

The tests in `tests/test_bot_llm_api.py` encode these distinctions explicitly.

### Change logging

Preserve both controls: network client loggers at `WARNING` and final-record
redaction on every application handler. OAuth credentials must never cross into
application/traffic log arguments. Test messages, traceback text, header/query
forms, historical scrubbing, and idempotency.

### Change folder behavior

Test explicit include/pinned peers, dynamic inclusion flags, exclusions, unread,
muted, archived, legacy Telethon filter results, timeouts, and error wrapping.

## 12. Known constraints

- `bot.py` is a large monolith; avoid creating new cross-module import cycles.
- Folder histories are loaded sequentially and large folders can take a long
  time.
- Entire selected histories are sent in one request per chat (`per_chat`) or one
  merged request for the whole folder (`combined`). There is no token-aware
  chunking or map/reduce summarization.
- Only text messages are analyzed.
- In-memory context is global and cannot safely support multiple admins.
- Runtime `.env` writes assume a single process and a writable working directory.
- ChatGPT plan use is subject to the signed-in account's shared subscription
  limits and current eligible model catalog.
- Schedule persistence rewrites all records and is designed for a small local
  schedule set, not concurrent multi-process access.
- Unit tests mock external services; production behavior also depends on Telegram
  session health, ChatGPT OAuth/session eligibility, shared limits, and timezone.
