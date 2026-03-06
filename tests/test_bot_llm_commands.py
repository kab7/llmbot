import asyncio
from types import SimpleNamespace

import bot
from llm_runtime import LLMRuntimeConfig


class DummyMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, parse_mode=None):
        self.replies.append((text, parse_mode))
        return self


class DummyUpdate:
    def __init__(self, user_id):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = DummyMessage()


class DummyContext:
    def __init__(self, args=None):
        self.args = args or []


def _reset_runtime(monkeypatch):
    monkeypatch.setattr(
        bot,
        "llm_runtime",
        LLMRuntimeConfig(
            "https://openrouter.ai/api/v1/chat/completions",
            "token-0000000000",
            "meta-llama/llama-3.3-70b-instruct:free",
        ),
    )
    monkeypatch.setattr(bot.config, "ADMIN_USER_ID", 1)


def test_seturl_setmodel_settoken_and_show(monkeypatch):
    _reset_runtime(monkeypatch)
    update = DummyUpdate(user_id=1)

    asyncio.run(bot.seturl_command(update, DummyContext(["https://example.com/v1"])))
    asyncio.run(bot.setmodel_command(update, DummyContext(["model/test"])))
    asyncio.run(bot.settoken_command(update, DummyContext(["new-token-123456"])))
    asyncio.run(bot.llmconfig_command(update, DummyContext()))

    reply_texts = [text for text, _ in update.message.replies]
    assert any("URL обновлен" in text for text in reply_texts)
    assert any("Модель обновлена" in text for text in reply_texts)
    assert any("Токен обновлен" in text for text in reply_texts)
    assert any("Текущие LLM настройки" in text for text in reply_texts)
    assert any("Fallback:" in text for text in reply_texts)


def test_set_commands_usage_and_validation(monkeypatch):
    _reset_runtime(monkeypatch)
    update = DummyUpdate(user_id=1)

    asyncio.run(bot.seturl_command(update, DummyContext([])))
    asyncio.run(bot.setmodel_command(update, DummyContext([])))
    asyncio.run(bot.settoken_command(update, DummyContext([])))
    asyncio.run(bot.seturl_command(update, DummyContext(["bad-url"])))

    texts = [text for text, _ in update.message.replies]
    assert any("Использование: /seturl" in text for text in texts)
    assert any("Использование: /setmodel" in text for text in texts)
    assert any("Использование: /settoken" in text for text in texts)
    assert any("URL должен начинаться" in text for text in texts)


def test_admin_only_blocks_non_admin(monkeypatch):
    _reset_runtime(monkeypatch)
    update = DummyUpdate(user_id=2)

    asyncio.run(bot.setmodel_command(update, DummyContext(["model/test"])))
    text, _ = update.message.replies[-1]
    assert text == "У вас нет доступа к этому боту."
