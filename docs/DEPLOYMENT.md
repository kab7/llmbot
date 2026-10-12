# Fresh Docker host and disaster recovery

Use this runbook for a replacement server. Code comes from Git; runtime state
does not. The operator must supply the replacement host/user and authorize
credential transfer to that host before deployment. Inspect `/etc/os-release`
before installing OS-specific Docker packages.

## Deployment layout

- Checkout: `/root/llmbot` (adjust ownership/path if the supplied login is not root).
- Compose service/container: `bot` / `llmbot`.
- Persistent host directory: `/data/srv/data/llmbot`, mounted at `/data`.
- Timezone: `Europe/Moscow`.
- Default for this recovery: `CHATGPT_MODEL=gpt-6.1-sol`, subject to a successful
  account-specific model catalog check.
- No incoming application port is required: the bot uses Telegram polling and
  outbound Telegram/OpenAI connections. SSH is needed for administration.

The former server `146.103.100.159` was reported unavailable on 2026-10-13.
Do not treat it as a working deployment destination. The replacement address is
pending; update `AGENTS.md` after the replacement is verified running.

## 1. Install Docker and obtain code

Install Docker Engine and the Compose CLI plugin using the instructions for the
actual OS: [Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Debian](https://docs.docker.com/engine/install/debian/), or
[other supported Linux systems](https://docs.docker.com/engine/install/).
Prefer `docker compose` on a fresh installation. Existing legacy installations
may use `docker-compose` with the same service/file.

Verify and enable the daemon on a systemd host:

```bash
docker version
docker compose version
systemctl enable --now docker
```

From the supplied privileged server shell:

```bash
git clone https://github.com/kab7/llmbot.git /root/llmbot
cd /root/llmbot
git rev-parse HEAD
install -d -m 700 /data/srv/data/llmbot
cp env.example .env
chmod 600 .env
```

If the checkout or `.env` already exists, inspect it and preserve it rather than
re-running clone/copy over existing state. Compare the checked-out commit with
GitHub before building.

## 2. Configure the same bot/account

Reuse the confirmed values for `TELEGRAM_BOT_TOKEN`, `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, and `ADMIN_USER_ID` from a protected local
or recovered `.env`. The bot token remains separate from the Telethon user
session. A server replacement does not itself require making a new BotFather
bot or changing the admin ID.

Build the new `.env` from the canonical `env.example`; transfer only applicable
values from an older file. Do not reintroduce removed provider variables.
Set:

```dotenv
CHATGPT_CREDENTIALS_FILE=/data/chatgpt_credentials.json
CHATGPT_MODEL=gpt-6.1-sol
```

Compose supplies the persistent session/database/log paths. Validate without
printing resolved secret environment values:

```bash
docker compose config --quiet
docker compose build
```

Do not use plain `docker compose config` in shared output because it expands
`env_file` secrets.

## 3. Restore or authorize the Telegram user

Keep the main service stopped while using session files:

```bash
docker compose stop bot
docker compose run --rm --no-deps bot python scripts/telegram_auth.py
```

Run from an interactive SSH terminal. The command uses Compose's persistent
`SESSION_NAME=/data/telethon_session`, prompts for the Telegram code and optional
2FA password without echo, verifies a user account, and disconnects. It never
starts bot polling or schedules, and avoids the main application's short startup
timeout while the operator enters a code. Session files are protected with mode
`0600`.

If a current, authorized session is recoverable, restore it securely before the
command; the command then validates it without sending a new login code. Do not
copy a live SQLite session database from an active process.

## 4. Authorize ChatGPT for the replacement host

The latest rotating refresh token was held by the former server. A laptop copy
may be stale, so do not assume it can resume the old server session. Complete a
fresh browser authorization on the local computer using a separate output file:

```bash
cd ~/Projects/llmbot
venv/bin/python scripts/chatgpt_auth.py --output chatgpt_credentials_replacement.json
```

The new file receives a stable host ID assigned to this replacement deployment.
For later reauthorization of that same deployment, reuse its credential record
and host ID. Transfer the complete record to the confirmed host over SSH:

```bash
scp chatgpt_credentials_replacement.json USER@NEW_HOST:/data/srv/data/llmbot/chatgpt_credentials.json
ssh USER@NEW_HOST chmod 600 /data/srv/data/llmbot/chatgpt_credentials.json
```

Adjust transfer/privilege handling when `USER` cannot write the persistent
directory directly. The server owns subsequent refreshes; do not run another
bot instance using the laptop copy. Never commit credentials or send their token
values through Telegram/chat. See OpenAI's
[self-hosted VM guidance](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms).

## 5. Recover schedules before starting

Preferred recovery source: the latest `/data/srv/data/llmbot/schedules.db` from
the old disk/snapshot, followed by a protected SQLite backup. Server backups from
the previous deployment were under `/root/llmbot-backups/`. These files are not
part of Git and were not downloaded locally by the previous deployment.

Copy a recovered consistent database with the service stopped and apply `0600`.
If only raw SQLite files are available, preserve any `-wal`/`-shm` files until
recovery is completed rather than copying only the main database from a live
snapshot. Do not replace an existing new database without backing it up.

The previous deployment restored three jobs, explicitly pinned to
`gpt-6.1-sol`, on 2026-10-11. Exact per-job IDs, targets, queries, times, and
`mark_as_read` values belong to the private runtime database, not this repository.
Confirm them from recovered state. If no recent database can be recovered,
recreate jobs only after their queries and read-marking intent are confirmed.
Do not silently substitute a March-era local database or infer permission to
mark channels read.

At startup stale `next_run` timestamps are advanced to the next occurrence, not
executed as catch-up summaries.

## 6. Check access and start

With the main service still stopped:

```bash
docker compose run --rm --no-deps bot python scripts/deploy_check.py --smoke-test
docker compose up -d --no-build bot
docker compose ps
docker compose logs --since 5m --tail 80 bot
```

The check requires valid config, an existing authorized user session, Bot API
`getMe` access, a usable ChatGPT OAuth session, the selected visible model, and
one completed `OK` inference. It neither polls Telegram nor executes schedules.
Errors are reported without raw exception bodies or tokens. OAuth refresh may
update the credential record; only the server should perform these checks after
the file has been transferred.

Confirm Telethon authorization, application readiness, absence of restart loops,
and the expected number of restored jobs. Then verify in Telegram:

```text
/llmconfig
/models
/folders
/schedules
Сделай одну общую сводку топ-10 новостей по всем каналам папки news за вчера
```

Compare the checked-out commit and runtime file hashes with the local/GitHub
revision, then update the replacement-host notes in `AGENTS.md`.

## 7. Keep recovery material outside this host

After verification, arrange a protected off-host backup of `.env`, current
ChatGPT credentials, Telethon session, and `schedules.db`. Stop the bot briefly
for a consistent session/database file copy, or use SQLite's online backup API
for the database while preserving session consistency. The traffic logs are not
needed for recovery. Do not put these backups in Git or public storage.
An on-host backup alone does not survive loss of the server/disk.
