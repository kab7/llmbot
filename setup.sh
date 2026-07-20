#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-venv}"
INSTALL_DEV=false
FORCE_RECREATE=false

usage() {
    echo "Usage: ./setup.sh [--dev] [--recreate]"
    echo "  --dev       install requirements-dev.txt"
    echo "  --recreate  rebuild the virtual environment even if it is healthy"
}

for arg in "$@"; do
    case "$arg" in
        --dev)
            INSTALL_DEV=true
            ;;
        --recreate)
            FORCE_RECREATE=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            usage
            exit 2
            ;;
    esac
done

is_supported_python() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) and sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1
}

find_python() {
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        if command -v "$PYTHON_BIN" >/dev/null 2>&1 && is_supported_python "$PYTHON_BIN"; then
            command -v "$PYTHON_BIN"
            return 0
        fi
        echo "PYTHON_BIN does not point to a supported Python 3.11-3.13: $PYTHON_BIN" >&2
        return 1
    fi

    local candidate
    for candidate in python3.12 python3.13 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && is_supported_python "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

venv_is_healthy() {
    [[ -x "$VENV_DIR/bin/python" ]] &&
        is_supported_python "$VENV_DIR/bin/python" &&
        "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1
}

PYTHON_CMD=""
if [[ "$FORCE_RECREATE" == false ]] &&
    [[ -z "${PYTHON_BIN:-}" ]] &&
    venv_is_healthy; then
    PYTHON_CMD="$VENV_DIR/bin/python"
else
    PYTHON_CMD="$(find_python || true)"
fi
if [[ -z "$PYTHON_CMD" ]]; then
    echo "Python 3.11-3.13 was not found."
    echo "Install Python 3.12 or set PYTHON_BIN to a compatible interpreter."
    exit 1
fi

echo "Using $("$PYTHON_CMD" --version) at $PYTHON_CMD"

if [[ "$FORCE_RECREATE" == true ]] || ! venv_is_healthy; then
    if [[ -d "$VENV_DIR" ]]; then
        echo "Rebuilding broken or incompatible virtual environment: $VENV_DIR"
    else
        echo "Creating virtual environment: $VENV_DIR"
    fi
    "$PYTHON_CMD" -m venv --clear "$VENV_DIR"
else
    echo "Reusing healthy virtual environment: $VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

if [[ "$INSTALL_DEV" == true ]]; then
    "$VENV_DIR/bin/python" -m pip install -r requirements-dev.txt
fi

if [[ ! -f .env ]]; then
    cp env.example .env
    chmod 600 .env
    echo "Created .env from env.example. Fill required Telegram/admin values."
else
    chmod 600 .env
    echo "Existing .env preserved."
fi

echo "Setup complete."
echo "Start the bot with ./start.sh"
if [[ "$INSTALL_DEV" == true ]]; then
    echo "Run tests with venv/bin/python -m pytest"
else
    echo "Install test dependencies later with ./setup.sh --dev"
fi
