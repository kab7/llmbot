# Быстрый старт за 5 минут 🚀

Следуйте этим шагам для быстрого запуска бота.

## Шаг 1: Клонируйте репозиторий

```bash
git clone <your-repo-url>
cd llmbot
```

## Шаг 2: Запустите автоматическую установку

```bash
./setup.sh
```

Скрипт автоматически:
- Проверит наличие Python
- Создаст виртуальное окружение
- Установит все зависимости

## Шаг 3: Получите API ключи

### 3.1 Telegram Bot Token
1. Откройте [@BotFather](https://t.me/botfather)
2. Отправьте `/newbot`
3. Следуйте инструкциям и скопируйте токен

### 3.2 Telethon API
1. Перейдите на [https://my.telegram.org](https://my.telegram.org)
2. Войдите и перейдите в "API development tools"
3. Создайте приложение
4. Скопируйте `api_id` и `api_hash`

### 3.3 OpenRouter API Key (опционально)
1. Перейдите на https://openrouter.ai/keys
2. Создайте API ключ
3. Ключ можно задать в `.env` или позже через `/settoken`

### 3.4 Ваш Telegram ID
1. Откройте [@userinfobot](https://t.me/userinfobot)
2. Отправьте любое сообщение
3. Скопируйте ваш ID

## Шаг 4: Настройте .env

Создайте файл `.env`:

```bash
cp env.example .env
nano .env  # или используйте любой текстовый редактор
```

Заполните:

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef123456...
TELEGRAM_PHONE=+79991234567

# OpenRouter-compatible LLM API (опционально)
# Получите токен: https://openrouter.ai/keys
PRIMARY_LLM_URL=https://openrouter.ai/api/v1/chat/completions
PRIMARY_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
PRIMARY_LLM_API_KEY=your_openrouter_api_key_here

# Admin
ADMIN_USER_ID=987654321
```

Установите права доступа:

```bash
chmod 600 .env
```

## Шаг 5: Запустите бота

```bash
./start.sh
```

При первом запуске Telethon попросит код из Telegram:
1. Введите код подтверждения
2. Если включена 2FA, введите пароль

## Шаг 6: Используйте бота!

Найдите вашего бота в Telegram и отправьте:

```
/start
```

### Примеры команд:

**Простая суммаризация:**
```
Суммаризируй чат Работа за последнюю неделю
```

**Гибкие периоды:**
```
Что сегодня писали в чате Команда?
Покажи чат Поддержка за последний час
Дай последние 500 сообщений из чата Разработка
```

**Работа с контекстом:**
```
Суммаризируй чат Проект за 3 дня
О чем договорились?
Какие следующие шаги?
```

**Свободный запрос:**
```
О чем говорили в чате Команда на тему бюджета?
```

**Полезные команды:**
```
/help              # Показать все команды
/context           # Показать текущий контекст
/reset             # Сбросить контекст
/llmconfig         # Показать текущие LLM настройки
/limits primary    # Показать лимиты primary ключа
/limits fallback   # Показать лимиты fallback ключа
/seturl primary <url>        # Поменять primary endpoint
/seturl fallback <url>       # Поменять fallback endpoint
/setmodel primary <model>    # Поменять primary модель
/setmodel fallback <model>   # Поменять fallback модель
/settoken primary <token>    # Поменять primary токен
/settoken fallback <token>   # Поменять fallback токен
```

## Команды бота

- `/start` - приветствие и информация о боте
- `/help` - показать все доступные команды
- `/context` - показать текущий контекст (последний чат и период)
- `/reset` - сбросить контекст
- `/llmconfig` - показать текущие LLM настройки
- `/limits [primary|fallback]` - показать лимиты API ключа
- `/seturl [primary|fallback] <url>` - задать URL OpenAI-compatible API
- `/setmodel [primary|fallback] <model>` - задать модель
- `/settoken [primary|fallback] <token>` - задать токен

**LLM runtime:**
- URL, токен и модель задаются в `config.py` по умолчанию
- Во время работы могут быть переопределены через `/seturl`, `/settoken`, `/setmodel`

## Что дальше?

- 📖 [README.md](../README.md) - полная документация
- 📝 [EXAMPLES.md](EXAMPLES.md) - больше примеров использования
- ❓ [FAQ.md](FAQ.md) - часто задаваемые вопросы
- 🔧 [INSTALL.md](INSTALL.md) - подробная установка

## Возникли проблемы?

1. Проверьте логи в консоли
2. Запустите `python bot.py` и проверьте подсказки в логах
3. Посмотрите [FAQ.md](FAQ.md)

**Готово! Наслаждайтесь использованием бота! 🎉**
