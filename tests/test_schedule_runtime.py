import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from schedule_runtime import (
    RECURRENCE_DAILY,
    RECURRENCE_INTERVAL_DAYS,
    RECURRENCE_MONTHLY,
    RECURRENCE_WEEKLY,
    build_schedule_record,
    compute_next_run,
    load_schedules,
    recurrence_to_text,
    save_schedules,
)


def test_compute_next_run_variants():
    now = datetime(2026, 3, 6, 19, 0, tzinfo=timezone.utc).astimezone()

    daily = {"recurrence_type": RECURRENCE_DAILY, "time": "20:00"}
    assert compute_next_run(daily, now).hour == 20

    weekly = {
        "recurrence_type": RECURRENCE_WEEKLY,
        "time": "20:00",
        "weekday": now.weekday(),
    }
    assert compute_next_run(weekly, now).hour == 20

    monthly = {
        "recurrence_type": RECURRENCE_MONTHLY,
        "time": "20:00",
        "day_of_month": now.day,
    }
    assert compute_next_run(monthly, now).hour == 20

    interval = {
        "recurrence_type": RECURRENCE_INTERVAL_DAYS,
        "time": "20:00",
        "interval_days": 3,
        "last_run": now.isoformat(),
    }
    assert (compute_next_run(interval, now) - now).days >= 2


def test_build_record_save_load_and_recurrence_text(tmp_path: Path):
    now = datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc).astimezone()
    rec = build_schedule_record(
        target_type="folder",
        target_name="AI",
        period_type="unread",
        period_value=None,
        query="суммаризируй",
        requested_model="anthropic/claude-opus-4.6",
        mark_as_read=True,
        chat_id=123,
        schedule_spec={
            "recurrence_type": RECURRENCE_DAILY,
            "time": "20:00",
            "interval_days": None,
            "weekday": None,
            "day_of_month": None,
        },
        folder_mode="combined",
        now_local=now,
    )
    assert rec["id"]
    assert rec["next_run"]
    assert "каждый день" in recurrence_to_text(rec)

    file_path = tmp_path / "schedules.db"
    save_schedules(file_path, [rec])
    loaded = load_schedules(file_path)
    assert loaded and loaded[0]["id"] == rec["id"]
    assert loaded[0]["folder_mode"] == "combined"
    assert loaded[0]["requested_model"] == "anthropic/claude-opus-4.6"


def test_load_schedules_invalid_sqlite_returns_empty(tmp_path: Path):
    file_path = tmp_path / "schedules.db"
    file_path.write_text("{not-a-sqlite-db", encoding="utf-8")
    assert load_schedules(file_path) == []


def test_compute_next_run_edge_cases_and_validation():
    december = datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)
    next_month = compute_next_run(
        {
            "recurrence_type": RECURRENCE_MONTHLY,
            "time": "20:00",
            "day_of_month": 31,
        },
        december,
    )
    assert next_month == datetime(2027, 1, 31, 20, 0, tzinfo=timezone.utc)

    february = datetime(2027, 2, 1, 10, 0, tzinfo=timezone.utc)
    clamped = compute_next_run(
        {
            "recurrence_type": RECURRENCE_MONTHLY,
            "time": "20:00",
            "day_of_month": 31,
        },
        february,
    )
    assert clamped == datetime(2027, 2, 28, 20, 0, tzinfo=timezone.utc)

    interval_now = datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)
    corrected_interval = compute_next_run(
        {
            "recurrence_type": RECURRENCE_INTERVAL_DAYS,
            "time": "20:00",
            "interval_days": -3,
            "last_run": None,
        },
        interval_now,
    )
    assert corrected_interval == interval_now.replace(
        hour=20, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    caught_up = compute_next_run(
        {
            "recurrence_type": RECURRENCE_INTERVAL_DAYS,
            "time": "20:00",
            "interval_days": 2,
            "last_run": datetime(
                2026, 3, 1, 20, 0, tzinfo=timezone.utc
            ).isoformat(),
        },
        interval_now,
    )
    assert caught_up == datetime(2026, 3, 11, 20, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="Некорректный формат времени"):
        compute_next_run({"recurrence_type": RECURRENCE_DAILY, "time": "20"}, interval_now)

    with pytest.raises(ValueError, match="Неизвестный recurrence_type"):
        compute_next_run({"recurrence_type": "yearly", "time": "20:00"}, interval_now)


def test_recurrence_to_text_variants():
    assert recurrence_to_text(
        {"recurrence_type": RECURRENCE_WEEKLY, "time": "09:00"}
    ) == "каждую неделю в 09:00"
    assert recurrence_to_text(
        {
            "recurrence_type": RECURRENCE_MONTHLY,
            "time": "10:30",
            "day_of_month": 6,
        }
    ) == "каждый месяц (6-го) в 10:30"
    assert recurrence_to_text(
        {
            "recurrence_type": RECURRENCE_INTERVAL_DAYS,
            "time": "19:30",
            "interval_days": 3,
        }
    ) == "раз в 3 дн. в 19:30"
    assert recurrence_to_text({"recurrence_type": "unknown"}) == (
        "неизвестное расписание"
    )


def test_load_schedules_migrates_requested_model_column(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                last_run TEXT,
                next_run TEXT,
                chat_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_name TEXT NOT NULL,
                period_type TEXT,
                period_value INTEGER,
                query TEXT NOT NULL,
                mark_as_read INTEGER NOT NULL,
                recurrence_type TEXT NOT NULL,
                time TEXT NOT NULL,
                interval_days INTEGER,
                weekday INTEGER,
                day_of_month INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO schedules (
                id, created_at, last_run, next_run, chat_id, target_type,
                target_name, period_type, period_value, query, mark_as_read,
                recurrence_type, time, interval_days, weekday, day_of_month
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy01",
                "2026-03-06T10:00:00+00:00",
                None,
                "2026-03-07T20:00:00+00:00",
                123,
                "chat",
                "Release",
                "days",
                1,
                "summary",
                0,
                RECURRENCE_DAILY,
                "20:00",
                None,
                None,
                None,
            ),
        )

    loaded = load_schedules(db_path)
    assert loaded[0]["id"] == "legacy01"
    assert loaded[0]["requested_model"] is None
    assert loaded[0]["folder_mode"] is None
    assert loaded[0]["mark_as_read"] is False
