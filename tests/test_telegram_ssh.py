import asyncio
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from telegram_access import SendJournal, TelegramAccess, TelegramBridge
from telegram_mcp import create_server
from telegram_rpc import AccessError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, decode_response, encode_request
from telegram_ssh import SSHBridge
from test_telegram_mcp import FakeClient

ROOT = Path(__file__).resolve().parents[1]


def run(coroutine):
    return asyncio.run(coroutine)


class FakeInput:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        pass


class FakeProcess:
    def __init__(self, stdout=b'{"result":{"id":"11"}}\n', stderr=b"", returncode=0):
        self.stdin = FakeInput()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode
        self.finished = asyncio.Event()
        self.killed = False

    async def wait(self):
        if self.returncode is None:
            await self.finished.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()


@pytest.mark.parametrize("host", ["-oProxyCommand=evil", "root@host;id", "host\nother", "", "host name"])
def test_ssh_host_rejects_option_and_shell_injection(host):
    with pytest.raises(ValueError):
        SSHBridge(host)


@pytest.mark.parametrize("options", [{"port": 0}, {"port": True}, {"port": 65536},
                                     {"container": "x;id"}, {"socket_path": "relative"},
                                     {"socket_path": "/tmp/x\ncommand"}])
def test_ssh_config_validation(options):
    with pytest.raises(ValueError):
        SSHBridge("root@example.test", **options)


def test_ssh_payload_is_only_on_stdin_and_each_call_is_fresh(monkeypatch):
    async def scenario():
        first, second = FakeProcess(), FakeProcess()
        factory = AsyncMock(side_effect=[first, second])
        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        bridge = SSHBridge("root@example.test", port=2222, socket_path="/tmp/a b/control.sock")
        text = 'private text; $(touch /tmp/never) " quoted'
        arguments = {"peer": "11", "text": text, "request_id": str(uuid4())}
        await bridge("send_message", arguments)
        await bridge("get_send_status", {"request_id": arguments["request_id"]})
        assert factory.await_count == 2
        argv = factory.call_args_list[0].args
        assert text not in str(argv) and arguments["request_id"] not in str(argv)
        assert "StrictHostKeyChecking=yes" in argv
        assert "ControlMaster=no" in argv and "ControlPath=none" in argv
        assert shlex.split(argv[-1]) == ["docker", "exec", "-i", "llmbot", "python", "-I", "-S",
                                          "/app/scripts/telegram_rpc.py", "--socket", "/tmp/a b/control.sock"]
        assert json.loads(first.stdin.data)["arguments"] == arguments
    run(scenario())


@pytest.mark.parametrize("stdout,stderr,returncode,expected", [
    (b"", b"private error token should never be exposed", 255, "unavailable"),
    (b"not JSON private text", b"", 0, "protocol_error"),
    (b"x" * (MAX_RESPONSE_BYTES + 1), b"", 0, "protocol_error"),
    (b'{"result":{}}', b"x" * 65537, 0, "protocol_error"),
    (b'{"error":{"code":"rate_limited","message":"Wait","retry_after_seconds":33}}', b"", 0, "rate_limited"),
])
def test_ssh_errors_are_bounded_and_not_retried(monkeypatch, stdout, stderr, returncode, expected):
    async def scenario():
        process = FakeProcess(stdout, stderr, returncode)
        factory = AsyncMock(return_value=process)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        with pytest.raises(AccessError) as captured:
            await SSHBridge("host")("resolve_chat", {"peer": "me"})
        assert captured.value.code == expected
        assert "private" not in str(captured.value)
        assert factory.await_count == 1
        if expected == "rate_limited":
            assert captured.value.retry_after == 33
    run(scenario())


def test_ssh_timeout_kills_child_and_next_request_recovers(monkeypatch):
    async def scenario():
        blocked, good = FakeProcess(returncode=None), FakeProcess()
        factory = AsyncMock(side_effect=[blocked, good])
        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        bridge = SSHBridge("host", timeout=0.02)
        with pytest.raises(AccessError, match="timed out"):
            await bridge("resolve_chat", {"peer": "me"})
        assert blocked.killed
        assert await bridge("resolve_chat", {"peer": "me"}) == {"id": "11"}
        assert factory.await_count == 2
    run(scenario())


def test_ssh_cancellation_kills_child_without_replay(monkeypatch):
    async def scenario():
        process = FakeProcess(returncode=None)
        factory = AsyncMock(return_value=process)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        task = asyncio.create_task(SSHBridge("host")("resolve_chat", {"peer": "me"}))
        while not factory.await_count:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and factory.await_count == 1
    run(scenario())


def test_missing_ssh_is_a_tool_error_not_a_stdio_crash(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("private path")))
    with pytest.raises(AccessError, match="interrupted"):
        run(SSHBridge("host")("resolve_chat", {"peer": "me"}))


@pytest.mark.parametrize("data", [b"", b"null", b"[]", b'{"result":[]}',
                                b'{"error":{"code":11,"message":"x"}}',
                                b'{"result":{},"error":{}}', b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_rpc_response_validation(data):
    with pytest.raises(AccessError, match="Invalid Telegram bridge response"):
        decode_response(data)


@pytest.mark.parametrize("operation,arguments", [
    ("delete_messages", {}), ("resolve_chat", []), ("resolve_chat", {"bad": object()}),
    ("resolve_chat", {"bad": float("nan")}), ("resolve_chat", {"bad": "x" * MAX_REQUEST_BYTES}),
])
def test_rpc_request_validation(operation, arguments):
    with pytest.raises(AccessError):
        encode_request(operation, arguments)


def test_helper_and_local_facade_dependency_boundaries():
    # -I -S disables site-packages. The server helper MUST still start and answer.
    result = subprocess.run([sys.executable, "-I", "-S", "scripts/telegram_rpc.py", "--socket", "/tmp/no-llmbot/socket"],
                            input=encode_request("resolve_chat", {"peer": "me"}), capture_output=True, check=True)
    assert json.loads(result.stdout)["error"]["code"] == "unavailable"
    code = "import sys; sys.path.insert(0,sys.argv[1]); from telegram_mcp import create_server; from telegram_ssh import SSHBridge; create_server(transport=SSHBridge('host')); assert not ({'telethon','bot','config','telegram_access'} & set(sys.modules))"
    subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], check=True)


@pytest.mark.parametrize("payload", [b"", b"{bad}\n", b"{}\n", b"[]\n", b"x" * (MAX_REQUEST_BYTES + 1),
                                     b'{"operation":"delete_messages","arguments":{}}\n'])
def test_one_shot_helper_rejects_invalid_input(payload):
    result = subprocess.run([sys.executable, "-I", "-S", "scripts/telegram_rpc.py", "--socket", "/tmp/no-llmbot/socket"],
                            input=payload, capture_output=True, check=True)
    assert set(json.loads(result.stdout)) == {"error"}
    assert not result.stderr


def test_same_mcp_session_recovers_from_ssh_failure_and_bot_restart(monkeypatch):
    real_exec = asyncio.create_subprocess_exec

    async def scenario(directory):
        client = FakeClient()
        service = TelegramAccess(lambda: client, SendJournal(Path(directory) / "journal.db"))
        socket = Path(directory) / "control.sock"
        bridge = TelegramBridge(service, socket)
        await bridge.start()
        calls = []

        async def factory(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return FakeProcess(stdout=b"", stderr=b"connection dropped", returncode=255)
            return await real_exec(sys.executable, "-I", "-S", str(ROOT / "scripts/telegram_rpc.py"),
                                   "--socket", str(socket), **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        server = create_server(transport=SSHBridge("host"))
        try:
            async with create_connected_server_and_client_session(server._mcp_server) as session:
                assert len((await session.list_tools()).tools) == 6
                assert not calls  # Discovery never contacts server/SSH.
                first = await session.call_tool("resolve_chat", {"peer": "me"})
                assert first.isError
                recovered = await session.call_tool("resolve_chat", {"peer": "me"})
                assert not recovered.isError and recovered.structuredContent["id"] == "11"
                await bridge.close()
                absent = await session.call_tool("resolve_chat", {"peer": "me"})
                assert absent.isError
                await bridge.start()
                after_restart = await session.call_tool("resolve_chat", {"peer": "me"})
                assert not after_restart.isError
                key = str(uuid4())
                arguments = {"peer": "me", "text": "synthetic message", "request_id": key}
                sent = await session.call_tool("send_message", arguments)
                again = await session.call_tool("send_message", arguments)
                status = await session.call_tool("get_send_status", {"request_id": key})
                assert sent.structuredContent == again.structuredContent == status.structuredContent
                client.send_message.assert_awaited_once()
        finally:
            await bridge.close()
    with tempfile.TemporaryDirectory(prefix="llmbot-ssh-", dir="/tmp") as directory:
        run(scenario(directory))


def test_write_receipt_survives_lost_ssh_response(monkeypatch, tmp_path):
    async def scenario():
        client = FakeClient()
        service = TelegramAccess(lambda: client, SendJournal(tmp_path / "journal.db"))
        key = str(uuid4())
        arguments = {"peer": "me", "text": "synthetic", "request_id": key}
        # Telegram accepted it, but SSH lost the response. Do NOT replay automatically.
        accepted = await service.send_message(**arguments)
        factory = AsyncMock(return_value=FakeProcess(stdout=b"", returncode=255))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
        with pytest.raises(AccessError):
            await SSHBridge("host")("send_message", arguments)
        assert factory.await_count == 1
        assert await service.get_send_status(key) == accepted
        assert await service.send_message(**arguments) == accepted
        client.send_message.assert_awaited_once()
    run(scenario())
