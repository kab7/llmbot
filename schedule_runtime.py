import json
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


RECURRENCE_DAILY = "daily"
RECURRENCE_WEEKLY = "weekly"
RECURRENCE_MONTHLY = "monthly"
RECURRENCE_INTERVAL_DAYS = "interval_days"


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
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_schedules(path: Path, schedules: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(schedules, ensure_ascii=False, indent=2), encoding="utf-8"
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
