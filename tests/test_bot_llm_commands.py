import asyncio
from types import SimpleNamespace

import bot
from llm_runtime import LLMRuntimeConfig


class DummyMessage:
    def __init__(self, text=""):
        self.replies = []
        self.text = text

    async def reply_text(self, text, parse_mode=None):
        self.replies.append((text, parse_mode))
        return DummyProcessingMessage(self.replies)


class DummyProcessingMessage:
    def __init__(self, replies):
        self.replies = replies
        self.edits = []

    async def edit_text(self, text, parse_mode=None):
        self.edits.append((text, parse_mode))
        return self

    async def delete(self):
        return None


class DummyUpdate:
    def __init__(self, user_id, text=""):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = DummyMessage(text=text)
        self.effective_chat = SimpleNamespace(id=42)


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
    update = DummyUpdate(
        user_id=1,
        text="суммаризируй непрочитанные в папке AI и отметь как прочитанные",
    )

    asyncio.run(bot.seturl_command(update, DummyContext(["https://example.com/v1"])))
    asyncio.run(
        bot.seturl_command(
            update, DummyContext(["fallback", "https://fallback.example/v1"])
        )
    )
    asyncio.run(
        bot.setmodel_command(update, DummyContext(["primary", "model/test,model/alt"]))
    )
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
    assert "PRIMARY_LLM_MODEL=model/test,model/alt" in env_text
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
    assert any("/setmodel primary <model1,model2,...>" in text for text in texts)
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


def test_process_user_message_shows_recognized_command(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(
        user_id=1,
        text="суммаризируй непрочитанные в папке AI и отметь как прочитанные",
    )
    bot.current_context.clear()

    async def fake_parse_command(_user_message):
        return {
            "target_type": "folder",
            "target_name": "AI",
            "period_type": "unread",
            "period_value": None,
            "mark_as_read": True,
            "query": "суммаризируй непрочитанные в папке AI и отметь как прочитанные",
            "recurrence_type": None,
            "interval_days": None,
            "time": None,
        }

    async def fake_resolve_folder(target_name, processing_msg):
        return [(object(), "Chat One", 2, None)], target_name, None

    async def fake_process_single_chat(*args, **kwargs):
        return True

    monkeypatch.setattr(bot, "parse_command_with_gpt", fake_parse_command)
    monkeypatch.setattr(bot, "_resolve_folder_chats", fake_resolve_folder)
    monkeypatch.setattr(bot, "_process_single_chat", fake_process_single_chat)

    asyncio.run(bot.process_user_message(update, DummyContext()))

    reply_texts = [text for text, _ in update.message.replies]
    recognized = next(text for text in reply_texts if text.startswith("🧭 Распознано:"))
    assert "Цель: folder 'AI'" in recognized
    assert "Источник цели: запрос" in recognized
    assert "Период: непрочитанные сообщения" in recognized
    assert "Источник периода: запрос" in recognized
    assert "Отметить как прочитанные: да" in recognized
    assert "Расписание: нет" in recognized


def test_process_user_message_aggregates_llm_stats_for_folder(monkeypatch, tmp_path):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(user_id=1, text="суммаризируй папку AI")
    bot.current_context.clear()

    async def fake_parse_command(_user_message):
        return {
            "target_type": "folder",
            "target_name": "AI",
            "period_type": "days",
            "period_value": 1,
            "mark_as_read": False,
            "query": "суммаризируй папку AI",
            "recurrence_type": None,
            "interval_days": None,
            "time": None,
        }

    async def fake_resolve_folder(target_name, processing_msg):
        return [(object(), "Chat One"), (object(), "Chat Two")], target_name, None

    async def fake_process_single_chat(
        update_obj,
        processing_msg,
        chat_entity,
        chat_name,
        idx,
        total,
        period_type,
        period_value,
        query,
        mark_as_read,
        requested_model=None,
        unread_count=None,
        read_inbox_max_id=None,
        llm_stats_accumulator=None,
    ):
        await update_obj.message.reply_text(f"summary for {chat_name}")
        bot._merge_llm_stats(
            llm_stats_accumulator,
            {
                "model/test": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 0,
                    "errors": 0,
                }
            },
        )
        return True

    monkeypatch.setattr(bot, "parse_command_with_gpt", fake_parse_command)
    monkeypatch.setattr(bot, "_resolve_folder_chats", fake_resolve_folder)
    monkeypatch.setattr(bot, "_process_single_chat", fake_process_single_chat)

    asyncio.run(bot.process_user_message(update, DummyContext()))

    reply_texts = [text for text, _ in update.message.replies]
    operation_summary = reply_texts[-1]
    assert operation_summary.startswith("✅ Обработано чатов: 2")
    assert "LLM-статистика:" in operation_summary
    assert "запросов 2" in operation_summary
    assert not any(
        "LLM-статистика:" in text for text in reply_texts[:-1] if "summary for" in text
    )


def test_process_user_message_routes_cross_folder_query_to_combined_mode(
    monkeypatch, tmp_path
):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(
        user_id=1,
        text="в папке news за вчера найти все упоминания складов WB",
    )
    bot.current_context.clear()
    calls = {}

    async def fake_parse_command(_user_message):
        return {
            "target_type": "folder",
            "target_name": "news",
            "folder_mode": "combined",
            "period_type": "yesterday",
            "period_value": None,
            "mark_as_read": False,
            "query": "найти все упоминания складов WB",
            "requested_model": None,
            "recurrence_type": None,
            "interval_days": None,
            "time": None,
        }

    async def fake_resolve_folder(target_name, processing_msg):
        return [(object(), "One"), (object(), "Two")], target_name, None

    async def fake_combined(
        update_obj,
        processing_msg,
        chats_to_process,
        folder_name,
        period_type,
        period_value,
        query,
        mark_as_read,
        requested_model=None,
        llm_stats_accumulator=None,
    ):
        calls["args"] = (
            len(chats_to_process),
            folder_name,
            period_type,
            query,
            mark_as_read,
        )
        return 2, 0

    async def fail_single(*args, **kwargs):
        raise AssertionError("per-chat mode must not run")

    monkeypatch.setattr(bot, "parse_command_with_gpt", fake_parse_command)
    monkeypatch.setattr(bot, "_resolve_folder_chats", fake_resolve_folder)
    monkeypatch.setattr(bot, "_process_combined_folder", fake_combined)
    monkeypatch.setattr(bot, "_process_single_chat", fail_single)

    asyncio.run(bot.process_user_message(update, DummyContext()))

    assert calls["args"] == (
        2,
        "news",
        "yesterday",
        "найти все упоминания складов WB",
        False,
    )
    reply_texts = [text for text, _ in update.message.replies]
    recognized = next(text for text in reply_texts if text.startswith("🧭 Распознано:"))
    assert "Режим папки: combined" in recognized
    assert reply_texts[-1].startswith("✅ Обработано чатов: 2")


def test_process_user_message_persists_combined_morning_schedule(
    monkeypatch, tmp_path
):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(
        user_id=1,
        text=(
            "каждое утро в 10:00 сделай топ-10 новостей по всем каналам "
            "из папки news за вчера"
        ),
    )
    bot.current_context.clear()
    saved = []
    scheduled = []

    async def fake_parse_command(_user_message):
        return {
            "target_type": "folder",
            "target_name": "news",
            "folder_mode": "combined",
            "period_type": "yesterday",
            "period_value": None,
            "mark_as_read": False,
            "query": _user_message,
            "requested_model": None,
            "recurrence_type": "daily",
            "interval_days": None,
            "time": "10:00",
        }

    async def fake_append(record):
        saved.append(record)

    monkeypatch.setattr(bot, "parse_command_with_gpt", fake_parse_command)
    monkeypatch.setattr(bot, "_append_schedule_record", fake_append)
    monkeypatch.setattr(bot, "_schedule_next_job", scheduled.append)

    asyncio.run(bot.process_user_message(update, DummyContext()))

    assert len(saved) == 1
    assert scheduled == saved
    assert saved[0]["target_type"] == "folder"
    assert saved[0]["target_name"] == "news"
    assert saved[0]["folder_mode"] == "combined"
    assert saved[0]["period_type"] == "yesterday"
    assert saved[0]["recurrence_type"] == "daily"
    assert saved[0]["time"] == "10:00"
    reply_texts = [text for text, _ in update.message.replies]
    assert any("Режим папки: combined" in text for text in reply_texts)


def test_process_user_message_shows_operation_stats_for_single_chat(
    monkeypatch, tmp_path
):
    _reset_runtime(monkeypatch, tmp_path / ".env")
    update = DummyUpdate(user_id=1, text="суммаризируй чат Work")
    bot.current_context.clear()

    async def fake_parse_command(_user_message):
        return {
            "target_type": "chat",
            "target_name": "Work",
            "period_type": "days",
            "period_value": 1,
            "mark_as_read": False,
            "query": "суммаризируй чат Work",
            "recurrence_type": None,
            "interval_days": None,
            "time": None,
        }

    async def fake_resolve_single(target_name, processing_msg):
        return [(object(), "Work")], target_name, None

    async def fake_process_single_chat(
        update_obj,
        processing_msg,
        chat_entity,
        chat_name,
        idx,
        total,
        period_type,
        period_value,
        query,
        mark_as_read,
        requested_model=None,
        unread_count=None,
        read_inbox_max_id=None,
        llm_stats_accumulator=None,
    ):
        await update_obj.message.reply_text(f"summary for {chat_name}")
        bot._merge_llm_stats(
            llm_stats_accumulator,
            {
                "model/test": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 0,
                    "errors": 0,
                }
            },
        )
        return True

    monkeypatch.setattr(bot, "parse_command_with_gpt", fake_parse_command)
    monkeypatch.setattr(bot, "_resolve_single_chat", fake_resolve_single)
    monkeypatch.setattr(bot, "_process_single_chat", fake_process_single_chat)

    asyncio.run(bot.process_user_message(update, DummyContext()))

    reply_texts = [text for text, _ in update.message.replies]
    assert "summary for Work" in reply_texts
    assert reply_texts[-1].startswith("LLM-статистика:")
    assert "запросов 1" in reply_texts[-1]
