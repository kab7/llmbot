"""Telegram operations on the bot's existing client, behind an owner-only socket.

Never imports bot/config, opens a Telethon session, or logs message/contact data.
The stdio MCP process has access only to this small RPC allowlist.
"""

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from telethon import errors, utils
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import Channel, Chat, User

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RPC_TIMEOUT = 60
OPERATIONS = (
    "search_contacts", "search_chats", "resolve_chat", "read_history",
    "send_message", "get_send_status",
)


class AccessError(Exception):
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    def as_dict(self):
        result = {"code": self.code, "message": str(self)}
        if self.retry_after is not None:
            result["retry_after_seconds"] = self.retry_after
        return result


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AccessError("invalid_arguments", f"{name}: expected {minimum}..{maximum}")
    return value


def _text(value, name, maximum=256, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise AccessError("invalid_arguments", f"Invalid {name}")
    if not allow_empty and not value.strip():
        raise AccessError("invalid_arguments", f"Empty {name}")
    return value


def _request_id(value):
    try:
        return str(UUID(_text(value, "request_id", 36)))
    except (ValueError, AttributeError):
        raise AccessError("invalid_arguments", "request_id must be a UUID") from None


def _date(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(_text(value, "date", 64).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise AccessError("invalid_arguments", "Dates require ISO 8601 with timezone") from None


def entity_info(entity):
    if not isinstance(entity, (User, Chat, Channel)):
        raise AccessError("not_found", "Unsupported or unavailable Telegram peer")
    return {
        "id": str(utils.get_peer_id(entity)),
        "name": utils.get_display_name(entity),
        "username": getattr(entity, "username", None),
        "type": "user" if isinstance(entity, User) else (
            "channel" if isinstance(entity, Channel) and entity.broadcast else "group"
        ),
        "is_bot": bool(getattr(entity, "bot", False)),
    }


def message_link(entity, message_id):
    username = getattr(entity, "username", None)
    if username and isinstance(entity, Channel):
        return f"https://t.me/{username}/{message_id}"
    if isinstance(entity, Channel):
        return f"https://t.me/c/{entity.id}/{message_id}"
    return None


class SendJournal:
    """Persist intent BEFORE contacting Telegram; never automatically replay it.

    A crash can leave a pending/unknown receipt. It must be reconciled with history,
    not resent with a new key. No plaintext outgoing message is stored here.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS sends (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL)""")

    def connect(self):
        return sqlite3.connect(self.path)

    def get(self, request_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT fingerprint, result FROM sends WHERE request_id=?", (request_id,)
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def reserve(self, request_id, fingerprint, result):
        with self.connect() as connection:
            connection.execute("INSERT INTO sends VALUES (?, ?, ?)", (
                request_id, fingerprint, json.dumps(result),
            ))

    def finish(self, request_id, result):
        with self.connect() as connection:
            connection.execute("UPDATE sends SET result=? WHERE request_id=?", (
                json.dumps(result), request_id,
            ))


class TelegramAccess:
    def __init__(self, client_getter, journal):
        self.client_getter = client_getter
        self.journal = journal
        self.send_lock = asyncio.Lock()

    def client(self):
        client = self.client_getter()
        if client is None or not client.is_connected():
            raise AccessError("unavailable", "Telegram client is disconnected; retry later")
        return client

    async def entity(self, peer):
        peer = _text(peer, "peer", 128).strip()
        if peer == "me":
            return await self.client().get_me()
        if re.fullmatch(r"-?[1-9]\d*", peer):
            target = int(peer)
        elif re.fullmatch(r"@[A-Za-z0-9_]{5,32}", peer):
            target = peer
        else:
            raise AccessError("invalid_peer", "Use a search result's numeric id, @username, or me; never a display name")
        try:
            return await self.client().get_entity(target)
        except ValueError:
            raise AccessError("not_found", "Peer not found; use search_contacts/search_chats first") from None

    async def search_contacts(self, query, limit=20):
        query = _text(query, "query", allow_empty=True).casefold().strip().lstrip("@")
        _integer(limit, "limit", 1, 100)
        contacts = await self.client()(GetContactsRequest(hash=0))
        matches = [entity_info(user) for user in contacts.users if not user.deleted]
        matches = [item for item in matches if not query or
                   query in item["name"].casefold() or query in (item["username"] or "").casefold()]
        return {"contacts": matches[:limit], "has_more": len(matches) > limit}

    async def search_chats(self, query="", limit=20, offset=0):
        query = _text(query, "query", allow_empty=True).casefold().strip().lstrip("@")
        _integer(limit, "limit", 1, 100)
        _integer(offset, "offset", 0, 10000)
        matches = []
        skipped = 0
        async for dialog in self.client().iter_dialogs():
            if not isinstance(dialog.entity, (User, Chat, Channel)):
                continue
            item = entity_info(dialog.entity)
            if query and query not in item["name"].casefold() and query not in (item["username"] or "").casefold():
                continue
            if skipped < offset:
                skipped += 1
                continue
            item["unread_count"] = dialog.unread_count
            matches.append(item)
            if len(matches) > limit:
                break
        return {"chats": matches[:limit], "next_offset": offset + limit if len(matches) > limit else None}

    async def resolve_chat(self, peer):
        return entity_info(await self.entity(peer))

    async def read_history(self, peer, limit=30, offset_id=0, query="", after=None, before=None):
        _integer(limit, "limit", 1, 100)
        _integer(offset_id, "offset_id", 0, 2**31 - 1)
        _text(query, "query", allow_empty=True)
        after, before = _date(after), _date(before)
        if after and before and after >= before:
            raise AccessError("invalid_arguments", "after must be earlier than before")
        entity = await self.entity(peer)
        messages = []
        async for message in self.client().iter_messages(
            entity, limit=limit + 1, offset_id=offset_id, offset_date=before, search=query or None,
        ):
            if after and message.date <= after:
                break
            messages.append({
                "id": message.id, "date": message.date.isoformat(),
                "sender_id": str(message.sender_id) if message.sender_id is not None else None,
                "outgoing": bool(message.out), "text": (message.raw_text or "")[:16000],
                "media_type": type(message.media).__name__ if message.media else None,
                "reply_to_message_id": message.reply_to_msg_id,
                "link": message_link(entity, message.id),
            })
        has_more = len(messages) > limit
        page = messages[:limit]
        return {"chat": entity_info(entity), "messages": list(reversed(page)),
                "next_offset_id": page[-1]["id"] if has_more else None}

    async def get_send_status(self, request_id):
        request_id = _request_id(request_id)
        receipt = self.journal.get(request_id)
        return receipt[1] if receipt else {"request_id": request_id, "status": "not_found"}

    async def send_message(self, peer, text, request_id, reply_to=None):
        request_id = _request_id(request_id)
        _text(text, "text", 4096)
        if len(text.encode("utf-16-le")) // 2 > 4096:
            raise AccessError("invalid_arguments", "text exceeds 4096 UTF-16 units; split explicitly")
        if reply_to is not None:
            _integer(reply_to, "reply_to", 1, 2**31 - 1)
        entity = await self.entity(peer)
        info = entity_info(entity)
        fingerprint = hashlib.sha256(json.dumps(
            [info["id"], text, reply_to], ensure_ascii=False,
        ).encode()).hexdigest()
        async with self.send_lock:
            previous = self.journal.get(request_id)
            if previous:
                if previous[0] != fingerprint:
                    raise AccessError("idempotency_conflict", "request_id was already used with different arguments")
                return previous[1]
            receipt = {"request_id": request_id, "status": "pending", "chat_id": info["id"],
                       "created_at": datetime.now(timezone.utc).isoformat()}
            self.journal.reserve(request_id, fingerprint, receipt)
            try:
                message = await self.client().send_message(
                    entity, text, reply_to=reply_to, parse_mode=None, link_preview=False,
                )
            except BaseException as error:
                # RPC rejections are definite failures; network/cancellation/crash is ambiguous.
                receipt["status"] = "failed" if isinstance(error, errors.RPCError) else "unknown"
                receipt["error_type"] = type(error).__name__
                if isinstance(error, errors.FloodWaitError):
                    receipt["retry_after_seconds"] = error.seconds
                self.journal.finish(request_id, receipt)
                if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                return receipt
            receipt.update(status="sent", message_id=message.id, link=message_link(entity, message.id))
            self.journal.finish(request_id, receipt)
            logger.info("Telegram MCP send completed request_id=%s", request_id)
            return receipt

    async def dispatch(self, operation, arguments):
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise AccessError("unknown_operation", "Unknown Telegram operation")
        if not isinstance(arguments, dict):
            raise AccessError("invalid_arguments", "arguments must be an object")
        function = getattr(self, operation)
        try:
            inspect.signature(function).bind(**arguments)
        except TypeError:
            raise AccessError("invalid_arguments", "Invalid operation arguments") from None
        return await function(**arguments)


class TelegramBridge:
    """One JSON request per Unix connection, bounded and never publicly exposed."""

    def __init__(self, service, socket_path):
        self.service = service
        self.path = Path(socket_path)
        self.server = None
        self.tasks = set()
        self.slots = asyncio.Semaphore(8)

    async def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = self.path.parent.lstat()
        if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
            raise RuntimeError("MCP socket needs a dedicated owner-controlled directory")
        if parent_stat.st_mode & 0o077:
            raise RuntimeError("MCP socket directory must have mode 0700")
        if self.path.is_symlink():
            raise RuntimeError("Refusing symlink at MCP socket path")
        if self.path.exists():
            if not stat.S_ISSOCK(self.path.stat().st_mode):
                raise RuntimeError("Refusing to replace a non-socket MCP path")
            # A live bridge must never be stolen by a second bot instance.
            try:
                _, writer = await asyncio.open_unix_connection(self.path)
            except (ConnectionRefusedError, FileNotFoundError):
                self.path.unlink(missing_ok=True)
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("Telegram bridge already running")
        self.server = await asyncio.start_unix_server(self.handle, path=self.path, limit=MAX_REQUEST_BYTES)
        self.path.chmod(0o600)

    async def close(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            for task in list(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*list(self.tasks), return_exceptions=True)
            self.path.unlink(missing_ok=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(RPC_TIMEOUT), self.slots:
                line = await reader.readline()
                if not line:
                    return
                payload = json.loads(line)
                if not isinstance(payload, dict) or set(payload) != {"operation", "arguments"}:
                    raise AccessError("invalid_arguments", "Invalid bridge request")
                result = await self.service.dispatch(payload["operation"], payload["arguments"])
                response = {"result": result}
        except AccessError as error:
            response = {"error": error.as_dict()}
        except errors.FloodWaitError as error:
            response = {"error": AccessError("rate_limited", "Telegram rate limit; wait before retrying", error.seconds).as_dict()}
        except (ValueError, UnicodeError):
            response = {"error": AccessError("invalid_arguments", "Malformed or oversized request").as_dict()}
        except Exception as error:
            logger.warning("Telegram MCP operation failed: %s", type(error).__name__)
            response = {"error": AccessError("telegram_error", "Telegram operation failed; no automatic send retry. Check get_send_status for writes.").as_dict()}
        finally:
            self.tasks.discard(task)
            if "response" in locals():
                encoded = json.dumps(response, ensure_ascii=False).encode() + b"\n"
                if len(encoded) > MAX_RESPONSE_BYTES:
                    encoded = b'{"error":{"code":"response_too_large","message":"Request a smaller page"}}\n'
                writer.write(encoded)
                try:
                    await writer.drain()
                except (ConnectionError, BrokenPipeError):
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass


async def bridge_call(socket_path, operation, arguments):
    writer = None
    try:
        async with asyncio.timeout(RPC_TIMEOUT + 5):
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
            payload = json.dumps({"operation": operation, "arguments": arguments}, ensure_ascii=False).encode() + b"\n"
            if len(payload) > MAX_REQUEST_BYTES:
                raise AccessError("invalid_arguments", "Request too large")
            writer.write(payload)
            await writer.drain()
            response = json.loads(await reader.readline())
            if "error" in response:
                error = response["error"]
                raise AccessError(error["code"], error["message"], error.get("retry_after_seconds"))
            return response["result"]
    except (OSError, TimeoutError, ValueError, KeyError):
        raise AccessError("unavailable", "Telegram bridge unavailable. For sends check get_send_status with the SAME request_id; never blindly resend.") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
