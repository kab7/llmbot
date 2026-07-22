# Changelog

This file records repository changes, not planned features. Current runtime
behavior is defined by code and tests.

## Unreleased

### Added

- Dedicated `COMBINED_LLM_REQUEST_TIMEOUT_SECONDS` setting (default 90 seconds)
  for large merged folder requests.
- One-turn citation repair after a combined answer omits exact source links or
  invents Telegram URLs; subsequent candidates receive the rejected answer and
  correction instruction.
- Final-record credential redaction for configured secrets, Telegram/OpenRouter
  tokens, Authorization values, query credentials, and traceback text.
- `scripts/scrub_logs.py` for safe in-place cleanup of historical text logs.
- Folder-wide `combined` mode: merge selected histories from all folder dialogs
  and execute one arbitrary LLM operation over the combined context.
- Original-post permalinks in combined history and output validation that
  requires real source URLs and rejects invented Telegram links.
- Calendar `yesterday` period and “каждое утро” daily-schedule recognition.
- Persistent `folder_mode` in the schedule SQLite schema with additive
  migration for existing databases.
- Canonical agent guide in `docs/AI_DEVELOPMENT.md`.
- Machine-readable parser-command and schedule-record schemas.
- Repository contract tests that compare code, schemas, SQLite columns,
  documented commands, `env.example`, and Docker runtime modules.

### Changed

- Rebuilt all documentation from current code behavior, including folders,
  unread boundaries, explicit mark-as-read, schedules, one-request model
  overrides, provider differences, persistence, and privacy.
- `setup.sh` is non-interactive, validates Python 3.11-3.13, repairs unhealthy
  environments, reuses a healthy venv when no compatible system Python is on
  `PATH`, supports `--dev` and `--recreate`, and preserves `.env`.
- `start.sh` validates interpreter compatibility and runtime imports before
  launching with `exec`.
- Runtime/dev dependencies now have compatibility upper bounds.
- Docker build compiles every runtime module.
- Compose declares explicit build configuration, init handling, graceful stop,
  environment mapping, and writable `.env`.
- Coverage now includes `schedule_runtime`.

### Fixed

- HTTP client INFO logs no longer expose Telegram Bot API tokens embedded in
  request URLs; runtime `/settoken` values are registered for redaction.
- “Вчера” now means the previous local calendar day; rolling 24 hours remains
  available through “за сутки”.
- Explicit mark-as-read no longer acknowledges chats when LLM analysis fails or
  every candidate response is rejected.
- Existing but broken `venv/` directories no longer pass setup/start checks.
- Removed documentation claims that Telethon sessions should be assumed
  encrypted or that chat content is never stored locally despite full LLM
  traffic logging.

## 1.2.0 - 2026-03-06

### Changed

- Removed the previous provider-specific LLM integration.
- Moved LLM calls to OpenRouter-compatible Chat Completions.
- Added mutable URL, token, and model configuration.

### Added

- `/llmconfig`
- `/seturl`
- `/settoken`
- `/setmodel`
- Initial automated tests and coverage configuration.

## 1.0.0 - 2025-12-10

### Added

- Initial single-admin Telegram chat analyzer.
- Telegram Bot API control plane and Telethon user client.
- Natural-language chat parsing and LLM analysis.
- Days, hours, today, and last-N-message selection.
- Fuzzy chat search.
- In-memory target/period context.
- `.env` credential loading and Telethon session persistence.

## Reporting changes

Repository: [kab7/llmbot](https://github.com/kab7/llmbot).

Bug reports should include sanitized reproduction steps, expected and actual
behavior, Python version, deployment mode, and safe log excerpts. Never attach
`.env`, Telegram sessions, schedule databases, or raw `llm_traffic.log`.
