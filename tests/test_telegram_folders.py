import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import (
    Channel, Chat, User, DialogFilter, DialogFilterChatlist, DialogFilterDefault,
    InputPeerUser, InputPeerChat, InputPeerChannel, InputPeerSelf, TextWithEntities,
)

from telegram_access import AccessError, TelegramAccess, SendJournal
from telegram_folders import (
    _compile_dialog_filter, _dialog_in_filter, _is_dialog_muted,
    get_dialog_filter_title, load_filter_notify_default,
)
from test_telegram_mcp import FakeClient, run


def folder(**kwargs):
    return DialogFilter(**dict(dict(id=7, title=TextWithEntities("YaChats", []),
                                   pinned_peers=[], include_peers=[], exclude_peers=[]), **kwargs))


def dialog(entity, **kwargs):
    return SimpleNamespace(**dict(dict(entity=entity, unread_count=0, folder_id=None,
                                      dialog=SimpleNamespace()), **kwargs))


class FolderClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.filters = [DialogFilterDefault(), folder(groups=True)]
        self.legacy = False
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, GetDialogFiltersRequest):
            return self.filters if self.legacy else SimpleNamespace(filters=self.filters)
        if isinstance(request, GetNotifySettingsRequest):
            return SimpleNamespace(mute_until=datetime.now(timezone.utc) + timedelta(days=1))
        return await super().__call__(request)

    async def iter_participants(self, entity):
        for person in [self.people[0], self.people[0], self.people[1]]:
            yield person


@pytest.fixture
def service(tmp_path):
    client = FolderClient()
    return TelegramAccess(lambda: client, SendJournal(tmp_path / "receipts.db"))


@pytest.mark.parametrize("legacy", [False, True])
def test_folder_discovery_titles_filtering_and_empty(service, legacy):
    client = service.client()
    client.legacy = legacy
    client.filters += [folder(id=8, title="Личное")]
    assert run(service.list_folders()) == {"folders": [
        {"id": 7, "title": "YaChats"}, {"id": 8, "title": "Личное"}]}
    assert run(service.list_folders(" yAcHa ")) == {"folders": [{"id": 7, "title": "YaChats"}]}
    assert run(service.list_folders("none")) == {"folders": []}
    assert get_dialog_filter_title(SimpleNamespace(title=None)) == ""
    client.filters = []
    assert run(service.list_folders()) == {"folders": []}


def test_shared_folder_pins_explicit_exclusions_and_typed_peer_ids(service):
    client = service.client()
    # Same bare ID for three different peer types must not collide.
    person = User(id=41, first_name="Person")
    channel = Channel(id=41, title="Channel", photo=None, date=None, broadcast=True)
    client.dialogs += [dialog(person), dialog(channel)]
    shared = DialogFilterChatlist(id=9, title=TextWithEntities("Shared", []),
                                 pinned_peers=[InputPeerUser(41, 1)],
                                 include_peers=[InputPeerChat(41)])
    client.filters = [shared]
    result = run(service.list_folder_chats(9))
    assert {item["id"] for item in result["chats"]} == {"41", "-41"}
    assert result["next_offset"] is None
    explicit = folder(include_peers=[InputPeerChat(41)], exclude_peers=[InputPeerChat(41)],
                      pinned_peers=[InputPeerChat(41)], groups=True)
    assert not _dialog_in_filter(dialog(client.group), explicit)


@pytest.mark.parametrize("field", ["include_peers", "pinned_peers"])
def test_explicit_peers_override_read_muted_and_archive(field):
    user = User(id=11, first_name="Test")
    selected = folder(**{field: [InputPeerUser(11, 1)]}, exclude_read=True,
                      exclude_muted=True, exclude_archived=True)
    item = dialog(user, unread_count=0, folder_id=1,
                  notify_settings=SimpleNamespace(mute_until=2**31 - 1))
    assert _dialog_in_filter(item, selected)


@pytest.mark.parametrize("entity,category", [
    (User(id=1, bot=True, contact=True), "bots"),
    (User(id=2, contact=True), "contacts"),
    (User(id=3), "non_contacts"),
    (Chat(id=4, title="Basic", photo=None, participants_count=1, date=None, version=1), "groups"),
    (Channel(id=5, title="Super", photo=None, date=None, megagroup=True), "groups"),
    (Channel(id=6, title="News", photo=None, date=None, broadcast=True), "broadcasts"),
])
def test_dynamic_categories_are_disjoint(entity, category):
    for flag in ("bots", "contacts", "non_contacts", "groups", "broadcasts"):
        assert _dialog_in_filter(dialog(entity), folder(**{flag: True})) == (flag == category)
    assert not _dialog_in_filter(dialog(object()), folder(groups=True))


def test_saved_messages_input_peer_self():
    selected = folder(include_peers=[InputPeerSelf()])
    assert _dialog_in_filter(dialog(User(id=11, is_self=True)), selected)
    assert not _dialog_in_filter(dialog(User(id=12)), selected)


@pytest.mark.parametrize("raw,unread_count,expected", [
    ({}, 0, False), ({}, 1, True), ({"unread_mark": True}, 0, True),
    ({"unread_mentions_count": 1}, 0, True),
])
def test_exclude_read_respects_marks_and_mentions(raw, unread_count, expected):
    item = dialog(User(id=11), unread_count=unread_count, dialog=SimpleNamespace(**raw))
    assert _dialog_in_filter(item, folder(non_contacts=True, exclude_read=True)) == expected


@pytest.mark.parametrize("archived,mentions,expected", [(False, 0, False), (False, 1, True), (True, 1, False)])
def test_muted_mentions_exception_applies_only_outside_archive(archived, mentions, expected):
    item = dialog(User(id=11), folder_id=1 if archived else None,
                  unread_mentions_count=mentions,
                  dialog=SimpleNamespace(notify_settings=SimpleNamespace(mute_until=2**31 - 1)))
    assert _dialog_in_filter(item, folder(non_contacts=True, exclude_muted=True)) == expected
    if archived:
        assert not _dialog_in_filter(item, folder(non_contacts=True, exclude_archived=True))


@pytest.mark.parametrize("value,expected", [
    (None, False), (0, False), (2**31 - 1, True), ("unknown", False),
    (datetime.now(timezone.utc) + timedelta(days=1), True),
    (datetime.now(timezone.utc) - timedelta(days=1), False),
    (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1), True),
])
def test_mute_timestamp_variants(value, expected):
    assert _is_dialog_muted(dialog(User(id=1), notify_settings=SimpleNamespace(mute_until=value))) == expected


def test_folder_pagination_filters_before_offset_and_deduplicates(service):
    client = service.client()
    client.filters = [folder(non_contacts=True, groups=True)]
    client.dialogs.insert(1, client.dialogs[0])
    first = run(service.list_folder_chats(7, limit=2))
    assert [item["id"] for item in first["chats"]] == ["11", "12"]
    assert first["folder"] == {"id": 7, "title": "YaChats"}
    assert first["next_offset"] == 2
    second = run(service.list_folder_chats(7, limit=2, offset=first["next_offset"]))
    assert [item["id"] for item in second["chats"]] == ["-41"]
    assert second["next_offset"] is None
    assert run(service.list_folder_chats(7, offset=10))["chats"] == []
    client.send_message.assert_not_called()
    assert not hasattr(client, "send_read_acknowledge")
    assert all(isinstance(request, GetDialogFiltersRequest) for request in client.requests)


def test_inherited_mute_is_loaded_once_per_category_and_explicit_unmute_wins(service):
    client = service.client()
    client.filters = [folder(non_contacts=True, groups=True, broadcasts=True, exclude_muted=True)]
    # First user inherits muted; second explicitly unmutes despite global setting.
    client.dialogs[1].dialog = SimpleNamespace(notify_settings=SimpleNamespace(mute_until=0))
    result = run(service.list_folder_chats(7))
    assert [item["id"] for item in result["chats"]] == ["12"]
    assert len([r for r in client.requests if isinstance(r, GetNotifySettingsRequest)]) == 3
    client.requests.clear()
    client.filters = [folder(include_peers=[InputPeerUser(11, 1)], exclude_muted=True)]
    assert run(service.list_folder_chats(7))["chats"][0]["id"] == "11"
    assert not any(isinstance(r, GetNotifySettingsRequest) for r in client.requests)


def test_shared_mute_default_cache_for_multiple_matching_dialogs(service):
    async def scenario():
        selected = folder(non_contacts=True, exclude_muted=True)
        compiled = _compile_dialog_filter(selected)
        for item in service.client().dialogs[:2]:
            await load_filter_notify_default(service.client(), item, selected, compiled)
            assert not _dialog_in_filter(item, selected, compiled)
        assert len(service.client().requests) == 1
    run(scenario())


@pytest.mark.parametrize("arguments", [
    {"folder_id": True}, {"folder_id": 0}, {"folder_id": "7"}, {"folder_id": -1},
    {"folder_id": 7, "limit": 0}, {"folder_id": 7, "limit": 101},
    {"folder_id": 7, "offset": -1}, {"folder_id": 7, "offset": True},
])
def test_folder_validation(service, arguments):
    with pytest.raises(AccessError):
        run(service.list_folder_chats(**arguments))


def test_missing_folder_and_disconnected_do_not_return_empty_success(service):
    with pytest.raises(AccessError, match="Folder not found"):
        run(service.list_folder_chats(99))
    with pytest.raises(AccessError):
        run(service.list_folders(query=7))
    service.client().connected = False
    with pytest.raises(AccessError, match="disconnected"):
        run(service.list_folders())


def test_folder_rpc_failure_propagates(service, monkeypatch):
    async def failure(self, request):
        raise asyncio.TimeoutError
    monkeypatch.setattr(FolderClient, "__call__", failure)
    with pytest.raises(asyncio.TimeoutError):
        run(service.list_folders())


def test_members_paginated_private_and_read_only(service):
    first = run(service.list_chat_members("-41", limit=1))
    assert first["next_offset"] == 1 and first["members"][0]["id"] == "11"
    second = run(service.list_chat_members("-41", limit=1, offset=1))
    assert second["next_offset"] is None and second["members"][0]["id"] == "12"
    assert "phone" not in first["members"][0] and "access_hash" not in first["members"][0]
    assert run(service.list_chat_members("@test_news", offset=10))["members"] == []
    with pytest.raises(AccessError, match="group or channel"):
        run(service.list_chat_members("11"))
    for args in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": True}):
        with pytest.raises(AccessError):
            run(service.list_chat_members("-41", **args))
    service.client().send_message.assert_not_called()


def test_new_tools_facade_dispatches_read_operations(service):
    from mcp.shared.memory import create_connected_server_and_client_session
    from telegram_mcp import create_server

    async def scenario():
        server = create_server(transport=lambda operation, arguments: service.dispatch(operation, arguments))
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            for name, arguments, key in [
                ("list_folders", {}, "folders"),
                ("list_folder_chats", {"folder_id": 7}, "chats"),
                ("list_chat_members", {"peer": "-41"}, "members"),
            ]:
                result = await session.call_tool(name, arguments)
                assert not result.isError and result.structuredContent[key]
            unknown = await session.call_tool("list_folder_chats", {"folder_id": 99})
            assert unknown.isError
    run(scenario())
