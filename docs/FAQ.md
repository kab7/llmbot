# FAQ and operational notes

## Why does `start.sh` say the venv is broken?

It checks all of the following:

- `venv/bin/python` is executable;
- Python is 3.11-3.13;
- pip exists;
- APScheduler, dotenv, requests, python-telegram-bot, and Telethon import.

Repair:

```bash
./setup.sh --dev --recreate
```

`setup.sh` also repairs an unhealthy environment without `--recreate`.

## Which Python versions are supported?

Python 3.11-3.13. Docker uses 3.12. Other versions are not part of the tested
contract.

## Which configuration values are required?

Startup requires valid:

- `TELEGRAM_BOT_TOKEN`;
- positive `TELEGRAM_API_ID`;
- `TELEGRAM_API_HASH`;
- `TELEGRAM_PHONE`;
- positive `ADMIN_USER_ID`.

An LLM token is optional at startup because `/settoken` can set one later.
Analysis requires a primary or fallback token.

## Where do Telegram credentials come from?

- Bot token: BotFather `/newbot`.
- API ID/hash: `https://my.telegram.org` → API development tools.
- Admin user ID: the numeric ID of the one allowed Telegram user.

## What targets can be analyzed?

One user dialog, group, supergroup, channel, or a Telegram folder. The Telethon
account must be able to see the target.

`/folders` shows filters returned by Telegram. Folder processing includes
explicit/pinned peers and dynamic filter flags, then applies configured
read/muted/archive exclusions.

## How does fuzzy matching work?

Matching is case-insensitive and removes emoji:

- exact: 1.0;
- substring: 0.9;
- otherwise: `SequenceMatcher`;
- acceptance threshold: 0.5.

Because 0.5 is permissive, check the bot's visible recognized-command message.

## What periods are supported?

- `days`: last N × 24 hours;
- `hours`: last N hours;
- `today`: local midnight through now;
- `last_messages`: latest N messages;
- `unread`: messages after the known read boundary;
- omitted: inherited context period or latest 300 messages.

“Вчера” is currently parsed as the last 24 hours, not the previous calendar day.

## Are media and service messages analyzed?

No. `get_chat_history()` includes only messages with non-empty `message.text`.
Media-only and service events are ignored.

## Does reading history mark messages as read?

No. Read acknowledgement requires explicit wording such as “отметь как
прочитанные”. A deterministic guard clears an LLM-invented `mark_as_read=true`.

Known edge case: LLM errors are returned as answer text rather than raised, so an
explicit mark-as-read request can still acknowledge a chat after an analysis
failure.

## How does unread mode work?

For a known zero unread count, the chat is skipped. Otherwise the bot uses the
dialog unread count as a limit and `read_inbox_max_id` as the lower message-ID
boundary when Telegram provides it.

If Telegram reports unread messages but they contain no text, an explicit
mark-as-read request can acknowledge the chat without producing a summary.

## What does context remember?

Only target type/name and period type/value. It does not retain message history,
questions, or answers. It is global for the single admin and disappears on
restart.

## How do model lists and fallback work?

Comma-separated models form an ordered list. The bot exhausts all primary models
for all retry rounds before entering fallback scope. Free models use configured
pacing and growing 429 backoff.

Duplicate `(URL, model, token)` candidates are removed.

## What does a one-request model override do?

Natural wording such as “используй модель X” sets `requested_model` for the
analysis call only. The parser still uses configured models. The override is
tried three times without configured fallback.

## Why did a model response get rejected?

The response validator rejects known HTML/code artifacts, unexpected scripts,
excessive mixed-script corruption, boilerplate, and dates absent from selected
history. The next candidate is then tried.

## How does Yandex Cloud configuration differ?

Use a model URI containing the folder:

```dotenv
PRIMARY_LLM_URL=https://ai.api.cloud.yandex.net/v1/chat/completions
PRIMARY_LLM_MODEL=gpt://<folder_id>/<model>
PRIMARY_LLM_API_KEY=...
```

The bot uses `Authorization: Api-Key` and `x-folder-id`. `/limits` is only for
OpenRouter-style key endpoints and refuses Yandex Cloud.

## How are schedules stored and run?

Records are stored in `SCHEDULES_FILE` SQLite. APScheduler receives one-shot date
jobs; after a successful run the bot computes and registers the next one.

Supported recurrence:

- daily;
- weekly, anchored to creation weekday;
- monthly, anchored to creation day and clamped in shorter months;
- every N days.

Uncaught job-level errors retry after 300 seconds. Per-chat errors in a folder
are counted as skipped; the overall job still advances normally.

## Why did an overdue schedule not run at startup?

Startup recomputes stale `next_run` values. It does not replay missed executions.

## Where is runtime state stored?

Local defaults:

- `.env`;
- `telethon_session.session`;
- `schedules.db`;
- `bot.log`;
- `llm_traffic.log`;
- in-memory `current_context`.

Docker redirects session, schedule, and log paths to `/data`.

## Are Telethon sessions encrypted?

Do not rely on that assumption. Treat the session file as active Telegram
authentication material. Anyone who obtains usable session data may gain account
access.

## Are chat contents stored locally?

Selected history is not stored in a dedicated history database, but full LLM
request payloads and responses are written to `llm_traffic.log`. Schedules also
store the original query. Both are sensitive.

## Where are chat contents sent?

To the endpoint and model shown by `/llmconfig`, including fallback candidates
when used. Provider retention and privacy depend on that external service.

## Why is a folder request slow?

Dialogs are enumerated and folder chats are analyzed sequentially. Entire
selected text history is sent in one LLM request per chat. Reduce the period or
message count, or select one chat.

## What does HTTP 429 handling do?

The current candidate is skipped. Free models reserve a provider/scope pacing
slot and apply configured backoff. Remaining models are attempted according to
scope and retry order. Aggregate statistics show requests and 429 counts.

## How do I inspect logs safely?

- `bot.log`: lifecycle, Telegram operations, errors, model names, statistics.
- `llm_traffic.log`: complete payloads and responses.

Never attach raw `llm_traffic.log`, `.env`, or session files to a public issue.
Redact chat content, URLs with secrets, IDs, and tokens first.

## How do I update the bot?

```bash
git pull
./setup.sh --dev
venv/bin/python -m pytest
./start.sh
```

Docker:

```bash
docker compose up -d --build
```

The production host currently uses `docker-compose`.

## How do I report a problem?

Repository: [kab7/llmbot](https://github.com/kab7/llmbot).

Include sanitized reproduction steps, expected/actual behavior, Python version,
deployment mode, and safe excerpts from `bot.log`. Do not include credentials,
session data, schedule databases, or raw LLM traffic.
