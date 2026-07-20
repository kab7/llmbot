#!/usr/bin/env python3
"""Remove credentials from existing text logs without printing secret values."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Iterable


REDACTION_MARKER = "[REDACTED]"
TELEGRAM_BOT_TOKEN_PATTERN = re.compile(
    r"\d{5,}:[A-Za-z0-9_-]{20,}"
)
AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?"
    r"(?:bearer|api-key)\s+)[^\s\"',}]+"
)
SECRET_QUERY_VALUE_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|token)="
    r")[^&#\s\"']+"
)
OPENROUTER_TOKEN_PATTERN = re.compile(r"sk-or-v1-[A-Za-z0-9_-]+")
SENSITIVE_ENV_SUFFIXES = ("_TOKEN", "_API_KEY", "_API_HASH")


def load_env_secrets(path: Path) -> set[str]:
    if not path.exists():
        return set()

    secrets: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key.endswith(SENSITIVE_ENV_SUFFIXES) and len(value) >= 8:
            secrets.add(value)
    return secrets


def redact_text(text: str, secrets: Iterable[str]) -> tuple[str, int]:
    redacted = text
    replacements = 0

    for pattern, replacement in (
        (TELEGRAM_BOT_TOKEN_PATTERN, REDACTION_MARKER),
        (OPENROUTER_TOKEN_PATTERN, REDACTION_MARKER),
        (AUTHORIZATION_VALUE_PATTERN, rf"\1{REDACTION_MARKER}"),
        (SECRET_QUERY_VALUE_PATTERN, rf"\1{REDACTION_MARKER}"),
    ):
        redacted, count = pattern.subn(replacement, redacted)
        replacements += count

    for secret in sorted(set(secrets), key=len, reverse=True):
        count = redacted.count(secret)
        if count:
            redacted = redacted.replace(secret, REDACTION_MARKER)
            replacements += count

    return redacted, replacements


def scrub_file(path: Path, secrets: Iterable[str]) -> int:
    original = path.read_text(encoding="utf-8", errors="replace")
    redacted, replacements = redact_text(original, secrets)
    if not replacements or redacted == original:
        return 0

    file_stat = path.stat()
    temp_path = path.with_name(f".{path.name}.redact.tmp")
    temp_path.write_text(redacted, encoding="utf-8")
    os.chmod(temp_path, file_stat.st_mode)
    try:
        os.chown(temp_path, file_stat.st_uid, file_stat.st_gid)
    except PermissionError:
        pass
    os.replace(temp_path, path)
    return replacements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="dotenv file used only to load values that must be redacted",
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    secrets = load_env_secrets(args.env_file)
    total_replacements = 0
    failed = False
    for path in args.paths:
        try:
            replacements = scrub_file(path, secrets)
        except (OSError, UnicodeError) as exc:
            print(f"{path}: ERROR: {exc}")
            failed = True
            continue
        total_replacements += replacements
        print(f"{path}: {replacements} replacement(s)")

    print(f"Total replacements: {total_replacements}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
