# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram Chat Analyzer Bot - analyzes and summarizes Telegram chat history using AI. Single-user bot that provides chat summarization, free-form questions about chat content, and unread channel overview.

**Language:** Python 3.11-3.13 (3.14 not supported due to library incompatibilities)

## Commands

```bash
# Setup (one-time)
./setup.sh

# Run the bot
./start.sh
# or manually:
source venv/bin/activate && python bot.py

# Validate configuration
python check_config.py
```

## Architecture

### Data Flow
```
User Message → Telegram Bot API → parse_command_with_gpt() → DeepSeek (parse) →
find_chat_by_name() (Telethon) → get_chat_history() (Telethon) →
process_chat_with_openai() → Alice AI LLM → Telegram Response
```

### Key Components

- **bot.py** - Main application (Telegram Bot API + Telethon + LLM integration)
- **config.py** - Configuration, LLM endpoints, system prompts
- **masker.py** / **parser.py** / **pii_token.py** - PII masking before LLM processing

### LLM Models (via Yandex Eliza API)
- **Parser:** DeepSeek v3.1 Terminus - parses natural language commands into JSON
- **Processor:** Alice AI LLM (235B) - analyzes and summarizes chat histories

### Key Functions in bot.py

| Function | Purpose |
|----------|---------|
| `call_llm_api()` | Universal LLM API interface |
| `parse_command_with_gpt()` | Parses user input into structured JSON |
| `find_chat_by_name()` | Fuzzy chat search (50%+ similarity threshold) |
| `get_chat_history()` | Loads messages for various periods |
| `process_chat_with_openai()` | Sends chat history to LLM for analysis |

### Global State
- `current_context` - Stores last chat/period for follow-up questions (in-memory only)
- `telethon_client` - Persistent Telethon connection

## Configuration

Environment variables in `.env` (copy from `env.example`):
- `TELEGRAM_BOT_TOKEN` - Bot token from BotFather
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` - From my.telegram.org
- `TELEGRAM_PHONE` - Phone number for Telethon
- `ELIZA_TOKEN` - OAuth token for Yandex Eliza API
- `ADMIN_USER_ID` - Your Telegram user ID (admin-only access)

## Design Decisions

- **Single-user by design:** `@admin_only` decorator restricts all commands to `ADMIN_USER_ID`
- **PII protection:** Phone numbers and emails masked before sending to LLM
- **Async throughout:** Full async/await architecture
- **Context in memory:** Lost on restart (no database)
- **Fixed models:** LLM models configured in config.py, not user-selectable
