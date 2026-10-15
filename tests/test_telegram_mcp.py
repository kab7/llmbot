import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from telethon import errors
from telethon.tl.types import Channel, Chat, User

import bot
import telegram_mcp
from telegram_access import (
    AccessError, OPERATIONS, SendJournal, TelegramAccess, TelegramBridge, bridge_call,
    entity_info, message_link,
)


def run(coroutine):
    return asyncio.run(coroutine)


class FakeClient:
    def __init__(self):
        self.people = [User(id=11, first_name="Иван", last_name="Тест", username="ivan_test"),
                       User(id=12, first_name="Иван", last_name="Другой", username="ivan_other")]
        self.channel = Channel(id=31, title="Новости", photo=None, date=None,
                               broadcast=True, username="test_news")
        self.group = Chat(id=41, title="Group", photo=None, participants_count=1,
                          date=None, version=1)
        self.dialogs = [SimpleNamespace(entity=item, unread_count=3) for item in
                        [*self.people, self.channel, self.group]]
        self.send_message = AsyncMock(return_value=SimpleNamespace(id=101))
        self.get_me = AsyncMock(return_value=self.people[0])
        self.connected = True
        self.history_kwargs = None
        self.messages = [SimpleNamespace(
            id=index, date=datetime(2026, 10, 15, 10, index, tzinfo=timezone.utc),
            sender_id=11, out=False, raw_text=f"text {index}", media=None, reply_to_msg_id=None,
        ) for index in [5, 4, 3, 2, 1]]

    def is_connected(self):
        return self.connected

    async def __call__(self, request):
        return SimpleNamespace(users=self.people)

    async def get_entity(self, target):
        for item in self.dialogs:
            entity = item.entity
            if str(target) in (entity_info(entity)["id"], "@" + (getattr(entity, "username", None) or "")):
                return entity
        raise ValueError("not found")

    async def iter_dialogs(self):
        for item in self.dialogs:
            yield item

    async def iter_messages(self, entity, **kwargs):
        self.history_kwargs = kwargs
        items = [message for message in self.messages if
                 (not kwargs["offset_id"] or message.id < kwargs["offset_id"]) and
                 (not kwargs["offset_date"] or message.date < kwargs["offset_date"])]
        for message in items[:kwargs["limit"]]:
            yield message


@pytest.fixture
def service(tmp_path):
    client = FakeClient()
    return TelegramAccess(lambda: client, SendJournal(tmp_path / "mcp.db"))


def test_search_exact_resolution_and_no_phone_leak(service):
    async def scenario():
        result = await service.search_contacts("ИВАН", 1)
        assert result["has_more"] and len(result["contacts"]) == 1
        assert "phone" not in result["contacts"][0]
        assert len((await service.search_contacts("@ivan_other"))["contacts"]) == 1
        assert not (await service.search_contacts("missing"))["contacts"]
        page = await service.search_chats("Иван", 1)
        assert page["next_offset"] == 1
        next_page = await service.search_chats("Иван", 1, page["next_offset"])
        assert next_page["next_offset"] is None
        assert next_page["chats"][0]["id"] == "12"
        assert (await service.resolve_chat("@test_news"))["id"] == "-1000000000031"
        assert (await service.resolve_chat("me"))["id"] == "11"
        assert (await service.resolve_chat("-41"))["type"] == "group"
        for peer in ("Иван", "t.me/ivan_test", "+79990001122"):
            with pytest.raises(AccessError, match="Use a search result"):
                await service.resolve_chat(peer)
        with pytest.raises(AccessError, match="not found"):
            await service.resolve_chat("999")
    run(scenario())


def test_history_is_chronological_paginated_and_read_only(service):
    async def scenario():
        result = await service.read_history("@test_news", limit=2)
        assert [m["id"] for m in result["messages"]] == [4, 5]
        assert result["next_offset_id"] == 4
        assert result["messages"][0]["link"] == "https://t.me/test_news/4"
        result = await service.read_history("11", limit=3, offset_id=4, query="test",
                                            after="2026-10-15T10:01:00Z", before="2026-10-15T10:05:00+00:00")
        assert [m["id"] for m in result["messages"]] == [2, 3]
        assert result["next_offset_id"] is None
        assert result["messages"][0]["link"] is None
        assert service.client().history_kwargs["search"] == "test"
        # Fake client intentionally has no read acknowledgement method.
        assert not hasattr(service.client(), "send_read_acknowledge")
        service.client().send_message.assert_not_called()
    run(scenario())


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset_id": -1},
    {"after": "2026-10-15"}, {"before": "garbage"},
    {"after": "2026-10-15T10:00:00Z", "before": "2026-10-14T10:00:00Z"},
])
def test_history_validation(service, arguments):
    with pytest.raises(AccessError):
        run(service.read_history("11", **arguments))


def test_entity_links_do_not_invent_private_user_links(service):
    client = service.client()
    assert message_link(client.people[0], 99) is None
    assert message_link(client.group, 99) is None
    client.channel.username = None
    assert message_link(client.channel, 99) == "https://t.me/c/31/99"
    assert entity_info(client.channel)["type"] == "channel"
    client.channel.broadcast = False
    assert entity_info(client.channel)["type"] == "group"


def test_send_is_literal_persistent_and_idempotent(service):
    async def scenario():
        key = str(uuid4())
        result, duplicate = await asyncio.gather(
            service.send_message("11", "**literal**", key, reply_to=5),
            service.send_message("@ivan_test", "**literal**", key, reply_to=5),
        )
        assert result == duplicate and result["status"] == "sent"
        assert result["message_id"] == 101
        service.client().send_message.assert_awaited_once_with(
            service.client().people[0], "**literal**", reply_to=5, parse_mode=None, link_preview=False,
        )
        restarted = TelegramAccess(service.client_getter, SendJournal(service.journal.path))
        assert await restarted.get_send_status(key) == result
        assert await restarted.send_message("11", "**literal**", key, reply_to=5) == result
        assert (service.journal.path.stat().st_mode & 0o777) == 0o600
        with pytest.raises(AccessError, match="different arguments"):
            await service.send_message("11", "different", key, reply_to=5)
        with service.journal.connect() as connection:
            assert "**literal**" not in str(connection.execute("SELECT * FROM sends").fetchall())
    run(scenario())


@pytest.mark.parametrize("arguments", [
    {"text": ""}, {"text": " "}, {"text": "x" * 4097}, {"text": "😀" * 2049},
    {"request_id": "invalid"}, {"reply_to": 0}, {"reply_to": True},
])
def test_send_validation_never_sends(service, arguments):
    parameters = {"peer": "11", "text": "test", "request_id": str(uuid4())} | arguments
    with pytest.raises(AccessError):
        run(service.send_message(**parameters))
    service.client().send_message.assert_not_called()


@pytest.mark.parametrize("error,expected", [
    (ConnectionError("private payload"), "unknown"),
    (errors.ChatWriteForbiddenError(None), "failed"),
    (errors.FloodWaitError(None, capture=33), "failed"),
])
def test_send_failures_are_not_replayed(service, error, expected):
    async def scenario():
        key = str(uuid4())
        service.client().send_message.side_effect = error
        result = await service.send_message("11", "test", key)
        assert result["status"] == expected
        assert "private payload" not in str(result)
        assert await service.send_message("11", "test", key) == result
        service.client().send_message.assert_awaited_once()
        if isinstance(error, errors.FloodWaitError):
            assert result["retry_after_seconds"] == 33
        assert (await service.get_send_status(str(uuid4())))["status"] == "not_found"
    run(scenario())


def test_pending_crash_receipt_does_not_resend(service):
    async def scenario():
        key = str(uuid4())
        service.client().send_message.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await service.send_message("me", "test", key)
        assert (await service.get_send_status(key))["status"] == "unknown"
        assert (await service.send_message("me", "test", key))["status"] == "unknown"
        service.client().send_message.assert_awaited_once()
    run(scenario())


@pytest.mark.parametrize("operation,arguments", [
    ("delete_messages", {}), ([], {}), ("send_message", {}),
    ("resolve_chat", {"peer": "me", "extra": 1}), ("resolve_chat", []),
])
def test_bridge_allowlist(service, operation, arguments):
    with pytest.raises(AccessError):
        run(service.dispatch(operation, arguments))


def test_disconnected_client_fails_without_new_session(service):
    service.client().connected = False
    with pytest.raises(AccessError, match="disconnected"):
        run(service.resolve_chat("me"))


def test_unix_bridge_mcp_protocol_and_schema(service):
    async def scenario(directory):
        socket = Path(directory) / "control.sock"
        bridge = TelegramBridge(service, socket)
        await bridge.start()
        try:
            assert socket.stat().st_mode & 0o777 == 0o600
            assert (await bridge_call(socket, "resolve_chat", {"peer": "me"}))["id"] == "11"
            with pytest.raises(AccessError, match="Unknown"):
                await bridge_call(socket, "delete_messages", {})
            server = telegram_mcp.create_server(str(socket))
            async with create_connected_server_and_client_session(server._mcp_server) as session:
                tools = (await session.list_tools()).tools
                assert {tool.name for tool in tools} == set(OPERATIONS)
                assert all(tool.annotations.readOnlyHint for tool in tools if tool.name != "send_message")
                send = next(tool for tool in tools if tool.name == "send_message")
                assert send.annotations.readOnlyHint is False
                assert send.annotations.idempotentHint is True
                assert send.inputSchema["required"] == ["peer", "text", "request_id"]
                result = await session.call_tool("resolve_chat", {"peer": "me"})
                assert not result.isError and result.structuredContent["id"] == "11"
                result = await session.call_tool("resolve_chat", {"peer": "ambiguous display name"})
                assert result.isError
                result = await session.call_tool("read_history", {"peer": "me", "limit": 101})
                assert result.isError
        finally:
            await bridge.close()
        assert not socket.exists()
    with tempfile.TemporaryDirectory(prefix="llmbot-mcp-", dir="/tmp") as directory:
        run(scenario(directory))


def test_unix_bridge_path_safety(service):
    async def scenario(directory):
        path = Path(directory) / "socket"
        path.write_text("preserve")
        with pytest.raises(RuntimeError, match="non-socket"):
            await TelegramBridge(service, path).start()
        assert path.read_text() == "preserve"
        path.unlink()
        path.symlink_to(Path(directory) / "missing")
        with pytest.raises(RuntimeError, match="symlink"):
            await TelegramBridge(service, path).start()
        path.unlink()
        bridge = TelegramBridge(service, path)
        await bridge.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await TelegramBridge(service, path).start()
        finally:
            await bridge.close()
        Path(directory).chmod(0o755)
        with pytest.raises(RuntimeError, match="0700"):
            await TelegramBridge(service, path).start()
    with tempfile.TemporaryDirectory(prefix="llmbot-mcp-", dir="/tmp") as directory:
        run(scenario(directory))


def test_stdio_entrypoint_and_unavailable_bridge():
    async def scenario():
        import sys
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[
            "scripts/telegram_mcp.py", "--socket", "/tmp/llmbot-mcp-nonexistent/socket",
        ])) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert "PERSONAL" in initialized.instructions
                assert len((await session.list_tools()).tools) == 6
                result = await session.call_tool("resolve_chat", {"peer": "me"})
                assert result.isError and "unavailable" in result.content[0].text
    run(scenario())


def test_bot_bridge_disabled_and_failure_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(bot.config, "TELEGRAM_MCP_SOCKET", "")
    assert run(bot.start_telegram_bridge()) is None
    monkeypatch.setattr(bot.config, "TELEGRAM_MCP_SOCKET", "/tmp/unused")
    monkeypatch.setattr(bot.config, "TELEGRAM_MCP_STATE_FILE", str(tmp_path / "state.db"))
    monkeypatch.setattr(bot.TelegramBridge, "start", AsyncMock(side_effect=RuntimeError("failure")))
    assert run(bot.start_telegram_bridge()) is None


def test_documented_mcp_schema_matches_generated_tools():
    generated = run(telegram_mcp.create_server("/tmp/unused").list_tools())
    schema = json.loads(Path("docs/schemas/telegram-mcp-tools.json").read_text())
    assert schema["tools"] == [tool.model_dump(mode="json", exclude_none=True) for tool in generated]
