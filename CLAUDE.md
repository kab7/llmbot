# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram Chat Analyzer Bot - analyzes and summarizes Telegram chat history using AI. Single-user bot that provides chat summarization and free-form questions about chat content.

**Language:** Python 3.11-3.13 (3.14 not supported due to library incompatibilities)

## Commands

```bash
# Setup (one-time)
./setup.sh

# Run the bot
./start.sh
# or manually:
source venv/bin/activate && python bot.py

# Configuration is validated on bot startup
```

## Architecture

### Data Flow
```
User Message → Telegram Bot API → parse_command_with_gpt() → Runtime LLM model →
find_chat_by_name() (Telethon) → get_chat_history() (Telethon) →
process_chat_with_openai() → Runtime LLM model → Telegram Response
```

### Key Components

- **bot.py** - Main application (Telegram Bot API + Telethon + LLM integration)
- **config.py** - Configuration, LLM endpoints, system prompts

### LLM Models (via OpenRouter-compatible API)
- **Runtime model:** configured via `DEFAULT_LLM_MODEL` in `config.py`
- Can be changed at runtime with `/setmodel`, `/seturl`, `/settoken`

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
- `PRIMARY_LLM_API_KEY` - API key for OpenRouter-compatible API (optional)
- `ADMIN_USER_ID` - Your Telegram user ID (admin-only access)

## Design Decisions

- **Single-user by design:** `@admin_only` decorator restricts all commands to `ADMIN_USER_ID`
- **Raw chat payload:** history is sent to LLM as-is (without local masking)
- **Async throughout:** Full async/await architecture
- **Context in memory:** Lost on restart (no database)
- **Runtime LLM config:** URL/model/token can be changed via chat commands
