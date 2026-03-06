import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import json
import requests
import time
from difflib import SequenceMatcher
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel
from telethon.tl.functions.messages import GetDialogFiltersRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.base import JobLookupError

import config
from llm_runtime import LLMRuntimeConfig
from schedule_runtime import (
    build_schedule_record,
    compute_next_run,
    load_schedules,
    recurrence_to_text,
    save_schedules,
)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Включаем логирование сетевых запросов
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("requests").setLevel(logging.INFO)

# Включаем логирование Telegram API запросов (для отладки)
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.INFO)
logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)

# Глобальные переменные для клиентов
telethon_client: Optional[TelegramClient] = None

# Текущий контекст (чат и период)
# {"chat_name": str, "period_type": str, "period_value": int}
current_context: Dict[str, Any] = {}
SCHEDULES_FILE = Path("schedules.json")
schedules_lock = asyncio.Lock()
scheduler: Optional[AsyncIOScheduler] = None
application_ref: Optional[Application] = None
SCHEDULE_RETRY_DELAY_SECONDS = 300

# Runtime-конфиг LLM (можно менять командами /seturl /settoken /setmodel)
llm_runtime = LLMRuntimeConfig(
    config.DEFAULT_LLM_URL,
    config.DEFAULT_LLM_TOKEN,
    config.DEFAULT_LLM_MODEL,
    config.DEFAULT_FALLBACK_LLM_URL,
    config.DEFAULT_FALLBACK_LLM_TOKEN,
    config.DEFAULT_FALLBACK_LLM_MODEL,
)

EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map symbols
    "\U0001f1e0-\U0001f1ff"  # flags (iOS)
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "\U0001f900-\U0001f9ff"  # Supplemental Symbols and Pictographs
    "\U0001fa00-\U0001fa6f"  # Chess Symbols
    "\U00002600-\U000026ff"  # Miscellaneous Symbols
    "]+",
    flags=re.UNICODE,
)

ALLOWED_TARGET_TYPES = {"chat", "folder", None}
ALLOWED_PERIOD_TYPES = {"days", "hours", "today", "last_messages", "unread", None}


def escape_markdown(text: str) -> str:
    """Экранирует спецсимволы Markdown для безопасной отправки в Telegram."""
    escape_chars = ["_", "*", "`", "["]
    for char in escape_chars:
        text = text.replace(char, "\\" + char)
    return text


# Декоратор для проверки прав доступа
def admin_only(func):
    """Декоратор для проверки, что команду вызывает админ"""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ADMIN_USER_ID:
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        return await func(update, context)

    return wrapper


def format_period_text(period_type: Optional[str], period_value: Optional[int]) -> str:
    """
    Форматирует период в читаемый текст

    Args:
        period_type: Тип периода
        period_value: Значение периода

    Returns:
        Текстовое описание периода
    """
    if period_type == "days" and period_value:
        return f"последние {period_value} {'день' if period_value == 1 else 'дней'}"
    elif period_type == "hours" and period_value:
        return f"последние {period_value} {'час' if period_value == 1 else 'часов'}"
    elif period_type == "today":
        return "сегодня"
    elif period_type == "last_messages" and period_value:
        return f"последние {period_value} сообщений"
    elif period_type == "unread":
        return "непрочитанные сообщения"
    else:
        return f"последние {config.DEFAULT_MESSAGES_LIMIT} сообщений"


def _looks_like_unread_request(
    user_message: Optional[str],
    query: Optional[str],
    period_type: Optional[str],
) -> bool:
    """
    Эвристика для запросов вида "все непрочитанные ...".
    Используется, чтобы не наследовать период из контекста (например, "today").
    """
    if period_type == "unread":
        return True
    if period_type is not None:
        return False
    haystack = f"{user_message or ''} {query or ''}".lower()
    return "непрочитан" in haystack or "unread" in haystack


def resolve_period_with_context(
    period_type: Optional[str],
    period_value: Optional[int],
    user_message: Optional[str],
    query: Optional[str],
    context_data: Dict[str, Any],
) -> tuple[Optional[str], Optional[int]]:
    """
    Возвращает итоговый период с учетом контекста и эвристики unread-запросов.
    """
    if _looks_like_unread_request(user_message, query, period_type):
        return "unread", None
    if period_type is None:
        return context_data.get("period_type"), context_data.get("period_value")
    return period_type, period_value


def call_llm_api(messages: list) -> str:
    """Вызов OpenRouter-compatible Chat Completions API."""
    try:
        if not llm_runtime.has_any_token():
            raise Exception("LLM токен не задан. Используй /settoken <token>.")

        candidates = llm_runtime.get_candidate_settings()
        primary = candidates[0]

        logger.info(f"🤖 Запрос к LLM: {primary.model}")

        logger.info(f"🧾 Сообщений в LLM запросе: {len(messages)}")
        if logger.isEnabledFor(logging.DEBUG):
            for msg in messages:
                role_emoji = (
                    "👤"
                    if msg["role"] == "user"
                    else "⚙️"
                    if msg["role"] == "system"
                    else "🤖"
                )
                preview = msg.get("content", "")[:100] + (
                    "..." if len(msg.get("content", "")) > 100 else ""
                )
                logger.debug(f"  {role_emoji} {msg['role']}: {preview}")

        def extract_error_details(resp) -> str:
            try:
                data = resp.json()
            except Exception:
                return resp.text[:300]

            error_obj = data.get("error")
            if isinstance(error_obj, dict):
                metadata = error_obj.get("metadata") or {}
                raw = metadata.get("raw")
                message = error_obj.get("message")
                if raw:
                    return str(raw)
                if message:
                    return str(message)
            return str(data)[:300]

        def build_headers(token: str) -> dict:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return headers

        logger.info(
            f"📤 HTTP POST candidates: {', '.join([c.model for c in candidates])}"
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"  Messages count: {len(messages)}")

        max_attempts = config.LLM_MAX_RETRIES
        response = None
        used_model = None
        used_url = None
        last_error = None

        for candidate_idx, candidate in enumerate(candidates, start=1):
            payload = {
                "model": candidate.model,
                "messages": messages,
            }
            headers = build_headers(candidate.token)
            logger.info(
                f"   Candidate {candidate_idx}/{len(candidates)}: model={candidate.model}, url={candidate.url}"
            )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  Payload size: {len(json.dumps(payload))} bytes")

            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.post(
                        candidate.url,
                        json=payload,
                        headers=headers,
                        timeout=config.LLM_REQUEST_TIMEOUT_SECONDS,
                    )
                except requests.exceptions.RequestException as e:
                    last_error = e
                    if isinstance(e, requests.exceptions.Timeout):
                        logger.error(
                            f"⏱️ Timeout от LLM (model={candidate.model}, timeout={config.LLM_REQUEST_TIMEOUT_SECONDS}s): {e}"
                        )
                        if candidate_idx < len(candidates):
                            logger.warning(
                                f"↪️ Timeout primary '{candidate.model}', сразу переключаюсь на fallback..."
                            )
                            response = None
                            break
                        raise Exception(
                            f"Timeout LLM API ({config.LLM_REQUEST_TIMEOUT_SECONDS}s): {str(e)}"
                        )
                    logger.error(
                        f"Ошибка HTTP запроса к API (model={candidate.model}, attempt {attempt}/{max_attempts}): {e}"
                    )
                    if attempt < max_attempts:
                        wait_seconds = 2 ** (attempt - 1)
                        time.sleep(wait_seconds)
                        continue
                    if candidate_idx < len(candidates):
                        logger.warning(
                            f"↪️ Ошибка primary candidate '{candidate.model}', переключаюсь на fallback..."
                        )
                        response = None
                        break
                    raise Exception(f"Ошибка LLM API: {str(e)}")

                logger.info(
                    f"📥 HTTP Response: {response.status_code} "
                    f"(model={candidate.model}, attempt {attempt}/{max_attempts})"
                )
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"  Response size: {len(response.text)} bytes")

                if response.status_code == 429 and attempt < max_attempts:
                    wait_seconds = 2 ** (attempt - 1)
                    details = extract_error_details(response)
                    logger.warning(
                        f"⏳ LLM rate limit (429), retry через {wait_seconds}s: {details}"
                    )
                    time.sleep(wait_seconds)
                    continue
                break

            if response is None:
                continue

            if response.status_code != 200 and candidate_idx < len(candidates):
                details = extract_error_details(response)
                next_model = candidates[candidate_idx].model
                logger.warning(
                    f"↪️ Переключаюсь на fallback-модель '{next_model}' после ошибки primary '{candidate.model}': {details}"
                )
                last_error = Exception(
                    f"Ошибка LLM API ({response.status_code}): {details}"
                )
                continue

            if response.status_code != 200:
                details = extract_error_details(response)
                logger.error(f"Ошибка API ({response.status_code}): {response.text}")
                if response.status_code == 429:
                    raise Exception(
                        f"Превышен лимит запросов к LLM API (429). {details}"
                    )
                raise Exception(f"Ошибка LLM API ({response.status_code}): {details}")

            try:
                result = response.json()
            except Exception as e:
                if candidate_idx < len(candidates):
                    logger.warning(
                        f"↪️ Некорректный JSON от primary '{candidate.model}', пробую fallback. Ошибка: {e}"
                    )
                    last_error = e
                    continue
                raise Exception(f"Ошибка парсинга ответа LLM API: {e}")

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"📋 Структура ответа: {list(result.keys())}")

            content = None
            if "choices" in result:
                content = result["choices"][0]["message"]["content"]
            else:
                err = Exception("API вернул неожиданный формат ответа")
                if candidate_idx < len(candidates):
                    logger.warning(
                        f"↪️ Неожиданный формат от primary '{candidate.model}', пробую fallback."
                    )
                    last_error = err
                    continue
                logger.error(
                    f"Неожиданный формат ответа API. Ключи верхнего уровня: {list(result.keys())}"
                )
                logger.error(
                    f"Полный ответ: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}"
                )
                raise err

            if not content:
                err = Exception("API вернул пустой ответ")
                if candidate_idx < len(candidates):
                    logger.warning(
                        f"↪️ Пустой ответ от primary '{candidate.model}', пробую fallback."
                    )
                    last_error = err
                    continue
                raise err

            used_model = candidate.model
            used_url = candidate.url
            logger.info(
                f"✅ Ответ от LLM получен (модель: {used_model}, url: {used_url})"
            )
            if logger.isEnabledFor(logging.DEBUG):
                preview = content[:200] + "..." if len(content) > 200 else content
                logger.debug(f"✅ Ответ от LLM: {preview}")
            return content

        if last_error:
            raise Exception(str(last_error))
        raise Exception("Не удалось получить корректный ответ от LLM API")

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка HTTP запроса к API: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Статус: {e.response.status_code}")
            logger.error(f"Ответ: {e.response.text}")
            if e.response.status_code == 429:
                raise Exception(
                    "Превышен лимит запросов к LLM API (429). Попробуйте чуть позже."
                )
        raise Exception(f"Ошибка LLM API: {str(e)}")

    except Exception as e:
        logger.error(f"Неожиданная ошибка при вызове LLM: {e}", exc_info=True)
        raise


async def init_telethon_client():
    """Инициализация Telethon клиента"""
    global telethon_client

    telethon_client = TelegramClient(
        config.SESSION_NAME, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH
    )

    await telethon_client.start(phone=config.TELEGRAM_PHONE)
    logger.info("✅ Telethon клиент подключен")


def _strip_markdown_json_fence(raw_text: str) -> str:
    """Убирает markdown-обертку ```json ... ``` из текста, если она есть."""
    cleaned_text = (raw_text or "").strip()
    if cleaned_text.startswith("```"):
        lines = cleaned_text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned_text = "\n".join(lines).strip()
    return cleaned_text


async def ensure_telethon_connected():
    """Проверяет подключение Telethon и переподключается при необходимости"""
    global telethon_client

    if telethon_client is None:
        logger.warning("⚠️ Telethon клиент не инициализирован, инициализирую...")
        await init_telethon_client()
        return

    if not telethon_client.is_connected():
        logger.warning("⚠️ Telethon отключен, переподключаюсь...")
        try:
            await telethon_client.connect()
            if not await telethon_client.is_user_authorized():
                logger.error(
                    "❌ Telethon не авторизован, требуется повторная авторизация"
                )
                await telethon_client.start(phone=config.TELEGRAM_PHONE)
            logger.info("✅ Telethon успешно переподключен")
        except Exception as e:
            logger.error(f"❌ Ошибка при переподключении Telethon: {e}")
            raise Exception(
                "Не удалось подключиться к Telegram. Проверьте интернет-соединение."
            )


async def parse_command_with_gpt(user_message: str) -> Dict[str, Any]:
    """
    Отправляет текст пользователя в GPT для парсинга команды

    Args:
        user_message: Текст сообщения пользователя

    Returns:
        Dict с структурированной командой в формате:
        {
            "target_type": "chat" | "folder" | null,
            "target_name": "название чата/папки" | null,
            "period_type": "days" | "hours" | "today" | "last_messages" | "unread" | null,
            "period_value": число дней/часов/сообщений | null,
            "mark_as_read": true | false,
            "query": "полный текст запроса пользователя",
            "recurrence_type": "daily" | "weekly" | "monthly" | "interval_days" | null,
            "interval_days": число | null,
            "time": "HH:MM" | null
        }
    """
    try:
        messages = [
            {"role": "system", "content": config.PARSER_PROMPT},
            {"role": "user", "content": user_message},
        ]

        response_text = await asyncio.to_thread(call_llm_api, messages)

        cleaned_text = _strip_markdown_json_fence(response_text)

        logger.debug(f"📋 Очищенный JSON для парсинга: {cleaned_text[:200]}")

        # Парсим JSON ответ
        command = json.loads(cleaned_text)
        return validate_command_payload(command)

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка при парсинге команды: {error_msg}")
        return {"error": error_msg}


def validate_command_payload(command: dict) -> Dict[str, Any]:
    """Проверяет и нормализует структуру команды, полученной от LLM."""
    if not isinstance(command, dict):
        raise ValueError("Ответ парсера должен быть JSON-объектом")

    target_type = command.get("target_type")
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"Некорректный target_type: {target_type}")

    target_name = command.get("target_name")
    if target_name is not None:
        if not isinstance(target_name, str):
            raise ValueError("target_name должен быть строкой или null")
        target_name = target_name.strip() or None

    period_type = command.get("period_type")
    if period_type not in ALLOWED_PERIOD_TYPES:
        raise ValueError(f"Некорректный period_type: {period_type}")

    period_value = command.get("period_value")
    if period_type in ("days", "hours", "last_messages"):
        if not isinstance(period_value, int):
            raise ValueError(
                f"period_value должен быть числом для period_type={period_type}"
            )
        if period_value < 1:
            raise ValueError("period_value должен быть >= 1")
    else:
        period_value = None

    mark_as_read = command.get("mark_as_read")
    if not isinstance(mark_as_read, bool):
        mark_as_read = False

    query = command.get("query")
    if query is not None:
        if not isinstance(query, str):
            raise ValueError("query должен быть строкой или null")
        query = query.strip() or None

    recurrence_type = command.get("recurrence_type")
    allowed_recurrence = {"daily", "weekly", "monthly", "interval_days", None}
    if recurrence_type not in allowed_recurrence:
        raise ValueError(f"Некорректный recurrence_type: {recurrence_type}")

    interval_days = command.get("interval_days")
    if recurrence_type == "interval_days":
        if not isinstance(interval_days, int) or interval_days <= 0:
            raise ValueError(
                "Для recurrence_type=interval_days укажи interval_days > 0"
            )
    else:
        interval_days = None

    time_value = command.get("time")
    time_missing = False
    if recurrence_type is None:
        time_value = None
        interval_days = None
    elif time_value is None:
        time_missing = True
    elif not isinstance(time_value, str) or not re.match(
        r"^\d{1,2}:\d{2}$", time_value
    ):
        raise ValueError("Для расписания нужно время в формате HH:MM")
    else:
        hour, minute = [int(part) for part in time_value.split(":", 1)]
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Некорректное время. Используй диапазон 00:00..23:59")
        time_value = f"{hour:02d}:{minute:02d}"

    return {
        "target_type": target_type,
        "target_name": target_name,
        "period_type": period_type,
        "period_value": period_value,
        "mark_as_read": mark_as_read,
        "query": query,
        "recurrence_type": recurrence_type,
        "interval_days": interval_days,
        "time": time_value,
        "time_missing": time_missing,
    }


def _parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone()


def _schedule_job_id(schedule_id: str) -> str:
    return f"schedule:{schedule_id}"


async def _load_schedule_records() -> list[dict]:
    async with schedules_lock:
        return load_schedules(SCHEDULES_FILE)


async def _append_schedule_record(record: dict) -> None:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        records.append(record)
        save_schedules(SCHEDULES_FILE, records)


async def _delete_schedule_record(schedule_id: str) -> bool:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        idx = _find_schedule_index(records, schedule_id)
        if idx < 0:
            return False
        records.pop(idx)
        save_schedules(SCHEDULES_FILE, records)
        return True


async def _get_schedule_record(schedule_id: str) -> Optional[dict]:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        idx = _find_schedule_index(records, schedule_id)
        if idx < 0:
            return None
        return records[idx].copy()


async def _mark_schedule_success(
    schedule_id: str, run_time: datetime
) -> Optional[dict]:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        idx = _find_schedule_index(records, schedule_id)
        if idx < 0:
            return None
        records[idx]["last_run"] = run_time.isoformat()
        records[idx]["next_run"] = compute_next_run(records[idx], run_time).isoformat()
        updated = records[idx].copy()
        save_schedules(SCHEDULES_FILE, records)
        return updated


async def _load_and_refresh_schedule_records(now: datetime) -> list[dict]:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        changed = False
        for record in records:
            if not record.get("next_run"):
                record["next_run"] = compute_next_run(record, now).isoformat()
                changed = True
                continue
            try:
                parsed_next_run = _parse_iso_datetime(record["next_run"])
            except Exception:
                parsed_next_run = None
            if parsed_next_run is None or parsed_next_run <= now:
                record["next_run"] = compute_next_run(record, now).isoformat()
                changed = True
        if changed:
            save_schedules(SCHEDULES_FILE, records)
        return [item.copy() for item in records]


async def _schedule_retry_after_failure(
    schedule_id: str, delay_seconds: int = SCHEDULE_RETRY_DELAY_SECONDS
) -> Optional[dict]:
    async with schedules_lock:
        records = load_schedules(SCHEDULES_FILE)
        idx = _find_schedule_index(records, schedule_id)
        if idx < 0:
            return None
        retry_at = datetime.now().astimezone() + timedelta(seconds=delay_seconds)
        records[idx]["next_run"] = retry_at.isoformat()
        updated = records[idx].copy()
        save_schedules(SCHEDULES_FILE, records)
        return updated


def _find_schedule_index(records: list[dict], schedule_id: str) -> int:
    for idx, item in enumerate(records):
        if item.get("id") == schedule_id:
            return idx
    return -1


class _ScheduledMessageProxy:
    def __init__(self, bot_obj, chat_id: int):
        self._bot = bot_obj
        self._chat_id = chat_id

    async def reply_text(self, text: str, parse_mode: Optional[str] = None):
        return await self._bot.send_message(
            chat_id=self._chat_id, text=text, parse_mode=parse_mode
        )


class _SilentProcessingMessage:
    async def edit_text(self, text: str, parse_mode: Optional[str] = None):
        logger.info(f"🗓️ Schedule status: {text}")

    async def delete(self):
        return None


async def _execute_scheduled_summary(record: dict) -> tuple[int, int]:
    if application_ref is None:
        raise RuntimeError("Application reference is not initialized")

    chat_id = int(record["chat_id"])
    update_proxy = type(
        "ScheduledUpdate",
        (),
        {"message": _ScheduledMessageProxy(application_ref.bot, chat_id)},
    )()
    processing_msg = _SilentProcessingMessage()

    target_type = record.get("target_type")
    target_name = record.get("target_name")
    period_type = record.get("period_type")
    period_value = record.get("period_value")
    query = record.get("query") or "Суммаризируй"
    mark_as_read = bool(record.get("mark_as_read"))

    if target_type == "folder":
        chats_to_process, _, error = await _resolve_folder_chats(
            target_name, processing_msg
        )
    else:
        chats_to_process, _, error = await _resolve_single_chat(
            target_name, processing_msg
        )
    if error:
        raise RuntimeError(error)

    processed_count = 0
    skipped_count = 0
    total = len(chats_to_process)

    for idx, chat_data in enumerate(chats_to_process, 1):
        if len(chat_data) == 3:
            chat_entity, chat_name, unread_count = chat_data
        else:
            chat_entity, chat_name = chat_data
            unread_count = None
        try:
            success = await _process_single_chat(
                update_proxy,
                processing_msg,
                chat_entity,
                chat_name,
                idx,
                total,
                period_type,
                period_value,
                query,
                mark_as_read,
                unread_count,
            )
            if success:
                processed_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            logger.error(
                f"❌ Ошибка в расписании при обработке чата '{chat_name}': {e}"
            )
            skipped_count += 1

    return processed_count, skipped_count


def _schedule_next_job(record: dict) -> None:
    if scheduler is None:
        return
    schedule_id = record.get("id")
    if not schedule_id:
        return
    run_at = _parse_iso_datetime(record["next_run"])
    scheduler.add_job(
        run_scheduled_summary_job,
        trigger=DateTrigger(run_date=run_at),
        id=_schedule_job_id(schedule_id),
        args=[schedule_id],
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )


async def run_scheduled_summary_job(schedule_id: str) -> None:
    if application_ref is None:
        return

    record = await _get_schedule_record(schedule_id)
    if record is None:
        return

    chat_id = int(record["chat_id"])
    await application_ref.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Запускаю периодическую суммаризацию '{schedule_id}' ({recurrence_to_text(record)})",
    )

    now = datetime.now().astimezone()
    run_success = False
    try:
        processed_count, skipped_count = await _execute_scheduled_summary(record)
        summary = (
            f"✅ Периодическая суммаризация '{schedule_id}' завершена.\n"
            f"Обработано: {processed_count}"
        )
        if skipped_count:
            summary += f"\nПропущено: {skipped_count}"
        await application_ref.bot.send_message(chat_id=chat_id, text=summary)
        run_success = True
    except Exception as e:
        await application_ref.bot.send_message(
            chat_id=chat_id, text=f"❌ Ошибка расписания '{schedule_id}': {e}"
        )
        logger.error(
            f"❌ Ошибка при выполнении расписания '{schedule_id}': {e}", exc_info=True
        )
        retry_record = await _schedule_retry_after_failure(schedule_id)
        if retry_record:
            _schedule_next_job(retry_record)
        return

    if run_success:
        updated_record = await _mark_schedule_success(schedule_id, now)
        if updated_record:
            _schedule_next_job(updated_record)


async def init_scheduler(application: Application) -> None:
    global scheduler, application_ref
    application_ref = application
    if scheduler is None:
        scheduler = AsyncIOScheduler(timezone=datetime.now().astimezone().tzinfo)
        scheduler.start()

    now = datetime.now().astimezone()
    records = await _load_and_refresh_schedule_records(now)
    for record in records:
        _schedule_next_job(record)

    logger.info(f"🗓️ Планировщик инициализирован. Активных расписаний: {len(records)}")


async def shutdown_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None


def calculate_similarity(str1: str, str2: str) -> float:
    """Вычислить схожесть двух строк (0.0 - 1.0)"""
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def _is_connection_error(error_msg: str) -> bool:
    """Проверяет, является ли ошибка ошибкой соединения"""
    error_lower = error_msg.lower()
    return "disconnected" in error_lower or "connection" in error_lower


def _handle_telegram_error(e: Exception, action: str) -> Exception:
    """
    Обрабатывает ошибки Telegram и возвращает понятное исключение

    Args:
        e: Исходное исключение
        action: Описание действия для сообщения об ошибке

    Returns:
        Exception с понятным сообщением
    """
    error_msg = str(e)
    if _is_connection_error(error_msg):
        logger.error(f"❌ Ошибка подключения к Telegram при {action}: {e}")
        return Exception("Потеряно соединение с Telegram. Попробуйте еще раз.")
    else:
        logger.error(f"❌ Ошибка при {action}: {e}")
        return Exception(f"Ошибка при {action}: {error_msg}")


def find_best_match(
    search_query: str, items: list, get_title_func, fuzzy: bool = True
) -> tuple[Any, str, float]:
    """
    Обобщенная функция поиска лучшего совпадения среди списка элементов

    Args:
        search_query: Поисковый запрос
        items: Список элементов для поиска
        get_title_func: Функция для получения названия(й) из элемента - возвращает список вариантов
        fuzzy: Использовать нечеткий поиск

    Returns:
        Tuple (best_item, best_title, best_similarity) или (None, None, 0.0)
    """
    # Нормализуем поисковый запрос
    search_normalized = remove_emojis(search_query.lower()).strip()

    best_match = None
    best_title = None
    best_similarity = 0.0

    for item in items:
        # Получаем варианты названий для этого элемента
        title_variants = get_title_func(item)
        if not title_variants:
            continue

        for variant in title_variants:
            if not variant:
                continue

            variant_normalized = remove_emojis(variant.lower()).strip()

            # 1. Точное совпадение
            if search_normalized == variant_normalized:
                return item, variant, 1.0

            # 2. Поиск как подстрока
            if (
                search_normalized in variant_normalized
                or variant_normalized in search_normalized
            ):
                similarity = 0.9  # Высокий приоритет для вхождения
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = item
                    best_title = variant

            # 3. Нечеткий поиск
            if fuzzy:
                similarity = calculate_similarity(search_normalized, variant_normalized)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = item
                    best_title = variant

    return best_match, best_title, best_similarity


def get_chat_display_name(entity) -> str:
    """
    Получает отображаемое название для Telegram entity

    Args:
        entity: User, Chat или Channel из Telethon

    Returns:
        Строка с названием чата/пользователя
    """
    if isinstance(entity, User):
        title = entity.first_name or ""
        if entity.last_name:
            title += f" {entity.last_name}"
        return title or str(entity.id)
    elif isinstance(entity, (Chat, Channel)):
        return entity.title if hasattr(entity, "title") else str(entity.id)
    return str(entity.id)


def get_entity_title_variants(entity) -> list:
    """
    Получает список вариантов названий для Telegram entity

    Args:
        entity: User, Chat или Channel из Telethon

    Returns:
        Список строк с вариантами названий
    """
    if isinstance(entity, User):
        # Личная переписка
        title = entity.first_name or ""
        if entity.last_name:
            title += f" {entity.last_name}"
        if entity.username:
            return [title, entity.username, f"@{entity.username}"]
        return [title] if title else []
    elif isinstance(entity, (Chat, Channel)):
        # Группа или канал
        return [entity.title] if hasattr(entity, "title") else []
    return []


def utc_to_local(utc_dt: datetime) -> datetime:
    """
    Конвертирует UTC datetime в локальное время

    Args:
        utc_dt: datetime объект в UTC (может быть aware или naive)

    Returns:
        datetime в локальном часовом поясе
    """
    # Если datetime naive (без timezone), считаем что это UTC
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)

    # Конвертируем в локальное время
    local_dt = utc_dt.astimezone()
    return local_dt


def remove_emojis(text: str) -> str:
    """Удаляет эмодзи из текста для более точного поиска"""
    return EMOJI_PATTERN.sub("", text).strip()


def _peer_key_from_peer(peer) -> Optional[tuple[str, int]]:
    """Преобразует peer из DialogFilter в унифицированный ключ."""
    if hasattr(peer, "user_id"):
        return ("user", peer.user_id)
    if hasattr(peer, "chat_id"):
        return ("chat", peer.chat_id)
    if hasattr(peer, "channel_id"):
        return ("channel", peer.channel_id)
    return None


def _peer_key_from_entity(entity) -> Optional[tuple[str, int]]:
    """Преобразует Telegram entity в унифицированный ключ."""
    if isinstance(entity, User):
        return ("user", entity.id)
    if isinstance(entity, Chat):
        return ("chat", entity.id)
    if isinstance(entity, Channel):
        return ("channel", entity.id)
    return None


def _is_group_entity(entity) -> bool:
    """Проверяет, является ли entity группой/супергруппой."""
    if isinstance(entity, Chat):
        return True
    if isinstance(entity, Channel):
        return bool(getattr(entity, "megagroup", False))
    return False


def _is_broadcast_entity(entity) -> bool:
    """Проверяет, является ли entity каналом-вещателем."""
    return isinstance(entity, Channel) and not bool(getattr(entity, "megagroup", False))


def _is_dialog_muted(dialog) -> bool:
    """Проверяет, замьючен ли диалог."""
    settings = getattr(dialog, "notify_settings", None)
    if settings is None and hasattr(dialog, "dialog") and dialog.dialog is not None:
        settings = getattr(dialog.dialog, "notify_settings", None)
    if settings is None:
        return False

    mute_until = getattr(settings, "mute_until", None)
    if mute_until is None:
        return False

    now = datetime.now(timezone.utc)
    if isinstance(mute_until, int):
        return mute_until > int(now.timestamp())
    if isinstance(mute_until, datetime):
        if mute_until.tzinfo is None:
            mute_until = mute_until.replace(tzinfo=timezone.utc)
        return mute_until > now
    return False


def _compile_dialog_filter(dialog_filter: Any) -> Dict[str, Any]:
    """Подготавливает структуру фильтра папки для быстрого применения к диалогам."""
    include_keys = {
        key
        for key in (
            _peer_key_from_peer(peer)
            for peer in getattr(dialog_filter, "include_peers", [])
        )
        if key is not None
    }
    exclude_keys = {
        key
        for key in (
            _peer_key_from_peer(peer)
            for peer in getattr(dialog_filter, "exclude_peers", [])
        )
        if key is not None
    }
    pinned_keys = {
        key
        for key in (
            _peer_key_from_peer(peer)
            for peer in getattr(dialog_filter, "pinned_peers", [])
        )
        if key is not None
    }
    return {
        "include_keys": include_keys,
        "exclude_keys": exclude_keys,
        "pinned_keys": pinned_keys,
    }


def _dialog_in_filter(
    dialog, dialog_filter: Any, compiled_filter: Optional[Dict[str, Any]] = None
) -> bool:
    """Определяет, входит ли диалог в папку с учетом include/exclude и динамических правил."""
    entity = dialog.entity
    peer_key = _peer_key_from_entity(entity)
    if peer_key is None:
        return False

    compiled = compiled_filter or _compile_dialog_filter(dialog_filter)
    include_keys = compiled["include_keys"]
    exclude_keys = compiled["exclude_keys"]
    pinned_keys = compiled["pinned_keys"]

    explicit_include = peer_key in include_keys or peer_key in pinned_keys
    dynamic_include = False

    if isinstance(entity, User):
        is_bot = bool(getattr(entity, "bot", False))
        is_contact = bool(getattr(entity, "contact", False))
        if getattr(dialog_filter, "bots", False) and is_bot:
            dynamic_include = True
        if getattr(dialog_filter, "contacts", False) and is_contact:
            dynamic_include = True
        if (
            getattr(dialog_filter, "non_contacts", False)
            and not is_contact
            and not is_bot
        ):
            dynamic_include = True

    if getattr(dialog_filter, "groups", False) and _is_group_entity(entity):
        dynamic_include = True

    if getattr(dialog_filter, "broadcasts", False) and _is_broadcast_entity(entity):
        dynamic_include = True

    if not explicit_include and not dynamic_include:
        return False

    if peer_key in exclude_keys:
        return False

    if (
        getattr(dialog_filter, "exclude_read", False)
        and getattr(dialog, "unread_count", 0) == 0
    ):
        return False

    if getattr(dialog_filter, "exclude_muted", False) and _is_dialog_muted(dialog):
        return False

    if (
        getattr(dialog_filter, "exclude_archived", False)
        and getattr(dialog, "folder_id", None) == 1
    ):
        return False

    return True


async def find_chat_by_name(chat_name: str, fuzzy: bool = True):
    """
    Находит чат по имени с использованием Telethon и нечеткого поиска

    Args:
        chat_name: Название чата или имя пользователя
        fuzzy: Использовать нечеткий поиск при отсутствии точного совпадения

    Returns:
        Tuple (entity, display_name, similarity) или (None, None, 0)
    """
    try:
        await ensure_telethon_connected()

        logger.info(f"🔍 Поиск чата '{chat_name}' через Telegram API...")

        # Собираем все диалоги в список
        dialogs = []
        async for dialog in telethon_client.iter_dialogs():
            dialogs.append(dialog.entity)

        logger.info(f"📊 Просканировано диалогов: {len(dialogs)}")

        # Используем обобщенную функцию поиска
        best_match, best_name, best_similarity = find_best_match(
            chat_name, dialogs, get_entity_title_variants, fuzzy
        )

        if best_match and best_similarity >= 0.5:
            logger.info(
                f"✅ Найден чат: '{best_name}' (схожесть: {best_similarity:.1%})"
            )
            return best_match, best_name, best_similarity

        logger.warning(f"❌ Чат '{chat_name}' не найден среди {len(dialogs)} диалогов")
        return None, None, 0.0

    except Exception as e:
        raise _handle_telegram_error(e, "поиске чата")


async def get_folders() -> Dict[int, Any]:
    """
    Получает список всех папок пользователя

    Returns:
        Dict[folder_id -> dialog_filter_object]
    """
    try:
        await ensure_telethon_connected()

        # Получаем список фильтров (папок)
        result = await telethon_client(GetDialogFiltersRequest())

        folders = {}

        for dialog_filter in result:
            if hasattr(dialog_filter, "id") and hasattr(dialog_filter, "title"):
                folders[dialog_filter.id] = dialog_filter
                logger.debug(f"  - ID {dialog_filter.id}: {dialog_filter.title}")

        logger.info(f"📁 Найдено папок: {len(folders)}")

        return folders

    except Exception as e:
        logger.error(f"❌ Ошибка при получении папок: {e}")
        return {}


async def find_folder_by_name(
    folder_name: str, fuzzy: bool = True
) -> tuple[Optional[int], Optional[str], float, Optional[Any]]:
    """
    Находит папку по имени с использованием нечеткого поиска

    Args:
        folder_name: Название папки для поиска
        fuzzy: Использовать нечеткий поиск

    Returns:
        Tuple (folder_id, folder_title, similarity, dialog_filter) или (None, None, 0, None)
    """
    try:
        logger.info(f"📁 Поиск папки '{folder_name}'...")

        folders = await get_folders()
        if not folders:
            logger.warning("❌ Папки не найдены")
            return None, None, 0.0, None

        # Преобразуем словарь папок в список кортежей (folder_id, dialog_filter)
        folder_items = [
            (folder_id, dialog_filter) for folder_id, dialog_filter in folders.items()
        ]

        # Функция для извлечения названия папки
        def get_folder_title(item):
            return [item[1].title]  # item[1] - это dialog_filter

        # Используем обобщенную функцию поиска
        best_item, best_title, best_similarity = find_best_match(
            folder_name, folder_items, get_folder_title, fuzzy
        )

        if best_item and best_similarity >= 0.5:
            folder_id, dialog_filter = best_item
            logger.info(
                f"✅ Найдена папка: '{best_title}' (схожесть: {best_similarity:.1%})"
            )
            return folder_id, best_title, best_similarity, dialog_filter

        logger.warning(f"❌ Папка '{folder_name}' не найдена")
        return None, None, 0.0, None

    except Exception as e:
        logger.error(f"❌ Ошибка при поиске папки: {e}")
        raise Exception(f"Ошибка при поиске папки: {str(e)}")


async def get_chats_in_folder(dialog_filter: Any) -> list:
    """
    Получает список чатов в указанной папке

    Args:
        dialog_filter: Объект DialogFilter из Telethon

    Returns:
        Список кортежей (entity, display_name, unread_count)
    """
    try:
        await ensure_telethon_connected()

        folder_id = dialog_filter.id
        folder_title = dialog_filter.title
        logger.info(f"📂 Загрузка чатов из папки '{folder_title}' (ID {folder_id})...")

        include_count = len(getattr(dialog_filter, "include_peers", []) or [])
        exclude_count = len(getattr(dialog_filter, "exclude_peers", []) or [])
        logger.debug(f"  include_peers={include_count}, exclude_peers={exclude_count}")
        compiled_filter = _compile_dialog_filter(dialog_filter)

        chats = []
        checked_count = 0

        # Проходим по всем диалогам и проверяем, входят ли они в папку
        async for dialog in telethon_client.iter_dialogs():
            checked_count += 1
            entity = dialog.entity

            # Получаем название
            title = get_chat_display_name(entity)

            # Проверяем, входит ли этот чат в папку (explicit + dynamic rules)
            if _dialog_in_filter(dialog, dialog_filter, compiled_filter):
                logger.debug(f"  ✅ Чат '{title}' (ID: {entity.id}) в папке")
                chats.append((entity, title, getattr(dialog, "unread_count", 0) or 0))

        logger.info(f"📊 Проверено диалогов: {checked_count}")
        logger.info(f"✅ Найдено чатов в папке '{folder_title}': {len(chats)}")

        return chats

    except Exception as e:
        logger.error(f"❌ Ошибка при получении чатов из папки: {e}")
        raise Exception(f"Ошибка при получении чатов из папки: {str(e)}")


async def mark_chat_as_read(chat_entity) -> bool:
    """
    Отмечает все сообщения в чате как прочитанные

    Args:
        chat_entity: Entity чата из Telethon

    Returns:
        True если успешно, False если произошла ошибка
    """
    try:
        await ensure_telethon_connected()

        # Получаем название чата для логирования
        chat_name = get_chat_display_name(chat_entity)

        logger.info(f"📖 Отмечаю сообщения в чате '{chat_name}' как прочитанные...")

        # Отмечаем чат прочитанным
        await telethon_client.send_read_acknowledge(chat_entity)

        logger.info(f"✅ Чат '{chat_name}' отмечен как прочитанный")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка при пометке чата как прочитанного: {e}")
        return False


async def get_chat_history(
    chat_entity, period_type: str = None, period_value: int = None
) -> tuple[str, Optional[int]]:
    """
    Получает историю сообщений из чата за указанный период

    Args:
        chat_entity: Entity чата из Telethon
        period_type: Тип периода ("days", "hours", "today", "last_messages", None)
        period_value: Значение периода (количество дней/часов/сообщений)

    Returns:
        Tuple (отформатированная история переписки, ID первого сообщения в выборке)
    """
    try:
        # Проверяем подключение к Telegram
        await ensure_telethon_connected()

        logger.info(
            f"📥 Загрузка истории чата через Telegram API (период: {period_type}, значение: {period_value})..."
        )
        logger.info(
            f"⏰ Текущее время: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

        messages = []
        offset_date = None
        limit = None

        # Определяем параметры загрузки в зависимости от типа периода
        # Telethon работает с UTC, поэтому все offset_date должны быть в UTC
        if period_type == "days" and period_value:
            # За последние N дней
            offset_date = datetime.now(timezone.utc) - timedelta(days=period_value)
            logger.info(
                f"📅 Загружаю сообщения начиная с {offset_date.astimezone().strftime('%Y-%m-%d %H:%M:%S')} (локальное время)"
            )

        elif period_type == "hours" and period_value:
            # За последние N часов
            offset_date = datetime.now(timezone.utc) - timedelta(hours=period_value)
            logger.info(
                f"🕐 Загружаю сообщения начиная с {offset_date.astimezone().strftime('%Y-%m-%d %H:%M:%S')} (локальное время)"
            )

        elif period_type == "today":
            # Сегодня с начала суток (локальная полночь в UTC)
            # Получаем текущее локальное время
            now_local = datetime.now().astimezone()
            # Устанавливаем полночь в локальном часовом поясе
            midnight_local = now_local.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            # Конвертируем в UTC для Telethon
            offset_date = midnight_local.astimezone(timezone.utc)
            logger.info(
                "📅 Загружаю сообщения с начала суток (00:00 локального времени)"
            )

        elif period_type == "last_messages" and period_value:
            # Последние N сообщений
            limit = period_value

        else:
            # По умолчанию - последние N сообщений (настраивается в config)
            limit = config.DEFAULT_MESSAGES_LIMIT

        # Загружаем сообщения
        # Формируем параметры для iter_messages (не передаем None значения)
        iter_params = {"reverse": True}
        if offset_date is not None:
            iter_params["offset_date"] = offset_date
            logger.debug(f"  offset_date (UTC): {offset_date}")
            logger.debug(f"  offset_date (local): {offset_date.astimezone()}")
        if limit is not None:
            iter_params["limit"] = limit
            logger.debug(f"  limit: {limit}")
        first_message_id = None
        async for message in telethon_client.iter_messages(chat_entity, **iter_params):
            if message.text:
                # Запоминаем ID первого сообщения в выборке
                if first_message_id is None:
                    first_message_id = message.id

                sender_name = (
                    get_chat_display_name(message.sender)
                    if message.sender
                    else "Неизвестно"
                )

                # Конвертируем UTC время в локальное
                local_time = utc_to_local(message.date)
                timestamp = local_time.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender_name}: {message.text}")

        if not messages:
            logger.warning("⚠️ Сообщения за указанный период не найдены")
            return "", None

        logger.info(
            f"✅ Загружено сообщений: {len(messages)} (временные метки в локальном часовом поясе)"
        )
        history = "\n".join(messages)
        logger.debug(f"  Общий размер истории: {len(history)} символов")
        return history, first_message_id

    except Exception as e:
        raise _handle_telegram_error(e, "получении истории чата")


async def process_chat_with_openai(
    chat_history: str, query: str, period_context: str = None
) -> str:
    """
    Обрабатывает историю чата согласно запросу пользователя

    Args:
        chat_history: История переписки
        query: Запрос пользователя (может содержать команду суммаризировать, вопрос и т.д.)
        period_context: Контекст периода ("за неделю", "за последний час" и т.д.)

    Returns:
        Ответ от LLM
    """
    try:
        # Формируем контекст для LLM
        history_context = (
            f"История чата ({period_context})" if period_context else "История чата"
        )

        messages = [
            {"role": "system", "content": config.PROCESSOR_PROMPT},
            {
                "role": "user",
                "content": f"{history_context}:\n\n{chat_history}\n\nЗапрос: {query}",
            },
        ]

        answer = await asyncio.to_thread(call_llm_api, messages)
        return answer

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка при обработке запроса с LLM: {error_msg}")
        return f"❌ {error_msg}"


def split_markdown_chunks(text: str, max_length: int) -> list[str]:
    """
    Разбивает уже экранированный Markdown-текст на части безопасно для Telegram.
    Не оставляет завершающий backslash в чанке.
    """
    if max_length < 2:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_length, len(text))
        if end < len(text):
            while end > start and text[end - 1] == "\\":
                end -= 1
            if end == start:
                end = min(start + max_length, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


@admin_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для работы с историей твоих Telegram чатов и папок.\n\n"
        "Просто напиши мне, что тебе нужно, например:\n"
        "**Для чатов:**\n"
        "• 'Сделай суммаризацию за неделю из чата Работа'\n"
        "• 'О чем говорили в личке с Иваном сегодня?'\n"
        "• 'Покажи последние 500 сообщений из чата Проект'\n\n"
        "**Для папок:**\n"
        "• 'Что нового в папке Рабочие чаты?'\n"
        "• 'Суммаризируй папку Личное за неделю и пометь прочитанным'\n"
        "• 'До чего договорились в папке Проекты?'\n\n"
        "🗓️ Периодические задачи можно задавать обычным текстом:\n"
        "• 'Суммаризируй папку AI каждый день в 20:00 и отмечай прочитанным'\n\n"
        "💡 Если не указывать чат/папку/период, буду использовать предыдущий!\n"
        "📖 Добавь 'и отметь прочитанным' для автоматической пометки сообщений!\n\n"
        f"{format_llm_settings_text()}\n\n"
        "Используй /help для просмотра всех команд"
    )


@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help - показать все доступные команды"""
    await update.message.reply_text(
        "📋 **Доступные команды:**\n\n"
        "/start - приветствие и информация о боте\n"
        "/help - показать это сообщение\n"
        "/folders - показать список ваших папок\n"
        "/context - показать текущий сохраненный контекст\n"
        "/reset - сбросить контекст\n\n"
        "/schedules - показать периодические задачи\n"
        "/delschedule <id> - удалить периодическую задачу\n\n"
        "/llmconfig - показать текущие LLM настройки\n"
        "/seturl <url> - задать URL OpenAI-compatible API\n"
        "/setmodel <model> - задать модель\n"
        "/settoken <token> - задать API токен\n\n"
        "**Примеры для чатов:**\n"
        "• Суммаризируй чат Работа за неделю\n"
        "• О чем говорили в личке с Иваном сегодня?\n"
        "• Покажи последние 500 сообщений из чата Проект\n\n"
        "**Примеры для папок:**\n"
        "• Что нового в папке Рабочие чаты?\n"
        "• Суммаризируй папку Личное за неделю и пометь прочитанным\n"
        "• До чего договорились в папке Проекты?\n\n"
        "**Периодические задачи:**\n"
        "• Суммаризируй папку AI каждый день в 20:00\n"
        "• Суммаризируй чат Работа каждую неделю в 09:00\n"
        "• Суммаризируй папку Новости раз в 3 дня в 19:30\n\n"
        "💡 Бот запоминает последний чат/папку и период!\n"
        "📖 Добавь 'и отметь прочитанным' для автоматической пометки!\n\n"
        f"{format_llm_settings_text()}\n\n"
    )


@admin_only
async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущий контекст"""
    if not current_context:
        await update.message.reply_text("📭 Контекст пуст. Начните новый запрос!")
        return

    ctx = current_context
    period_info = format_period_text(ctx.get("period_type"), ctx.get("period_value"))

    await update.message.reply_text(
        f"📝 Текущий контекст:\n\n"
        f"Чат: {ctx.get('target_name', 'не указан')}\n"
        f"Тип: {ctx.get('target_type', 'chat')}\n"
        f"Период: {period_info}\n\n"
        f"Следующий запрос без указания чата/периода будет использовать эти настройки."
    )


@admin_only
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбросить контекст"""
    current_context.clear()
    await update.message.reply_text("🔄 Контекст сброшен!")


def format_llm_settings_text() -> str:
    """Форматирует текущие runtime настройки LLM для пользователя."""
    settings = llm_runtime.get_settings()
    fallback = llm_runtime.get_fallback_settings()
    return (
        "⚙️ Текущие LLM настройки:\n\n"
        "Primary:\n"
        f"• URL: {settings.url}\n"
        f"• Модель: {settings.model}\n"
        f"• Токен: {settings.masked_token()}\n\n"
        "Fallback:\n"
        f"• URL: {fallback.url}\n"
        f"• Модель: {fallback.model}\n"
        f"• Токен: {fallback.masked_token()}"
    )


@admin_only
async def llmconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие настройки LLM."""
    await update.message.reply_text(format_llm_settings_text())


@admin_only
async def seturl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить URL LLM API."""
    if not context.args:
        current_url = llm_runtime.get_settings().url
        await update.message.reply_text(
            f"Использование: /seturl <url>\nТекущий URL: {current_url}"
        )
        return

    raw_url = " ".join(context.args).strip()
    try:
        normalized = llm_runtime.set_url(raw_url)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    await update.message.reply_text(f"✅ URL обновлен:\n{normalized}")


@admin_only
async def setmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить модель для LLM API."""
    if not context.args:
        current_model = llm_runtime.get_settings().model
        await update.message.reply_text(
            f"Использование: /setmodel <model>\nТекущая модель: {current_model}"
        )
        return

    raw_model = " ".join(context.args).strip()
    try:
        model = llm_runtime.set_model(raw_model)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    await update.message.reply_text(f"✅ Модель обновлена: {model}")


@admin_only
async def settoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить токен для LLM API."""
    if not context.args:
        await update.message.reply_text(
            "Использование: /settoken <token>\n"
            f"Текущий токен: {llm_runtime.get_settings().masked_token()}"
        )
        return

    raw_token = " ".join(context.args).strip()
    try:
        masked = llm_runtime.set_token(raw_token)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    await update.message.reply_text(f"✅ Токен обновлен: {masked}")


@admin_only
async def schedules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все активные периодические задачи."""
    records = await _load_schedule_records()
    if not records:
        await update.message.reply_text("🗓️ Расписаний пока нет.")
        return

    lines = ["🗓️ Активные расписания:\n"]
    for rec in records:
        next_run = rec.get("next_run")
        next_run_text = (
            _parse_iso_datetime(next_run).strftime("%Y-%m-%d %H:%M")
            if next_run
            else "не задано"
        )
        period_text = format_period_text(
            rec.get("period_type"), rec.get("period_value")
        )
        lines.append(
            f"• ID: {rec.get('id')}\n"
            f"  Цель: {rec.get('target_type')} '{rec.get('target_name')}'\n"
            f"  Период: {period_text}\n"
            f"  Расписание: {recurrence_to_text(rec)}\n"
            f"  Следующий запуск: {next_run_text}\n"
        )

    await update.message.reply_text("\n".join(lines))


@admin_only
async def delschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет задачу расписания по ID."""
    if not context.args:
        await update.message.reply_text("Использование: /delschedule <id>")
        return

    schedule_id = context.args[0].strip()
    removed = await _delete_schedule_record(schedule_id)
    if not removed:
        await update.message.reply_text(
            f"❌ Расписание с ID '{schedule_id}' не найдено."
        )
        return

    if scheduler is not None:
        try:
            scheduler.remove_job(_schedule_job_id(schedule_id))
        except JobLookupError:
            pass
    await update.message.reply_text(f"✅ Расписание '{schedule_id}' удалено.")


def generate_channel_link(entity, message_id: int = None) -> str:
    """
    Генерирует ссылку на канал или сообщение.

    Args:
        entity: Entity канала/форума из Telethon
        message_id: ID сообщения (для приватных каналов)

    Returns:
        URL ссылка на канал/сообщение, или None если ссылка невозможна
    """
    # Для публичных каналов (с username)
    if hasattr(entity, "username") and entity.username:
        if message_id and message_id > 0:
            return f"https://t.me/{entity.username}/{message_id}"
        return f"https://t.me/{entity.username}"

    # Для обычных групп (Chat, не Channel) ссылки не работают
    # Это ограничение Telegram API - для приватных обычных групп нельзя создать ссылку
    if isinstance(entity, Chat):
        logger.debug(f"ℹ️ Обычная группа (Chat) '{entity.title}' - ссылка недоступна")
        return None

    # Для супергрупп и каналов (Channel)
    # В Telegram API супергруппы и каналы имеют ID формата -100XXXXXXXXXX
    # Для ссылок t.me/c/{channel_id}/{message_id} нужен ID без префикса -100

    # Преобразуем: -1001234567890 -> 1001234567890 -> 1234567890
    str_id = str(abs(entity.id))

    # Если ID начинается с "100", убираем этот префикс
    if str_id.startswith("100"):
        channel_id = str_id[3:]
    else:
        channel_id = str_id

    # Для приватных каналов нужен ID сообщения
    if not message_id or message_id < 1:
        logger.warning(f"⚠️ Нет корректного message_id для entity.id={entity.id}")
        return None

    logger.debug(
        f"🔗 generate_channel_link: entity.id={entity.id} -> channel_id={channel_id}, message_id={message_id}"
    )
    return f"https://t.me/c/{channel_id}/{message_id}"


@admin_only
async def folders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список папок пользователя"""
    processing_msg = await update.message.reply_text("Загружаю список папок... ⏳")

    try:
        folders = await get_folders()

        if not folders:
            await processing_msg.edit_text("📁 У вас нет пользовательских папок.")
            return

        # Формируем список папок
        folder_list = "📁 **Ваши папки:**\n\n"
        for folder_id, dialog_filter in sorted(folders.items()):
            folder_title = dialog_filter.title
            folder_list += f"• {folder_title} (ID: {folder_id})\n"

        folder_list += (
            "\n💡 Используйте папки в командах: _'Что нового в папке {название}'_"
        )

        await processing_msg.edit_text(folder_list, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"❌ Ошибка при получении списка папок: {e}")
        await processing_msg.edit_text(f"❌ Ошибка: {str(e)}")


async def _resolve_folder_chats(
    target_name: str, processing_msg
) -> tuple[list, Optional[str], Optional[str]]:
    """
    Находит папку и возвращает список чатов в ней

    Returns:
        Tuple (chats_to_process, folder_title, error_message)
    """
    await processing_msg.edit_text(f"Ищу папку '{target_name}'... 📁")
    try:
        folder_id, folder_title, similarity, dialog_filter = await find_folder_by_name(
            target_name, fuzzy=True
        )
    except Exception as e:
        return [], None, str(e)

    if folder_id is None or dialog_filter is None:
        return (
            [],
            None,
            f"Папка '{target_name}' не найдена. Попробуй указать название точнее.",
        )

    # Информируем о найденной папке
    if similarity < 1.0:
        await processing_msg.edit_text(
            f"✅ Найдена папка: '{folder_title}' (схожесть: {similarity:.0%})\n\nЗагружаю чаты... 📂"
        )
    else:
        await processing_msg.edit_text(
            f"✅ Папка найдена: '{folder_title}'\n\nЗагружаю чаты... 📂"
        )

    try:
        chats = await get_chats_in_folder(dialog_filter)
    except Exception as e:
        return [], folder_title, str(e)

    if not chats:
        return [], folder_title, f"В папке '{folder_title}' нет чатов."

    await processing_msg.edit_text(
        f"✅ Найдено {len(chats)} чатов в папке '{folder_title}'\n\nНачинаю обработку... 🔄"
    )
    return chats, folder_title, None


async def _get_unread_count_for_chat(chat_entity) -> Optional[int]:
    """Возвращает unread_count для конкретного чата из списка диалогов."""
    try:
        await ensure_telethon_connected()
        chat_id = getattr(chat_entity, "id", None)
        async for dialog in telethon_client.iter_dialogs():
            if getattr(dialog.entity, "id", None) == chat_id:
                return int(getattr(dialog, "unread_count", 0) or 0)
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить unread_count для чата: {e}")
    return None


async def _resolve_single_chat(
    target_name: str, processing_msg
) -> tuple[list, Optional[str], Optional[str]]:
    """
    Находит один чат и возвращает его в списке

    Returns:
        Tuple (chats_to_process, found_name, error_message)
    """
    await processing_msg.edit_text(f"Ищу чат '{target_name}'... 🔍")
    try:
        chat_entity, found_name, similarity = await find_chat_by_name(
            target_name, fuzzy=True
        )
    except Exception as e:
        return [], None, str(e)

    if not chat_entity:
        return (
            [],
            None,
            f"Чат '{target_name}' не найден. Попробуй указать название точнее.",
        )

    # Информируем о найденном чате
    if similarity < 1.0:
        await processing_msg.edit_text(
            f"✅ Найден чат: '{found_name}' (схожесть: {similarity:.0%})\n\nЗагружаю историю... 📥"
        )
    else:
        await processing_msg.edit_text(
            f"✅ Чат найден: '{found_name}'\n\nЗагружаю историю... 📥"
        )

    unread_count = await _get_unread_count_for_chat(chat_entity)
    return [(chat_entity, found_name, unread_count)], found_name, None


async def _process_single_chat(
    update: Update,
    processing_msg,
    chat_entity,
    chat_name: str,
    idx: int,
    total: int,
    period_type,
    period_value,
    query: str,
    mark_as_read: bool,
    unread_count: Optional[int] = None,
) -> bool:
    """
    Обрабатывает один чат и отправляет результат

    Returns:
        True если успешно обработан, False если пропущен
    """
    # Обновляем статус
    if total > 1:
        await processing_msg.edit_text(
            f"Обрабатываю чат {idx}/{total}: '{chat_name}'... 📥"
        )

    # Для режима unread загружаем именно объем непрочитанных сообщений (если он известен).
    effective_period_type = period_type
    effective_period_value = period_value
    if period_type == "unread":
        if unread_count is not None and unread_count <= 0:
            logger.info(f"⏭️ Пропускаем чат '{chat_name}': нет непрочитанных сообщений")
            return False
        effective_period_type = "last_messages"
        effective_period_value = (
            unread_count if unread_count is not None else config.DEFAULT_MESSAGES_LIMIT
        )

    # Получаем историю чата
    chat_history, first_message_id = await get_chat_history(
        chat_entity, effective_period_type, effective_period_value
    )

    # Проверяем, что история не пустая
    if not chat_history or first_message_id is None:
        logger.info(f"⏭️ Пропускаем чат '{chat_name}': {chat_history}")
        return False

    # Формируем контекст периода для LLM
    if period_type == "unread" and unread_count is not None:
        period_text = f"непрочитанные сообщения ({unread_count})"
    elif period_type == "unread":
        period_text = "непрочитанные сообщения"
    else:
        period_text = format_period_text(effective_period_type, effective_period_value)

    # Отправляем историю в LLM
    if total > 1:
        await processing_msg.edit_text(
            f"Анализирую чат {idx}/{total}: '{chat_name}'... 💭"
        )
    else:
        await processing_msg.edit_text("Анализирую переписку с помощью AI... 💭")

    result = await process_chat_with_openai(chat_history, query, period_text)

    # Генерируем ссылку на чат (ведет на первое суммаризированное сообщение)
    chat_link = generate_channel_link(chat_entity, message_id=first_message_id)

    # Формируем и отправляем результат
    use_markdown = (
        total > 1 or chat_link is not None
    )  # Используем Markdown, если есть ссылка
    if use_markdown:
        safe_chat_name = escape_markdown(chat_name)
        if chat_link:
            result_prefix = (
                f"💬 **[{safe_chat_name}]({chat_link})** ({period_text}):\n\n"
            )
        else:
            result_prefix = f"💬 **{safe_chat_name}** ({period_text}):\n\n"
    else:
        result_prefix = f"💬 Чат '{chat_name}' ({period_text}):\n\n"

    # Telegram имеет ограничение на длину сообщения
    max_length = 4096
    safe_result = escape_markdown(result) if use_markdown else result
    full_message = result_prefix + safe_result

    if len(full_message) <= max_length:
        await update.message.reply_text(
            full_message, parse_mode="Markdown" if use_markdown else None
        )
    else:
        await update.message.reply_text(
            result_prefix, parse_mode="Markdown" if use_markdown else None
        )
        for chunk in split_markdown_chunks(safe_result, max_length):
            await update.message.reply_text(
                chunk, parse_mode="Markdown" if use_markdown else None
            )

    # Если нужно отметить прочитанным
    if mark_as_read:
        read_success = await mark_chat_as_read(chat_entity)
        if read_success:
            logger.info(f"✅ Чат '{chat_name}' отмечен как прочитанный")
        else:
            logger.warning(f"⚠️ Не удалось отметить чат '{chat_name}' как прочитанный")

    return True


@admin_only
async def process_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений от пользователя"""
    user_message = update.message.text
    processing_msg = await update.message.reply_text("Обрабатываю твою команду... ⏳")

    try:
        if not llm_runtime.has_any_token():
            await processing_msg.edit_text(
                "❌ Ни primary, ни fallback LLM токен не заданы.\n\n"
                "Установи primary токен командой:\n"
                "/settoken <token>"
            )
            return

        # Шаг 1: Парсим команду с помощью LLM
        await processing_msg.edit_text("Анализирую команду... 🤖")
        command = await parse_command_with_gpt(user_message)

        if command.get("error"):
            error_text = command.get("error")
            if (
                "Превышен лимит запросов" in error_text
                or "Too Many Requests" in error_text
                or "429" in error_text
            ):
                await processing_msg.edit_text(
                    f"⚠️ {error_text}\n\n"
                    "Это ограничение API. Подождите немного и попробуйте снова."
                )
            elif "Ошибка авторизации" in error_text:
                await processing_msg.edit_text(
                    f"❌ {error_text}\n\nПроверьте настройки в файле .env"
                )
            else:
                await processing_msg.edit_text(f"❌ Ошибка: {error_text}")
            return

        # Шаг 2: Извлекаем параметры команды
        target_type = command.get("target_type")  # "chat" | "folder" | null
        target_name = command.get("target_name")  # название чата/папки
        period_type = command.get("period_type")
        period_value = command.get("period_value")
        mark_as_read = command.get(
            "mark_as_read", False
        )  # Отмечать ли сообщения прочитанными
        query = command.get("query") or user_message
        recurrence_type = command.get("recurrence_type")
        interval_days = command.get("interval_days")
        schedule_time = command.get("time")
        schedule_time_missing = command.get("time_missing", False)

        # Если цель не указана, используем из контекста
        if not target_name:
            if current_context.get("target_name"):
                target_name = current_context["target_name"]
                target_type = current_context.get("target_type", "chat")
                logger.info(f"Используем из контекста: {target_type} '{target_name}'")
            else:
                await processing_msg.edit_text(
                    "❌ Не удалось определить чат или папку. Укажите название в запросе."
                )
                return

        # Если тип не указан, считаем что это чат (для обратной совместимости)
        if not target_type:
            target_type = "chat"

        # Определяем итоговый период (с учетом контекста и unread-эвристики)
        period_type, period_value = resolve_period_with_context(
            period_type,
            period_value,
            user_message,
            query,
            current_context,
        )
        if period_type:
            logger.info(f"Итоговый период: {period_type}={period_value}")

        # Логируем, если нужно отмечать прочитанным
        if mark_as_read:
            logger.info("📖 Будут отмечены сообщения как прочитанные после обработки")

        # Шаг 3: При наличии периодичности создаем расписание вместо немедленного запуска
        if recurrence_type:
            if schedule_time_missing:
                await processing_msg.edit_text(
                    "⏰ Для периодической суммаризации укажи время, например: "
                    '"каждый день в 20:00".'
                )
                return

            now = datetime.now().astimezone()
            schedule_spec = {
                "recurrence_type": recurrence_type,
                "time": schedule_time,
                "interval_days": interval_days,
                "weekday": now.weekday() if recurrence_type == "weekly" else None,
                "day_of_month": now.day if recurrence_type == "monthly" else None,
            }
            schedule_record = build_schedule_record(
                target_type=target_type,
                target_name=target_name,
                period_type=period_type,
                period_value=period_value,
                query=query,
                mark_as_read=mark_as_read,
                chat_id=update.effective_chat.id,
                schedule_spec=schedule_spec,
                now_local=now,
            )

            await _append_schedule_record(schedule_record)
            _schedule_next_job(schedule_record)

            next_run_text = _parse_iso_datetime(schedule_record["next_run"]).strftime(
                "%Y-%m-%d %H:%M"
            )
            await processing_msg.edit_text(
                "✅ Периодическая суммаризация сохранена.\n\n"
                f"ID: {schedule_record['id']}\n"
                f"Цель: {target_type} '{target_name}'\n"
                f"Период суммаризации: {format_period_text(period_type, period_value)}\n"
                f"Расписание: {recurrence_to_text(schedule_record)}\n"
                f"Следующий запуск: {next_run_text}\n\n"
                "Управление:\n"
                "• /schedules\n"
                f"• /delschedule {schedule_record['id']}"
            )
            return

        # Шаг 4: Определяем список чатов для обработки
        if target_type == "folder":
            chats_to_process, resolved_name, error = await _resolve_folder_chats(
                target_name, processing_msg
            )
        else:
            chats_to_process, resolved_name, error = await _resolve_single_chat(
                target_name, processing_msg
            )

        if error:
            await processing_msg.edit_text(f"❌ {error}")
            return

        # Сохраняем успешный контекст для следующего запроса
        current_context["target_type"] = target_type
        current_context["target_name"] = resolved_name or target_name
        current_context["period_type"] = period_type
        current_context["period_value"] = period_value

        # Шаг 5: Обрабатываем каждый чат
        processed_count = 0
        skipped_count = 0
        total = len(chats_to_process)

        for idx, chat_data in enumerate(chats_to_process, 1):
            if len(chat_data) == 3:
                chat_entity, chat_name, unread_count = chat_data
            else:
                chat_entity, chat_name = chat_data
                unread_count = None
            try:
                success = await _process_single_chat(
                    update,
                    processing_msg,
                    chat_entity,
                    chat_name,
                    idx,
                    total,
                    period_type,
                    period_value,
                    query,
                    mark_as_read,
                    unread_count,
                )
                if success:
                    processed_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке чата '{chat_name}': {e}")
                await update.message.reply_text(
                    f"❌ Ошибка при обработке чата '{chat_name}': {str(e)[:200]}"
                )
                skipped_count += 1

        # Удаляем сообщение о процессе
        await processing_msg.delete()

        # Итоговое сообщение для папок
        if len(chats_to_process) > 1:
            summary = f"✅ Обработано чатов: {processed_count}"
            if skipped_count > 0:
                summary += f" (пропущено: {skipped_count})"
            if mark_as_read and processed_count > 0:
                summary += "\n📖 Сообщения отмечены как прочитанные"
            await update.message.reply_text(summary)
        elif mark_as_read and processed_count > 0:
            # Для одного чата тоже покажем
            await update.message.reply_text("📖 Сообщения отмечены как прочитанные")

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        try:
            await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)[:200]}")
        except Exception:
            # Если не удалось отредактировать, отправляем новое сообщение
            try:
                await update.message.reply_text(f"❌ Произошла ошибка: {str(e)[:200]}")
            except Exception:
                logger.error("Не удалось отправить сообщение об ошибке пользователю")


async def main():
    """Основная функция запуска бота"""
    required_issues, optional_issues = config.get_config_issues()
    if required_issues:
        logger.error(
            "❌ Конфигурация неполная. Заполните обязательные переменные в .env:"
        )
        for key, description in required_issues:
            logger.error(f"  - {key}: {description}")
        logger.error("Используйте env.example как шаблон.")
        return

    if optional_issues and not llm_runtime.has_any_token():
        for key, description in optional_issues:
            logger.warning(f"⚠️ {key} не задан: {description}")
        logger.warning("LLM токен можно задать после запуска бота через /settoken.")

    # Инициализируем Telethon клиент
    await init_telethon_client()

    # Создаем приложение бота
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("folders", folders_command))
    application.add_handler(CommandHandler("context", context_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("llmconfig", llmconfig_command))
    application.add_handler(CommandHandler("seturl", seturl_command))
    application.add_handler(CommandHandler("setmodel", setmodel_command))
    application.add_handler(CommandHandler("settoken", settoken_command))
    application.add_handler(CommandHandler("schedules", schedules_command))
    application.add_handler(CommandHandler("delschedule", delschedule_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_message)
    )

    # Инициализируем и запускаем бота
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await init_scheduler(application)

    logger.info("✅ Бот запущен и готов к работе! Нажмите Ctrl+C для остановки")

    try:
        # Держим бота запущенным
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # Корректное завершение
        logger.info("Остановка бота...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await shutdown_scheduler()

        # Отключаем Telethon
        if telethon_client and telethon_client.is_connected():
            await telethon_client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
