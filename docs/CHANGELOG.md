# Changelog

This file records repository changes, not planned features. Current runtime
behavior is defined by code and tests.

## Unreleased

### Added

- Completion-truncation handling for `finish_reason=length`/`max_tokens`:
  partial answers are rejected, one compact full-rewrite instruction is added,
  and exhaustion fails safely instead of delivering an unfinished summary.
- Authoritative combined-folder source manifests with loaded message counts,
  plus validation and one-turn repair for false model claims that only one
  source was supplied or that all other sources are absent.
- Configurable `LLM_MAX_OUTPUT_TOKENS` completion cap (default `4096`) in every
  provider payload, preventing large-context requests from reserving tens of
  thousands of output tokens at OpenRouter.
- Explicit `deepseek`/`дипсик` request alias that selects only configured
  DeepSeek candidates while preserving their provider routes and fallback order.
- Provider safety-filter classification from `finish_reason`, before citation
  validation and repair logic.
- Semantic splitting for long summaries: whole numbered news items, bullets,
  headings, or paragraphs are kept together across Telegram messages whenever
  an individual item fits.
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

- DeepSeek (`deepseek/deepseek-v3.2`) is now the source default primary model;
  production model order is configured separately through `.env`.
- Russian-language dates written literally in source history are now accepted
  by summary grounding checks, in addition to dates derived from timestamps and
  URL paths.
- Telegram-link hallucination checks now compare against every URL present in
  the supplied history rather than only canonical `Оригинал` markers; duplicate
  URLs remain valid and are ignored by set-based validation.
- Date validation now normalizes `YYYY-MM-DD` timestamps and `/YYYY/MM/DD/`
  source-URL paths, preventing grounded verbalized dates from being rejected.
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
