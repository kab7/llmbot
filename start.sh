#!/bin/bash

echo "🤖 Запуск Telegram Chat Analyzer Bot..."
echo ""

# Проверяем наличие виртуального окружения
if [ ! -d "venv" ]; then
    echo "⚠️  Виртуальное окружение не найдено."
    echo "Создайте его командой: python3 -m venv venv"
    echo "Затем активируйте: source venv/bin/activate"
    echo "И установите зависимости: pip install -r requirements.txt"
    echo ""
    read -p "Хотите создать виртуальное окружение сейчас? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 -m venv venv
        source venv/bin/activate
        pip install -r requirements.txt
    else
        exit 1
    fi
fi

# Проверяем конфигурацию
echo "🔍 Проверка конфигурации..."
python check_config.py

if [ $? -eq 0 ]; then
    echo ""
    echo "🚀 Запуск бота..."
    python bot.py
else
    echo ""
    echo "❌ Сначала настройте конфигурацию в файле .env"
    exit 1
fi

