# Installation, build, and recovery

## Supported runtime

- Python 3.11, 3.12, or 3.13.
- Python 3.12 is preferred and used by the Docker image.
- Runtime dependencies: `requirements.txt`.
- Test dependencies: `requirements-dev.txt`.

The setup script checks the interpreter numerically and will not select Python
3.10 or 3.14.

## Local installation

```bash
git clone https://github.com/kab7/llmbot.git
cd llmbot
./setup.sh --dev
```

`setup.sh`:

1. changes to the repository root, so it can be called from another directory;
2. reuses an existing healthy supported `venv` unless recreation or
   `PYTHON_BIN` was requested; otherwise selects `PYTHON_BIN` when supplied,
   then tries Python 3.12, 3.13, 3.11, and `python3`;
3. checks whether `venv/bin/python`, its version, and pip are healthy;
4. rebuilds an unhealthy environment with `python -m venv --clear`;
5. upgrades pip and installs runtime dependencies;
6. installs test dependencies only with `--dev`;
7. preserves an existing `.env`, or copies `env.example` when missing;
8. applies mode `600` to `.env`.

Options:

```bash
./setup.sh --dev
./setup.sh --recreate
./setup.sh --dev --recreate
./setup.sh --help
```

To force an exact interpreter:

```bash
PYTHON_BIN=/path/to/python3.12 ./setup.sh --dev --recreate
```

`VENV_DIR` can override the environment path for local experiments:

```bash
VENV_DIR=.venv-test ./setup.sh --dev
```

`venv/` is the supported default because `start.sh` uses it unless the same
`VENV_DIR` override is supplied.

## macOS Python installation

Homebrew:

```bash
brew install python@3.12
PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12" ./setup.sh --dev
```

An installer from [python.org](https://www.python.org/downloads/) is also valid.

Verify:

```bash
venv/bin/python --version
venv/bin/python -m pip check
```

## Repair a broken venv

Do not manually activate a venv whose Python symlink points to a removed
interpreter. Rebuild it:

```bash
./setup.sh --dev --recreate
```

The normal `./setup.sh` path also repairs a broken environment automatically.
`start.sh` performs its own version/import checks and prints the repair command
instead of attempting to run with a partial environment.

## Configuration

Copying is only necessary when setup has not already created `.env`:

```bash
cp env.example .env
chmod 600 .env
```

Required startup variables:

| Variable | Meaning |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot API token from BotFather. |
| `TELEGRAM_API_ID` | Positive integer from my.telegram.org. |
| `TELEGRAM_API_HASH` | API hash from my.telegram.org. |
| `TELEGRAM_PHONE` | Phone number for Telethon login. |
| `ADMIN_USER_ID` | Positive Telegram user ID allowed to use the bot. |

LLM tokens are optional at startup because `/settoken` can set one later, but
analysis requires at least one of `PRIMARY_LLM_API_KEY` or
`FALLBACK_LLM_TOKEN`.

The complete variable inventory and defaults are in `env.example`.

## First run

```bash
./start.sh
```

The first Telethon login can require interactive input. Complete it in an
interactive terminal before relying on an unattended service or container.

Generated session files grant Telegram-account access. Back them up securely,
never commit them, and do not describe them as application-encrypted secrets.

## Docker build

Validate and build:

```bash
docker compose config
docker compose build
docker compose up -d
```

Legacy production command:

```bash
docker-compose config
docker-compose build
docker-compose up -d
```

The Dockerfile:

- starts from `python:3.12-slim`;
- installs only runtime requirements;
- copies all four runtime Python modules;
- runs `compileall` during build;
- starts `python bot.py`.

Compose paths:

| Container path | Source |
| --- | --- |
| `/app/.env` | Repository `.env`, mounted read/write. |
| `/data/telethon_session*` | Host `/data/srv/data/llmbot`. |
| `/data/schedules.db` | Host `/data/srv/data/llmbot`. |
| `/data/bot.log` | Host `/data/srv/data/llmbot`. |
| `/data/llm_traffic.log` | Host `/data/srv/data/llmbot`. |

The container timezone is `Europe/Moscow`, which controls “today” and schedule
times.

## Tests

```bash
venv/bin/python -m pytest
```

Useful targeted runs:

```bash
venv/bin/python -m pytest --no-cov tests/test_repository_contracts.py
venv/bin/python -m pytest --no-cov tests/test_schedule_runtime.py
venv/bin/python -m pytest --no-cov tests/test_bot_llm_api.py
```

`--no-cov` avoids applying the global 50% threshold to a deliberately partial
suite. Always run the full suite with coverage before handoff.

## Common failures

### `Virtual environment is missing, broken, incompatible, or incomplete`

```bash
./setup.sh --dev --recreate
```

### No supported Python found

Install Python 3.12 or set `PYTHON_BIN` to a 3.11-3.13 interpreter.

### Dependency download fails

Check DNS, proxy, TLS inspection, and package-index access. Re-run setup after
network access is restored.

### Bot exits after reporting configuration issues

Read the missing keys in `bot.log` or console output and update `.env`. Startup
validation intentionally stops before Telegram polling when required Telegram or
admin values are invalid.

### First container run cannot authorize Telethon

Run it attached so the login code and 2FA prompt are visible, or create the
session in a controlled interactive environment using the same `SESSION_NAME`
path.
