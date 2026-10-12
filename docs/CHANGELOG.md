# Changelog

This file records repository changes. Current runtime behavior is defined by
Python code and tests.

## Unreleased

### Removed

- All Yandex Cloud and OpenRouter runtime integrations, configuration variables,
  model aliases, fallback scopes, free-model pacing, and endpoint/token commands.
- `/seturl` and `/settoken`; OAuth secrets are no longer accepted through
  Telegram or `.env`.
- The mutable provider module and its provider-specific tests.

### Added

- Fresh-host/disaster-recovery runbook, isolated hidden-input Telegram session
  setup, and non-polling deployment checks included in the Docker image.

- Official Sign in with ChatGPT OAuth authorization through
  `scripts/chatgpt_auth.py`: dynamic client registration, loopback callback,
  PKCE/state/nonce, ID-token validation, required plan scope, and owner-only
  atomic credential storage.
- `chatgpt_runtime.py` with serialized rotating-token refresh, account-specific
  model discovery, `auto` model selection, Responses payload conversion, SSE
  parsing, and mandatory `response.completed` enforcement.
- `/models` and the simplified `/setmodel <slug|auto>` command.
- Docker persistence for `/data/chatgpt_credentials.json`.
- Tests for OAuth credentials, refresh rotation, model discovery, preview-safe
  Responses fields, returning-account and deployment-host validation, streaming
  completion/failure, and bot retry behavior.
- Folder-wide combined analysis, original-post citation validation, authoritative
  source manifests, semantic Telegram message splitting, and persistent schedules.

### Changed

- ChatGPT Plus/Pro plan usage is now the only LLM route.
- All inference uses the public `POST /v1/responses` endpoint with `store=false`
  and `stream=true`; preview-unsupported output/token sampling fields are omitted.
- `CHATGPT_MODEL=auto` selects the first visible account model; exact per-request
  slugs remain supported.
- Incomplete output is retried with one full compact-rewrite instruction and is
  never delivered or followed by mark-as-read.
- Persisted Yandex/OpenRouter model overrides are cleared automatically while
  preserving the rest of each schedule, so existing jobs use the ChatGPT
  default after migration.
- Documentation, schemas, env inventory, Docker contracts, and tests now describe
  the subscription-only architecture.

### Security

- OAuth tokens live only in the protected credential JSON and request headers.
- Credential files are ignored by Git and must not be pasted into chats, logs,
  `.env`, issues, or source control.
- Local `.codex_tmp` deployment staging is excluded from Docker build context
  as well as Git.
- HTTP-client INFO logs remain suppressed; historical logs can be sanitized with
  `scripts/scrub_logs.py`.

## 1.2.0 - 2026-03-06

### Changed

- Replaced the original provider-specific integration with OpenAI-compatible
  Chat Completions and mutable URL/token/model configuration.

### Added

- Runtime LLM configuration commands.
- Initial automated tests and coverage configuration.

## 1.0.0 - 2025-12-10

### Added

- Initial single-admin Telegram chat analyzer.
- Telegram Bot API control plane and Telethon user client.
- Natural-language parsing and LLM analysis.
- Days, hours, today, and last-N-message selection.
- Fuzzy chat search and in-memory target/period context.

## Reporting changes

Repository: [kab7/llmbot](https://github.com/kab7/llmbot).

Bug reports should include sanitized reproduction steps, expected and actual
behavior, Python version, deployment mode, and safe log excerpts. Never attach
`.env`, ChatGPT OAuth credentials, Telegram sessions, schedule databases, or
raw `llm_traffic.log`.
