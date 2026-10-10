import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

import bot
from chatgpt_runtime import (
    ChatGPTCandidate,
    ChatGPTCredentialsError,
    ChatGPTResponse,
    ChatGPTResponseError,
)


class FakeChatGPTClient:
    def __init__(self, responses=None, *, configured=True, model="gpt-test"):
        self.responses = list(responses or [ChatGPTResponse("ok-answer", model)])
        self.configured = configured
        self.model = model
        self.calls = []
        self.credentials_path = "credentials.json"

    def is_configured(self):
        return self.configured

    def create_response(self, messages, *, requested_model=None, timeout=90):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "requested_model": requested_model,
                "timeout": timeout,
            }
        )
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _set_chatgpt_client(monkeypatch, responses=None, **kwargs):
    client = FakeChatGPTClient(responses=responses, **kwargs)
    monkeypatch.setattr(bot, "chatgpt_client", client)
    monkeypatch.setattr(bot.config, "LLM_REQUEST_TIMEOUT_SECONDS", 20)
    monkeypatch.setattr(bot.config, "LLM_MAX_RETRIES", 3)
    return client


def test_call_llm_api_success(monkeypatch):
    client = _set_chatgpt_client(monkeypatch)

    answer = bot.call_llm_api([{"role": "user", "content": "hi"}])

    assert answer == "ok-answer"
    assert client.calls[0]["requested_model"] == "gpt-test"
    assert client.calls[0]["timeout"] == 20


def test_call_llm_api_with_meta_uses_actual_model(monkeypatch):
    _set_chatgpt_client(
        monkeypatch,
        responses=[ChatGPTResponse("answer", "gpt-actual")],
        model="auto",
    )

    result = bot.call_llm_api_with_meta([{"role": "user", "content": "hi"}])

    assert result["content"] == "answer"
    assert result["model"] == "gpt-actual"
    assert result["url"] == bot.OPENAI_RESPONSES_URL
    assert result["scope"] == "chatgpt_subscription"


def test_call_llm_api_with_meta_uses_timeout_override(monkeypatch):
    client = _set_chatgpt_client(monkeypatch)

    bot.call_llm_api_with_meta(
        [{"role": "user", "content": "hi"}],
        request_timeout_seconds_override=75,
    )

    assert client.calls[0]["timeout"] == 75


def test_call_llm_api_retries_transient_response_failure(monkeypatch):
    client = _set_chatgpt_client(
        monkeypatch,
        responses=[
            ChatGPTResponseError("upstream", code="server_error", status_code=500),
            ChatGPTResponse("ok-after-retry", "gpt-test"),
        ],
    )
    sleeps = []
    monkeypatch.setattr(bot.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = bot.call_llm_api_with_meta([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok-after-retry"
    assert len(client.calls) == 2
    assert sleeps == [1]
    assert result["stats"]["gpt-test"]["errors"] == 1
    assert result["stats"]["gpt-test"]["successes"] == 1


def test_call_llm_api_validator_repairs_and_retries(monkeypatch):
    client = _set_chatgpt_client(
        monkeypatch,
        responses=[
            ChatGPTResponse("bad", "gpt-test"),
            ChatGPTResponse("good", "gpt-test"),
        ],
    )
    monkeypatch.setattr(bot.time, "sleep", lambda *_: None)

    def validator(content, candidate):
        if content == "bad":
            return f"bad answer from {candidate.model}"
        return None

    result = bot.call_llm_api_with_meta(
        [{"role": "user", "content": "hi"}],
        response_validator=validator,
    )

    assert result["content"] == "good"
    assert len(client.calls) == 2
    assert result["stats"]["gpt-test"]["rejected"] == 1


def test_call_llm_api_rewrites_incomplete_response(monkeypatch):
    messages = [{"role": "user", "content": "Сделай полную сводку"}]
    client = _set_chatgpt_client(
        monkeypatch,
        responses=[
            ChatGPTResponseError(
                "incomplete",
                code="max_output_tokens",
                partial_text="Обрезанный ответ",
            ),
            ChatGPTResponse("Краткий завершённый ответ.", "gpt-test"),
        ],
    )
    monkeypatch.setattr(bot.time, "sleep", lambda *_: None)

    result = bot.call_llm_api_with_meta(messages)

    assert result["content"] == "Краткий завершённый ответ."
    assert messages[-2] == {"role": "assistant", "content": "Обрезанный ответ"}
    assert "Перепиши ответ заново" in messages[-1]["content"]
    assert result["stats"]["gpt-test"]["rejected"] == 1


def test_call_llm_api_reports_subscription_limit(monkeypatch):
    _set_chatgpt_client(
        monkeypatch,
        responses=[
            ChatGPTResponseError(
                "limit",
                code="subscription_sharing_usage_limit_exceeded",
            )
            for _ in range(3)
        ],
    )
    monkeypatch.setattr(bot.time, "sleep", lambda *_: None)

    with pytest.raises(Exception, match="Лимит подписки ChatGPT"):
        bot.call_llm_api([{"role": "user", "content": "hi"}])


def test_call_llm_api_requires_oauth_credentials(monkeypatch):
    _set_chatgpt_client(monkeypatch, configured=False)

    with pytest.raises(ChatGPTCredentialsError, match="chatgpt_auth.py"):
        bot.call_llm_api([{"role": "user", "content": "hi"}])


def test_call_llm_api_propagates_credentials_refresh_failure(monkeypatch):
    _set_chatgpt_client(
        monkeypatch,
        responses=[ChatGPTCredentialsError("refresh revoked")],
    )

    with pytest.raises(Exception, match="refresh revoked"):
        bot.call_llm_api([{"role": "user", "content": "hi"}])


def test_build_requested_model_candidate_uses_exact_chatgpt_slug():
    candidates = bot._build_requested_model_candidates("gpt-specific")

    assert candidates == [ChatGPTCandidate(model="gpt-specific")]


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


def test_parse_command_with_gpt_requested_model(monkeypatch):
    monkeypatch.setattr(
        bot,
        "call_llm_api",
        lambda messages: (
            "```json\n"
            '{"target_type":"folder","target_name":"AI","period_type":null,"period_value":null,'
            '"mark_as_read":false,"query":"суммаризируй папку AI с помощью gpt-specific",'
            '"requested_model":"gpt-specific","recurrence_type":null,"interval_days":null,"time":null}\n'
            "```"
        ),
    )
    result = asyncio.run(
        bot.parse_command_with_gpt(
            "суммаризируй папку AI с помощью gpt-specific"
        )
    )
    assert result["requested_model"] == "gpt-specific"


def test_process_chat_with_openai(monkeypatch):
    captured = {}

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        captured["messages"] = messages
        captured["override"] = candidates_override
        assert response_validator is not None
        return {
            "content": "summary",
            "model": "model/test",
            "url": bot.OPENAI_RESPONSES_URL,
            "stats": {
                "model/test": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 0,
                    "errors": 0,
                }
            },
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)
    result = asyncio.run(
        bot.process_chat_with_openai("secret data", "сумм", "последний день")
    )
    assert "summary" in result
    assert "Модель: `model/test`" in result
    assert "LLM-статистика:" in result
    payload = captured["messages"][1]["content"]
    assert "secret data" in payload
    assert "[PII]" not in payload
    assert captured["override"] is None

    def raise_error(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        raise Exception("boom")

    monkeypatch.setattr(bot, "call_llm_api_with_meta", raise_error)
    result_error = asyncio.run(bot.process_chat_with_openai("data", "q"))
    assert result_error.startswith("❌ ")


def test_process_chat_with_openai_strips_operational_parts_from_summary_query(
    monkeypatch,
):
    captured = {}

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        captured["messages"] = messages
        return {
            "content": "summary",
            "model": "model/test",
            "url": bot.OPENAI_RESPONSES_URL,
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    asyncio.run(
        bot.process_chat_with_openai(
            "chat history",
            (
                "суммаризируй все непрочитанные чаты в папке AI, "
                "отметь их как прочитанные и присылай мне саммари каждый день в 10:00"
            ),
            "непрочитанные сообщения",
        )
    )

    payload = captured["messages"][1]["content"]
    assert "отметь их как прочитанные" not in payload.lower()
    assert "каждый день" not in payload.lower()
    assert "10:00" not in payload
    assert "суммаризируй все непрочитанные чаты в папке ai" in payload.lower()


def test_process_chat_with_openai_uses_requested_model_override(monkeypatch):
    captured = {}

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        captured["override"] = candidates_override
        captured["max_attempts_override"] = max_attempts_override
        return {
            "content": "summary",
            "model": "gpt-specific",
            "url": bot.OPENAI_RESPONSES_URL,
            "stats": {
                "gpt-specific": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 0,
                    "errors": 0,
                }
            },
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    result = asyncio.run(
        bot.process_chat_with_openai(
            "chat history",
            "суммаризируй папку AI",
            "последние 1 день",
            requested_model="gpt-specific",
        )
    )

    assert "summary" in result
    assert captured["max_attempts_override"] == 3
    assert len(captured["override"]) == 1
    assert captured["override"][0].model == "gpt-specific"


def test_process_chat_with_openai_requested_model_failure_has_no_fallback(monkeypatch):
    captured = {}

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        captured["override"] = candidates_override
        captured["max_attempts_override"] = max_attempts_override
        raise Exception("429 upstream")

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    result = asyncio.run(
        bot.process_chat_with_openai(
            "chat history",
            "суммаризируй папку AI",
            "последние 1 день",
            requested_model="gpt-specific",
        )
    )

    assert "gpt-specific" in result
    assert "после 3 попыток" in result
    assert captured["max_attempts_override"] == 3
    assert len(captured["override"]) == 1


def test_process_chat_with_openai_retries_on_low_quality(monkeypatch):
    calls = []

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        calls.append(candidates_override)
        assert response_validator is not None
        bad_content = 'Снижение ручного кодирования,.attr(loading="lazy") и мусор в录入 тексте'
        assert response_validator(bad_content, SimpleNamespace(model="gpt-test"))
        return {
            "content": "Нормальная краткая суммаризация без артефактов.",
            "model": "gpt-test",
            "url": bot.OPENAI_RESPONSES_URL,
            "stats": {
                "gpt-test": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 1,
                    "errors": 0,
                },
            },
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)
    result = asyncio.run(
        bot.process_chat_with_openai(
            "[2026-03-06 21:57:33] User: текст",
            "суммаризируй",
            "последние 1 день",
        )
    )

    assert len(calls) == 1
    assert calls[0] is None
    assert "Нормальная краткая суммаризация" in result
    assert "Модель: `gpt-test`" in result
    assert "`gpt-test`" in result
    assert "отклонено 1" in result
    assert "тех. ошибок 0" in result


def test_process_chat_with_openai_requires_real_source_url(monkeypatch):
    captured = {}
    source_url = "https://t.me/source_a/11"
    embedded_url = "https://t.me/source_b/22"

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        captured["validator"] = response_validator
        captured["messages"] = messages
        assert response_validator(
            "Новость без ссылки", SimpleNamespace(model="model/test")
        )
        assert len(messages) == 4
        assert messages[-2] == {"role": "assistant", "content": "Новость без ссылки"}
        assert messages[-1]["role"] == "user"
        assert "[Оригинал](URL)" in messages[-1]["content"]
        assert response_validator(
            "Новость — [оригинал](https://t.me/invented/99)",
            SimpleNamespace(model="model/test"),
        )
        assert len(messages) == 4
        assert (
            response_validator(
                f"Новость — [оригинал]({source_url})",
                SimpleNamespace(model="model/test"),
            )
            is None
        )
        assert (
            response_validator(
                (
                    f"Новость — [оригинал]({source_url}), "
                    f"[контекст]({embedded_url}), повтор: {embedded_url}"
                ),
                SimpleNamespace(model="model/test"),
            )
            is None
        )
        return {
            "content": f"Новость — [оригинал]({source_url})",
            "model": "model/test",
            "url": "https://example.test/v1",
            "stats": {},
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    result = asyncio.run(
        bot._process_chat_with_openai_result(
            (
                f"Source [Оригинал]({source_url}) and "
                f"embedded Telegram reference [details]({embedded_url})"
            ),
            "сделай общую сводку",
            "вчера",
            required_source_urls={source_url},
        )
    )

    assert source_url in result["answer"]
    assert captured["validator"] is not None
    assert len(captured["messages"]) == 4


def test_process_chat_with_openai_repairs_false_single_source_claim(monkeypatch):
    source_url = "https://t.me/source_a/11"
    combined_sources = [
        (1, "Source A", 5),
        (2, "Source B", 3),
        (3, "Source C", 2),
    ]

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        false_claim = (
            "В предоставленной истории содержится информация только от одного "
            "канала — Source A. Сообщения из других источников отсутствуют. "
            f"[оригинал]({source_url})"
        )
        reason = response_validator(
            false_claim, SimpleNamespace(model="model/test")
        )
        assert "только один источник" in reason
        assert len(messages) == 4
        assert messages[-2] == {"role": "assistant", "content": false_claim}
        repair = messages[-1]["content"]
        assert "Source A — загружено сообщений: 5" in repair
        assert "Source B — загружено сообщений: 3" in repair
        assert "Source C — загружено сообщений: 2" in repair
        assert "Не нужно искусственно включать каждый источник" in repair
        assert (
            response_validator(
                f"Главная новость — [оригинал]({source_url})",
                SimpleNamespace(model="model/test"),
            )
            is None
        )
        return {
            "content": f"Главная новость — [оригинал]({source_url})",
            "model": "model/test",
            "url": "https://example.test/v1",
            "stats": {},
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    result = asyncio.run(
        bot._process_chat_with_openai_result(
            (
                "=== ИСТОЧНИК 1: Source A ===\n"
                f"item [Оригинал]({source_url})\n"
                "=== ИСТОЧНИК 2: Source B ===\nitem\n"
                "=== ИСТОЧНИК 3: Source C ===\nitem"
            ),
            "сделай общую сводку",
            "вчера",
            required_source_urls={source_url},
            combined_sources=combined_sources,
        )
    )

    assert source_url in result["answer"]


def test_process_chat_with_openai_does_not_notify_about_429_backoff(monkeypatch):
    notifications = []

    async def notifier(message_text: str):
        notifications.append(message_text)

    def fake_call(
        messages,
        candidates_override=None,
        rate_limit_callback=None,
        response_validator=None,
        max_attempts_override=None,
    ):
        assert rate_limit_callback is None
        return {
            "content": "summary",
            "model": "meta-llama/llama-3.3-70b-instruct:free",
            "url": bot.OPENAI_RESPONSES_URL,
            "stats": {
                "meta-llama/llama-3.3-70b-instruct:free": {
                    "requests": 1,
                    "rate_limits": 0,
                    "successes": 1,
                    "rejected": 0,
                    "errors": 0,
                }
            },
        }

    monkeypatch.setattr(bot, "call_llm_api_with_meta", fake_call)

    result = asyncio.run(
        bot.process_chat_with_openai(
            "chat history",
            "суммаризируй",
            "непрочитанные сообщения",
            rate_limit_notifier=notifier,
        )
    )

    assert "summary" in result
    assert notifications == []


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


def test_get_chat_history_yesterday_filters_today_and_adds_original_links(
    monkeypatch,
):
    calls = {}
    today_start = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_message_date = (today_start - timedelta(hours=2)).astimezone(
        timezone.utc
    )
    today_message_date = (today_start + timedelta(hours=1)).astimezone(timezone.utc)

    async def fake_ensure_connected():
        return None

    class DummyClient:
        async def iter_messages(self, chat_entity, **kwargs):
            calls["kwargs"] = kwargs
            sender = SimpleNamespace(id=1, first_name="News", last_name=None)
            yield SimpleNamespace(
                id=101,
                text="yesterday post",
                sender=sender,
                date=yesterday_message_date,
            )
            yield SimpleNamespace(
                id=102,
                text="today post",
                sender=sender,
                date=today_message_date,
            )

    monkeypatch.setattr(bot, "ensure_telethon_connected", fake_ensure_connected)
    monkeypatch.setattr(bot, "telethon_client", DummyClient())
    entity = SimpleNamespace(id=-1001234567890, username="daily_news")

    history, first_id = asyncio.run(
        bot.get_chat_history(
            entity,
            "yesterday",
            None,
            include_message_links=True,
        )
    )

    expected_start = (today_start - timedelta(days=1)).astimezone(timezone.utc)
    assert calls["kwargs"]["reverse"] is True
    assert calls["kwargs"]["offset_date"] == expected_start
    assert first_id == 101
    assert "yesterday post" in history
    assert "today post" not in history
    assert "[Оригинал](https://t.me/daily_news/101)" in history


def test_process_combined_folder_uses_one_llm_call_and_preserves_source_links(
    monkeypatch,
):
    calls = {"histories": [], "llm": [], "marked": []}

    class DummyProcessing:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text):
            self.edits.append(text)

    class DummyMessage:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, parse_mode=None):
            self.sent.append((text, parse_mode))

    async def fake_get_history(
        chat_entity,
        period_type=None,
        period_value=None,
        include_message_links=False,
    ):
        calls["histories"].append(
            (chat_entity.username, period_type, period_value, include_message_links)
        )
        message_id = 11 if chat_entity.username == "source_a" else 22
        return (
            f"[2026-07-19 10:00:00] News: item https://example.com/article "
            f"[Оригинал](https://t.me/{chat_entity.username}/{message_id})",
            message_id,
        )

    async def fake_process(
        chat_history,
        query,
        period_text,
        rate_limit_notifier=None,
        requested_model=None,
        required_source_urls=None,
        request_timeout_seconds=None,
        combined_sources=None,
    ):
        calls["llm"].append(
            (
                chat_history,
                query,
                period_text,
                requested_model,
                required_source_urls,
                request_timeout_seconds,
                combined_sources,
            )
        )
        long_answer = "\n\n".join(
            [
                (
                    "1. Общая новость — [оригинал](https://t.me/source_a/11)\n"
                    + "A" * 1600
                ),
                (
                    "2. Подтверждение — [оригинал](https://t.me/source_b/22)\n"
                    + "B" * 1600
                ),
                "3. Еще одна новость\n" + "C" * 1600,
            ]
        )
        return {
            "answer": long_answer,
            "stats": {},
        }

    async def fake_mark_read(chat_entity):
        calls["marked"].append(chat_entity.username)
        return True

    entity_a = SimpleNamespace(
        id=-1001111111111, username="source_a", title="Source A"
    )
    entity_b = SimpleNamespace(
        id=-1002222222222, username="source_b", title="Source B"
    )
    update = SimpleNamespace(message=DummyMessage())

    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "_process_chat_with_openai_result", fake_process)
    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    processed, skipped = asyncio.run(
        bot._process_combined_folder(
            update,
            DummyProcessing(),
            [
                (entity_a, "Source A", 2, 9),
                (entity_b, "Source B", 1, 20),
            ],
            "news",
            "unread",
            None,
            "сделай топ-10 новостей",
            True,
        )
    )

    assert (processed, skipped) == (2, 0)
    assert len(calls["llm"]) == 1
    (
        combined_history,
        combined_query,
        period_text,
        _,
        required_urls,
        request_timeout_seconds,
        combined_sources,
    ) = calls["llm"][0]
    assert combined_history.count("РЕЕСТР ФАКТИЧЕСКИ ЗАГРУЖЕННЫХ") == 2
    assert "Source A — загружено сообщений: 1" in combined_history
    assert "Source B — загружено сообщений: 1" in combined_history
    assert "ИСТОЧНИК 1" in combined_history
    assert "ИСТОЧНИК 2" in combined_history
    assert "https://t.me/source_a/11" in combined_history
    assert "https://t.me/source_b/22" in combined_history
    assert "точные markdown-ссылки" in combined_query
    assert required_urls == {
        "https://t.me/source_a/11",
        "https://t.me/source_b/22",
    }
    assert request_timeout_seconds == bot.config.COMBINED_LLM_REQUEST_TIMEOUT_SECONDS
    assert combined_sources == [(1, "Source A", 1), (2, "Source B", 1)]
    assert "https://example.com/article" not in required_urls
    assert period_text.startswith("непрочитанные сообщения")
    assert all(item[3] is True for item in calls["histories"])
    assert calls["marked"] == ["source_a", "source_b"]
    assert len(update.message.sent) == 3
    sent_texts = [text for text, _ in update.message.sent]
    assert "https://t.me/source_a/11" in sent_texts[1]
    assert "https://t.me/source_b/22" in sent_texts[1]
    for title in ("1. Общая новость", "2. Подтверждение", "3. Еще одна новость"):
        assert sum(title in text for text in sent_texts) == 1
    assert "3. Еще одна новость" in sent_texts[2]


def test_process_combined_folder_does_not_mark_read_after_llm_failure(monkeypatch):
    marked = []

    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        async def reply_text(self, text, parse_mode=None):
            return None

    async def fake_get_history(*args, **kwargs):
        return (
            "News [Оригинал](https://t.me/source_a/11)",
            11,
        )

    async def fake_process(*args, **kwargs):
        return {"answer": "❌ provider failed", "stats": {}, "ok": False}

    async def fake_mark_read(chat_entity):
        marked.append(chat_entity)
        return True

    entity = SimpleNamespace(id=-1001111111111, username="source_a")
    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "_process_chat_with_openai_result", fake_process)
    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    result = asyncio.run(
        bot._process_combined_folder(
            SimpleNamespace(message=DummyMessage()),
            DummyProcessing(),
            [(entity, "Source A", 1, 10)],
            "news",
            "unread",
            None,
            "сделай сводку",
            True,
        )
    )

    assert result == (0, 1)
    assert marked == []


def test_execute_scheduled_summary_routes_combined_folder_once(monkeypatch):
    calls = {}

    class DummyBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            return None

    entity = SimpleNamespace(id=-1001234567890, username="news")
    monkeypatch.setattr(bot, "application_ref", SimpleNamespace(bot=DummyBot()))

    async def fake_resolve_folder(target_name, processing_msg):
        return [(entity, "News", 3, 100)], target_name, None

    async def fake_combined(
        update,
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
            chats_to_process,
            folder_name,
            period_type,
            period_value,
            query,
            mark_as_read,
        )
        return 1, 0

    async def fail_single(*args, **kwargs):
        raise AssertionError("per-chat mode must not run")

    monkeypatch.setattr(bot, "_resolve_folder_chats", fake_resolve_folder)
    monkeypatch.setattr(bot, "_process_combined_folder", fake_combined)
    monkeypatch.setattr(bot, "_process_single_chat", fail_single)

    result = asyncio.run(
        bot._execute_scheduled_summary(
            {
                "chat_id": 77,
                "target_type": "folder",
                "target_name": "news",
                "folder_mode": "combined",
                "period_type": "yesterday",
                "period_value": None,
                "query": "сделай топ-10 новостей",
                "mark_as_read": False,
            }
        )
    )

    assert result == (1, 0, {})
    assert calls["args"][1:] == (
        "news",
        "yesterday",
        None,
        "сделай топ-10 новостей",
        False,
    )


def test_process_single_chat_unread_mode_uses_unread_count(monkeypatch):
    calls = {}

    async def fake_get_history(chat_entity, period_type=None, period_value=None):
        calls["period_type"] = period_type
        calls["period_value"] = period_value
        return "history", 101

    async def fake_process(
        chat_history, query, period_text, rate_limit_notifier=None, requested_model=None
    ):
        calls["period_text"] = period_text
        return {"answer": "summary", "stats": {}}

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
    monkeypatch.setattr(bot, "_process_chat_with_openai_result", fake_process)
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

    async def fake_process(
        chat_history, query, period_text, rate_limit_notifier=None, requested_model=None
    ):
        calls["history"] = chat_history
        return {"answer": "summary", "stats": {}}

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
    monkeypatch.setattr(bot, "_process_chat_with_openai_result", fake_process)
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


def test_process_single_chat_does_not_mark_read_after_llm_failure(monkeypatch):
    calls = {"marked": 0}

    async def fake_get_history(*args, **kwargs):
        return "history", 1

    async def fake_process(*args, **kwargs):
        return {"answer": "❌ failed", "stats": {}, "ok": False}

    async def fake_mark_read(chat_entity):
        calls["marked"] += 1
        return True

    class DummyProcessing:
        async def edit_text(self, text):
            return None

    class DummyMessage:
        async def reply_text(self, text, parse_mode=None):
            return None

    monkeypatch.setattr(bot, "get_chat_history", fake_get_history)
    monkeypatch.setattr(bot, "_process_chat_with_openai_result", fake_process)
    monkeypatch.setattr(bot, "mark_chat_as_read", fake_mark_read)

    success = asyncio.run(
        bot._process_single_chat(
            SimpleNamespace(message=DummyMessage()),
            DummyProcessing(),
            SimpleNamespace(id=-100123, username="source"),
            "Source",
            1,
            1,
            "unread",
            None,
            "query",
            True,
            unread_count=1,
        )
    )

    assert success is False
    assert calls["marked"] == 0


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
