# Структура проекта

```
llmbot/
│
├── bot.py                 # Основной файл бота
├── llm_runtime.py         # Runtime-настройки LLM (url/token/model)
├── config.py              # Конфигурация и настройки (включая модели)
├── requirements.txt       # Python зависимости
├── requirements-dev.txt   # Зависимости для тестов
├── pyproject.toml         # Конфигурация pytest/coverage
│
├── .env                   # Переменные окружения (создается вручную)
├── env.example            # Пример файла .env
├── .gitignore             # Игнорируемые Git файлы
│
├── setup.sh               # Скрипт автоматической установки
├── start.sh               # Скрипт запуска бота
│
├── README.md              # Главная документация (входная точка)
├── CLAUDE.md              # Технические заметки для агента
├── docs/
│   ├── QUICKSTART.md
│   ├── INSTALL.md
│   ├── EXAMPLES.md
│   ├── FAQ.md
│   ├── CHANGELOG.md
│   └── PROJECT_STRUCTURE.md
│
├── telethon_session.session   # Сессия Telethon (создается автоматически)
├── telethon_session.session-journal  # Журнал сессии
│
├── tests/                 # Автотесты
└── venv/                  # Виртуальное окружение Python (создается автоматически)
```

## Описание файлов

### Основные файлы

#### `bot.py`
Главный файл приложения. Содержит:
- Логику работы с Telegram Bot API
- Интеграцию с Telethon для доступа к истории
- Функции для работы с OpenRouter-compatible LLM API
- Обработчики команд пользователя
- Парсинг и обработку запросов
- Управление контекстом запросов
- Нечеткий поиск чатов

**Основные компоненты:**
- `call_llm_api()` - универсальный интерфейс к LLM через OpenRouter API
- `find_chat_by_name()` - нечеткий поиск чатов
- `get_chat_history()` - загрузка истории сообщений с поддержкой гибких периодов
- `parse_command_with_gpt()` - парсинг команд пользователя
- `process_user_message()` - обработка текстовых сообщений
- `start_command()` - команда /start
- `help_command()` - команда /help
- `context_command()` - команда /context
- `reset_command()` - команда /reset
- `main()` - главная функция запуска

**Глобальные переменные:**
- `current_context` - текущий контекст (chat_name, period_type, period_value)
- `telethon_client` - клиент Telethon

#### `config.py`
Конфигурация приложения:
- Загрузка переменных окружения из `.env`
- Настройки Telegram Bot API
- Настройки Telethon
- Настройки OpenRouter API
- Настройки моделей по умолчанию
- Имя файла сессии Telethon

**Основные константы:**
- `DEFAULT_MESSAGES_LIMIT = 300` - количество сообщений по умолчанию
- `DEFAULT_LLM_URL` - URL OpenAI-compatible endpoint
- `DEFAULT_LLM_MODEL` - модель по умолчанию
- `DEFAULT_LLM_TOKEN` - ключ по умолчанию из `PRIMARY_LLM_API_KEY`
- `PARSER_PROMPT` - system prompt для парсинга команд пользователя
- `PROCESSOR_PROMPT` - system prompt для обработки и анализа переписок

**Изменение моделей:**
Отредактируйте константы в `config.py` или меняйте значения на лету через команды:
1. `/seturl` - URL endpoint
2. `/setmodel` - модель
3. `/settoken` - API ключ

**Изменение промптов:**
Промпты для LLM вынесены в `config.py` и легко настраиваются:
- `PARSER_PROMPT` - инструкции для парсинга команд в JSON
- `PROCESSOR_PROMPT` - инструкции для анализа истории чатов

### Скрипты

#### `setup.sh`
Автоматическая установка:
- Проверка наличия Python
- Создание виртуального окружения
- Установка зависимостей из `requirements.txt`
- Предложение создать `.env` из `env.example`

#### `start.sh`
Запуск бота:
- Проверка версии Python (3.11-3.13)
- Активация виртуального окружения
- Запуск `bot.py`

### Конфигурационные файлы

#### `.env`
Файл переменных окружения (создается вручную):
- `TELEGRAM_BOT_TOKEN` - токен бота от BotFather
- `TELEGRAM_API_ID` - API ID от my.telegram.org
- `TELEGRAM_API_HASH` - API Hash от my.telegram.org
- `TELEGRAM_PHONE` - номер телефона для Telethon
- `PRIMARY_LLM_API_KEY` - API ключ для OpenRouter-compatible API (опционально)
- `ADMIN_USER_ID` - Telegram ID администратора

**Безопасность:**
- Исключен из Git (`.gitignore`)
- Рекомендуется `chmod 600 .env`
- Никогда не коммитить в репозиторий

#### `env.example`
Пример файла `.env` с пояснениями:
- Показывает все необходимые переменные
- Содержит комментарии и примеры значений
- Используется как шаблон для создания `.env`

#### `requirements.txt`
Python зависимости:
- `python-telegram-bot>=21.0` - Telegram Bot API
- `telethon` - доступ к истории Telegram
- `requests` - HTTP-запросы к LLM API
- `python-dotenv` - загрузка переменных из `.env`

#### `requirements-dev.txt`
Зависимости для тестов и отчета покрытия:
- `pytest`
- `coverage`
- `pytest-cov`

### Документация

#### `README.md`
Главная документация:
- Обзор возможностей
- Быстрая установка
- Настройка и запуск
- Базовые примеры
- Архитектура проекта
- Безопасность
- Troubleshooting

#### `docs/QUICKSTART.md`
Быстрый старт за 5 минут:
- Пошаговая инструкция
- Минимальная настройка
- Первый запуск
- Базовые команды

#### `docs/INSTALL.md`
Подробная установка:
- Установка Python на разных ОС
- Создание виртуального окружения
- Установка зависимостей
- Получение API ключей
- Настройка переменных окружения
- Troubleshooting установки

#### `docs/EXAMPLES.md`
Примеры использования:
- Базовые команды
- Работа с контекстом
- Нечеткий поиск
- Свободные запросы
- Управление настройками
- Продвинутые сценарии
- Комбинирование функций

#### `docs/FAQ.md`
Часто задаваемые вопросы:
- Установка и настройка
- Использование
- Технические вопросы
- Безопасность
- Производительность
- Troubleshooting

#### `docs/CHANGELOG.md`
История изменений:
- Список всех изменений по версиям
- Новые функции
- Исправления багов
- Планы на будущее

### Генерируемые файлы

#### `telethon_session.session`
Файл сессии Telethon:
- Создается автоматически при первом запуске
- Сохраняет авторизацию в Telegram
- Зашифрован и безопасен
- Исключен из Git
- Рекомендуется `chmod 600 *.session`

#### `venv/`
Виртуальное окружение Python:
- Создается через `python3 -m venv venv`
- Содержит изолированные Python пакеты
- Не включается в Git
- Активируется через `source venv/bin/activate`

## Архитектура кода

### Логика обработки запроса

```
Пользователь
    ↓
[Telegram Bot API]
    ↓
process_user_message()
    ↓
parse_command_with_gpt()
    ↓
call_llm_api() → [OpenRouter-compatible API]
    ↓
[JSON команда: chat, period, query]
    ↓
find_chat_by_name() → [Telethon]
    ↓
[Entity чата + схожесть]
    ↓
get_chat_history() → [Telethon]
    ↓
[История сообщений]
    ↓
process_chat_with_openai()
    ↓
call_llm_api() → [OpenRouter-compatible API]
    ↓
[Анализ/суммаризация]
    ↓
[Telegram Bot API]
    ↓
Пользователь
```

### Управление контекстом

```python
current_context = {
    "chat_name": "название чата",
    "period_type": "days" | "hours" | "today" | "last_messages" | None,
    "period_value": число | None
}
```

Бот запоминает последний использованный чат и период. Для одного пользователя не нужен словарь по `user_id`.

## Расширение функциональности

### Добавление новой команды

1. Создайте async функцию-обработчик:
```python
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ответ")
```

2. Зарегистрируйте обработчик в `main()`:
```python
application.add_handler(CommandHandler("my_command", my_command))
```

## Лучшие практики

### Разработка
- Используйте виртуальное окружение
- Следуйте PEP 8 для стиля кода
- Документируйте новые функции
- Тестируйте изменения перед коммитом

### Безопасность
- Никогда не коммитьте `.env`
- Используйте `chmod 600` для чувствительных файлов
- Регулярно обновляйте зависимости
- Проверяйте логи на утечки credentials

### Производительность
- Используйте async/await где возможно
- Кэшируйте результаты где уместно
- Оптимизируйте размер загружаемой истории
- Выбирайте подходящие модели для задач

---

**Вопросы?** Проверьте [FAQ.md](FAQ.md) или создайте Issue.
