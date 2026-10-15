"""Standard-library-only wire client shared by local MCP and the SSH helper.

Do not import Telethon, MCP, bot, config, dotenv or runtime credentials here.
"""

import asyncio
import json

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


def encode_request(operation, arguments):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise AccessError("unknown_operation", "Unknown Telegram operation")
    if not isinstance(arguments, dict):
        raise AccessError("invalid_arguments", "arguments must be an object")
    try:
        data = json.dumps({"operation": operation, "arguments": arguments},
                          ensure_ascii=False, allow_nan=False).encode() + b"\n"
    except (TypeError, ValueError, UnicodeError):
        raise AccessError("invalid_arguments", "Invalid JSON arguments") from None
    if len(data) > MAX_REQUEST_BYTES:
        raise AccessError("invalid_arguments", "Request too large")
    return data


def decode_response(data):
    try:
        if not data or len(data) > MAX_RESPONSE_BYTES:
            raise ValueError
        response = json.loads(data)
        if not isinstance(response, dict):
            raise ValueError
        if set(response) == {"result"} and isinstance(response["result"], dict):
            return response["result"]
        if set(response) != {"error"} or not isinstance(response["error"], dict):
            raise ValueError
        error = response["error"]
        if not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise AccessError("protocol_error", "Invalid Telegram bridge response; check send status before retrying writes") from None
    raise AccessError(error["code"], error["message"], error.get("retry_after_seconds"))


async def bridge_call(socket_path, operation, arguments, *, timeout=RPC_TIMEOUT + 5):
    payload = encode_request(operation, arguments)
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
            writer.write(payload)
            await writer.drain()
            return decode_response(await reader.readline())
    except (OSError, TimeoutError, ValueError):
        raise AccessError("unavailable", "Telegram bridge unavailable. For sends check get_send_status with the SAME request_id; never blindly resend.") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
