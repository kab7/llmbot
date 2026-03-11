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
SESSION_NAME = os.getenv("SESSION_NAME", "telethon_session")

# Настройки по умолчанию
# Количество сообщений для загрузки, если период не указан
DEFAULT_MESSAGES_LIMIT = 300
LLM_REQUEST_TIMEOUT_SECONDS = max(5, _parse_int_env("LLM_REQUEST_TIMEOUT_SECONDS", 20))
LLM_MAX_RETRIES = max(1, _parse_int_env("LLM_MAX_RETRIES", 3))
PRIMARY_FREE_MODEL_INTERVAL_SECONDS = max(
    0, _parse_int_env("PRIMARY_FREE_MODEL_INTERVAL_SECONDS", 4)
)
PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS = max(
    0, _parse_int_env("PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS", 12)
)
PRIMARY_FREE_MODEL_429_BACKOFF_STEP_SECONDS = max(
    0, _parse_int_env("PRIMARY_FREE_MODEL_429_BACKOFF_STEP_SECONDS", 4)
)
FALLBACK_FREE_MODEL_INTERVAL_SECONDS = max(
    0, _parse_int_env("FALLBACK_FREE_MODEL_INTERVAL_SECONDS", 4)
)
FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS = max(
    0, _parse_int_env("FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS", 12)
)
FALLBACK_FREE_MODEL_429_BACKOFF_STEP_SECONDS = max(
    0, _parse_int_env("FALLBACK_FREE_MODEL_429_BACKOFF_STEP_SECONDS", 4)
)
LOG_FILE_PATH = (os.getenv("LOG_FILE_PATH") or "bot.log").strip() or "bot.log"
LOG_MAX_BYTES = max(1024 * 1024, _parse_int_env("LOG_MAX_BYTES", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = max(1, _parse_int_env("LOG_BACKUP_COUNT", 5))
LLM_TRAFFIC_LOG_PATH = (
    os.getenv("LLM_TRAFFIC_LOG_PATH") or "llm_traffic.log"
).strip() or "llm_traffic.log"
LLM_TRAFFIC_LOG_MAX_BYTES = max(
    1024 * 1024, _parse_int_env("LLM_TRAFFIC_LOG_MAX_BYTES", 20 * 1024 * 1024)
)
LLM_TRAFFIC_LOG_BACKUP_COUNT = max(1, _parse_int_env("LLM_TRAFFIC_LOG_BACKUP_COUNT", 5))

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
PARSER_PROMPT = """Ты - детерминированный парсер команд Telegram-бота.
Верни ТОЛЬКО один валидный JSON-объект, без markdown и без пояснений.
Ответ должен быть валидным JSON.

Верни РОВНО эти 9 ключей (без дополнительных):
{
  "target_type": "chat" | "folder" | null,
  "target_name": string | null,
  "period_type": "days" | "hours" | "today" | "last_messages" | "unread" | null,
  "period_value": integer | null,
  "mark_as_read": boolean,
  "query": string | null,
  "recurrence_type": "daily" | "weekly" | "monthly" | "interval_days" | null,
  "interval_days": integer | null,
  "time": "HH:MM" | null
}

Общие правила:
1. Ничего не выдумывай. Если сущность не указана явно, ставь null.
2. target_type:
   - "chat" для "в чате ...", "из чата ...", "чат ..."
   - "folder" для "в папке ...", "из папки ...", "папка ..."
3. target_name: только имя цели без служебных слов, лишние кавычки/пробелы убери.
4. query: исходный текст запроса пользователя целиком (как есть по смыслу; не сокращай до одного слова).
5. mark_as_read=true, если есть явное намерение отметить прочитанным:
   "отметь/пометь как прочитанные", "mark as read", "read all", и т.п. Иначе false.

Период:
1. Если явно есть "непрочитанные"/"unread", всегда period_type="unread", period_value=null.
2. Если период не указан явно, верни period_type=null и period_value=null.
3. Для period_type:
   - "days": "за N дней", "за неделю" -> 7, "за сутки"/"вчера" -> 1
   - "hours": "за N часов", "за последний час" -> 1
   - "today": "сегодня"
   - "last_messages": "последние N сообщений"
4. Для period_type в {"days","hours","last_messages"} period_value обязан быть целым числом > 0.
   В остальных случаях period_value=null.

Расписание:
1. Если расписание не указано, recurrence_type=null, interval_days=null, time=null.
2. "каждый день"/"ежедневно" -> recurrence_type="daily"
3. "каждую неделю"/"еженедельно" -> recurrence_type="weekly"
4. "каждый месяц"/"ежемесячно" -> recurrence_type="monthly"
5. "раз в N дней" -> recurrence_type="interval_days", interval_days=N
6. Если recurrence_type != "interval_days", interval_days=null.
7. Если время указано, нормализуй к формату HH:MM (24h), например:
   "8 вечера" -> "20:00", "9 утра" -> "09:00".
8. Если расписание есть, но время не указано, time=null.

Самопроверка перед ответом:
- JSON валиден;
- есть все 9 ключей;
- типы значений соответствуют схеме;
- никаких комментариев/текста вне JSON.

Критичные уточнения:
- "за вчера" и "за сутки" это period_type="days", period_value=1 (НЕ "today").
- Если нет явного запроса "непрочитанные", не ставь period_type="unread".
- Если нет явного запроса "отметь как прочитанные", ставь mark_as_read=false.

Примеры:
Пользователь: "суммаризируй все чаты в папке AI за вчера"
Ответ:
{"target_type":"folder","target_name":"AI","period_type":"days","period_value":1,"mark_as_read":false,"query":"суммаризируй все чаты в папке AI за вчера","recurrence_type":null,"interval_days":null,"time":null}

Пользователь: "суммаризируй непрочитанные в чате Работа и отметь как прочитанные"
Ответ:
{"target_type":"chat","target_name":"Работа","period_type":"unread","period_value":null,"mark_as_read":true,"query":"суммаризируй непрочитанные в чате Работа и отметь как прочитанные","recurrence_type":null,"interval_days":null,"time":null}

Пользователь: "покажи последние 300 сообщений из чата Release"
Ответ:
{"target_type":"chat","target_name":"Release","period_type":"last_messages","period_value":300,"mark_as_read":false,"query":"покажи последние 300 сообщений из чата Release","recurrence_type":null,"interval_days":null,"time":null}

Пользователь: "суммаризируй чат DevOps каждый день в 20:00"
Ответ:
{"target_type":"chat","target_name":"DevOps","period_type":null,"period_value":null,"mark_as_read":false,"query":"суммаризируй чат DevOps каждый день в 20:00","recurrence_type":"daily","interval_days":null,"time":"20:00"}

Пользователь: "суммаризируй папку AI раз в 3 дня в 19:30"
Ответ:
{"target_type":"folder","target_name":"AI","period_type":null,"period_value":null,"mark_as_read":false,"query":"суммаризируй папку AI раз в 3 дня в 19:30","recurrence_type":"interval_days","interval_days":3,"time":"19:30"}
"""

PROCESSOR_PROMPT = """Ты - аналитик, который работает с историей Telegram чатов.
Отвечай только на основе предоставленной истории и запроса пользователя.

Строгие правила:
1. Не выдумывай факты, события и цитаты.
2. Если данных не хватает, явно напиши, чего именно не хватает.
3. Сохраняй техническую точность: версии, команды, числа, сроки, имена сервисов.
4. Если есть неопределенность или противоречия в сообщениях, пометь это явно.
5. Пиши на русском, кратко и по делу.
6. Верни только итоговый ответ для пользователя. Не описывай процесс работы.
7. Не пиши служебный мусор: "Суммаризация сообщения", "Выжимка из сообщения",
   "Суть канала", "Статус выполнения запроса", "Примечание", "Отмечено как прочитанное",
   "ежедневное саммари в 10:00", "по предоставленной истории" и подобные фразы.
8. Не комментируй расписание, отметку прочитанного, ограничения интерфейса, бота,
   API, автоматизацию или техническую реализацию, если пользователь явно не спросил об этом.
9. Если в истории одно сообщение или одна тема, сразу дай нормальное саммари по сути,
   без вводных заголовков вроде "Тема" или "Суммаризация сообщения".

Режим по умолчанию для суммаризации:
- Давай просто хорошую, понятную выжимку содержания.
- Используй нейтральный формат без лишних обязательных разделов.
- Форматируй ответ в легком markdown без обязательного заголовка в начале:
  списки `-`, акценты `**...**`, ссылки `[текст](url)`, инлайн-код `` `...` ``.
- Не добавляй блоки "Решения", "Задачи", "Риски", "Открытые вопросы" и т.п.,
  если пользователь явно этого не просил.

Если пользователь явно просит конкретный срез (например: "что решили", "какие риски",
"отдельно выдели открытые вопросы", "покажи next steps"), тогда выдели именно этот срез
отдельным блоком. Если в истории таких данных нет, прямо напиши, что не найдено.

Если запрос не про суммаризацию, отвечай точно по вопросу. При необходимости добавь
короткий блок "Основание" с 1-3 фактами из истории.
"""
