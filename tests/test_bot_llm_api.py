import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import requests

import bot
from llm_runtime import LLMRuntimeConfig


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload, ensure_ascii=False)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(self.text, response=self)


def _set_runtime_token(
    monkeypatch, token="token-1234567890", model="model/test"
):
    monkeypatch.setattr(bot.config, "LLM_REQUEST_TIMEOUT_SECONDS", 20)
    monkeypatch.setattr(bot.config, "LLM_MAX_RETRIES", 3)
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_INTERVAL_SECONDS", 4)
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS", 12)
    monkeypatch.setattr(bot.config, "FALLBACK_FREE_MODEL_INTERVAL_SECONDS", 4)
    monkeypatch.setattr(bot.config, "FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS", 12)
    bot._free_model_next_allowed_at.clear()
    monkeypatch.setattr(
        bot,
        "llm_runtime",
        LLMRuntimeConfig(
            "https://openrouter.ai/api/v1/chat/completions",
            token,
            model,
        ),
    )


def test_call_llm_api_success(monkeypatch):
    _set_runtime_token(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        assert timeout == 20
        return DummyResponse(
            200,
            {"choices": [{"message": {"content": "ok-answer"}}]},
        )

    monkeypatch.setattr(bot.requests, "post", fake_post)
    answer = bot.call_llm_api([{"role": "user", "content": "hi"}])

    assert answer == "ok-answer"
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["model"] == "model/test"
    assert captured["headers"]["Authorization"].startswith("Bearer ")


def test_call_llm_api_with_meta_success(monkeypatch):
    _set_runtime_token(monkeypatch)

    def fake_post(url, json=None, headers=None, timeout=None):
        return DummyResponse(200, {"choices": [{"message": {"content": "ok-answer"}}]})

    monkeypatch.setattr(bot.requests, "post", fake_post)
    answer = bot.call_llm_api_with_meta([{"role": "user", "content": "hi"}])

    assert answer["content"] == "ok-answer"
    assert answer["model"] == "model/test"
    assert answer["url"].startswith("https://openrouter.ai/")


def test_call_llm_api_with_meta_uses_actual_model_from_response(monkeypatch):
    _set_runtime_token(monkeypatch)

    def fake_post(url, json=None, headers=None, timeout=None):
        return DummyResponse(
            200,
            {
                "model": "arcee-ai/trinity-large-preview:free",
                "choices": [{"message": {"content": "ok-answer"}}],
            },
        )

    monkeypatch.setattr(bot.requests, "post", fake_post)
    answer = bot.call_llm_api_with_meta([{"role": "user", "content": "hi"}])

    assert answer["content"] == "ok-answer"
    assert answer["model"] == "arcee-ai/trinity-large-preview:free"


def test_call_llm_api_fallback_to_openrouter_free_on_rate_limit(monkeypatch):
    _set_runtime_token(monkeypatch)
    monkeypatch.setattr(bot.time, "sleep", lambda *_: None)
    seen_models = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen_models.append(json["model"])
        if json["model"] == "model/test":
            return DummyResponse(429, {"error": {"message": "ratelimit"}})
        if json["model"] == "openrouter/free":
            return DummyResponse(
                200, {"choices": [{"message": {"content": "ok-fallback"}}]}
            )
        return DummyResponse(500, {"error": "unexpected-model"})

    monkeypatch.setattr(bot.requests, "post", fake_post)
    answer = bot.call_llm_api([{"role": "user", "content": "hi"}])

    assert answer == "ok-fallback"
    assert seen_models.count("model/test") == 3
    assert "openrouter/free" in seen_models


def test_call_llm_api_fallback_uses_configured_url_token_and_model(monkeypatch):
    monkeypatch.setattr(
        bot,
        "llm_runtime",
        LLMRuntimeConfig(
            "https://primary.example/v1",
            "primary-token-1234",
            "primary/model",
            fallback_url="https://fallback.example/v1",
            fallback_token="fallback-token-5678",
            fallback_model="fallback/model",
        ),
    )
    seen_calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen_calls.append(
            {
                "url": url,
                "model": json["model"],
                "auth": headers.get("Authorization", ""),
            }
        )
        if "primary.example" in url:
            return DummyResponse(500, {"error": {"message": "primary failed"}})
        return DummyResponse(
            200, {"choices": [{"message": {"content": "fallback-ok"}}]}
        )

    monkeypatch.setattr(bot.requests, "post", fake_post)
    answer = bot.call_llm_api([{"role": "user", "content": "hi"}])

    assert answer == "fallback-ok"
    assert seen_calls[0]["url"] == "https://primary.example/v1/chat/completions"
    assert seen_calls[0]["model"] == "primary/model"
    assert seen_calls[0]["auth"] == "Bearer primary-token-1234"
    assert seen_calls[1]["url"] == "https://fallback.example/v1/chat/completions"
    assert seen_calls[1]["model"] == "fallback/model"
    assert seen_calls[1]["auth"] == "Bearer fallback-token-5678"


def test_call_llm_api_validation_and_failures(monkeypatch):
    _set_runtime_token(monkeypatch, token="")
    try:
        bot.call_llm_api([{"role": "user", "content": "hi"}])
        raise AssertionError("expected exception")
    except Exception as e:  # noqa: BLE001
        assert "/settoken" in str(e)

    _set_runtime_token(monkeypatch)
    monkeypatch.setattr(
        bot.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(200, {"unexpected": "format"}),
    )
    try:
        bot.call_llm_api([{"role": "user", "content": "hi"}])
        raise AssertionError("expected exception")
    except Exception as e:  # noqa: BLE001
        assert "неожиданный формат" in str(e).lower()

    monkeypatch.setattr(bot.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        bot.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(429, {"error": "ratelimit"}, "ratelimit"),
    )
    try:
        bot.call_llm_api([{"role": "user", "content": "hi"}])
        raise AssertionError("expected exception")
    except Exception as e:  # noqa: BLE001
        assert "Превышен лимит запросов" in str(e)

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(bot.requests, "post", raise_timeout)
    try:
        bot.call_llm_api([{"role": "user", "content": "hi"}])
        raise AssertionError("expected exception")
    except Exception as e:  # noqa: BLE001
        assert "Timeout LLM API" in str(e)


def test_call_llm_api_applies_interval_only_for_free_models(monkeypatch):
    _set_runtime_token(monkeypatch, model="meta-llama/llama-3.3-70b-instruct:free")
    seen = {"count": 0}
    sleeps = []
    current_time = {"value": 100.0}

    monkeypatch.setattr(bot.time, "monotonic", lambda: current_time["value"])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time["value"] += seconds

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["count"] += 1
        return DummyResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(bot.time, "sleep", fake_sleep)
    monkeypatch.setattr(bot.requests, "post", fake_post)

    bot.call_llm_api([{"role": "user", "content": "hi"}])
    bot.call_llm_api([{"role": "user", "content": "hi-again"}])

    assert seen["count"] == 2
    assert sleeps == [4]


def test_call_llm_api_does_not_apply_interval_for_paid_models(monkeypatch):
    bot._free_model_next_allowed_at.clear()
    monkeypatch.setattr(bot.config, "LLM_REQUEST_TIMEOUT_SECONDS", 20)
    monkeypatch.setattr(bot.config, "LLM_MAX_RETRIES", 3)
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_INTERVAL_SECONDS", 4)
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS", 12)
    monkeypatch.setattr(
        bot,
        "llm_runtime",
        LLMRuntimeConfig(
            "https://openrouter.ai/api/v1/chat/completions",
            "token-1234567890",
            "deepseek/deepseek-v3.2",
        ),
    )
    sleeps = []

    monkeypatch.setattr(bot.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(bot.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        bot.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(
            200, {"choices": [{"message": {"content": "ok"}}]}
        ),
    )

    bot.call_llm_api([{"role": "user", "content": "hi"}])
    bot.call_llm_api([{"role": "user", "content": "hi-again"}])

    assert sleeps == []


def test_call_llm_api_free_model_uses_configured_429_backoff(monkeypatch):
    _set_runtime_token(monkeypatch, model="meta-llama/llama-3.3-70b-instruct:free")
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(bot.config, "PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS", 11)
    sleeps = []
    current_time = {"value": 50.0}
    attempts = {"count": 0}

    monkeypatch.setattr(bot.time, "monotonic", lambda: current_time["value"])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time["value"] += seconds

    def fake_post(url, json=None, headers=None, timeout=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return DummyResponse(429, {"error": {"message": "ratelimit"}})
        return DummyResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(bot.time, "sleep", fake_sleep)
    monkeypatch.setattr(bot.requests, "post", fake_post)

    answer = bot.call_llm_api([{"role": "user", "content": "hi"}])

    assert answer == "ok"
    assert attempts["count"] == 2
    assert sleeps == [11]


def test_parse_command_with_gpt(monkeypatch):
    monkeypatch.setattr(
        bot,
        "call_llm_api",
        lambda messages: (
            "```json\n"
            '{"target_type":"chat","target_name":"Work","period_type":null,"period_value":null,"mark_as_read":false,"query":"q"}\n'
            "```"
        ),
    )
    result = asyncio.run(bot.parse_command_with_gpt("test"))
    assert result["target_type"] == "chat"
    assert result["target_name"] == "Work"

    monkeypatch.setattr(bot, "call_llm_api", lambda messages: "{not-json}")
    error_result = asyncio.run(bot.parse_command_with_gpt("test"))
    assert "error" in error_result

    monkeypatch.setattr(
        bot,
        "call_llm_api",
        lambda messages: (
            '{"target_type":"bad","target_name":"Work","period_type":null,'
            '"period_value":null,"mark_as_read":false,"query":"q"}'
        ),
    )
    schema_error = asyncio.run(bot.parse_command_with_gpt("test"))
    assert "error" in schema_error
    assert "target_type" in schema_error["error"]


def test_parse_command_with_gpt_schedule_fields(monkeypatch):
    monkeypatch.setattr(
        bot,
        "call_llm_api",
        lambda messages: (
            "```json\n"
            '{"target_type":"folder","target_name":"AI","period_type":"unread","period_value":null,'
            '"mark_as_read":true,"query":"Суммаризируй","recurrence_type":"interval_days",'
            '"interval_days":3,"time":"20:00"}\n'
            "```"
        ),
    )
    result = asyncio.run(bot.parse_command_with_gpt("суммаризируй раз в 3 дня в 20:00"))
    assert result["recurrence_type"] == "interval_days"
    assert result["interval_days"] == 3
    assert result["time"] == "20:00"
    assert result["time_missing"] is False

    monkeypatch.setattr(
        bot,
        "call_llm_api",
        lambda messages: (
            '{"target_type":"chat","target_name":"Work","period_type":null,"period_value":null,'
            '"mark_as_read":false,"query":"q","recurrence_type":"daily","interval_days":null,"time":null}'
        ),
    )
    missing_time = asyncio.run(bot.parse_command_with_gpt("суммаризируй каждый день"))
    assert missing_time["recurrence_type"] == "daily"
    assert missing_time["time"] is None
    assert missing_time["time_missing"] is True


def test_process_chat_with_openai(monkeypatch):
    captured = {}

    def fake_call(messages, candidates_override=None):
        captured["messages"] = messages
        captured["override"] = candidates_override
        return {
            "content": "summary",
            "model": "model/test",
            "url": "https://openrouter.ai/api/v1/chat/completions",
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)
    result = asyncio.run(
        bot.process_chat_with_openai("secret data", "сумм", "последний день")
    )
    assert "summary" in result
    assert "Модель: `model/test`" in result
    payload = captured["messages"][1]["content"]
    assert "secret data" in payload
    assert "[PII]" not in payload
    assert captured["override"] is None

    def raise_error(messages, candidates_override=None):
        raise Exception("boom")

    monkeypatch.setattr(bot, "call_llm_api_with_meta", raise_error)
    result_error = asyncio.run(bot.process_chat_with_openai("data", "q"))
    assert result_error.startswith("❌ ")


def test_process_chat_with_openai_retries_on_low_quality(monkeypatch):
    calls = []

    monkeypatch.setattr(
        bot,
        "llm_runtime",
        LLMRuntimeConfig(
            "https://primary.example/v1",
            "primary-token-1234",
            "primary/model",
            fallback_url="https://fallback.example/v1",
            fallback_token="fallback-token-5678",
            fallback_model="fallback/model",
        ),
    )

    def fake_call(messages, candidates_override=None):
        calls.append(candidates_override)
        if candidates_override:
            return {
                "content": "Нормальная краткая суммаризация без артефактов.",
                "model": "fallback/model",
                "url": "https://fallback.example/v1/chat/completions",
            }
        return {
            "content": 'Снижение ручного кодирования,.attr(loading="lazy") и мусор в录入 тексте',
            "model": "primary/model",
            "url": "https://primary.example/v1/chat/completions",
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)
    result = asyncio.run(
        bot.process_chat_with_openai(
            "[2026-03-06 21:57:33] User: текст",
            "суммаризируй",
            "последние 1 день",
        )
    )

    assert len(calls) == 2
    assert calls[0] is None
    assert isinstance(calls[1], list)
    assert calls[1][0].model == "fallback/model"
    assert "Нормальная краткая суммаризация" in result
    assert "Модель: `fallback/model`" in result


def test_get_chat_history_empty_returns_empty_string(monkeypatch):
    async def fake_ensure_connected():
        return None

    class DummyClient:
        async def iter_messages(self, chat_entity, **kwargs):
            if False:  # pragma: no cover
                yield None
            return

    monkeypatch.setattr(bot, "ensure_telethon_connected", fake_ensure_connected)
    monkeypatch.setattr(bot, "telethon_client", DummyClient())

    history, first_id = asyncio.run(bot.get_chat_history(object(), "last_messages", 10))
    assert history == ""
    assert first_id is None


def test_get_chat_history_last_messages_uses_latest_messages_in_chronological_order(
    monkeypatch,
):
    calls = {}

    async def fake_ensure_connected():
        return None

    class DummyClient:
        async def iter_messages(self, chat_entity, **kwargs):
            calls["kwargs"] = kwargs
            sender = SimpleNamespace(id=1, first_name="User", last_name=None)
            for message_id, text in (
                (30, "newest"),
                (20, "middle"),
                (10, "oldest in slice"),
            ):
                yield SimpleNamespace(
                    id=message_id,
                    text=text,
                    sender=sender,
                    date=datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc),
                )

    monkeypatch.setattr(bot, "ensure_telethon_connected", fake_ensure_connected)
    monkeypatch.setattr(bot, "telethon_client", DummyClient())

    history, first_id = asyncio.run(bot.get_chat_history(object(), "last_messages", 3))

    assert calls["kwargs"]["reverse"] is False
    assert first_id == 10
    assert history.index("oldest in slice") < history.index("middle") < history.index(
        "newest"
    )


def test_get_chat_history_unread_uses_read_boundary(monkeypatch):
    calls = {}

    async def fake_ensure_connected():
        return None

    class DummyClient:
        async def iter_messages(self, chat_entity, **kwargs):
            calls["kwargs"] = kwargs
            sender = SimpleNamespace(id=1, first_name="User", last_name=None)
            if kwargs.get("min_id") == 100:
                for message_id, text in (
                    (102, "fresh 2026"),
                    (101, "still unread 2026"),
                ):
                    yield SimpleNamespace(
                        id=message_id,
                        text=text,
                        sender=sender,
                        date=datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc),
                    )
            else:
                for message_id, text in (
                    (2, "old 2023"),
                    (1, "very old 2023"),
                ):
                    yield SimpleNamespace(
                        id=message_id,
                        text=text,
                        sender=sender,
                        date=datetime(2023, 9, 22, 10, 0, tzinfo=timezone.utc),
                    )

    monkeypatch.setattr(bot, "ensure_telethon_connected", fake_ensure_connected)
    monkeypatch.setattr(bot, "telethon_client", DummyClient())

    history, first_id = asyncio.run(
        bot.get_chat_history(
            object(),
            "unread",
            {"limit": 2, "read_inbox_max_id": 100},
        )
    )

    assert calls["kwargs"]["min_id"] == 100
    assert first_id == 101
    assert "fresh 2026" in history
    assert "old 2023" not in history


def test_process_single_chat_unread_mode_uses_unread_count(monkeypatch):
    calls = {}

    async def fake_get_history(chat_entity, period_type=None, period_value=None):
        calls["period_type"] = period_type
        calls["period_value"] = period_value
        return "history", 101

    async def fake_process(chat_history, query, period_text):
        calls["period_text"] = period_text
        return "summary"

    async def fake_mark_read(chat_entity):
        calls["marked"] = True
        return True

    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, parse_mode=None):
            self.sent.append((text, parse_mode))
            return None

    update = type("Update", (), {"message": DummyMessage()})()
    chat_entity = type(
        "Entity", (), {"id": -1001234567890, "title": "AI Chat", "username": None}
    )()

    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "process_chat_with_openai", fake_process)
    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    success = asyncio.run(
        bot._process_single_chat(
            update,
            DummyProcessing(),
            chat_entity,
            "AI Chat",
            1,
            2,
            "unread",
            None,
            "query",
            True,
            unread_count=7,
        )
    )

    assert success is True
    assert calls["period_type"] == "unread"
    assert calls["period_value"] == {
        "limit": 7,
        "read_inbox_max_id": None,
    }
    assert calls["period_text"] == "непрочитанные сообщения (7)"
    assert calls["marked"] is True


def test_process_single_chat_unread_mode_uses_unread_history(monkeypatch):
    calls = {}

    async def fake_get_history(chat_entity, period_type=None, period_value=None):
        calls["period_type"] = period_type
        calls["period_value"] = period_value
        if period_type == "unread":
            return "[2026-03-09 10:00:00] User: fresh unread", 101
        return "[2023-09-22 10:00:00] User: stale old", 1

    async def fake_process(chat_history, query, period_text):
        calls["history"] = chat_history
        return "summary"

    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, parse_mode=None):
            self.sent.append((text, parse_mode))
            return None

    update = type("Update", (), {"message": DummyMessage()})()
    chat_entity = type(
        "Entity", (), {"id": -1001234567890, "title": "AI Chat", "username": None}
    )()

    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "process_chat_with_openai", fake_process)
    async def fake_mark_read(chat_entity):
        return True

    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    success = asyncio.run(
        bot._process_single_chat(
            update,
            DummyProcessing(),
            chat_entity,
            "AI Chat",
            1,
            2,
            "unread",
            None,
            "query",
            False,
            unread_count=2,
        )
    )

    assert success is True
    assert calls["period_type"] == "unread"
    assert calls["history"] == "[2026-03-09 10:00:00] User: fresh unread"


def test_process_single_chat_unread_marks_read_when_nothing_to_summarize(monkeypatch):
    calls = {"marked": 0}

    async def fake_get_history(chat_entity, period_type=None, period_value=None):
        return "", None

    async def fake_mark_read(chat_entity):
        calls["marked"] += 1
        return True

    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        async def reply_text(self, text, parse_mode=None):
            return None

    update = type("Update", (), {"message": DummyMessage()})()

    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    success = asyncio.run(
        bot._process_single_chat(
            update,
            DummyProcessing(),
            object(),
            "Media Chat",
            1,
            2,
            "unread",
            None,
            "query",
            True,
            unread_count=3,
        )
    )

    assert success is False
    assert calls["marked"] == 1


def test_process_single_chat_unread_mode_skips_empty_unread():
    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        async def reply_text(self, text, parse_mode=None):
            return None

    update = type("Update", (), {"message": DummyMessage()})()

    success = asyncio.run(
        bot._process_single_chat(
            update,
            DummyProcessing(),
            object(),
            "AI Chat",
            1,
            2,
            "unread",
            None,
            "query",
            False,
            unread_count=0,
        )
    )

    assert success is False


def test_resolve_single_chat_includes_unread_state(monkeypatch):
    class DummyProcessing:
        async def edit_text(self, text):
            return None

    entity = type("Entity", (), {"id": 123, "title": "Work", "username": None})()

    async def fake_find_chat_by_name(*args, **kwargs):
        return entity, "Work", 1.0

    async def fake_get_unread_state(*args, **kwargs):
        return 11, 222

    monkeypatch.setattr(bot, "find_chat_by_name", fake_find_chat_by_name)
    monkeypatch.setattr(bot, "_get_unread_state_for_chat", fake_get_unread_state)

    chats, found, error = asyncio.run(
        bot._resolve_single_chat("Work", DummyProcessing())
    )
    assert error is None
    assert found == "Work"
    assert chats[0][2] == 11
    assert chats[0][3] == 222


def test_run_scheduled_summary_job_failure_sets_retry(monkeypatch):
    messages = []
    scheduled = {}
    called = {"success": False}

    class DummyBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            messages.append((chat_id, text))
            return None

    monkeypatch.setattr(bot, "application_ref", type("App", (), {"bot": DummyBot()})())

    async def fake_get_schedule_record(_id):
        return {
            "id": "sch1",
            "chat_id": 77,
            "target_type": "chat",
            "target_name": "Work",
            "period_type": "today",
            "period_value": None,
            "query": "q",
            "mark_as_read": False,
            "recurrence_type": "daily",
            "time": "20:00",
            "next_run": datetime.now().astimezone().isoformat(),
        }

    async def fake_execute(_rec):
        raise Exception("boom")

    async def fake_retry(_id, delay_seconds=300):
        return {
            "id": "sch1",
            "chat_id": 77,
            "recurrence_type": "daily",
            "time": "20:00",
            "next_run": datetime.now().astimezone().isoformat(),
        }

    async def fake_mark_success(*args, **kwargs):
        called["success"] = True
        return None

    monkeypatch.setattr(bot, "_get_schedule_record", fake_get_schedule_record)
    monkeypatch.setattr(bot, "_execute_scheduled_summary", fake_execute)
    monkeypatch.setattr(bot, "_schedule_retry_after_failure", fake_retry)
    monkeypatch.setattr(
        bot, "_schedule_next_job", lambda rec: scheduled.update({"record": rec})
    )
    monkeypatch.setattr(bot, "_mark_schedule_success", fake_mark_success)

    asyncio.run(bot.run_scheduled_summary_job("sch1"))

    assert any("Ошибка расписания" in text for _, text in messages)
    assert "record" in scheduled
    assert called["success"] is False
