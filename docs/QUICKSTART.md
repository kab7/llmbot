# Quick start

## 1. Install

```bash
git clone https://github.com/kab7/llmbot.git
cd llmbot
./setup.sh --dev
cp env.example .env
```

Supported Python: 3.11-3.13.

## 2. Configure Telegram and paths

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+79991234567
ADMIN_USER_ID=...
CHATGPT_CREDENTIALS_FILE=chatgpt_credentials.json
CHATGPT_MODEL=auto
```

See `env.example` for timeouts, logging, and data paths.

## 3. Authorize ChatGPT Plus/Pro

Run on a machine with a browser:

```bash
venv/bin/python scripts/chatgpt_auth.py
```

Approve `Continue with ChatGPT`. The script writes a renewable
`chatgpt_credentials.json` with permission mode `0600`. Do not copy token
values from that JSON into `.env`, Telegram, or a chat.

For Docker on a remote server, transfer the complete protected file:

```bash
scp chatgpt_credentials.json root@SERVER:/data/srv/data/llmbot/chatgpt_credentials.json
ssh root@SERVER chmod 600 /data/srv/data/llmbot/chatgpt_credentials.json
```

Treat that generated credential file and its stored host ID as belonging to the
server deployment; do not run a second local bot instance from the same file.

The container uses `CHATGPT_CREDENTIALS_FILE=/data/chatgpt_credentials.json`
and refreshes the OAuth session automatically.

## 4. Start

```bash
./start.sh
```

First Telethon startup may request a Telegram login code. Later starts reuse the
session file.

## 5. Verify

In Telegram:

```text
/llmconfig
/models
/folders
Сделай одну общую сводку папки news за вчера
```

Expected:

- `/llmconfig` says the ChatGPT OAuth session is connected;
- `/models` returns slugs visible to the signed-in account;
- the analysis response ends with the actual model slug;
- combined folder results cite original Telegram posts when links are available.

## 6. Commands

- `/start`
- `/help`
- `/folders`
- `/context`
- `/reset`
- `/llmconfig`
- `/models`
- `/setmodel <slug|auto>`
- `/schedules`
- `/delschedule <id>`

`/setmodel` persists `CHATGPT_MODEL` to `.env`. OAuth credentials are never
accepted through a bot command.

## 7. Tests

```bash
venv/bin/python -m pytest
```

For architecture and invariants, read [AI_DEVELOPMENT.md](AI_DEVELOPMENT.md).
