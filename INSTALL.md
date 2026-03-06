# Установка Python и зависимостей

## Для macOS

### Шаг 1: Проверка наличия Python

Откройте терминал и проверьте, установлен ли Python:

```bash
python3 --version
```

Если видите версию (например, `Python 3.11.x`), переходите к **Шагу 3**.

### Шаг 2: Установка Python (если не установлен)

#### Вариант A: Через Homebrew (рекомендуется)

1. Установите Homebrew, если его нет:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. Установите Python:
```bash
brew install python@3.11
```

#### Вариант B: Через официальный сайт

1. Скачайте Python с [python.org/downloads](https://www.python.org/downloads/)
2. Запустите установщик и следуйте инструкциям

### Шаг 3: Создание виртуального окружения (рекомендуется)

Перейдите в директорию проекта:

```bash
cd /Users/kapustinskiy/Projects/llmbot
```

Создайте виртуальное окружение:

```bash
python3 -m venv venv
```

Активируйте виртуальное окружение:

```bash
source venv/bin/activate
```

После активации в начале строки терминала появится `(venv)`.

### Шаг 4: Установка зависимостей

```bash
pip install -r requirements.txt
```

Это установит все необходимые библиотеки:
- `python-telegram-bot` - для работы с Telegram Bot API
- `telethon` - для доступа к истории чатов
- `openai` - для работы с GPT
- `python-dotenv` - для загрузки переменных окружения

### Шаг 5: Проверка установки

```bash
python check_config.py
```

Этот скрипт проверит, что все библиотеки установлены и покажет какие переменные окружения нужно настроить.

## Быстрая установка (все в одном)

Скопируйте и вставьте в терминал:

```bash
# Перейти в директорию проекта
cd /Users/kapustinskiy/Projects/llmbot

# Создать виртуальное окружение
python3 -m venv venv

# Активировать
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

# Проверить установку
python check_config.py
```

## После установки

1. Создайте файл `.env`:
```bash
cp env.example .env
chmod 600 .env
```

2. Отредактируйте `.env` и заполните все значения

3. Запустите бота:
```bash
python bot.py
```

## Деактивация виртуального окружения

Когда закончите работу с ботом:

```bash
deactivate
```

## Повторный запуск бота

При следующем запуске нужно только:

```bash
cd /Users/kapustinskiy/Projects/llmbot
source venv/bin/activate
python bot.py
```

## Возможные проблемы

### "python3: command not found"

Python не установлен. Установите его через Homebrew или с официального сайта (см. Шаг 2).

### "pip: command not found"

Обычно pip устанавливается вместе с Python. Попробуйте:

```bash
python3 -m pip install -r requirements.txt
```

### Ошибки при установке библиотек

Обновите pip:

```bash
pip install --upgrade pip
```

Затем повторите установку зависимостей.

### "Permission denied"

Если используете виртуальное окружение, проблем быть не должно.
Если устанавливаете глобально, попробуйте:

```bash
pip install --user -r requirements.txt
```

**НЕ используйте `sudo pip`!**

## Зачем нужно виртуальное окружение?

✅ Изолирует зависимости проекта от системного Python
✅ Позволяет использовать разные версии библиотек для разных проектов
✅ Не требует прав администратора
✅ Легко удалить и пересоздать

Без виртуального окружения все библиотеки устанавливаются глобально, что может привести к конфликтам.

