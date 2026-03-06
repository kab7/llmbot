import os
from dotenv import load_dotenv

load_dotenv()


def _parse_int_env(name: str, default: int = 0) -> int:
    """Безопасно парсит целое значение из env; при ошибке возвращает default."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Telegram Bot API
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Telethon (User client)
TELEGRAM_API_ID = _parse_int_env("TELEGRAM_API_ID", 0)
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

# Admin user ID (who can use the bot)
ADMIN_USER_ID = _parse_int_env("ADMIN_USER_ID", 0)

# Session file for Telethon
# Telethon сохраняет сессию в файл для избежания повторной авторизации.
# Это БЕЗОПАСНО: файл содержит зашифрованные данные авторизации.
# Файл автоматически добавлен в .gitignore.
# Рекомендуется установить права доступа: chmod 600 telethon_session.session
SESSION_NAME = "telethon_session"

# Настройки по умолчанию
# Количество сообщений для загрузки, если период не указан
DEFAULT_MESSAGES_LIMIT = 300
LLM_REQUEST_TIMEOUT_SECONDS = max(5, _parse_int_env("LLM_REQUEST_TIMEOUT_SECONDS", 20))
LLM_MAX_RETRIES = max(1, _parse_int_env("LLM_MAX_RETRIES", 3))

# LLM настройки по умолчанию (OpenRouter-compatible API)
# Можно переопределить через команды /seturl /settoken /setmodel во время работы бота.
# PRIMARY_LLM_* - основной namespace переменных.
DEFAULT_LLM_URL = (
    os.getenv("PRIMARY_LLM_URL") or "https://openrouter.ai/api/v1/chat/completions"
)
DEFAULT_LLM_MODEL = (
    os.getenv("PRIMARY_LLM_MODEL") or "meta-llama/llama-3.3-70b-instruct:free"
)
DEFAULT_LLM_TOKEN = os.getenv("PRIMARY_LLM_API_KEY", "")
DEFAULT_FALLBACK_LLM_URL = os.getenv(
    "FALLBACK_LLM_URL", "https://openrouter.ai/api/v1/chat/completions"
)
DEFAULT_FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", "openrouter/free")
DEFAULT_FALLBACK_LLM_TOKEN = os.getenv("FALLBACK_LLM_TOKEN", DEFAULT_LLM_TOKEN)

REQUIRED_CONFIG_DESCRIPTIONS = {
    "TELEGRAM_BOT_TOKEN": "Telegram Bot Token от @BotFather",
    "TELEGRAM_API_ID": "API ID от my.telegram.org (положительное число)",
    "TELEGRAM_API_HASH": "API Hash от my.telegram.org",
    "TELEGRAM_PHONE": "Номер телефона в формате +79991234567",
    "ADMIN_USER_ID": "Ваш Telegram User ID (положительное число)",
}

OPTIONAL_CONFIG_DESCRIPTIONS = {
    "PRIMARY_LLM_API_KEY/FALLBACK_LLM_TOKEN": "Опционально: токен LLM (минимум для одной из моделей; можно задать позже через /settoken)",
}


def get_config_issues() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Возвращает проблемы конфигурации:
    - required_issues: отсутствующие/некорректные обязательные переменные
    - optional_issues: отсутствующие опциональные переменные
    """
    required_issues = []
    optional_issues = []

    if not (TELEGRAM_BOT_TOKEN or "").strip():
        required_issues.append(
            ("TELEGRAM_BOT_TOKEN", REQUIRED_CONFIG_DESCRIPTIONS["TELEGRAM_BOT_TOKEN"])
        )
    if TELEGRAM_API_ID <= 0:
        required_issues.append(
            ("TELEGRAM_API_ID", REQUIRED_CONFIG_DESCRIPTIONS["TELEGRAM_API_ID"])
        )
    if not (TELEGRAM_API_HASH or "").strip():
        required_issues.append(
            ("TELEGRAM_API_HASH", REQUIRED_CONFIG_DESCRIPTIONS["TELEGRAM_API_HASH"])
        )
    if not (TELEGRAM_PHONE or "").strip():
        required_issues.append(
            ("TELEGRAM_PHONE", REQUIRED_CONFIG_DESCRIPTIONS["TELEGRAM_PHONE"])
        )
    if ADMIN_USER_ID <= 0:
        required_issues.append(
            ("ADMIN_USER_ID", REQUIRED_CONFIG_DESCRIPTIONS["ADMIN_USER_ID"])
        )

    if (
        not (DEFAULT_LLM_TOKEN or "").strip()
        and not (DEFAULT_FALLBACK_LLM_TOKEN or "").strip()
    ):
        optional_issues.append(
            (
                "PRIMARY_LLM_API_KEY/FALLBACK_LLM_TOKEN",
                OPTIONAL_CONFIG_DESCRIPTIONS["PRIMARY_LLM_API_KEY/FALLBACK_LLM_TOKEN"],
            )
        )

    return required_issues, optional_issues


# Промпты для LLM моделей
PARSER_PROMPT = """Ты - парсер команд для бота, который работает с историей Telegram чатов.
Твоя задача - извлечь из текста пользователя название чата/папки, период и запрос.

ВАЖНО: Если чат/папка или период НЕ УКАЗАНЫ явно в сообщении, возвращай null для этих полей.

Формат ответа (JSON):
{
    "target_type": "chat" | "folder" | null,
    "target_name": "название чата/папки" | null,
    "period_type": "days" | "hours" | "today" | "last_messages" | "unread" | null,
    "period_value": число (дней/часов/сообщений) | null,
    "mark_as_read": true | false,
    "query": "полный текст запроса пользователя",
    "recurrence_type": "daily" | "weekly" | "monthly" | "interval_days" | null,
    "interval_days": число | null,
    "time": "HH:MM" | null
}

target_type определяет тип цели:
- "chat" - если пользователь указал конкретный чат ("в чате X", "из чата Y", "чат Z")
- "folder" - если пользователь указал папку ("в папке X", "из папки Y", "папка Z")
- null - если не указано явно

mark_as_read определяет, нужно ли отметить сообщения прочитанными:
- true - если пользователь попросил "отметь прочитанным", "пометь прочитанным", "и отметь прочитанным"
- false - по умолчанию

Типы периодов:
- "days" - за последние N дней (period_value = количество дней)
  - Для "вчера" используй: "period_type": "days", "period_value": 1
  - Для "за неделю" используй: "period_type": "days", "period_value": 7
  - Для "за сутки" используй: "period_type": "days", "period_value": 1
- "hours" - за последние N часов (period_value = количество часов)
  - Для "за последний час" используй: "period_type": "hours", "period_value": 1
  - Для "за последние 5 часов" используй: "period_type": "hours", "period_value": 5
- "today" - сегодня с начала суток до текущего момента (period_value = null)
- "last_messages" - последние N сообщений (period_value = количество сообщений)
  - Для "последние 100 сообщений" используй: "period_type": "last_messages", "period_value": 100
  - Для "последние 1000 сообщений" используй: "period_type": "last_messages", "period_value": 1000
- "unread" - непрочитанные сообщения в выбранных чатах (period_value = null)
  - Для "все непрочитанные", "непрочитанные сообщения" используй: "period_type": "unread", "period_value": null
- null - период не указан (будет использован предыдущий или по умолчанию)

Правила по расписанию:
- Если периодичность не указана, верни: "recurrence_type": null, "interval_days": null, "time": null
- Для "каждый день", "ежедневно": "recurrence_type": "daily"
- Для "каждую неделю", "еженедельно": "recurrence_type": "weekly"
- Для "каждый месяц", "ежемесячно": "recurrence_type": "monthly"
- Для "раз в N дней": "recurrence_type": "interval_days", "interval_days": N
- Если периодичность есть, но время не указано: time = null
- Если время указано, нормализуй к формату HH:MM (24-часовой)

Примеры:
- "Суммаризируй что происходило в чате Работа за последние сутки" -> {"target_type": "chat", "target_name": "Работа", "period_type": "days", "period_value": 1, "mark_as_read": false, "query": "Суммаризируй что происходило"}
- "Что обсуждали в чате Проект за последний час и отметь прочитанным?" -> {"target_type": "chat", "target_name": "Проект", "period_type": "hours", "period_value": 1, "mark_as_read": true, "query": "Что обсуждали"}
- "Что нового в папке Рабочие чаты?" -> {"target_type": "folder", "target_name": "Рабочие чаты", "period_type": null, "period_value": null, "mark_as_read": false, "query": "Что нового"}
- "Суммаризируй папку Личное за неделю и пометь прочитанным" -> {"target_type": "folder", "target_name": "Личное", "period_type": "days", "period_value": 7, "mark_as_read": true, "query": "Суммаризируй"}
- "Что сегодня писали в папке Работа?" -> {"target_type": "folder", "target_name": "Работа", "period_type": "today", "period_value": null, "mark_as_read": false, "query": "Что писали"}
- "Покажи последние 500 сообщений из чата Работа и отметь прочитанным" -> {"target_type": "chat", "target_name": "Работа", "period_type": "last_messages", "period_value": 500, "mark_as_read": true, "query": "Покажи"}
- "Суммаризируй папку AI, все непрочитанные сообщения и отметь как прочитанные" -> {"target_type": "folder", "target_name": "AI", "period_type": "unread", "period_value": null, "mark_as_read": true, "query": "Суммаризируй"}
- "Суммаризируй папку AI, все непрочитанные сообщения каждый день в 20:00 и отмечай прочитанными" -> {"target_type": "folder", "target_name": "AI", "period_type": "unread", "period_value": null, "mark_as_read": true, "query": "Суммаризируй", "recurrence_type": "daily", "interval_days": null, "time": "20:00"}
- "О чем говорили?" -> {"target_type": null, "target_name": null, "period_type": null, "period_value": null, "mark_as_read": false, "query": "О чем говорили?"}
- "До чего договорились в чате Проект?" -> {"target_type": "chat", "target_name": "Проект", "period_type": null, "period_value": null, "mark_as_read": false, "query": "До чего договорились?"}

Отвечай ТОЛЬКО валидным JSON, без дополнительного текста."""

PROCESSOR_PROMPT = """Ты - умный ассистент, который работает с историей Telegram чатов.
Пользователь предоставляет тебе историю переписки и свой запрос.

Твои возможности:
- Суммаризация переписок (краткое резюме основных тем, решений, планов)
- Ответы на вопросы о содержании чата
- Поиск конкретной информации в истории
- Анализ договоренностей и следующих шагов

Правила:
1. Отвечай на основе ТОЛЬКО той информации, что есть в истории чата
2. Если информации недостаточно для ответа, честно скажи об этом
3. Будь конкретным и ссылайся на конкретные сообщения где возможно
4. Если просят суммаризацию - структурируй ответ (темы, решения, планы, даты)
5. Отвечай на русском языке четко и по делу"""
