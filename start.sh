#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-venv}"
PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]] ||
    ! "$PYTHON" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1 ||
    ! "$PYTHON" -c 'import apscheduler, dotenv, requests, telegram, telethon' >/dev/null 2>&1; then
    echo "Virtual environment is missing, broken, incompatible, or incomplete."
    echo "Repair it with: ./setup.sh"
    exit 1
fi

exec "$PYTHON" bot.py
