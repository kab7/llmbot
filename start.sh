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
        venv/bin/pip install -r requirements.txt
    else
        exit 1
    fi
fi

echo ""
echo "🚀 Запуск бота..."
venv/bin/python bot.py
