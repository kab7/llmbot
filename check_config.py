#!/usr/bin/env python3
"""
Скрипт для проверки конфигурации бота
"""
import os
from dotenv import load_dotenv

def check_config():
    # Загружаем переменные окружения
    load_dotenv()
    
    # Список обязательных переменных
    required_vars = {
        'TELEGRAM_BOT_TOKEN': 'Telegram Bot Token от @BotFather',
        'TELEGRAM_API_ID': 'API ID от my.telegram.org',
        'TELEGRAM_API_HASH': 'API Hash от my.telegram.org',
        'TELEGRAM_PHONE': 'Номер телефона в формате +79991234567',
        'ELIZA_TOKEN': 'Eliza API OAuth токен',
        'ADMIN_USER_ID': 'Ваш Telegram User ID'
    }
    
    print("🔍 Проверка конфигурации...\n")
    
    all_ok = True
    missing = []
    
    for var, description in required_vars.items():
        value = os.getenv(var)
        if value:
            # Маскируем значение для безопасности
            if len(value) > 10:
                masked = f"{value[:5]}...{value[-5:]}"
            else:
                masked = "*" * len(value)
            print(f"✅ {var}: {masked}")
        else:
            print(f"❌ {var}: НЕ УСТАНОВЛЕН")
            missing.append((var, description))
            all_ok = False
    
    if all_ok:
        print("\n✅ Все переменные окружения установлены!")
        print("\n📝 Следующий шаг: запустите бота командой:")
        print("   ./start.sh")
        print("   или")
        print("   python bot.py")
    else:
        print("\n❌ Отсутствуют обязательные переменные:\n")
        for var, description in missing:
            print(f"  • {var}")
            print(f"    Описание: {description}\n")
        
        print("💡 Как исправить:")
        print("  1. Скопируйте env.example в .env: cp env.example .env")
        print("  2. Отредактируйте .env и заполните все значения")
        print("  3. Запустите этот скрипт снова: python check_config.py")

if __name__ == '__main__':
    check_config()
