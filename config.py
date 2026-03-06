import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot API
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Telethon (User client)
TELEGRAM_API_ID = int(os.getenv('TELEGRAM_API_ID', 0))
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_PHONE = os.getenv('TELEGRAM_PHONE')

# Eliza API (единственный провайдер LLM)
ELIZA_TOKEN = os.getenv('ELIZA_TOKEN')

# Admin user ID (who can use the bot)
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))

# Session file for Telethon
# Telethon сохраняет сессию в файл для избежания повторной авторизации.
# Это БЕЗОПАСНО: файл содержит зашифрованные данные авторизации.
# Файл автоматически добавлен в .gitignore.
# Рекомендуется установить права доступа: chmod 600 telethon_session.session
SESSION_NAME = 'telethon_session'

# Настройки по умолчанию
# Количество сообщений для загрузки, если период не указан
DEFAULT_MESSAGES_LIMIT = 300

# LLM модели (фиксированные)
# Модель для парсинга команд пользователя
PARSER_MODEL_NAME = "deepseek-internal"
PARSER_MODEL_URL = "https://api.eliza.yandex.net/internal/deepseek-v3-1-terminus/v1/chat/completions"

# Модель для обработки и анализа переписок
PROCESSOR_MODEL_NAME = "aliceai-llm"
PROCESSOR_MODEL_URL = "https://api.eliza.yandex.net/internal/zeliboba_lts_235b_aligned_quantized_202510/generative/v1/chat/completions"

# Промпты для LLM моделей
PARSER_PROMPT = """Ты - парсер команд для бота, который работает с историей Telegram чатов.
Твоя задача - извлечь из текста пользователя название чата/папки, период и запрос.

ВАЖНО: Если чат/папка или период НЕ УКАЗАНЫ явно в сообщении, возвращай null для этих полей.

Формат ответа (JSON):
{
    "target_type": "chat" | "folder" | null,
    "target_name": "название чата/папки" | null,
    "period_type": "days" | "hours" | "today" | "unread" | "last_messages" | null,
    "period_value": число (дней/часов/сообщений) | null,
    "mark_as_read": true | false,
    "query": "полный текст запроса пользователя"
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
- "unread" - только непрочитанные сообщения (period_value = null)
  - Для "среди непрочитанных" используй: "period_type": "unread", "period_value": null
  - Для "непрочитанные сообщения" используй: "period_type": "unread", "period_value": null
- "last_messages" - последние N сообщений (period_value = количество сообщений)
  - Для "последние 100 сообщений" используй: "period_type": "last_messages", "period_value": 100
  - Для "последние 1000 сообщений" используй: "period_type": "last_messages", "period_value": 1000
- null - период не указан (будет использован предыдущий или по умолчанию)

Примеры:
- "Суммаризируй что происходило в чате Работа за последние сутки" -> {"target_type": "chat", "target_name": "Работа", "period_type": "days", "period_value": 1, "mark_as_read": false, "query": "Суммаризируй что происходило"}
- "Что обсуждали в чате Проект за последний час и отметь прочитанным?" -> {"target_type": "chat", "target_name": "Проект", "period_type": "hours", "period_value": 1, "mark_as_read": true, "query": "Что обсуждали"}
- "Что нового в папке Рабочие чаты?" -> {"target_type": "folder", "target_name": "Рабочие чаты", "period_type": "unread", "period_value": null, "mark_as_read": false, "query": "Что нового"}
- "Суммаризируй папку Личное за неделю и пометь прочитанным" -> {"target_type": "folder", "target_name": "Личное", "period_type": "days", "period_value": 7, "mark_as_read": true, "query": "Суммаризируй"}
- "Что сегодня писали в папке Работа?" -> {"target_type": "folder", "target_name": "Работа", "period_type": "today", "period_value": null, "mark_as_read": false, "query": "Что писали"}
- "Покажи последние 500 сообщений из чата Работа и отметь прочитанным" -> {"target_type": "chat", "target_name": "Работа", "period_type": "last_messages", "period_value": 500, "mark_as_read": true, "query": "Покажи"}
- "Что в непрочитанных в чате Проект?" -> {"target_type": "chat", "target_name": "Проект", "period_type": "unread", "period_value": null, "mark_as_read": false, "query": "Что в непрочитанных"}
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

