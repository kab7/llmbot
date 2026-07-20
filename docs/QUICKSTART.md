# Quick start

This file mirrors the current startup and command paths in the code.

## 1. Clone and install

Use Python 3.11-3.13:

```bash
git clone https://github.com/kab7/llmbot.git
cd llmbot
./setup.sh --dev
```

Useful setup options:

```bash
./setup.sh --recreate       # force a clean venv
./setup.sh --dev --recreate # clean venv with test dependencies
PYTHON_BIN=python3.12 ./setup.sh --dev
```

The script automatically rebuilds a missing, broken, or unsupported `venv`.

## 2. Configure `.env`

`setup.sh` copies `env.example` only if `.env` does not already exist.

Required:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+79991234567
ADMIN_USER_ID=...
```

Configure at least one LLM token:

```dotenv
PRIMARY_LLM_URL=https://openrouter.ai/api/v1/chat/completions
PRIMARY_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
PRIMARY_LLM_API_KEY=...

FALLBACK_LLM_URL=https://openrouter.ai/api/v1/chat/completions
FALLBACK_LLM_MODEL=openrouter/free
FALLBACK_LLM_TOKEN=
```

An empty `FALLBACK_LLM_TOKEN` means the fallback has no token. If the variable is
absent, the startup value of `PRIMARY_LLM_API_KEY` is copied once; later
`/settoken primary` changes do not update that copy. Set the fallback token
explicitly when authenticated fallback is required. See `env.example` for retry,
pacing, logging, and data-path settings.

## 3. Start

```bash
./start.sh
```

`start.sh` refuses to launch when `venv` is missing, broken, outside Python
3.11-3.13, or lacks runtime imports. Repair it with `./setup.sh`.

On first Telethon authorization, enter the Telegram login code and optional 2FA
password. Preserve the generated session file.

## 4. Verify Telegram behavior

Send:

```text
/start
/folders
Суммаризируй чат Работа за неделю
О чем договорились?
/context
```

Folder and unread examples:

```text
Суммаризируй непрочитанные в папке AI
В папке news за вчера найти все упоминания складов WB
Сделай топ-10 новостей по всем непрочитанным каналам в папке news и отметь их прочитанными
Суммаризируй непрочитанные в чате Поддержка и отметь как прочитанные
```

The first folder request uses the default per-chat mode. The next two merge all
selected folder histories into one LLM context and return one answer with links
to original posts.

Schedule example:

```text
Суммаризируй папку AI каждый день в 20:00
Каждое утро в 10:00 сделай топ-10 новостей по всем каналам из папки news за вчера
/schedules
/delschedule <id>
```

## 5. Command inventory

- `/start`
- `/help`
- `/folders`
- `/context`
- `/reset`
- `/llmconfig`
- `/limits [primary|fallback]`
- `/seturl [primary|fallback] <url>`
- `/setmodel primary|fallback <model[,model2,...]>`
- `/settoken [primary|fallback] <token>`
- `/schedules`
- `/delschedule <id>`

`/seturl`, `/setmodel`, and `/settoken` persist changes to `.env`.

## 6. Run tests

```bash
venv/bin/python -m pytest
```

The suite includes command/schema/documentation contracts in addition to unit
tests for LLM behavior, Telethon selection, folders, unread state, and schedules.

Next:

- [Installation and recovery](INSTALL.md)
- [Examples](EXAMPLES.md)
- [FAQ](FAQ.md)
- [AI development guide](AI_DEVELOPMENT.md)
