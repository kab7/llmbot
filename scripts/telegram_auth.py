#!/usr/bin/env python3
"""Create/check the Telethon user session without starting bot polling or jobs."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from telethon import TelegramClient


class TelegramSetupError(RuntimeError):
    pass


def session_file(session_name: str) -> Path:
    name = str(session_name or '').strip()
    if not name:
        raise TelegramSetupError('SESSION_NAME must not be empty')
    return Path(name if name.endswith('.session') else f'{name}.session')


def protect_session(path: Path) -> None:
    for suffix in ('', '-journal', '-wal', '-shm'):
        candidate = Path(f'{path}{suffix}')
        if candidate.is_file():
            candidate.chmod(0o600)


async def authorize(
    session_name: str,
    api_id: int,
    api_hash: str,
    phone: str,
) -> Path:
    if api_id <= 0 or not api_hash or not phone:
        raise TelegramSetupError('TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_PHONE are required')
    path = session_file(session_name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect_session(path)
    client = TelegramClient(session_name, api_id, api_hash, receive_updates=False)
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=30)
        if not authorized:
            if not sys.stdin.isatty():
                raise TelegramSetupError('An interactive terminal is required for Telegram login')
            await client.start(
                phone=phone,
                code_callback=lambda: getpass.getpass('Telegram login code: '),
                password=lambda: getpass.getpass('Telegram 2FA password (if enabled): '),
            )
        identity = await asyncio.wait_for(client.get_me(), timeout=30)
        if identity is None or getattr(identity, 'bot', False):
            raise TelegramSetupError('The Telethon session must belong to a Telegram user account')
    finally:
        await client.disconnect()
        protect_session(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config

    os.umask(0o077)
    try:
        path = asyncio.run(authorize(
            config.SESSION_NAME,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
            config.TELEGRAM_PHONE,
        ))
    except TelegramSetupError as exc:
        print(f'Telegram setup failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        # Network exception text can include authentication material.
        print(f'Telegram setup failed ({type(exc).__name__})', file=sys.stderr)
        return 1
    print(f'Telegram user session ready: {path}')
    print('No bot polling or scheduled summaries were started.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
