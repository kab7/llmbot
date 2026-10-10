# FAQ

## Which Python versions are supported?

Python 3.11-3.13. Production uses Python 3.12. Run `./setup.sh --dev` to
create or repair `venv`.

## Which settings are required?

Telegram polling requires `TELEGRAM_BOT_TOKEN`, positive `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, and positive `ADMIN_USER_ID`. Analysis
also requires the protected file named by `CHATGPT_CREDENTIALS_FILE`.

## Can I use my ChatGPT subscription without an API key?

Yes, for an eligible ChatGPT Plus/Pro account. This repository uses the official
Sign in with ChatGPT OAuth flow and ChatGPT plan usage. It does not use a normal
OpenAI API key and it has no alternate LLM provider.

```bash
venv/bin/python scripts/chatgpt_auth.py
```

Approve `Continue with ChatGPT`. The generated record contains access, refresh,
and ID tokens. Treat the entire file as a secret.

## May I paste a token into Telegram or a support chat?

No. The bot deliberately has no `/settoken` command. Do not paste OAuth tokens
or the credential JSON into Telegram, Codex/ChatGPT chat, logs, issues, or
`.env`. Generate the file locally and transfer it over SSH for remote deploys.

## How does OAuth refresh work?

The bot refreshes shortly before access-token expiry using the issued client ID
and rotating refresh token. Replacement credentials are saved atomically with
owner-only permissions. Concurrent refreshes are serialized. If refresh is
revoked, rerun `scripts/chatgpt_auth.py`, replace the deployed file, and restart.

## Which model is used?

`CHATGPT_MODEL=auto` selects the first visible model in the signed-in account's
catalog. Use `/models`, `/setmodel <slug>`, or `/setmodel auto`. The catalog is
account/workspace specific. Natural wording such as “используй модель <slug>”
applies that exact slug to the analysis call only.

## Are subscription limits separate for the bot?

No. Plan usage is shared with the same ChatGPT account, including ChatGPT,
Codex, and other authorized apps. A limit error may arrive after streaming has
begun; the bot treats it as failure and never delivers partial text.

## What targets and periods are supported?

The bot analyzes one chat/channel or a Telegram folder. Folder `per_chat` mode
runs one result per dialog; `combined` merges source-labelled histories and runs
one cross-folder operation. Combined mode is selected for a common digest, top-N
or one search over the folder. Periods: today, yesterday, rolling N hours/days,
last N messages, unread, or the default latest-message limit.

## Does reading history mark messages read?

No. Mark-as-read requires explicit source wording and runs only after successful
analysis. A failed, incomplete, or rejected result leaves unread state unchanged.

## How are source links enforced?

Combined history adds canonical markdown links labelled `Оригинал` when Telegram can form a
permalink. The validator requires a supplied original link when linkable history
exists and rejects invented Telegram URLs. Public channels use
`t.me/<username>/<message_id>`; private channels/supergroups may use `t.me/c/...`;
some legacy chats and private dialogs have no usable permalink.

## Why was a response rejected?

Possible causes include: no terminal `response.completed`, failed/incomplete
SSE, subscription limits, empty output, suspicious artifacts, unsupported dates,
missing/invented source links, or a false single-source claim in combined mode.
The first incomplete response is retried with a full compact rewrite request.
The subscription route does not accept `max_output_tokens`, so the bot omits it.

## How are long Telegram results split?

The bot prefers whole numbered news items, headings, and bullets. It falls back
to length splitting only when one semantic item exceeds Telegram's limit.

## How do schedules work?

Natural-language recurrence supports daily, weekly, monthly, and every-N-days
jobs. SQLite persists combined/per-chat mode, exact model override, period,
query, and mark-as-read intent. Overrides left by the removed Yandex/OpenRouter
runtime are cleared automatically and the preserved schedule uses the current
ChatGPT default. If a newer exact ChatGPT slug is not visible to the account,
recreate that schedule or switch it to the default model.

## Where is runtime state stored?

- `.env`: paths/settings and Telegram credentials;
- `chatgpt_credentials.json`: renewable ChatGPT OAuth credentials;
- Telethon `*.session`: Telegram user authorization;
- `schedules.db`: recurring jobs;
- `bot.log`: operations/errors;
- `llm_traffic.log`: complete LLM prompts and outputs.

All are sensitive. The OAuth credential file and Telethon session grant account
access.

## Where are chat contents sent?

Selected text is sent to `https://api.openai.com/v1/responses` with
`store=false` and `stream=true`. Each request contains its required context. The
bot does not call ChatGPT backend-api endpoints.

## Why is a combined folder request slow?

Histories are loaded sequentially and merged into one potentially large request.
Combined analysis uses `COMBINED_LLM_REQUEST_TIMEOUT_SECONDS`; ordinary calls use
`LLM_REQUEST_TIMEOUT_SECONDS`. There is no token-aware chunking.

## How do I inspect logs safely?

`llm_traffic.log` contains private Telegram history even when credentials are
redacted. Never publish it raw. To scrub historical token-like values:

```bash
venv/bin/python scripts/scrub_logs.py bot.log llm_traffic.log
```

Review chat content, IDs, links, and names separately before sharing.

## How do I update and test?

```bash
git pull
./setup.sh --dev
venv/bin/python -m pytest
docker-compose build
docker-compose up -d
```

The credential and data volume must survive container recreation.
