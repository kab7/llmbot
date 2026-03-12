import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bot
from schedule_runtime import save_schedules


def test_escape_markdown():
    assert bot.escape_markdown("_*`[") == "\\_\\*\\`\\["


def test_format_period_text_variants():
    assert bot.format_period_text("days", 1) == "последние 1 день"
    assert bot.format_period_text("days", 3) == "последние 3 дней"
    assert bot.format_period_text("hours", 1) == "последние 1 час"
    assert bot.format_period_text("hours", 4) == "последние 4 часов"
    assert bot.format_period_text("today", None) == "сегодня"
    assert bot.format_period_text("last_messages", 50) == "последние 50 сообщений"
    assert bot.format_period_text("unread", None) == "непрочитанные сообщения"
    assert "последние" in bot.format_period_text(None, None)


def test_similarity_and_connection_helpers():
    assert bot.calculate_similarity("abc", "abc") == 1.0
    assert bot._is_connection_error("Disconnected from server")
    assert bot._is_connection_error("Connection reset")
    assert not bot._is_connection_error("random error")


def test_handle_telegram_error_messages():
    err = bot._handle_telegram_error(Exception("connection reset"), "чтении")
    assert "Потеряно соединение" in str(err)
    err = bot._handle_telegram_error(Exception("bad request"), "чтении")
    assert "Ошибка при чтении" in str(err)


def test_find_best_match_variants():
    items = ["Project Alpha", "Finance", "Random"]
    get_title = lambda x: [x]  # noqa: E731

    exact_item, exact_name, exact_score = bot.find_best_match(
        "Finance", items, get_title, fuzzy=True
    )
    assert (exact_item, exact_name, exact_score) == ("Finance", "Finance", 1.0)

    sub_item, sub_name, sub_score = bot.find_best_match(
        "Proj", items, get_title, fuzzy=True
    )
    assert sub_item == "Project Alpha"
    assert sub_name == "Project Alpha"
    assert sub_score >= 0.9

    no_fuzzy_item, _, no_fuzzy_score = bot.find_best_match(
        "zzz", items, get_title, fuzzy=False
    )
    assert no_fuzzy_item is None
    assert no_fuzzy_score == 0.0


def test_utc_to_local_and_remove_emojis():
    naive = datetime(2026, 1, 1, 10, 0, 0)
    aware = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert bot.utc_to_local(naive).tzinfo is not None
    assert bot.utc_to_local(aware).tzinfo is not None
    assert bot.remove_emojis("hi😀 test🚀") == "hi test"


def test_validate_command_payload_variants():
    payload = {
        "target_type": "chat",
        "target_name": "  Work  ",
        "period_type": "today",
        "period_value": 999,
        "mark_as_read": "yes",
        "query": "  summarize  ",
    }
    normalized = bot.validate_command_payload(payload)
    assert normalized["target_name"] == "Work"
    assert normalized["period_value"] is None
    assert normalized["mark_as_read"] is False
    assert normalized["query"] == "summarize"
    assert normalized["requested_model"] is None

    bad_target = payload | {"target_type": "invalid"}
    try:
        bot.validate_command_payload(bad_target)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "target_type" in str(e)

    bad_period = payload | {"period_type": "days", "period_value": "7"}
    try:
        bot.validate_command_payload(bad_period)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "period_value" in str(e)

    unread_payload = payload | {"period_type": "unread", "period_value": 123}
    normalized_unread = bot.validate_command_payload(unread_payload)
    assert normalized_unread["period_type"] == "unread"
    assert normalized_unread["period_value"] is None


def test_resolve_period_with_context_unread_and_fallback():
    context = {"period_type": "today", "period_value": None}

    period_type, period_value = bot.resolve_period_with_context(
        None,
        None,
        "суммаризируй все непрочитанные в папке AI",
        "суммаризируй все непрочитанные",
        context,
    )
    assert period_type == "unread"
    assert period_value is None

    period_type, period_value = bot.resolve_period_with_context(
        None,
        None,
        "суммаризируй папку AI",
        "суммаризируй",
        context,
    )
    assert period_type == "today"
    assert period_value is None

    period_type, period_value = bot.resolve_period_with_context(
        "days",
        7,
        "за неделю",
        "за неделю",
        context,
    )
    assert period_type == "days"
    assert period_value == 7


def test_validate_command_payload_schedule_variants():
    base = {
        "target_type": "chat",
        "target_name": "Work",
        "period_type": "today",
        "period_value": None,
        "mark_as_read": False,
        "query": "q",
    }
    payload = base | {
        "recurrence_type": "daily",
        "interval_days": None,
        "time": "20:00",
        "requested_model": "anthropic/claude-opus-4.6",
    }
    normalized = bot.validate_command_payload(payload)
    assert normalized["recurrence_type"] == "daily"
    assert normalized["time"] == "20:00"
    assert normalized["time_missing"] is False
    assert normalized["requested_model"] == "anthropic/claude-opus-4.6"

    interval_payload = payload | {
        "recurrence_type": "interval_days",
        "interval_days": 3,
    }
    normalized_interval = bot.validate_command_payload(interval_payload)
    assert normalized_interval["interval_days"] == 3

    no_schedule = bot.validate_command_payload(base)
    assert no_schedule["recurrence_type"] is None
    assert no_schedule["time"] is None
    assert no_schedule["time_missing"] is False

    missing_time = bot.validate_command_payload(payload | {"time": None})
    assert missing_time["recurrence_type"] == "daily"
    assert missing_time["time"] is None
    assert missing_time["time_missing"] is True

    try:
        bot.validate_command_payload(payload | {"time": "99:99"})
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Некорректное время" in str(e)


def test_split_markdown_chunks_keeps_escape_integrity():
    text = "abc\\_def\\*ghi"
    chunks = bot.split_markdown_chunks(text, 5)
    assert "".join(chunks) == text
    for chunk in chunks[:-1]:
        assert not chunk.endswith("\\")


def test_render_markdownish_to_telegram_html_converts_common_patterns():
    src = "## Заголовок\n**жирный** и `код`\n[Ссылка](https://example.com)"
    out = bot._render_markdownish_to_telegram_html(src)
    assert "<b>Заголовок</b>" in out
    assert "<b>жирный</b>" in out
    assert "<code>код</code>" in out
    assert '<a href="https://example.com">Ссылка</a>' in out


def test_sanitize_url_for_logs_hides_query_and_credentials():
    masked = bot._sanitize_url_for_logs(
        "https://user:pass@example.com/v1/chat/completions?api_key=secret&x=1"
    )
    assert "secret" not in masked
    assert "user:pass" not in masked
    assert "?" not in masked
    assert masked.startswith("https://example.com/")


def test_analyze_summary_quality_detects_artifacts_and_dates():
    history = "[2026-03-06 10:00:00] User: пример"
    bad_summary = (
        'Сводка: пункт,.attr(loading="lazy") и символы в录入 тексте. '
        "Дата: 14 марта 2026."
    )
    score, issues = bot._analyze_summary_quality(bad_summary, history)
    assert score > 0
    assert issues


def test_analyze_summary_quality_detects_boilerplate():
    history = "[2026-03-10 13:08:44] User: пример"
    bad_summary = (
        "## Суммаризация сообщения\n\n"
        "Статус выполнения запроса:\n"
        "- Отмечено как прочитанное: да\n"
    )
    score, issues = bot._analyze_summary_quality(bad_summary, history)
    assert score > 0
    assert any("служебные шаблоны" in issue for issue in issues)


def test_analyze_summary_quality_allows_common_tech_mixed_tokens():
    history = "[2026-03-10 13:08:44] User: обсуждали AI-кодинг и LangFuse"
    summary = (
        "Обсуждали AI-кодинга, LLM-инструменты и доработки LangFuse-ом "
        "для проверки агентного флоу."
    )
    score, issues = bot._analyze_summary_quality(summary, history)
    assert score == 0
    assert issues == []


def test_cleanup_summary_text_removes_attr_artifacts_and_boilerplate():
    src = (
        '## Суммаризация сообщения\n\n'
        '**Тема:** Как AI-кодинг-агенты формируют стек.\n'
        'Текст,.attr(loading="lazy")\n\n'
        '---\n'
        'Статус выполнения запроса:\n'
        '- Отмечено как прочитанное: да\n\n'
        'Примечание: требуется техническая реализация.\n'
    )
    cleaned = bot._cleanup_summary_text(src)
    assert "Суммаризация сообщения" not in cleaned
    assert "Тема:" not in cleaned
    assert ".attr(" not in cleaned
    assert "Статус выполнения запроса" not in cleaned
    assert "Примечание" not in cleaned
    assert "\n\n\n" not in cleaned


def test_format_llm_stats_shows_rejected_separately():
    text = bot._format_llm_stats(
        {
            "model/test": {
                "requests": 3,
                "rate_limits": 1,
                "successes": 1,
                "rejected": 1,
                "errors": 1,
            }
        }
    )
    assert "LLM-статистика:" in text
    assert "принято 1" in text
    assert "отклонено 1" in text
    assert "тех. ошибок 1" in text


def test_merge_llm_stats_accumulates_across_chats():
    merged = bot._merge_llm_stats(
        {"model/a": {"requests": 1, "rate_limits": 0, "successes": 1, "rejected": 0, "errors": 0}},
        {
            "model/a": {"requests": 2, "rate_limits": 1, "successes": 0, "rejected": 1, "errors": 0},
            "model/b": {"requests": 1, "rate_limits": 0, "successes": 1, "rejected": 0, "errors": 0},
        },
    )
    assert merged["model/a"] == {
        "requests": 3,
        "rate_limits": 1,
        "successes": 1,
        "rejected": 1,
        "errors": 0,
    }
    assert merged["model/b"]["requests"] == 1


def test_format_operation_summary_includes_aggregate_stats():
    text = bot._format_operation_summary(
        total_chats=2,
        processed_count=2,
        skipped_count=1,
        mark_as_read=True,
        llm_stats={
            "model/test": {
                "requests": 3,
                "rate_limits": 1,
                "successes": 1,
                "rejected": 1,
                "errors": 1,
            }
        },
    )
    assert "Обработано чатов: 2 (пропущено: 1)" in text
    assert "Сообщения отмечены как прочитанные" in text
    assert "LLM-статистика:" in text


def test_build_analysis_query_strips_schedule_and_mark_as_read():
    query = (
        "суммаризируй все непрочитанные чаты в папке AI, "
        "отметь их как прочитанные и присылай мне саммари каждый день в 10:00"
    )
    cleaned = bot._build_analysis_query(query)
    assert "отметь их как прочитанные" not in cleaned.lower()
    assert "каждый день" not in cleaned.lower()
    assert "10:00" not in cleaned
    assert "суммаризируй все непрочитанные чаты в папке AI" in cleaned


def test_build_analysis_query_strips_requested_model_clause():
    query = "суммаризируй папку AI с помощью anthropic/claude-opus-4.6"
    cleaned = bot._build_analysis_query(query)
    assert "claude-opus-4.6" not in cleaned
    assert "с помощью" not in cleaned.lower()
    assert cleaned == "суммаризируй папку AI"


def test_compact_query_for_display():
    assert bot._compact_query_for_display(None) == "(пусто)"
    assert bot._compact_query_for_display("  a   b   c  ", max_length=20) == "a b c"
    assert bot._compact_query_for_display("x" * 30, max_length=10) == "xxxxxxx..."


def test_looks_like_schedule_request_variants():
    assert bot._looks_like_schedule_request("суммаризируй каждый день в 20:00")
    assert bot._looks_like_schedule_request("раз в 3 дня в 19:30")
    assert bot._looks_like_schedule_request("weekly summary at 10:00")
    assert not bot._looks_like_schedule_request("суммаризируй чат работа")


def test_apply_schedule_intent_guard_ignores_hallucinated_schedule():
    recurrence_type, interval_days, schedule_time, schedule_time_missing = (
        bot._apply_schedule_intent_guard(
            schedule_intent=False,
            recurrence_type="daily",
            interval_days=None,
            schedule_time="10:00",
            schedule_time_missing=False,
            user_message="суммаризируй папку AI за вчера",
        )
    )
    assert recurrence_type is None
    assert interval_days is None
    assert schedule_time is None
    assert schedule_time_missing is False


def test_apply_schedule_intent_guard_keeps_explicit_schedule():
    recurrence_type, interval_days, schedule_time, schedule_time_missing = (
        bot._apply_schedule_intent_guard(
            schedule_intent=True,
            recurrence_type="interval_days",
            interval_days=3,
            schedule_time="19:30",
            schedule_time_missing=False,
            user_message="суммаризируй папку AI раз в 3 дня в 19:30",
        )
    )
    assert recurrence_type == "interval_days"
    assert interval_days == 3
    assert schedule_time == "19:30"
    assert schedule_time_missing is False


def test_apply_parser_intent_guards_fixes_hallucinated_unread_and_mark():
    target_type, period_type, period_value, mark_as_read = (
        bot._apply_parser_intent_guards(
            user_message="суммаризируй все чаты в папке AI за вчера",
            target_type="chat",
            period_type="unread",
            period_value=None,
            mark_as_read=True,
        )
    )

    assert target_type == "folder"
    assert period_type == "days"
    assert period_value == 1
    assert mark_as_read is False


def test_apply_parser_intent_guards_keeps_explicit_unread_and_mark():
    target_type, period_type, period_value, mark_as_read = (
        bot._apply_parser_intent_guards(
            user_message=(
                "суммаризируй непрочитанные сообщения в папке AI и отметь как прочитанные"
            ),
            target_type="folder",
            period_type="unread",
            period_value=None,
            mark_as_read=True,
        )
    )

    assert target_type == "folder"
    assert period_type == "unread"
    assert period_value is None
    assert mark_as_read is True


def test_has_mark_as_read_intent_with_intermediate_words():
    assert bot._has_mark_as_read_intent("отметь их как прочитанные")
    assert bot._has_mark_as_read_intent("пометь сообщения прочитанными")
    assert bot._has_mark_as_read_intent("mark them as read")
    assert not bot._has_mark_as_read_intent("суммаризируй чат")


def test_apply_parser_intent_guards_corrects_explicit_yesterday_from_today():
    target_type, period_type, period_value, mark_as_read = (
        bot._apply_parser_intent_guards(
            user_message="суммаризируй все чаты в папке AI за вчера",
            target_type="folder",
            period_type="today",
            period_value=None,
            mark_as_read=False,
        )
    )

    assert target_type == "folder"
    assert period_type == "days"
    assert period_value == 1
    assert mark_as_read is False


def test_init_scheduler_recomputes_stale_next_run(monkeypatch):
    now = datetime.now().astimezone()
    stale = (now - timedelta(days=1)).isoformat()
    records = [
        {
            "id": "sch1",
            "next_run": stale,
            "last_run": None,
            "recurrence_type": "daily",
            "time": now.strftime("%H:%M"),
            "interval_days": None,
            "weekday": None,
            "day_of_month": None,
        }
    ]
    scheduled = []

    class DummyScheduler:
        def __init__(self, timezone=None):
            self.timezone = timezone
            self.started = False

        def start(self):
            self.started = True

        def shutdown(self, wait=False):
            return None

    async def fake_load_and_refresh(now_local):
        refreshed = [item.copy() for item in records]
        refreshed[0]["next_run"] = (now_local + timedelta(days=1)).isoformat()
        return refreshed

    monkeypatch.setattr(bot, "scheduler", None)
    monkeypatch.setattr(bot, "AsyncIOScheduler", DummyScheduler)
    monkeypatch.setattr(
        bot, "_load_and_refresh_schedule_records", fake_load_and_refresh
    )
    monkeypatch.setattr(
        bot, "_schedule_next_job", lambda rec: scheduled.append(rec.copy())
    )

    asyncio.run(bot.init_scheduler(object()))

    assert scheduled
    rewritten = scheduled[0]["next_run"]
    assert bot._parse_iso_datetime(rewritten) > now

    asyncio.run(bot.shutdown_scheduler())


def test_load_and_refresh_schedule_records_skips_invalid(tmp_path, monkeypatch):
    now = datetime.now().astimezone()
    schedules_file = tmp_path / "schedules.db"
    save_schedules(
        schedules_file,
        [
            {
                "id": "ok1",
                "chat_id": 10,
                "target_type": "chat",
                "target_name": "Work",
                "period_type": "today",
                "period_value": None,
                "query": "q",
                "mark_as_read": False,
                "recurrence_type": "daily",
                "time": "20:00",
                "next_run": "",
                "last_run": None,
                "created_at": now.isoformat(),
                "interval_days": None,
                "weekday": None,
                "day_of_month": None,
            },
            {
                "id": "bad1",
                "chat_id": 10,
                "target_type": "chat",
                "target_name": "Work",
                "period_type": None,
                "period_value": None,
                "query": "q",
                "mark_as_read": False,
                "recurrence_type": "daily",
                "time": "99:99",
                "next_run": "",
                "last_run": None,
                "created_at": now.isoformat(),
                "interval_days": None,
                "weekday": None,
                "day_of_month": None,
            },
        ],
    )
    monkeypatch.setattr(bot, "SCHEDULES_FILE", schedules_file)

    records = asyncio.run(bot._load_and_refresh_schedule_records(now))
    assert len(records) == 1
    assert records[0]["id"] == "ok1"


def test_dialog_filter_helpers(monkeypatch):
    class FakeUser:
        def __init__(self, user_id, bot_user=False, contact=False):
            self.id = user_id
            self.bot = bot_user
            self.contact = contact

    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id

    class FakeChannel:
        def __init__(self, channel_id, megagroup=False):
            self.id = channel_id
            self.megagroup = megagroup

    monkeypatch.setattr(bot, "User", FakeUser)
    monkeypatch.setattr(bot, "Chat", FakeChat)
    monkeypatch.setattr(bot, "Channel", FakeChannel)

    group_dialog = SimpleNamespace(
        entity=FakeChat(10),
        unread_count=5,
        folder_id=0,
        notify_settings=None,
        dialog=None,
    )
    group_filter = SimpleNamespace(
        include_peers=[],
        exclude_peers=[],
        pinned_peers=[],
        bots=False,
        contacts=False,
        non_contacts=False,
        groups=True,
        broadcasts=False,
        exclude_read=False,
        exclude_muted=False,
        exclude_archived=False,
    )
    assert bot._dialog_in_filter(group_dialog, group_filter) is True

    group_filter.exclude_read = True
    group_dialog.unread_count = 0
    assert bot._dialog_in_filter(group_dialog, group_filter) is False

    include_only_filter = SimpleNamespace(
        include_peers=[SimpleNamespace(chat_id=10)],
        exclude_peers=[],
        pinned_peers=[],
        bots=False,
        contacts=False,
        non_contacts=False,
        groups=False,
        broadcasts=False,
        exclude_read=False,
        exclude_muted=False,
        exclude_archived=False,
    )
    group_dialog.unread_count = 0
    assert bot._dialog_in_filter(group_dialog, include_only_filter) is True
    include_only_filter.exclude_muted = True
    group_dialog.notify_settings = SimpleNamespace(
        mute_until=int(datetime.now(timezone.utc).timestamp()) + 3600
    )
    assert bot._dialog_in_filter(group_dialog, include_only_filter) is False
    include_only_filter.exclude_muted = False
    include_only_filter.exclude_archived = True
    group_dialog.notify_settings = None
    group_dialog.folder_id = 1
    assert bot._dialog_in_filter(group_dialog, include_only_filter) is False

    user_dialog = SimpleNamespace(
        entity=FakeUser(42, bot_user=True, contact=False),
        unread_count=1,
        folder_id=0,
        notify_settings=None,
        dialog=None,
    )
    bot_filter = SimpleNamespace(
        include_peers=[],
        exclude_peers=[],
        pinned_peers=[],
        bots=True,
        contacts=False,
        non_contacts=False,
        groups=False,
        broadcasts=False,
        exclude_read=False,
        exclude_muted=False,
        exclude_archived=False,
    )
    assert bot._dialog_in_filter(user_dialog, bot_filter) is True


def test_is_dialog_muted_variants():
    future_ts = int(datetime.now(timezone.utc).timestamp()) + 3600
    past_ts = int(datetime.now(timezone.utc).timestamp()) - 3600
    muted_dialog = SimpleNamespace(
        notify_settings=SimpleNamespace(mute_until=future_ts), dialog=None
    )
    unmuted_dialog = SimpleNamespace(
        notify_settings=SimpleNamespace(mute_until=past_ts), dialog=None
    )
    assert bot._is_dialog_muted(muted_dialog) is True
    assert bot._is_dialog_muted(unmuted_dialog) is False


def test_generate_channel_link():
    public_entity = SimpleNamespace(username="chan", id=-1001234567890, title="Channel")
    assert (
        bot.generate_channel_link(public_entity, message_id=15)
        == "https://t.me/chan/15"
    )
    assert bot.generate_channel_link(public_entity) == "https://t.me/chan"

    private_entity = SimpleNamespace(id=-1001234567890, title="Private")
    assert (
        bot.generate_channel_link(private_entity, message_id=42)
        == "https://t.me/c/1234567890/42"
    )
    assert bot.generate_channel_link(private_entity, message_id=0) is None
