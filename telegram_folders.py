"""Folder membership shared by the bot and personal Telegram MCP.

Uses an existing client only. No session, configuration, bot imports or logging.
Precedence follows Telegram Desktop's Data::ChatFilter::contains:
https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/data/data_chat_filters.cpp
"""

from datetime import datetime, timezone

from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.types import (
    Channel, Chat, User, InputPeerSelf,
    InputNotifyUsers, InputNotifyChats, InputNotifyBroadcasts,
)


def get_dialog_filter_title(dialog_filter):
    title = getattr(dialog_filter, "title", "")
    return title if isinstance(title, str) else str(getattr(title, "text", "") or "")


def dialog_filters(result):
    """Normalize modern and legacy Telethon results; omit the default All tab."""
    return [item for item in getattr(result, "filters", result)
            if type(getattr(item, "id", None)) is int and hasattr(item, "title")]


def _peer_key_from_peer(peer):
    if isinstance(peer, InputPeerSelf):
        return ("self", 0)
    for kind in ("user", "chat", "channel"):
        value = getattr(peer, kind + "_id", None)
        if value is not None:
            return (kind, value)
    return None


def _peer_key_from_entity(entity):
    for kind, cls in (("user", User), ("chat", Chat), ("channel", Channel)):
        if isinstance(entity, cls):
            return (kind, entity.id)
    return None


def _category(entity):
    if isinstance(entity, User):
        return "bots" if entity.bot else "contacts" if entity.contact else "non_contacts"
    if isinstance(entity, Chat):
        return "groups"
    if isinstance(entity, Channel):
        return "broadcasts" if getattr(entity, "broadcast", False) else "groups"
    return None


def _dialog_value(dialog, name, default=None):
    value = getattr(dialog, name, None)
    return getattr(getattr(dialog, "dialog", None), name, default) if value is None else value


def _is_dialog_muted(dialog, default_settings=None):
    settings = _dialog_value(dialog, "notify_settings")
    mute_until = getattr(settings, "mute_until", None)
    if mute_until is None:
        mute_until = getattr(default_settings, "mute_until", None)
    now = datetime.now(timezone.utc)
    if isinstance(mute_until, datetime):
        return mute_until.replace(tzinfo=timezone.utc) > now if mute_until.tzinfo is None else mute_until > now
    return isinstance(mute_until, int) and mute_until > now.timestamp()


def _compile_dialog_filter(dialog_filter):
    compiled = {
        name + "_keys": {key for peer in (getattr(dialog_filter, name + "_peers", None) or [])
                         if (key := _peer_key_from_peer(peer)) is not None}
        for name in ("include", "exclude", "pinned")
    }
    compiled["notify_defaults"] = {}
    return compiled


def _matches_keys(entity, keys):
    return _peer_key_from_entity(entity) in keys or (
        isinstance(entity, User) and getattr(entity, "is_self", False) and ("self", 0) in keys
    )


def _explicit_include(dialog, compiled):
    return _matches_keys(dialog.entity, compiled["include_keys"] | compiled["pinned_keys"])


def _dialog_in_filter(dialog, dialog_filter, compiled_filter=None, *, ignore_muted=False):
    compiled = compiled_filter if compiled_filter is not None else _compile_dialog_filter(dialog_filter)
    category = _category(dialog.entity)
    if category is None or _matches_keys(dialog.entity, compiled["exclude_keys"]):
        return False
    # Explicit inclusion/pinning overrides dynamic read/mute/archive conditions.
    if _explicit_include(dialog, compiled):
        return True
    if not getattr(dialog_filter, category, False):
        return False
    archived = _dialog_value(dialog, "folder_id") == 1
    mentions = (_dialog_value(dialog, "unread_mentions_count", 0) or 0) > 0
    unread = (_dialog_value(dialog, "unread_count", 0) or 0) > 0 or bool(_dialog_value(dialog, "unread_mark", False))
    if getattr(dialog_filter, "exclude_read", False) and not (unread or mentions):
        return False
    if getattr(dialog_filter, "exclude_archived", False) and archived:
        return False
    defaults = compiled.get("notify_defaults", {})
    if not ignore_muted and getattr(dialog_filter, "exclude_muted", False):
        if _is_dialog_muted(dialog, defaults.get(_notify_category(dialog.entity))) and not (mentions and not archived):
            return False
    return True


def _notify_category(entity):
    return "users" if isinstance(entity, User) else "broadcasts" if _category(entity) == "broadcasts" else "chats"


async def load_filter_notify_default(client, dialog, dialog_filter, compiled):
    """Lazily load at most three inherited mute defaults per folder traversal."""
    if not getattr(dialog_filter, "exclude_muted", False) or _explicit_include(dialog, compiled):
        return
    if not _dialog_in_filter(dialog, dialog_filter, compiled, ignore_muted=True):
        return
    if getattr(_dialog_value(dialog, "notify_settings"), "mute_until", None) is not None:
        return
    category = _notify_category(dialog.entity)
    defaults = compiled["notify_defaults"]
    if category not in defaults:
        notify_type = {"users": InputNotifyUsers, "chats": InputNotifyChats,
                       "broadcasts": InputNotifyBroadcasts}[category]
        defaults[category] = await client(GetNotifySettingsRequest(notify_type()))
