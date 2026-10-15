"""Local bounded SSH transport. One fresh, short-lived connection per tool call.

No retries (especially for writes), shell interpolation of tool data, credentials,
Telethon session, or persistent remote MCP process. Errors never echo payloads.
"""

import asyncio
import re
import shlex

from telegram_rpc import AccessError, MAX_RESPONSE_BYTES, decode_response, encode_request

SSH_REQUEST_TIMEOUT = 50
REMOTE_SOCKET = "/tmp/llmbot-mcp/control.sock"


async def _read_bounded(stream, limit):
    result = bytearray()
    while chunk := await stream.read(65536):
        result.extend(chunk)
        if len(result) > limit:
            raise AccessError("protocol_error", "SSH response exceeds size limit")
    return bytes(result)


class SSHBridge:
    def __init__(self, host, *, port=22, container="llmbot", socket_path=REMOTE_SOCKET,
                 timeout=SSH_REQUEST_TIMEOUT):
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:\-]*", host):
            raise ValueError("Invalid SSH host")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid SSH port")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}", container):
            raise ValueError("Invalid container name")
        if not socket_path.startswith("/") or any(char in socket_path for char in "\x00\r\n"):
            raise ValueError("Invalid absolute socket path")
        self.host = host
        self.port = port
        self.container = container
        self.socket_path = socket_path
        self.timeout = timeout
        self.slots = asyncio.Semaphore(2)

    def command(self):
        # SSH joins its remote arguments through a shell. Quote the FIXED command;
        # tool arguments/text travel only through encrypted stdin, never argv.
        remote = shlex.join([
            "docker", "exec", "-i", self.container, "python", "-I", "-S",
            "/app/scripts/telegram_rpc.py", "--socket", self.socket_path,
        ])
        return [
            "ssh", "-T", "-p", str(self.port),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=1",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", self.host, remote,
        ]

    async def __call__(self, operation, arguments):
        payload = encode_request(operation, arguments)
        process = None
        try:
            async with asyncio.timeout(self.timeout), self.slots:
                process = await asyncio.create_subprocess_exec(
                    *self.command(), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                stdout, _, returncode = await asyncio.gather(
                    _read_bounded(process.stdout, MAX_RESPONSE_BYTES),
                    _read_bounded(process.stderr, 65536), process.wait(),
                )
                if returncode:
                    raise AccessError("unavailable", "SSH/remote helper failed. For sends check get_send_status with the SAME request_id; never blindly resend.")
                return decode_response(stdout)
        except (OSError, TimeoutError):
            raise AccessError("unavailable", "SSH request interrupted or timed out. The MCP connection remains usable; check send status before retrying writes.") from None
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
