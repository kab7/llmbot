#!/bin/bash

echo "🚀 Установка Telegram Chat Analyzer Bot"
echo "========================================"
echo ""

# Проверка наличия Python
echo "📋 Проверка Python..."

# Попробуем найти подходящую версию Python
PYTHON_CMD=""
if command -v python3.12 &> /dev/null; then
    PYTHON_CMD="python3.12"
elif command -v python3.11 &> /dev/null; then
    PYTHON_CMD="python3.11"
elif command -v python3 &> /dev/null; then
    # Проверяем версию python3
    VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
    MAJOR=$(echo $VERSION | cut -d'.' -f1)
    MINOR=$(echo $VERSION | cut -d'.' -f2)
    
    if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ] && [ "$MINOR" -le 13 ]; then
        PYTHON_CMD="python3"
    else
        echo "⚠️  Найден Python $VERSION, но рекомендуется Python 3.11-3.13"
        echo ""
        if [ "$MINOR" -eq 14 ]; then
            echo "❌ Python 3.14 пока не поддерживается из-за несовместимости библиотек!"
            echo ""
        fi
        echo "Установите Python 3.12:"
        echo "   brew install python@3.12"
        echo ""
        read -p "Продолжить с Python $VERSION? (не рекомендуется) (y/n) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
        PYTHON_CMD="python3"
    fi
else
    echo "❌ Python не найден!"
    echo ""
    echo "Установите Python 3.12 через Homebrew:"
    echo "   brew install python@3.12"
    echo ""
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version)
echo "✅ Используется: $PYTHON_VERSION ($PYTHON_CMD)"

echo ""

# Создание виртуального окружения
if [ -d "venv" ]; then
    echo "📦 Виртуальное окружение уже существует"
    read -p "Пересоздать его с правильной версией Python? (рекомендуется) (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🗑️  Удаление старого окружения..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo "📦 Создание виртуального окружения с $PYTHON_CMD..."
    $PYTHON_CMD -m venv venv
    if [ $? -eq 0 ]; then
        echo "✅ Виртуальное окружение создано"
    else
        echo "❌ Ошибка при создании виртуального окружения"
        exit 1
    fi
fi

echo ""

# Активация виртуального окружения
echo "🔌 Активация виртуального окружения..."
source venv/bin/activate

echo ""

# Обновление pip
echo "⬆️  Обновление pip..."
pip install --upgrade pip --quiet

echo ""

# Установка зависимостей
echo "📚 Установка зависимостей из requirements.txt..."
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "✅ Все зависимости установлены"
else
    echo "❌ Ошибка при установке зависимостей"
    exit 1
fi

echo ""
echo "========================================"
echo "✅ Установка завершена успешно!"
echo "========================================"
echo ""

if [ -f ".env" ]; then
    echo "✅ Файл .env существует"
    echo "ℹ️  Проверка конфигурации выполнится автоматически при запуске бота."
else
    echo "⚠️  Файл .env не найден"
    echo ""
    read -p "Создать .env из примера? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cp env.example .env
        chmod 600 .env
        echo "✅ Файл .env создан"
        echo ""
        echo "📝 Теперь отредактируйте .env и заполните все значения:"
        echo "   nano .env"
        echo "   # или откройте в любом редакторе"
        echo ""
        echo "После заполнения .env запустите:"
        echo "   source venv/bin/activate"
        echo "   python bot.py"
    fi
fi

echo ""
echo "📖 Документация:"
echo "   README.md             - полная инструкция"
echo "   docs/INSTALL.md       - установка Python и зависимостей"
echo "   docs/QUICKSTART.md    - быстрый старт"
echo "   docs/FAQ.md           - частые вопросы"
echo ""
echo "🎉 Готово к работе!"
