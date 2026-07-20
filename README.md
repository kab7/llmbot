# Telegram Chat Analyzer Bot

Single-admin Telegram bot that reads chat history through Telethon and answers
questions or produces summaries through an OpenAI-compatible LLM API.

Repository: [kab7/llmbot](https://github.com/kab7/llmbot)

The Python code is the behavioral source of truth. Agent-oriented implementation
documentation is in [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md), and
machine-readable derived contracts are in [docs/SCHEMAS.md](docs/SCHEMAS.md).

## Current capabilities

- Analyze one Telegram chat, private dialog, group, or channel.
- Analyze all dialogs matched by a Telegram folder.
- Select the last N days, hours, messages, today from local midnight, or unread
  messages.
- Optionally acknowledge processed chats as read when explicitly requested.
- Reuse the last successfully resolved target and period as in-memory context.
- Use ordered primary and fallback LLM model lists with retries, free-model
  pacing, and response-quality validation.
- Override the analysis model for one natural-language request.
- Create daily, weekly, monthly, and every-N-days schedules from natural
  language.
- Persist schedules in SQLite and runtime LLM changes in `.env`.
- Restrict every Telegram handler to `ADMIN_USER_ID`.

Only text messages are analyzed. Selected history is sent to the configured LLM
provider as one request; there is currently no token-aware chunking.

## Quick start

Requirements: Python 3.11-3.13. Production uses Python 3.12.

```bash
git clone https://github.com/kab7/llmbot.git
cd llmbot
./setup.sh --dev
```

`setup.sh` finds a supported Python, creates or repairs `venv`, installs runtime
dependencies, optionally installs test dependencies with `--dev`, and creates
`.env` from `env.example` only when `.env` does not exist.

Fill at least these required values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+79991234567
ADMIN_USER_ID=...
```

At least one LLM token is needed to analyze messages. It may be configured
before startup:

```dotenv
PRIMARY_LLM_URL=https://openrouter.ai/api/v1/chat/completions
PRIMARY_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
PRIMARY_LLM_API_KEY=...
```

or later in Telegram:

```text
/settoken primary <token>
```

Start the bot:

```bash
./start.sh
```

On the first Telethon authorization, enter the Telegram login code and 2FA
password if requested. The resulting session file is sensitive account
authentication material.

Detailed installation and repair instructions:
[docs/INSTALL.md](docs/INSTALL.md).

## Docker

The image uses Python 3.12 and compiles all runtime modules during build.
Compose stores mutable state under `/data` in the container.

```bash
docker compose up -d --build
```

The current production host uses the legacy command:

```bash
docker-compose up -d --build
```

The checked-in Compose file maps host `/data/srv/data/llmbot` to container
`/data`, mounts `.env` read/write, and sets `TZ=Europe/Moscow`.

## Telegram commands

| Command | Behavior |
| --- | --- |
| `/start` | Show introduction and current LLM settings. |
| `/help` | Show commands and examples. |
| `/folders` | List Telegram folders returned by Telethon. |
| `/context` | Show the in-memory target and period. |
| `/reset` | Clear in-memory context. |
| `/llmconfig` | Show primary/fallback URL, model lists, and masked tokens. |
| `/limits [primary\|fallback]` | Call an OpenRouter-style key-limits endpoint; defaults to primary. |
| `/seturl [primary\|fallback] <url>` | Change and persist an endpoint; defaults to primary. |
| `/setmodel primary\|fallback <model[,model2,...]>` | Change and persist an ordered model list; scope is required. |
| `/settoken [primary\|fallback] <token>` | Change and persist a token; defaults to primary. |
| `/schedules` | List persisted periodic jobs. |
| `/delschedule <id>` | Delete a persisted job and its in-process scheduler entry. |

All commands and free-text requests are admin-only.

## Natural-language examples

Single chat:

```text
Суммаризируй чат Работа за неделю
Что сегодня писали в личке с Иваном?
Покажи последние 500 сообщений из чата Release
Суммаризируй непрочитанные в чате Поддержка и отметь как прочитанные
```

Folder:

```text
Суммаризируй непрочитанные во всех чатах папки AI
Что решили в папке Проекты за последние 3 дня?
Суммаризируй папку Новости и отметь как прочитанные
```

One-request model override:

```text
Суммаризируй папку AI с помощью anthropic/claude-opus-4.6
```

Schedule:

```text
Суммаризируй папку AI каждый день в 20:00
Суммаризируй чат Работа каждую неделю в 09:00
Суммаризируй папку Новости раз в 3 дня в 19:30
```

A recurring request is saved instead of running immediately. Weekly and monthly
jobs are anchored to the local weekday/day on which they are created.

More examples: [docs/EXAMPLES.md](docs/EXAMPLES.md).

## Period semantics

| Parsed period | Selection |
| --- | --- |
| `days=N` | Current UTC time minus N × 24 hours. “Вчера” and “за сутки” both map to the last 24 hours. |
| `hours=N` | Current UTC time minus N hours. |
| `today` | Local midnight through now. |
| `last_messages=N` | Latest N messages, returned to the LLM chronologically. |
| `unread` | Messages above the known `read_inbox_max_id`, limited by unread count or the default. |
| omitted | Context period when available; otherwise latest 300 messages. |

Folder membership follows Telegram filter include/pinned peers, dynamic type
flags, and read/muted/archive exclusions. Chat and folder fuzzy matches are
accepted at similarity `>= 0.5`.

## LLM behavior

Primary candidates are tried in configured order for all retry rounds before
fallback scope begins. Free models use independent primary/fallback pacing and
429 backoff. Provider responses can be rejected for suspicious artifacts,
unsupported dates, or other quality problems.

Yandex Cloud uses:

```dotenv
PRIMARY_LLM_URL=https://ai.api.cloud.yandex.net/v1/chat/completions
PRIMARY_LLM_MODEL=gpt://<folder_id>/<model>
PRIMARY_LLM_API_KEY=...
```

The bot then sends `Authorization: Api-Key` and derives `x-folder-id` from the
model URI. `/limits` is not supported for Yandex Cloud.

## Runtime files and privacy

Ignored runtime state:

- `.env`: Telegram and LLM credentials;
- `*.session`: Telethon authorization;
- `schedules.db`: periodic jobs and original queries;
- `bot.log`: application log;
- `llm_traffic.log`: complete LLM payloads and responses;
- `venv/`: local Python environment.

`llm_traffic.log` contains chat history sent to providers. It is not a sanitized
audit log. Do not publish logs, `.env`, session files, or schedule databases.

Runtime LLM commands update `.env` atomically. In-memory context is lost on
restart. Schedules survive restart.

## Development

```bash
./setup.sh --dev
venv/bin/python -m pytest
```

Pytest enforces configured coverage of at least 50% across `bot`, `config`,
`llm_runtime`, and `schedule_runtime`.

Documentation index:

- [AI development guide](docs/AI_DEVELOPMENT.md)
- [Schemas](docs/SCHEMAS.md)
- [Quick start](docs/QUICKSTART.md)
- [Installation and venv repair](docs/INSTALL.md)
- [Examples](docs/EXAMPLES.md)
- [FAQ](docs/FAQ.md)
- [Project structure](docs/PROJECT_STRUCTURE.md)
- [Changelog](docs/CHANGELOG.md)

License: MIT.
