# Telegram Chat Analyzer Bot

Single-admin Telegram bot that reads chat history through Telethon and performs
free-form analysis with an eligible ChatGPT Plus/Pro subscription. It uses the
official Sign in with ChatGPT OAuth flow and the streaming Responses API; no LLM
API key or alternate provider is supported.

## Capabilities

- Analyze one chat or every dialog in a Telegram folder.
- Folder `per_chat` mode: one independent result per dialog.
- Folder `combined` mode: merge source-labelled histories and run one operation
  over the complete folder context.
- Support today, yesterday, rolling hours/days, last N messages, and unread.
- Include original Telegram post links in combined results and reject invented
  source links.
- Mark processed dialogs read only when explicitly requested and only after a
  successful analysis.
- Persist daily, weekly, monthly, and every-N-days jobs in SQLite.
- Split long output on semantic news/item boundaries.
- Restrict every Telegram handler to `ADMIN_USER_ID`.
- Discover models available to the signed-in ChatGPT account and select one
  globally or for a single request.
- Optional LOCAL Telegram MCP for Codex with short-lived SSH calls: search contacts/dialogs, read
  history without marking read, and send messages as the personal account using
  the bot's existing session. See [Telegram MCP](docs/TELEGRAM_MCP.md).

Selected history is sent to OpenAI in one request per chat or one merged request
per combined folder. There is currently no token-aware chunking or map/reduce.

## Install

Requirements: Python 3.11-3.13.

```bash
git clone https://github.com/kab7/llmbot.git
cd llmbot
./setup.sh --dev
cp env.example .env
```

Fill the Telegram/admin values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+79991234567
ADMIN_USER_ID=...
CHATGPT_CREDENTIALS_FILE=chatgpt_credentials.json
CHATGPT_MODEL=auto
```

## Authorize the ChatGPT subscription

Do not paste an access token or refresh token into Telegram, this chat, `.env`,
or source control. Generate a complete renewable credential record locally:

```bash
venv/bin/python scripts/chatgpt_auth.py
```

The script opens `Continue with ChatGPT`, uses a loopback OAuth callback,
validates the ID token and required plan-usage permission, and writes
`chatgpt_credentials.json` with owner-only permissions. Eligible ChatGPT
Plus/Pro plan usage and limits are shared with the same ChatGPT account.

Start the bot after authorization:

```bash
./start.sh
```

For a remote deployment, complete OAuth on a local computer, then copy the
protected file over SSH:

```bash
scp chatgpt_credentials.json root@SERVER:/data/srv/data/llmbot/chatgpt_credentials.json
ssh root@SERVER chmod 600 /data/srv/data/llmbot/chatgpt_credentials.json
```

The generated credential record, including its stable host ID, then belongs to
that server deployment. Do not reuse the same file for another running host.

Docker maps that host file to `/data/chatgpt_credentials.json` via
`CHATGPT_CREDENTIALS_FILE`. The container refreshes access tokens and rotates
the saved refresh token automatically.

## Docker

Fresh host / server-loss recovery: [deployment runbook](docs/DEPLOYMENT.md).
Create or verify the Telegram user session before detached startup:

```bash
docker compose build
docker compose run --rm --no-deps bot python scripts/telegram_auth.py
docker compose run --rm --no-deps bot python scripts/deploy_check.py --smoke-test
docker compose up -d --no-build
```

```bash
docker-compose build
docker-compose up -d
docker-compose logs -f bot
```

Mutable state is stored in the host directory mounted at
`/data/srv/data/llmbot`. The first Telethon authorization is easiest locally;
copy the resulting session only over a trusted channel.

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Show usage examples and ChatGPT status. |
| `/help` | Show command help. |
| `/folders` | List Telegram folders. |
| `/context` | Show the in-memory target and period. |
| `/reset` | Clear in-memory context. |
| `/llmconfig` | Show the fixed Responses endpoint, model, credential path, and OAuth status. |
| `/models` | List visible models for the signed-in ChatGPT account. |
| `/setmodel <slug\|auto>` | Select one model or account-order auto selection; persists to `.env`. |
| `/schedules` | List persisted periodic jobs. |
| `/delschedule <id>` | Delete a persisted job. |

The bot intentionally has no `/settoken` or endpoint-switching command.

## Natural-language examples

Single chat:

```text
Суммаризируй чат Работа за неделю
Покажи последние 500 сообщений из чата Проект
Суммаризируй непрочитанные в чате Работа и отметь как прочитанные
```

Folder, separate results:

```text
Суммаризируй каждый канал отдельно в папке AI за вчера
```

Folder, one shared context:

```text
Сделай одну общую сводку топ-10 новостей по всем каналам папки news за вчера
В папке news за вчера найди все упоминания складов WB
Сделай топ-10 новостей по всем непрочитанным каналам в папке news и отметь их прочитанными
```

Schedule:

```text
Каждое утро в 10:00 сделай одну общую сводку топ-10 новостей по всем каналам папки news за вчера
```

Exact model for one analysis:

```text
Суммаризируй папку AI с помощью <slug из /models>
```

If no target or period is supplied, the last successfully resolved values are
reused until restart or `/reset`.

## ChatGPT runtime behavior

- `CHATGPT_MODEL=auto` selects the first visible slug returned by the signed-in
  account's `/v1/models` catalog.
- Each inference uses `POST https://api.openai.com/v1/responses` with
  `store=false` and `stream=true`.
- System content is sent as `instructions`; user/assistant history is supplied
  in the `input` array.
- Only `response.completed` is accepted as success. Failed, incomplete, or
  interrupted streams are retried; partial output is never delivered.
- OAuth access tokens refresh automatically. A revoked session requires running
  `scripts/chatgpt_auth.py` again.
- Plus/Pro subscription limits are shared with ChatGPT, Codex, and other apps
  using the same plan.

## Runtime files and privacy

Never commit or publish:

- `.env`;
- `chatgpt_credentials.json`;
- Telethon `*.session` files;
- `schedules.db`;
- `bot.log` and `llm_traffic.log`.

`llm_traffic.log` contains full selected Telegram history, prompts, and model
output. Authorization/header/query forms are redacted and HTTP-client INFO logs
are suppressed, but the chat content remains sensitive.

## Development

Read [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md) before behavior changes.
Python code is the behavioral source of truth; update schemas, tests, and docs in
the same change.

```bash
./setup.sh --dev
venv/bin/python -m pytest
```

Documentation:

- [Quick start](docs/QUICKSTART.md)
- [Installation](docs/INSTALL.md)
- [Examples](docs/EXAMPLES.md)
- [FAQ](docs/FAQ.md)
- [Schemas](docs/SCHEMAS.md)
- [Project structure](docs/PROJECT_STRUCTURE.md)
- [Changelog](docs/CHANGELOG.md)

License: MIT.
