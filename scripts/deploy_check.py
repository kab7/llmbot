#!/usr/bin/env python3
"""Check deployment access without polling Telegram or executing summaries."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from telegram import Bot
from telethon import TelegramClient

try:
    from .telegram_auth import session_file
except ImportError:
    from telegram_auth import session_file


class PreflightError(RuntimeError):
    pass


async def check_telegram_user(settings) -> None:
    if not session_file(settings.SESSION_NAME).is_file():
        raise PreflightError('Telegram user session is missing; run scripts/telegram_auth.py')
    client = TelegramClient(
        settings.SESSION_NAME, settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH,
        receive_updates=False,
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=30):
            raise PreflightError('Telegram user session is not authorized; run scripts/telegram_auth.py')
        identity = await asyncio.wait_for(client.get_me(), timeout=30)
        if identity is None or getattr(identity, 'bot', False):
            raise PreflightError('Telethon requires a Telegram user account, not a bot account')
    finally:
        await client.disconnect()


async def check_bot_api(settings) -> None:
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
        identity = await asyncio.wait_for(bot.get_me(), timeout=30)
        if not identity.is_bot:
            raise PreflightError('Bot API getMe did not identify a bot')


async def run_checks(settings, *, smoke_test: bool = False) -> None:
    required, _ = settings.get_config_issues()
    if required:
        raise PreflightError('Missing/invalid configuration: ' + ', '.join(key for key, _ in required))
    await check_telegram_user(settings)
    print('Telegram user session: OK')
    await check_bot_api(settings)
    print('Telegram Bot API: OK')

    from chatgpt_runtime import ChatGPTSubscriptionClient

    client = ChatGPTSubscriptionClient(settings.CHATGPT_CREDENTIALS_FILE, settings.CHATGPT_MODEL)
    models = await asyncio.to_thread(client.list_models)
    if not models:
        raise PreflightError('ChatGPT account has no visible models')
    selected_model = models[0]['slug'] if client.model.casefold() == 'auto' else client.model
    if selected_model not in {item['slug'] for item in models}:
        raise PreflightError(f'Configured ChatGPT model is not visible: {selected_model}')
    print(f'ChatGPT OAuth/model catalog: OK ({selected_model})')
    if smoke_test:
        response = await asyncio.to_thread(
            client.create_response,
            [{'role': 'user', 'content': 'Reply with exactly OK.'}],
            requested_model=selected_model,
            timeout=60,
        )
        if response.content.strip() != 'OK':
            raise PreflightError('ChatGPT smoke test did not return the expected OK response')
        print(f'ChatGPT completed inference: OK ({response.model})')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-test', action='store_true', help='Also perform a small ChatGPT inference request')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    try:
        asyncio.run(run_checks(config, smoke_test=args.smoke_test))
    except PreflightError as exc:
        print(f'Deployment check failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Deployment check failed ({type(exc).__name__}); check connectivity and authorization', file=sys.stderr)
        return 1
    print('Deployment checks passed. No polling or scheduled summaries were started.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
