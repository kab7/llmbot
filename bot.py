import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import json
import requests
import urllib3
from difflib import SequenceMatcher

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel
from telethon.tl.functions.channels import GetForumTopicsRequest
from telethon.tl.functions.messages import GetDialogFiltersRequest

import config
from masker import mask

# Отключаем предупреждения SSL для Eliza API
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Включаем логирование сетевых запросов
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("requests").setLevel(logging.INFO)

# Включаем логирование Telegram API запросов (для отладки)
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.INFO)
logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)

# Глобальные переменные для клиентов
telethon_client: Optional[TelegramClient] = None

# Текущий контекст (чат и период)
# {"chat_name": str, "period_type": str, "period_value": int}
current_context: Dict[str, Any] = {}


def escape_markdown(text: str) -> str:
    """Экранирует спецсимволы Markdown для безопасной отправки в Telegram."""
    escape_chars = ['_', '*', '`', '[']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text


# Декоратор для проверки прав доступа
def admin_only(func):
    """Декоратор для проверки, что команду вызывает админ"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ADMIN_USER_ID:
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        return await func(update, context)
    return wrapper


def format_period_text(period_type: Optional[str], period_value: Optional[int]) -> str:
    """
    Форматирует период в читаемый текст
    
    Args:
        period_type: Тип периода
        period_value: Значение периода
        
    Returns:
        Текстовое описание периода
    """
    if period_type == "days" and period_value:
        return f"последние {period_value} {'день' if period_value == 1 else 'дней'}"
    elif period_type == "hours" and period_value:
        return f"последние {period_value} {'час' if period_value == 1 else 'часов'}"
    elif period_type == "today":
        return "сегодня"
    elif period_type == "unread":
        return "непрочитанные сообщения"
    elif period_type == "last_messages" and period_value:
        return f"последние {period_value} сообщений"
    else:
        return f"последние {config.DEFAULT_MESSAGES_LIMIT} сообщений"


def call_llm_api(messages: list, model_name: str, model_url: str) -> str:
    """
    Вызов Eliza API (OpenAI-compatible)
    
    Args:
        messages: Список сообщений в формате OpenAI
        model_name: Название модели для payload (deepseek-internal, aliceai-llm)
        model_url: URL endpoint для запроса
        
    Returns:
        Текст ответа от модели
    """
    try:
        logger.info(f"🤖 Запрос к LLM: {model_name}")
        
        # Логируем сообщения
        for msg in messages:
            role_emoji = "👤" if msg["role"] == "user" else "⚙️" if msg["role"] == "system" else "🤖"
            preview = msg.get("content", "")[:100] + ("..." if len(msg.get("content", "")) > 100 else "")
            logger.info(f"  {role_emoji} {msg['role']}: {preview}")
        
        # Формируем headers и payload
        headers = {
            "Authorization": f"OAuth {config.ELIZA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model_name,
            "messages": messages
        }
        
        # Логируем детали запроса
        logger.info(f"📤 HTTP POST {model_url}")
        logger.info(f"   Модель в payload: {model_name}")
        logger.debug(f"  Payload size: {len(json.dumps(payload))} bytes")
        logger.debug(f"  Messages count: {len(messages)}")
        
        response = requests.post(
            model_url,
            json=payload,
            headers=headers,
            verify=False,  # Отключаем проверку SSL для Eliza
            timeout=60
        )
        
        # Логируем детали ответа
        logger.info(f"📥 HTTP Response: {response.status_code}")
        logger.debug(f"  Response size: {len(response.text)} bytes")
        
        if response.status_code != 200:
            logger.error(f"Ошибка API ({response.status_code}): {response.text}")
            response.raise_for_status()
        
        result = response.json()
        
        # Логируем полный ответ для отладки (только структуру, без содержимого)
        logger.debug(f"📋 Структура ответа: {list(result.keys())}")
        
        # Eliza возвращает ответ в разных форматах:
        # 1. {"response": {"choices": [...]}} - стандартный формат
        # 2. {"choices": [...]} - прямой формат OpenAI
        # 3. {"response": {"result": {"alternatives": [...]}}} - формат Yandex GPT
        
        content = None
        
        if "response" in result:
            response_data = result["response"]
            logger.debug(f"📋 Структура response: {list(response_data.keys())}")
            
            if "choices" in response_data:
                # Формат: {"response": {"choices": [...]}}
                content = response_data["choices"][0]["message"]["content"]
            elif "result" in response_data and "alternatives" in response_data["result"]:
                # Формат Yandex GPT: {"response": {"result": {"alternatives": [...]}}}
                content = response_data["result"]["alternatives"][0]["message"]["text"]
            else:
                logger.error(f"Неожиданная структура в 'response': {response_data}")
                raise Exception("API вернул неожиданный формат ответа в 'response'")
                
        elif "choices" in result:
            # Формат OpenAI: {"choices": [...]}
            content = result["choices"][0]["message"]["content"]
            
        elif "result" in result and "alternatives" in result["result"]:
            # Прямой формат Yandex GPT: {"result": {"alternatives": [...]}}
            content = result["result"]["alternatives"][0]["message"]["text"]
        else:
            logger.error(f"Неожиданный формат ответа API. Ключи верхнего уровня: {list(result.keys())}")
            logger.error(f"Полный ответ: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")
            raise Exception("API вернул неожиданный формат ответа")
        
        if not content:
            raise Exception("API вернул пустой ответ")
        
        # Логируем ответ
        preview = content[:200] + "..." if len(content) > 200 else content
        logger.info(f"✅ Ответ от LLM: {preview}")
        
        return content
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка HTTP запроса к API: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Статус: {e.response.status_code}")
            logger.error(f"Ответ: {e.response.text}")
        raise Exception(f"Ошибка LLM API: {str(e)}")
    
    except Exception as e:
        logger.error(f"Неожиданная ошибка при вызове LLM: {e}", exc_info=True)
        raise


async def init_telethon_client():
    """Инициализация Telethon клиента"""
    global telethon_client
    
    telethon_client = TelegramClient(
        config.SESSION_NAME,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH
    )
    
    await telethon_client.start(phone=config.TELEGRAM_PHONE)
    logger.info("✅ Telethon клиент подключен")


async def ensure_telethon_connected():
    """Проверяет подключение Telethon и переподключается при необходимости"""
    global telethon_client
    
    if telethon_client is None:
        logger.warning("⚠️ Telethon клиент не инициализирован, инициализирую...")
        await init_telethon_client()
        return
    
    if not telethon_client.is_connected():
        logger.warning("⚠️ Telethon отключен, переподключаюсь...")
        try:
            await telethon_client.connect()
            if not await telethon_client.is_user_authorized():
                logger.error("❌ Telethon не авторизован, требуется повторная авторизация")
                await telethon_client.start(phone=config.TELEGRAM_PHONE)
            logger.info("✅ Telethon успешно переподключен")
        except Exception as e:
            logger.error(f"❌ Ошибка при переподключении Telethon: {e}")
            raise Exception("Не удалось подключиться к Telegram. Проверьте интернет-соединение.")


def is_muted(notify_settings) -> bool:
    """
    Проверяет, замьючен ли канал/топик по notify_settings
    
    Args:
        notify_settings: объект notify_settings из Telethon
        
    Returns:
        True если замьючен, False если нет
    """
    if not notify_settings:
        return False
    
    # Проверка 1: silent (уведомления полностью отключены)
    if hasattr(notify_settings, 'silent') and notify_settings.silent:
        return True
    
    # Проверка 2: mute_until (замьючен до определенного времени)
    if hasattr(notify_settings, 'mute_until') and notify_settings.mute_until:
        mute_until = notify_settings.mute_until
        
        # Если datetime - проверяем что в будущем
        if hasattr(mute_until, 'timestamp'):
            now_ts = datetime.now(timezone.utc).timestamp()
            return mute_until.timestamp() > now_ts
        
        # Если int - проверяем что больше текущего времени
        elif isinstance(mute_until, int) and mute_until > 0:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            return mute_until > now_ts
    
    return False


async def parse_command_with_gpt(user_message: str) -> Dict[str, Any]:
    """
    Отправляет текст пользователя в GPT для парсинга команды
    
    Args:
        user_message: Текст сообщения пользователя
        
    Returns:
        Dict с структурированной командой в формате:
        {
            "chat_name": "название чата" | null,
            "period_type": "days" | "last_messages" | null,
            "period_value": число дней или количество сообщений | null,
            "query": "полный текст запроса пользователя"
        }
    """
    try:
        messages = [
            {"role": "system", "content": config.PARSER_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        response_text = call_llm_api(
            messages, 
            config.PARSER_MODEL_NAME,
            config.PARSER_MODEL_URL
        )
        
        # Некоторые модели (DeepSeek, YandexGPT) возвращают JSON в markdown блоках
        # Очищаем от markdown обертки если есть
        cleaned_text = response_text.strip()
        
        # Убираем ```json ... ``` или ``` ... ```
        if cleaned_text.startswith("```"):
            # Убираем первую строку с ```json или ```
            lines = cleaned_text.split('\n')
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            # Убираем последнюю строку с ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned_text = '\n'.join(lines).strip()
        
        logger.debug(f"📋 Очищенный JSON для парсинга: {cleaned_text[:200]}")
        
        # Парсим JSON ответ
        command = json.loads(cleaned_text)
        return command
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка при парсинге команды: {error_msg}")
        return {
            "error": error_msg
        }


def calculate_similarity(str1: str, str2: str) -> float:
    """Вычислить схожесть двух строк (0.0 - 1.0)"""
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def _is_connection_error(error_msg: str) -> bool:
    """Проверяет, является ли ошибка ошибкой соединения"""
    error_lower = error_msg.lower()
    return "disconnected" in error_lower or "connection" in error_lower


def _handle_telegram_error(e: Exception, action: str) -> Exception:
    """
    Обрабатывает ошибки Telegram и возвращает понятное исключение

    Args:
        e: Исходное исключение
        action: Описание действия для сообщения об ошибке

    Returns:
        Exception с понятным сообщением
    """
    error_msg = str(e)
    if _is_connection_error(error_msg):
        logger.error(f"❌ Ошибка подключения к Telegram при {action}: {e}")
        return Exception("Потеряно соединение с Telegram. Попробуйте еще раз.")
    else:
        logger.error(f"❌ Ошибка при {action}: {e}")
        return Exception(f"Ошибка при {action}: {error_msg}")


def find_best_match(search_query: str, items: list, get_title_func, fuzzy: bool = True) -> tuple[Any, str, float]:
    """
    Обобщенная функция поиска лучшего совпадения среди списка элементов
    
    Args:
        search_query: Поисковый запрос
        items: Список элементов для поиска
        get_title_func: Функция для получения названия(й) из элемента - возвращает список вариантов
        fuzzy: Использовать нечеткий поиск
        
    Returns:
        Tuple (best_item, best_title, best_similarity) или (None, None, 0.0)
    """
    # Нормализуем поисковый запрос
    search_normalized = remove_emojis(search_query.lower()).strip()
    
    best_match = None
    best_title = None
    best_similarity = 0.0
    
    for item in items:
        # Получаем варианты названий для этого элемента
        title_variants = get_title_func(item)
        if not title_variants:
            continue
        
        for variant in title_variants:
            if not variant:
                continue
                
            variant_normalized = remove_emojis(variant.lower()).strip()
            
            # 1. Точное совпадение
            if search_normalized == variant_normalized:
                return item, variant, 1.0
            
            # 2. Поиск как подстрока
            if search_normalized in variant_normalized or variant_normalized in search_normalized:
                similarity = 0.9  # Высокий приоритет для вхождения
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = item
                    best_title = variant
            
            # 3. Нечеткий поиск
            if fuzzy:
                similarity = calculate_similarity(search_normalized, variant_normalized)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = item
                    best_title = variant
    
    return best_match, best_title, best_similarity


def get_chat_display_name(entity) -> str:
    """
    Получает отображаемое название для Telegram entity

    Args:
        entity: User, Chat или Channel из Telethon

    Returns:
        Строка с названием чата/пользователя
    """
    if isinstance(entity, User):
        title = entity.first_name or ""
        if entity.last_name:
            title += f" {entity.last_name}"
        return title or str(entity.id)
    elif isinstance(entity, (Chat, Channel)):
        return entity.title if hasattr(entity, 'title') else str(entity.id)
    return str(entity.id)


def get_entity_title_variants(entity) -> list:
    """
    Получает список вариантов названий для Telegram entity

    Args:
        entity: User, Chat или Channel из Telethon

    Returns:
        Список строк с вариантами названий
    """
    if isinstance(entity, User):
        # Личная переписка
        title = entity.first_name or ""
        if entity.last_name:
            title += f" {entity.last_name}"
        if entity.username:
            return [title, entity.username, f"@{entity.username}"]
        return [title] if title else []
    elif isinstance(entity, (Chat, Channel)):
        # Группа или канал
        return [entity.title] if hasattr(entity, 'title') else []
    return []


def utc_to_local(utc_dt: datetime) -> datetime:
    """
    Конвертирует UTC datetime в локальное время
    
    Args:
        utc_dt: datetime объект в UTC (может быть aware или naive)
        
    Returns:
        datetime в локальном часовом поясе
    """
    # Если datetime naive (без timezone), считаем что это UTC
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    # Конвертируем в локальное время
    local_dt = utc_dt.astimezone()
    return local_dt


def remove_emojis(text: str) -> str:
    """Удаляет эмодзи из текста для более точного поиска"""
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        u"\U0001FA00-\U0001FA6F"  # Chess Symbols
        u"\U00002600-\U000026FF"  # Miscellaneous Symbols
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub('', text).strip()


async def find_chat_by_name(chat_name: str, fuzzy: bool = True):
    """
    Находит чат по имени с использованием Telethon и нечеткого поиска

    Args:
        chat_name: Название чата или имя пользователя
        fuzzy: Использовать нечеткий поиск при отсутствии точного совпадения

    Returns:
        Tuple (entity, display_name, similarity) или (None, None, 0)
    """
    try:
        await ensure_telethon_connected()

        logger.info(f"🔍 Поиск чата '{chat_name}' через Telegram API...")

        # Собираем все диалоги в список
        dialogs = []
        async for dialog in telethon_client.iter_dialogs():
            dialogs.append(dialog.entity)

        logger.info(f"📊 Просканировано диалогов: {len(dialogs)}")

        # Используем обобщенную функцию поиска
        best_match, best_name, best_similarity = find_best_match(
            chat_name, dialogs, get_entity_title_variants, fuzzy
        )

        if best_match and best_similarity >= 0.5:
            logger.info(f"✅ Найден чат: '{best_name}' (схожесть: {best_similarity:.1%})")
            return best_match, best_name, best_similarity

        logger.warning(f"❌ Чат '{chat_name}' не найден среди {len(dialogs)} диалогов")
        return None, None, 0.0

    except Exception as e:
        raise _handle_telegram_error(e, "поиске чата")


async def get_folders() -> Dict[int, Any]:
    """
    Получает список всех папок пользователя
    
    Returns:
        Dict[folder_id -> dialog_filter_object]
    """
    try:
        await ensure_telethon_connected()
        
        # Получаем список фильтров (папок)
        result = await telethon_client(GetDialogFiltersRequest())
        
        folders = {}
        
        for dialog_filter in result:
            if hasattr(dialog_filter, 'id') and hasattr(dialog_filter, 'title'):
                folders[dialog_filter.id] = dialog_filter
                logger.debug(f"  - ID {dialog_filter.id}: {dialog_filter.title}")
        
        logger.info(f"📁 Найдено папок: {len(folders)}")
        
        return folders
    
    except Exception as e:
        logger.error(f"❌ Ошибка при получении папок: {e}")
        return {}


async def find_folder_by_name(folder_name: str, fuzzy: bool = True) -> tuple[Optional[int], Optional[str], float, Optional[Any]]:
    """
    Находит папку по имени с использованием нечеткого поиска
    
    Args:
        folder_name: Название папки для поиска
        fuzzy: Использовать нечеткий поиск
        
    Returns:
        Tuple (folder_id, folder_title, similarity, dialog_filter) или (None, None, 0, None)
    """
    try:
        logger.info(f"📁 Поиск папки '{folder_name}'...")
        
        folders = await get_folders()
        if not folders:
            logger.warning("❌ Папки не найдены")
            return None, None, 0.0, None
        
        # Преобразуем словарь папок в список кортежей (folder_id, dialog_filter)
        folder_items = [(folder_id, dialog_filter) for folder_id, dialog_filter in folders.items()]
        
        # Функция для извлечения названия папки
        def get_folder_title(item):
            return [item[1].title]  # item[1] - это dialog_filter
        
        # Используем обобщенную функцию поиска
        best_item, best_title, best_similarity = find_best_match(folder_name, folder_items, get_folder_title, fuzzy)
        
        if best_item and best_similarity >= 0.5:
            folder_id, dialog_filter = best_item
            logger.info(f"✅ Найдена папка: '{best_title}' (схожесть: {best_similarity:.1%})")
            return folder_id, best_title, best_similarity, dialog_filter
        
        logger.warning(f"❌ Папка '{folder_name}' не найдена")
        return None, None, 0.0, None
    
    except Exception as e:
        logger.error(f"❌ Ошибка при поиске папки: {e}")
        raise Exception(f"Ошибка при поиске папки: {str(e)}")


async def get_chats_in_folder(dialog_filter: Any) -> list:
    """
    Получает список чатов в указанной папке
    
    Args:
        dialog_filter: Объект DialogFilter из Telethon
        
    Returns:
        Список кортежей (entity, display_name)
    """
    try:
        await ensure_telethon_connected()
        
        folder_id = dialog_filter.id
        folder_title = dialog_filter.title
        logger.info(f"📂 Загрузка чатов из папки '{folder_title}' (ID {folder_id})...")
        
        # Получаем список ID чатов в папке из include_peers
        included_peer_ids = set()
        if hasattr(dialog_filter, 'include_peers'):
            for peer in dialog_filter.include_peers:
                # Извлекаем ID из peer
                if hasattr(peer, 'user_id'):
                    included_peer_ids.add(peer.user_id)
                elif hasattr(peer, 'chat_id'):
                    included_peer_ids.add(peer.chat_id)
                elif hasattr(peer, 'channel_id'):
                    included_peer_ids.add(peer.channel_id)
        
        logger.debug(f"  Папка содержит {len(included_peer_ids)} чатов по ID")
        
        chats = []
        checked_count = 0
        
        # Проходим по всем диалогам и проверяем, входят ли они в папку
        async for dialog in telethon_client.iter_dialogs():
            checked_count += 1
            entity = dialog.entity
            entity_id = abs(entity.id)  # Убираем знак если есть
            
            # Получаем название
            title = get_chat_display_name(entity)
            
            # Проверяем, входит ли этот чат в папку
            if entity_id in included_peer_ids or entity.id in included_peer_ids:
                logger.debug(f"  ✅ Чат '{title}' (ID: {entity.id}) в папке")
                chats.append((entity, title))
        
        logger.info(f"📊 Проверено диалогов: {checked_count}")
        logger.info(f"✅ Найдено чатов в папке '{folder_title}': {len(chats)}")
        
        return chats
    
    except Exception as e:
        logger.error(f"❌ Ошибка при получении чатов из папки: {e}")
        raise Exception(f"Ошибка при получении чатов из папки: {str(e)}")


async def mark_chat_as_read(chat_entity) -> bool:
    """
    Отмечает все сообщения в чате как прочитанные
    
    Args:
        chat_entity: Entity чата из Telethon
        
    Returns:
        True если успешно, False если произошла ошибка
    """
    try:
        await ensure_telethon_connected()
        
        # Получаем название чата для логирования
        chat_name = get_chat_display_name(chat_entity)
        
        logger.info(f"📖 Отмечаю сообщения в чате '{chat_name}' как прочитанные...")
        
        # Отмечаем чат прочитанным
        await telethon_client.send_read_acknowledge(chat_entity)
        
        logger.info(f"✅ Чат '{chat_name}' отмечен как прочитанный")
        return True
    
    except Exception as e:
        logger.error(f"❌ Ошибка при пометке чата как прочитанного: {e}")
        return False


async def _get_unread_params(chat_entity) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Получает параметры для загрузки непрочитанных сообщений

    Args:
        chat_entity: Entity чата из Telethon

    Returns:
        Tuple (limit, min_id, error_message) - error_message не None если произошла ошибка
    """
    try:
        dialog = None
        async for d in telethon_client.iter_dialogs():
            if d.entity.id == chat_entity.id:
                dialog = d
                break

        if not dialog:
            logger.warning("⚠️ Не удалось найти dialog для этого чата")
            return None, None, "Не удалось получить информацию о непрочитанных сообщениях."

        unread_count = dialog.unread_count
        read_inbox_max_id = dialog.dialog.read_inbox_max_id

        if unread_count == 0:
            logger.warning("⚠️ В этом чате нет непрочитанных сообщений")
            return None, None, "В этом чате нет непрочитанных сообщений."

        logger.info(f"📬 Непрочитанных сообщений: {unread_count}, последнее прочитанное: {read_inbox_max_id}")

        min_id = read_inbox_max_id if read_inbox_max_id is not None else None
        return unread_count, min_id, None

    except Exception as e:
        raise _handle_telegram_error(e, "получении непрочитанных сообщений")


async def get_chat_history(chat_entity, period_type: str = None, period_value: int = None) -> tuple[str, Optional[int]]:
    """
    Получает историю сообщений из чата за указанный период

    Args:
        chat_entity: Entity чата из Telethon
        period_type: Тип периода ("days", "hours", "today", "unread", "last_messages", None)
        period_value: Значение периода (количество дней/часов/сообщений)

    Returns:
        Tuple (отформатированная история переписки, ID первого сообщения в выборке)
    """
    try:
        # Проверяем подключение к Telegram
        await ensure_telethon_connected()
        
        logger.info(f"📥 Загрузка истории чата через Telegram API (период: {period_type}, значение: {period_value})...")
        logger.info(f"⏰ Текущее время: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        messages = []
        offset_date = None
        limit = None
        min_id = None
        
        # Определяем параметры загрузки в зависимости от типа периода
        # Telethon работает с UTC, поэтому все offset_date должны быть в UTC
        if period_type == "days" and period_value:
            # За последние N дней
            offset_date = datetime.now(timezone.utc) - timedelta(days=period_value)
            logger.info(f"📅 Загружаю сообщения начиная с {offset_date.astimezone().strftime('%Y-%m-%d %H:%M:%S')} (локальное время)")
        
        elif period_type == "hours" and period_value:
            # За последние N часов
            offset_date = datetime.now(timezone.utc) - timedelta(hours=period_value)
            logger.info(f"🕐 Загружаю сообщения начиная с {offset_date.astimezone().strftime('%Y-%m-%d %H:%M:%S')} (локальное время)")
        
        elif period_type == "today":
            # Сегодня с начала суток (локальная полночь в UTC)
            # Получаем текущее локальное время
            now_local = datetime.now().astimezone()
            # Устанавливаем полночь в локальном часовом поясе
            midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            # Конвертируем в UTC для Telethon
            offset_date = midnight_local.astimezone(timezone.utc)
            logger.info(f"📅 Загружаю сообщения с начала суток (00:00 локального времени)") 
        
        elif period_type == "unread":
            # Только непрочитанные сообщения
            limit, min_id, error_msg = await _get_unread_params(chat_entity)
            if error_msg:
                return error_msg, None
        
        elif period_type == "last_messages" and period_value:
            # Последние N сообщений
            limit = period_value
        
        else:
            # По умолчанию - последние N сообщений (настраивается в config)
            limit = config.DEFAULT_MESSAGES_LIMIT
        
        # Загружаем сообщения
        # Формируем параметры для iter_messages (не передаем None значения)
        iter_params = {'reverse': True}
        if offset_date is not None:
            iter_params['offset_date'] = offset_date
            logger.debug(f"  offset_date (UTC): {offset_date}")
            logger.debug(f"  offset_date (local): {offset_date.astimezone()}")
        if limit is not None:
            iter_params['limit'] = limit
            logger.debug(f"  limit: {limit}")
        if min_id is not None:
            iter_params['min_id'] = min_id
            logger.debug(f"  min_id: {min_id}")

        first_message_id = None
        async for message in telethon_client.iter_messages(chat_entity, **iter_params):
            if message.text:
                # Запоминаем ID первого сообщения в выборке
                if first_message_id is None:
                    first_message_id = message.id

                sender_name = get_chat_display_name(message.sender) if message.sender else "Неизвестно"

                # Конвертируем UTC время в локальное
                local_time = utc_to_local(message.date)
                timestamp = local_time.strftime("%Y-%m-%d %H:%M:%S")
                messages.append(f"[{timestamp}] {sender_name}: {message.text}")

        if not messages:
            logger.warning("⚠️ Сообщения за указанный период не найдены")
            return "Сообщения за указанный период не найдены.", None

        logger.info(f"✅ Загружено сообщений: {len(messages)} (временные метки в локальном часовом поясе)")
        history = "\n".join(messages)
        logger.debug(f"  Общий размер истории: {len(history)} символов")
        return history, first_message_id
    
    except Exception as e:
        raise _handle_telegram_error(e, "получении истории чата")


async def process_chat_with_openai(chat_history: str, query: str, period_context: str = None) -> str:
    """
    Обрабатывает историю чата согласно запросу пользователя
    
    Args:
        chat_history: История переписки
        query: Запрос пользователя (может содержать команду суммаризировать, вопрос и т.д.)
        period_context: Контекст периода ("непрочитанные сообщения", "за неделю" и т.д.)
        
    Returns:
        Ответ от LLM
    """
    try:
        # Маскируем персональные данные (телефоны и email) перед отправкой в LLM
        logger.info("🔒 Маскирование персональных данных...")
        original_length = len(chat_history)
        masked_history = mask(chat_history, replacer='[PII]')
        masked_length = len(masked_history)
        
        if masked_length != original_length:
            logger.info(f"✅ Персональные данные замаскированы (размер изменился: {original_length} → {masked_length})")
        else:
            logger.info("✅ Персональные данные не обнаружены")
        
        # Формируем контекст для LLM
        history_context = f"История чата ({period_context})" if period_context else "История чата"
        
        messages = [
            {"role": "system", "content": config.PROCESSOR_PROMPT},
            {"role": "user", "content": f"{history_context}:\n\n{masked_history}\n\nЗапрос: {query}"}
        ]
        
        answer = call_llm_api(
            messages,
            config.PROCESSOR_MODEL_NAME,
            config.PROCESSOR_MODEL_URL
        )
        return answer
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка при обработке запроса с LLM: {error_msg}")
        return f"❌ {error_msg}"


@admin_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для работы с историей твоих Telegram чатов и папок.\n\n"
        "Просто напиши мне, что тебе нужно, например:\n"
        "**Для чатов:**\n"
        "• 'Сделай суммаризацию за неделю из чата Работа'\n"
        "• 'О чем говорили в личке с Иваном сегодня?'\n"
        "• 'Что в непрочитанных в чате Проект и отметь прочитанным?'\n\n"
        "**Для папок:**\n"
        "• 'Что нового в папке Рабочие чаты?'\n"
        "• 'Суммаризируй папку Личное за неделю и пометь прочитанным'\n"
        "• 'До чего договорились в папке Проекты?'\n\n"
        "💡 Если не указывать чат/папку/период, буду использовать предыдущий!\n"
        "📖 Добавь 'и отметь прочитанным' для автоматической пометки сообщений!\n\n"
        f"⚙️ Модели:\n"
        f"• Парсинг: {config.PARSER_MODEL_NAME}\n"
        f"• Обработка: {config.PROCESSOR_MODEL_NAME}\n\n"
        "Используй /help для просмотра всех команд"
    )


@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help - показать все доступные команды"""
    await update.message.reply_text(
        "📋 **Доступные команды:**\n\n"
        "/start - приветствие и информация о боте\n"
        "/help - показать это сообщение\n"
        "/folders - показать список ваших папок\n"
        "/unread - список каналов с непрочитанными сообщениями\n"
        "/context - показать текущий сохраненный контекст\n"
        "/reset - сбросить контекст\n\n"
        "**Примеры для чатов:**\n"
        "• Суммаризируй чат Работа за неделю\n"
        "• О чем говорили в личке с Иваном сегодня?\n"
        "• Что в непрочитанных в чате Проект и отметь прочитанным?\n\n"
        "**Примеры для папок:**\n"
        "• Что нового в папке Рабочие чаты?\n"
        "• Суммаризируй папку Личное за неделю и пометь прочитанным\n"
        "• До чего договорились в папке Проекты?\n\n"
        "💡 Бот запоминает последний чат/папку и период!\n"
        "📖 Добавь 'и отметь прочитанным' для автоматической пометки!\n\n"
        "**Модели:**\n"
        f"• Парсинг: {config.PARSER_MODEL_NAME}\n"
        f"• Обработка: {config.PROCESSOR_MODEL_NAME}\n\n"
    )


@admin_only
async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущий контекст"""
    if not current_context:
        await update.message.reply_text("📭 Контекст пуст. Начните новый запрос!")
        return
    
    ctx = current_context
    period_info = format_period_text(ctx.get("period_type"), ctx.get("period_value"))
    
    await update.message.reply_text(
        f"📝 Текущий контекст:\n\n"
        f"Чат: {ctx.get('target_name', 'не указан')}\n"
        f"Тип: {ctx.get('target_type', 'chat')}\n"
        f"Период: {period_info}\n\n"
        f"Следующий запрос без указания чата/периода будет использовать эти настройки."
    )


@admin_only
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбросить контекст"""
    current_context.clear()
    await update.message.reply_text("🔄 Контекст сброшен!")


def get_first_unread_message_id(obj) -> Optional[int]:
    """
    Вычисляет ID первого непрочитанного сообщения
    
    Args:
        obj: Dialog или Topic объект из Telethon
        
    Returns:
        ID первого непрочитанного сообщения или None
    """
    # Если есть read_inbox_max_id, то первое непрочитанное = read_inbox_max_id + 1
    if hasattr(obj, 'read_inbox_max_id') and obj.read_inbox_max_id:
        return obj.read_inbox_max_id + 1
    
    # Fallback: используем top_message
    if hasattr(obj, 'top_message') and obj.top_message:
        return obj.top_message
    
    return None


def generate_channel_link(entity, topic_id: int = None, message_id: int = None) -> str:
    """
    Генерирует ссылку на канал или топик форума
    
    Args:
        entity: Entity канала/форума из Telethon
        topic_id: ID топика (для форумов)
        message_id: ID сообщения (для приватных каналов)
        
    Returns:
        URL ссылка на канал/топик, или None если ссылка невозможна
    """
    from telethon.tl.types import Chat
    
    # Для публичных каналов (с username)
    if hasattr(entity, 'username') and entity.username:
        if topic_id:
            return f"https://t.me/{entity.username}/{topic_id}"
        if message_id and message_id > 0:
            return f"https://t.me/{entity.username}/{message_id}"
        return f"https://t.me/{entity.username}"
    
    # Для обычных групп (Chat, не Channel) ссылки не работают
    # Это ограничение Telegram API - для приватных обычных групп нельзя создать ссылку
    if isinstance(entity, Chat):
        logger.debug(f"ℹ️ Обычная группа (Chat) '{entity.title}' - ссылка недоступна")
        return None
    
    # Для супергрупп и каналов (Channel)
    # В Telegram API супергруппы и каналы имеют ID формата -100XXXXXXXXXX
    # Для ссылок t.me/c/{channel_id}/{message_id} нужен ID без префикса -100
    
    # Преобразуем: -1001234567890 -> 1001234567890 -> 1234567890
    str_id = str(abs(entity.id))
    
    # Если ID начинается с "100", убираем этот префикс
    if str_id.startswith("100"):
        channel_id = str_id[3:]
    else:
        channel_id = str_id
    
    # Для приватных каналов нужен ID сообщения
    if not message_id or message_id < 1:
        logger.warning(f"⚠️ Нет корректного message_id для entity.id={entity.id}")
        return None
    
    logger.debug(f"🔗 generate_channel_link: entity.id={entity.id} -> channel_id={channel_id}, message_id={message_id}, topic_id={topic_id}")
    
    if topic_id:
        return f"https://t.me/c/{channel_id}/{topic_id}"
    return f"https://t.me/c/{channel_id}/{message_id}"


def format_channel_line(name: str, link: Optional[str], unread_count: Optional[int], mentions_count: int) -> str:
    """
    Форматирует строку для вывода канала/топика

    Args:
        name: Название канала/топика
        link: Ссылка на канал/топик (None если ссылка невозможна)
        unread_count: Количество непрочитанных (None = "*")
        mentions_count: Количество упоминаний

    Returns:
        Отформатированная строка
    """
    count_str = "*" if unread_count is None else str(unread_count)
    mentions_str = f", 🔔 {mentions_count} упоминаний" if mentions_count > 0 else ""
    safe_name = escape_markdown(name)

    # Если ссылка недоступна (обычные группы), показываем без ссылки
    if link is None:
        return f"• {safe_name} ({count_str}{mentions_str})\n"

    return f"• [{safe_name}]({link}) ({count_str}{mentions_str})\n"


@admin_only
async def folders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список папок пользователя"""
    processing_msg = await update.message.reply_text("Загружаю список папок... ⏳")
    
    try:
        folders = await get_folders()
        
        if not folders:
            await processing_msg.edit_text("📁 У вас нет пользовательских папок.")
            return
        
        # Формируем список папок
        folder_list = "📁 **Ваши папки:**\n\n"
        for folder_id, dialog_filter in sorted(folders.items()):
            folder_title = dialog_filter.title
            folder_list += f"• {folder_title} (ID: {folder_id})\n"
        
        folder_list += "\n💡 Используйте папки в командах: _'Что нового в папке {название}'_"
        
        await processing_msg.edit_text(folder_list, parse_mode='Markdown')
    
    except Exception as e:
        logger.error(f"❌ Ошибка при получении списка папок: {e}")
        await processing_msg.edit_text(f"❌ Ошибка: {str(e)}")


async def _collect_forum_topics(entity, channel_name: str) -> tuple[list, bool]:
    """
    Собирает непрочитанные топики из форум-канала

    Args:
        entity: Entity форума из Telethon
        channel_name: Название канала для отображения

    Returns:
        Tuple (список топиков, успешно ли загрузились топики)
    """
    topics_list = []
    offset_date = None
    offset_id = 0
    offset_topic = 0

    try:
        while True:
            result = await telethon_client(GetForumTopicsRequest(
                channel=entity,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100
            ))

            if not result.topics:
                break

            for topic in result.topics:
                # Проверяем, не замьючен ли топик
                topic_muted = is_muted(getattr(topic, 'notify_settings', None))

                # Пропускаем замьюченные топики или топики без непрочитанных
                if topic_muted or not hasattr(topic, 'unread_count') or topic.unread_count == 0:
                    continue

                topic_title = topic.title if hasattr(topic, 'title') else f"Топик {topic.id}"
                topic_mentions = getattr(topic, 'unread_mentions_count', 0)

                topics_list.append({
                    'name': f"{channel_name} — {topic_title}",
                    'unread_count': topic.unread_count,
                    'mentions_count': topic_mentions,
                    'has_mentions': topic_mentions > 0,
                    'is_forum_unknown': False,
                    'entity': entity,
                    'topic_id': topic.id,
                    'message_id': get_first_unread_message_id(topic)
                })

            # Подготовка к следующей итерации
            if len(result.topics) < 100:
                break

            last_topic = result.topics[-1]
            offset_topic = last_topic.id
            if hasattr(last_topic, 'date'):
                offset_date = last_topic.date
            offset_id = last_topic.top_message if hasattr(last_topic, 'top_message') else 0

        return topics_list, True

    except Exception as e:
        logger.warning(f"⚠️ Не удалось загрузить топики для форума '{channel_name}': {e}")
        return [], False


def _should_skip_dialog(dialog, entity) -> bool:
    """Проверяет, нужно ли пропустить диалог"""
    # Пропускаем личные переписки
    if isinstance(entity, User):
        return True

    # Пропускаем без непрочитанных
    if dialog.unread_count == 0:
        return True

    # Пропускаем архивированные
    if dialog.archived:
        return True

    # Проверяем замьючен ли канал/форум
    channel_muted = (
        is_muted(getattr(dialog, 'notify_settings', None)) or
        (hasattr(dialog, 'dialog') and dialog.dialog and
         is_muted(getattr(dialog.dialog, 'notify_settings', None)))
    )
    return channel_muted


async def collect_unread_channels() -> list:
    """
    Собирает список каналов и топиков с непрочитанными сообщениями

    Returns:
        Список словарей с информацией о каналах/топиках
    """
    await ensure_telethon_connected()
    channels_list = []

    async for dialog in telethon_client.iter_dialogs():
        entity = dialog.entity

        # Используем хелпер для проверки, нужно ли пропустить диалог
        if _should_skip_dialog(dialog, entity):
            continue

        channel_name = entity.title if hasattr(entity, 'title') else str(entity.id)
        is_forum = getattr(entity, 'forum', False)

        # Для форум-каналов получаем топики
        if is_forum:
            logger.debug(f"🔍 Форум-канал '{channel_name}' - загружаем топики...")
            topics, success = await _collect_forum_topics(entity, channel_name)

            if success and topics:
                channels_list.extend(topics)
                continue

            # Если не удалось загрузить - показываем форум целиком с пометкой
            is_forum_unknown = not success
            unread_count = None if not success else dialog.unread_count
        else:
            is_forum_unknown = False
            unread_count = dialog.unread_count

        # Добавляем в список (обычные каналы или форумы с ошибкой загрузки топиков)
        channels_list.append({
            'name': channel_name,
            'unread_count': unread_count,
            'mentions_count': dialog.unread_mentions_count,
            'has_mentions': dialog.unread_mentions_count > 0,
            'is_forum_unknown': is_forum_unknown,
            'entity': entity,
            'topic_id': None,
            'message_id': get_first_unread_message_id(dialog.dialog) if hasattr(dialog, 'dialog') else None
        })

    return channels_list


def sort_unread_channels(channels_list: list) -> list:
    """
    Сортирует каналы с группировкой топиков одного форума
    
    Args:
        channels_list: Список каналов/топиков
        
    Returns:
        Отсортированный список
    """
    # 1. Группируем по форумам (название форума = часть до " — ")
    groups = defaultdict(list)
    for item in channels_list:
        # Извлекаем название форума (если это топик)
        if ' — ' in item['name']:
            forum_name = item['name'].split(' — ')[0]
        else:
            # Обычный канал - каждый в своей группе
            forum_name = item['name']
        
        groups[forum_name].append(item)
    
    # 2. Для каждой группы вычисляем приоритет сортировки
    group_priorities = {}
    for forum_name, items in groups.items():
        # Проверяем наличие упоминаний в группе
        has_mentions_in_group = any(item['has_mentions'] for item in items)
        # Максимальное количество непрочитанных в группе
        max_unread = max((item['unread_count'] for item in items if item['unread_count'] is not None), default=0)
        
        group_priorities[forum_name] = (has_mentions_in_group, max_unread)
    
    # 3. Сортируем группы по приоритету
    sorted_groups = sorted(groups.items(), 
                          key=lambda x: (-int(group_priorities[x[0]][0]), -group_priorities[x[0]][1]))
    
    # 4. Внутри каждой группы сортируем топики
    sorted_channels_list = []
    for forum_name, items in sorted_groups:
        # Сортируем топики внутри группы: mentions, потом количество
        sorted_items = sorted(items, 
                             key=lambda x: (-int(x['has_mentions']), 
                                          -(x['unread_count'] if x['unread_count'] is not None else 0)))
        sorted_channels_list.extend(sorted_items)
    
    return sorted_channels_list


def format_unread_messages(channels_list: list) -> list:
    """
    Форматирует список непрочитанных каналов в сообщения для отправки
    
    Args:
        channels_list: Отсортированный список каналов
        
    Returns:
        Список сообщений (строк) для отправки
    """
    messages_to_send = []
    current_message = "📬 **Каналы с непрочитанными:**\n\n"
    max_length = 4096
        
    # Проходим по всем каналам
    for ch in channels_list:
        # Генерируем ссылку и форматируем строку
        link = generate_channel_link(ch['entity'], ch.get('topic_id'), ch.get('message_id'))
        line = format_channel_line(ch['name'], link, ch['unread_count'], ch['mentions_count'])
            
        # Проверяем, поместится ли строка
        if len(current_message) + len(line) > max_length:
            messages_to_send.append(current_message)
            current_message = line
        else:
            current_message += line
    
    # Итоговая статистика
    total_items = len(channels_list)
    # Суммируем только каналы с известным количеством непрочитанных
    total_unread = sum(ch['unread_count'] for ch in channels_list if ch['unread_count'] is not None)
    total_mentions = sum(ch['mentions_count'] for ch in channels_list if ch['has_mentions'])
    has_forums_unknown = any(ch.get('is_forum_unknown', False) for ch in channels_list)
    
    if total_mentions > 0:
        stats = f"\n📊 **Всего:** {total_items}, {total_unread} непрочитанных, 🔔 {total_mentions} упоминаний"
    else:
        stats = f"\n📊 **Всего:** {total_items}, {total_unread} непрочитанных"
    
    # Добавляем примечание о форумах с неизвестным количеством, если они есть
    if has_forums_unknown:
        stats += "\n\n_* — точное количество в форуме не определено_"
    
    if len(current_message) + len(stats) > max_length:
        messages_to_send.append(current_message)
        current_message = stats
    else:
        current_message += stats
    
    # Добавляем последнее сообщение
    if current_message:
        messages_to_send.append(current_message)
    
    return messages_to_send


@admin_only
async def unread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список каналов с непрочитанными сообщениями"""
    processing_msg = await update.message.reply_text("Загружаю список непрочитанных... ⏳")
    
    try:
        # Собираем данные о непрочитанных
        channels_list = await collect_unread_channels()
        
        # Проверяем, есть ли непрочитанные
        if not channels_list:
            await processing_msg.edit_text("✅ Нет непрочитанных сообщений в каналах!")
            return
        
        # Сортируем каналы
        sorted_channels = sort_unread_channels(channels_list)
        
        # Форматируем сообщения
        messages_to_send = format_unread_messages(sorted_channels)
        
        # Отправляем сообщения
        await processing_msg.edit_text(messages_to_send[0], parse_mode='Markdown')
        for msg in messages_to_send[1:]:
            await update.message.reply_text(msg, parse_mode='Markdown')
        
        logger.info(f"✅ Найдено непрочитанных: {len(sorted_channels)}")
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Ошибка при получении списка непрочитанных: {e}")
        if _is_connection_error(error_msg):
            await processing_msg.edit_text("❌ Потеряно соединение с Telegram. Попробуйте еще раз.")
        else:
            await processing_msg.edit_text(f"❌ Ошибка: {error_msg}")


async def _resolve_folder_chats(target_name: str, processing_msg) -> tuple[list, Optional[str], Optional[str]]:
    """
    Находит папку и возвращает список чатов в ней

    Returns:
        Tuple (chats_to_process, folder_title, error_message)
    """
    await processing_msg.edit_text(f"Ищу папку '{target_name}'... 📁")
    try:
        folder_id, folder_title, similarity, dialog_filter = await find_folder_by_name(target_name, fuzzy=True)
    except Exception as e:
        return [], None, str(e)

    if not folder_id or not dialog_filter:
        return [], None, f"Папка '{target_name}' не найдена. Попробуй указать название точнее."

    # Информируем о найденной папке
    if similarity < 1.0:
        await processing_msg.edit_text(f"✅ Найдена папка: '{folder_title}' (схожесть: {similarity:.0%})\n\nЗагружаю чаты... 📂")
    else:
        await processing_msg.edit_text(f"✅ Папка найдена: '{folder_title}'\n\nЗагружаю чаты... 📂")

    try:
        chats = await get_chats_in_folder(dialog_filter)
    except Exception as e:
        return [], folder_title, str(e)

    if not chats:
        return [], folder_title, f"В папке '{folder_title}' нет чатов."

    await processing_msg.edit_text(f"✅ Найдено {len(chats)} чатов в папке '{folder_title}'\n\nНачинаю обработку... 🔄")
    return chats, folder_title, None


async def _resolve_single_chat(target_name: str, processing_msg) -> tuple[list, Optional[str], Optional[str]]:
    """
    Находит один чат и возвращает его в списке

    Returns:
        Tuple (chats_to_process, found_name, error_message)
    """
    await processing_msg.edit_text(f"Ищу чат '{target_name}'... 🔍")
    try:
        chat_entity, found_name, similarity = await find_chat_by_name(target_name, fuzzy=True)
    except Exception as e:
        return [], None, str(e)

    if not chat_entity:
        return [], None, f"Чат '{target_name}' не найден. Попробуй указать название точнее."

    # Информируем о найденном чате
    if similarity < 1.0:
        await processing_msg.edit_text(f"✅ Найден чат: '{found_name}' (схожесть: {similarity:.0%})\n\nЗагружаю историю... 📥")
    else:
        await processing_msg.edit_text(f"✅ Чат найден: '{found_name}'\n\nЗагружаю историю... 📥")

    return [(chat_entity, found_name)], found_name, None


async def _process_single_chat(
    update: Update, processing_msg, chat_entity, chat_name: str,
    idx: int, total: int, period_type, period_value, query: str, mark_as_read: bool
) -> bool:
    """
    Обрабатывает один чат и отправляет результат

    Returns:
        True если успешно обработан, False если пропущен
    """
    # Обновляем статус
    if total > 1:
        await processing_msg.edit_text(f"Обрабатываю чат {idx}/{total}: '{chat_name}'... 📥")

    # Получаем историю чата
    chat_history, first_message_id = await get_chat_history(chat_entity, period_type, period_value)

    # Проверяем, что история не пустая
    if "не найдены" in chat_history or "нет непрочитанных" in chat_history:
        logger.info(f"⏭️ Пропускаем чат '{chat_name}': {chat_history}")
        return False

    # Формируем контекст периода для LLM
    period_text = format_period_text(period_type, period_value)
    
    # Отправляем историю в LLM
    if total > 1:
        await processing_msg.edit_text(f"Анализирую чат {idx}/{total}: '{chat_name}'... 💭")
    else:
        await processing_msg.edit_text(f"Анализирую переписку с помощью AI... 💭")

    result = await process_chat_with_openai(chat_history, query, period_text)

    # Генерируем ссылку на чат (ведет на первое суммаризированное сообщение)
    chat_link = generate_channel_link(chat_entity, message_id=first_message_id)

    # Формируем и отправляем результат
    use_markdown = total > 1 or chat_link is not None  # Используем Markdown, если есть ссылка
    if use_markdown:
        safe_chat_name = escape_markdown(chat_name)
        if chat_link:
            result_prefix = f"💬 **[{safe_chat_name}]({chat_link})** ({period_text}):\n\n"
        else:
            result_prefix = f"💬 **{safe_chat_name}** ({period_text}):\n\n"
    else:
        result_prefix = f"💬 Чат '{chat_name}' ({period_text}):\n\n"

    # Telegram имеет ограничение на длину сообщения
    max_length = 4096
    safe_result = escape_markdown(result) if use_markdown else result
    full_message = result_prefix + safe_result

    if len(full_message) <= max_length:
        await update.message.reply_text(full_message, parse_mode='Markdown' if use_markdown else None)
    else:
        await update.message.reply_text(result_prefix, parse_mode='Markdown' if use_markdown else None)
        for i in range(0, len(safe_result), max_length):
            await update.message.reply_text(safe_result[i:i+max_length])

    # Если нужно отметить прочитанным
    if mark_as_read:
        read_success = await mark_chat_as_read(chat_entity)
        if read_success:
            logger.info(f"✅ Чат '{chat_name}' отмечен как прочитанный")
        else:
            logger.warning(f"⚠️ Не удалось отметить чат '{chat_name}' как прочитанный")

    return True


@admin_only
async def process_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений от пользователя"""
    user_message = update.message.text
    processing_msg = await update.message.reply_text("Обрабатываю твою команду... ⏳")

    try:
        # Шаг 1: Парсим команду с помощью LLM
        await processing_msg.edit_text("Анализирую команду... 🤖")
        command = await parse_command_with_gpt(user_message)
        
        if command.get("error"):
            error_text = command.get('error')
            if "Превышен лимит запросов" in error_text:
                await processing_msg.edit_text(
                    f"⚠️ {error_text}\n\n"
                    "Это ограничение API. Подождите немного и попробуйте снова."
                )
            elif "Ошибка авторизации" in error_text:
                await processing_msg.edit_text(
                    f"❌ {error_text}\n\n"
                    "Проверьте настройки в файле .env"
                )
            else:
                await processing_msg.edit_text(f"❌ Ошибка: {error_text}")
            return
        
        # Шаг 2: Извлекаем параметры команды
        target_type = command.get("target_type")  # "chat" | "folder" | null
        target_name = command.get("target_name")  # название чата/папки
        period_type = command.get("period_type")
        period_value = command.get("period_value")
        mark_as_read = command.get("mark_as_read", False)  # Отмечать ли сообщения прочитанными
        
        # Если цель не указана, используем из контекста
        if not target_name:
            if current_context.get("target_name"):
                target_name = current_context["target_name"]
                target_type = current_context.get("target_type", "chat")
                logger.info(f"Используем из контекста: {target_type} '{target_name}'")
            else:
                await processing_msg.edit_text("❌ Не удалось определить чат или папку. Укажите название в запросе.")
                return
        
        # Если тип не указан, считаем что это чат (для обратной совместимости)
        if not target_type:
            target_type = "chat"
        
        # Если период не указан, используем из контекста
        if period_type is None:
            period_type = current_context.get("period_type")
            period_value = current_context.get("period_value")
            if period_type:
                logger.info(f"Используем период из контекста: {period_type}={period_value}")
        
        # Логируем, если нужно отмечать прочитанным
        if mark_as_read:
            logger.info("📖 Будут отмечены сообщения как прочитанные после обработки")
        
        # Сохраняем контекст для следующего раза
        current_context["target_type"] = target_type
        current_context["target_name"] = target_name
        current_context["period_type"] = period_type
        current_context["period_value"] = period_value
        
        # Шаг 3: Определяем список чатов для обработки
        if target_type == "folder":
            chats_to_process, resolved_name, error = await _resolve_folder_chats(target_name, processing_msg)
        else:
            chats_to_process, resolved_name, error = await _resolve_single_chat(target_name, processing_msg)

        if error:
            await processing_msg.edit_text(f"❌ {error}")
            return

        # Обновляем контекст
        if resolved_name:
            current_context["target_name"] = resolved_name

        # Шаг 4: Обрабатываем каждый чат
        query = command.get("query", user_message)
        processed_count = 0
        skipped_count = 0
        total = len(chats_to_process)

        for idx, (chat_entity, chat_name) in enumerate(chats_to_process, 1):
            try:
                success = await _process_single_chat(
                    update, processing_msg, chat_entity, chat_name,
                    idx, total, period_type, period_value, query, mark_as_read
                )
                if success:
                    processed_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке чата '{chat_name}': {e}")
                await update.message.reply_text(f"❌ Ошибка при обработке чата '{chat_name}': {str(e)[:200]}")
                skipped_count += 1
        
        # Удаляем сообщение о процессе
        await processing_msg.delete()
        
        # Итоговое сообщение для папок
        if len(chats_to_process) > 1:
            summary = f"✅ Обработано чатов: {processed_count}"
            if skipped_count > 0:
                summary += f" (пропущено: {skipped_count})"
            if mark_as_read and processed_count > 0:
                summary += "\n📖 Сообщения отмечены как прочитанные"
            await update.message.reply_text(summary)
        elif mark_as_read and processed_count > 0:
            # Для одного чата тоже покажем
            await update.message.reply_text("📖 Сообщения отмечены как прочитанные")
    
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        try:
            await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)[:200]}")
        except Exception:
            # Если не удалось отредактировать, отправляем новое сообщение
            try:
                await update.message.reply_text(f"❌ Произошла ошибка: {str(e)[:200]}")
            except Exception:
                logger.error("Не удалось отправить сообщение об ошибке пользователю")


async def main():
    """Основная функция запуска бота"""
    # Проверяем наличие необходимых конфигов
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        logger.error("Telethon API credentials не установлены!")
        return
    
    if not config.ELIZA_TOKEN:
        logger.error("ELIZA_TOKEN не установлен!")
        return
    
    # Инициализируем Telethon клиент
    await init_telethon_client()
    
    # Создаем приложение бота
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("folders", folders_command))
    application.add_handler(CommandHandler("unread", unread_command))
    application.add_handler(CommandHandler("context", context_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_message))
    
    # Инициализируем и запускаем бота
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logger.info("✅ Бот запущен и готов к работе! Нажмите Ctrl+C для остановки")
    
    try:
        # Держим бота запущенным
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # Корректное завершение
        logger.info("Остановка бота...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        
        # Отключаем Telethon
        if telethon_client and telethon_client.is_connected():
            await telethon_client.disconnect()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)

