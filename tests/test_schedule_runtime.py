from datetime import datetime, timezone
from pathlib import Path

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
        mark_as_read=True,
        chat_id=123,
        schedule_spec={
            "recurrence_type": RECURRENCE_DAILY,
            "time": "20:00",
            "interval_days": None,
            "weekday": None,
            "day_of_month": None,
        },
        now_local=now,
    )
    assert rec["id"]
    assert rec["next_run"]
    assert "каждый день" in recurrence_to_text(rec)

    file_path = tmp_path / "schedules.db"
    save_schedules(file_path, [rec])
    loaded = load_schedules(file_path)
    assert loaded and loaded[0]["id"] == rec["id"]


def test_load_schedules_invalid_sqlite_returns_empty(tmp_path: Path):
    file_path = tmp_path / "schedules.db"
    file_path.write_text("{not-a-sqlite-db", encoding="utf-8")
    assert load_schedules(file_path) == []
