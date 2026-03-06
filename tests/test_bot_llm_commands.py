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


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _reset_runtime(monkeypatch, env_path):
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
    monkeypatch.setattr(bot, "ENV_FILE", env_path)


def test_seturl_setmodel_settoken_and_show(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    _reset_runtime(monkeypatch, env_path)
    update = DummyUpdate(user_id=1)

    asyncio.run(bot.seturl_command(update, DummyContext(["https://example.com/v1"])))
    asyncio.run(
        bot.seturl_command(
            update, DummyContext(["fallback", "https://fallback.example/v1"])
        )
    )
    asyncio.run(bot.setmodel_command(update, DummyContext(["primary", "model/test"])))
    asyncio.run(
        bot.setmodel_command(update, DummyContext(["fallback", "fallback/model"]))
    )
    asyncio.run(bot.settoken_command(update, DummyContext(["new-token-123456"])))
    asyncio.run(
        bot.settoken_command(update, DummyContext(["fallback", "fallback-token-5555"]))
    )
    asyncio.run(bot.llmconfig_command(update, DummyContext()))

    reply_texts = [text for text, _ in update.message.replies]
    assert any("URL (primary) обновлен" in text for text in reply_texts)
    assert any("URL (fallback) обновлен" in text for text in reply_texts)
    assert any("Модель (primary) обновлена" in text for text in reply_texts)
    assert any("Модель (fallback) обновлена" in text for text in reply_texts)
    assert any("Токен (primary) обновлен" in text for text in reply_texts)
    assert any("Токен (fallback) обновлен" in text for text in reply_texts)
    assert any("Текущие LLM настройки" in text for text in reply_texts)
    assert any("Fallback:" in text for text in reply_texts)

    env_text = env_path.read_text(encoding="utf-8")
    assert "PRIMARY_LLM_URL=https://example.com/v1/chat/completions" in env_text
    assert "FALLBACK_LLM_URL=https://fallback.example/v1/chat/completions" in env_text
    assert "PRIMARY_LLM_MODEL=model/test" in env_text
    assert "FALLBACK_LLM_MODEL=fallback/model" in env_text
    assert "PRIMARY_LLM_API_KEY=new-token-123456" in env_text
    assert "FALLBACK_LLM_TOKEN=fallback-token-5555" in env_text


def test_set_commands_usage_and_validation(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(user_id=1)

    asyncio.run(bot.seturl_command(update, DummyContext([])))
    asyncio.run(bot.setmodel_command(update, DummyContext([])))
    asyncio.run(bot.settoken_command(update, DummyContext([])))
    asyncio.run(bot.setmodel_command(update, DummyContext(["model-only"])))
    asyncio.run(bot.setmodel_command(update, DummyContext(["primary"])))
    asyncio.run(bot.seturl_command(update, DummyContext(["bad-url"])))
    asyncio.run(bot.settoken_command(update, DummyContext(["fallback"])))

    texts = [text for text, _ in update.message.replies]
    assert any("/seturl <url>" in text for text in texts)
    assert any("/setmodel primary <model>" in text for text in texts)
    assert any("Укажи scope модели: primary или fallback" in text for text in texts)
    assert any("Значение model не указано" in text for text in texts)
    assert any("/settoken <token>" in text for text in texts)
    assert any("URL должен начинаться" in text for text in texts)
    assert any("Значение token не указано" in text for text in texts)


def test_limits_command_success_and_errors(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    bot.llm_runtime.set_fallback_url("https://fallback.example/v1")
    update = DummyUpdate(user_id=1)
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        if "openrouter.ai/api/v1/key" in url:
            return DummyResponse(
                status_code=200,
                payload={"data": {"label": "primary", "limit": 1000, "usage": 12}},
                text="",
            )
        return DummyResponse(status_code=503, payload={"error": "down"}, text="down")

    monkeypatch.setattr(bot.requests, "get", fake_get)

    asyncio.run(bot.limits_command(update, DummyContext([])))
    asyncio.run(bot.limits_command(update, DummyContext(["fallback"])))
    asyncio.run(bot.limits_command(update, DummyContext(["wrong"])))

    texts = [text for text, _ in update.message.replies]
    assert any("Лимиты (primary)" in text for text in texts)
    assert any("HTTP 503" in text for text in texts)
    assert any("Использование: /limits [primary|fallback]" in text for text in texts)
    assert calls[0][0].endswith("/v1/key")


def test_admin_only_blocks_non_admin(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(user_id=2)

    asyncio.run(bot.setmodel_command(update, DummyContext(["primary", "model/test"])))
    text, _ = update.message.replies[-1]
    assert text == "У вас нет доступа к этому боту."


def test_schedules_command_includes_query(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(user_id=1)

    async def fake_load_schedule_records():
        return [
            {
                "id": "sch1",
                "chat_id": 123,
                "target_type": "folder",
                "target_name": "AI",
                "period_type": "days",
                "period_value": 1,
                "query": "суммаризируй все чаты в папке AI за вчера и выдели ключевые темы",
                "mark_as_read": True,
                "recurrence_type": "daily",
                "time": "20:00",
                "interval_days": None,
                "weekday": None,
                "day_of_month": None,
                "created_at": "2026-03-07T00:00:00+03:00",
                "last_run": None,
                "next_run": "2026-03-08T20:00:00+03:00",
            }
        ]

    monkeypatch.setattr(bot, "_load_schedule_records", fake_load_schedule_records)
    asyncio.run(bot.schedules_command(update, DummyContext()))

    text, _ = update.message.replies[-1]
    assert "Запрос:" in text
    assert "суммаризируй все чаты в папке AI за вчера" in text
    assert "Отмечать как прочитанные: да" in text
