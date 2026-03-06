import logging
import sqlite3
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


RECURRENCE_DAILY = "daily"
RECURRENCE_WEEKLY = "weekly"
RECURRENCE_MONTHLY = "monthly"
RECURRENCE_INTERVAL_DAYS = "interval_days"
logger = logging.getLogger(__name__)

_SCHEDULE_COLUMNS = [
    "id",
    "created_at",
    "last_run",
    "next_run",
    "chat_id",
    "target_type",
    "target_name",
    "period_type",
    "period_value",
    "query",
    "mark_as_read",
    "recurrence_type",
    "time",
    "interval_days",
    "weekday",
    "day_of_month",
]


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
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


def _row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    schedule = {key: row[key] for key in _SCHEDULE_COLUMNS}
    schedule["mark_as_read"] = bool(schedule["mark_as_read"])
    return schedule


def _parse_time_hhmm(value: str) -> tuple[int, int]:
    parts = (value or "").split(":")
    if len(parts) != 2:
        raise ValueError(f"Некорректный формат времени: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    return hour, minute


def _build_local_datetime(
    now_local: datetime, year: int, month: int, day: int, hour: int, minute: int
) -> datetime:
    last_day = monthrange(year, month)[1]
    safe_day = min(day, last_day)
    return now_local.replace(
        year=year,
        month=month,
        day=safe_day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def compute_next_run(
    schedule: dict[str, Any], now_local: Optional[datetime] = None
) -> datetime:
    now = now_local or datetime.now().astimezone()
    recurrence_type = schedule["recurrence_type"]
    hour, minute = _parse_time_hhmm(schedule["time"])

    if recurrence_type == RECURRENCE_DAILY:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if recurrence_type == RECURRENCE_WEEKLY:
        weekday = int(schedule.get("weekday", now.weekday()))
        days_ahead = (weekday - now.weekday()) % 7
        candidate = now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        ) + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    if recurrence_type == RECURRENCE_MONTHLY:
        day_of_month = int(schedule.get("day_of_month") or now.day)
        candidate = _build_local_datetime(
            now, now.year, now.month, day_of_month, hour, minute
        )
        if candidate <= now:
            year = now.year
            month = now.month + 1
            if month == 13:
                month = 1
                year += 1
            candidate = _build_local_datetime(
                now, year, month, day_of_month, hour, minute
            )
        return candidate

    if recurrence_type == RECURRENCE_INTERVAL_DAYS:
        interval_days = int(schedule.get("interval_days") or 1)
        if interval_days <= 0:
            interval_days = 1

        last_run_raw = schedule.get("last_run")
        if last_run_raw:
            last_run = datetime.fromisoformat(last_run_raw).astimezone(now.tzinfo)
            base = last_run.replace(hour=hour, minute=minute, second=0, microsecond=0)
            candidate = base + timedelta(days=interval_days)
            while candidate <= now:
                candidate += timedelta(days=interval_days)
            return candidate

        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=interval_days)
        return candidate

    raise ValueError(f"Неизвестный recurrence_type: {recurrence_type}")


def build_schedule_record(
    *,
    target_type: str,
    target_name: str,
    period_type: Optional[str],
    period_value: Optional[int],
    query: str,
    mark_as_read: bool,
    chat_id: int,
    schedule_spec: dict[str, Any],
    now_local: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now_local or datetime.now().astimezone()
    record: dict[str, Any] = {
        "id": uuid4().hex[:8],
        "created_at": now.isoformat(),
        "last_run": None,
        "next_run": "",
        "chat_id": int(chat_id),
        "target_type": target_type,
        "target_name": target_name,
        "period_type": period_type,
        "period_value": period_value,
        "query": query,
        "mark_as_read": bool(mark_as_read),
        "recurrence_type": schedule_spec["recurrence_type"],
        "time": schedule_spec["time"],
        "interval_days": schedule_spec.get("interval_days"),
        "weekday": schedule_spec.get("weekday"),
        "day_of_month": schedule_spec.get("day_of_month"),
    }
    record["next_run"] = compute_next_run(record, now).isoformat()
    return record


def load_schedules(path: Path) -> list[dict[str, Any]]:
    try:
        with _open_db(path) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"SELECT {', '.join(_SCHEDULE_COLUMNS)} FROM schedules ORDER BY rowid"
            ).fetchall()
    except sqlite3.DatabaseError as e:
        logger.error(f"Некорректная SQLite база расписаний в {path}: {e}")
        return []
    return [_row_to_schedule(row) for row in rows]


def save_schedules(path: Path, schedules: list[dict[str, Any]]) -> None:
    rows = []
    for schedule in schedules:
        rows.append(
            (
                schedule.get("id"),
                schedule.get("created_at"),
                schedule.get("last_run"),
                schedule.get("next_run"),
                int(schedule.get("chat_id")),
                schedule.get("target_type"),
                schedule.get("target_name"),
                schedule.get("period_type"),
                schedule.get("period_value"),
                schedule.get("query") or "",
                1 if schedule.get("mark_as_read") else 0,
                schedule.get("recurrence_type"),
                schedule.get("time"),
                schedule.get("interval_days"),
                schedule.get("weekday"),
                schedule.get("day_of_month"),
            )
        )

    with _open_db(path) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM schedules")
        if rows:
            conn.executemany(
                """
                INSERT INTO schedules (
                    id,
                    created_at,
                    last_run,
                    next_run,
                    chat_id,
                    target_type,
                    target_name,
                    period_type,
                    period_value,
                    query,
                    mark_as_read,
                    recurrence_type,
                    time,
                    interval_days,
                    weekday,
                    day_of_month
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def recurrence_to_text(schedule: dict[str, Any]) -> str:
    recurrence_type = schedule.get("recurrence_type")
    at_time = schedule.get("time") or "??:??"
    if recurrence_type == RECURRENCE_DAILY:
        return f"каждый день в {at_time}"
    if recurrence_type == RECURRENCE_WEEKLY:
        return f"каждую неделю в {at_time}"
    if recurrence_type == RECURRENCE_MONTHLY:
        day_of_month = schedule.get("day_of_month")
        return f"каждый месяц ({day_of_month}-го) в {at_time}"
    if recurrence_type == RECURRENCE_INTERVAL_DAYS:
        interval_days = schedule.get("interval_days")
        return f"раз в {interval_days} дн. в {at_time}"
    return "неизвестное расписание"
