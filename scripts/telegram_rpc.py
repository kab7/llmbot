#!/usr/bin/env python3
"""One-shot server helper: stdlib only, bounded JSON stdin -> Unix RPC -> stdout.

It starts no MCP, Telethon, config loader, polling or jobs. It exits after ONE
request. Even an idle stdin is bounded by the helper's own total timeout.
"""

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_rpc import AccessError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, bridge_call, encode_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    # Production helper runs on Linux; bound also a stalled SSH stdin, not only RPC.
    if hasattr(signal, "alarm"):
        signal.alarm(48)
    try:
        raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST_BYTES:
            raise AccessError("invalid_arguments", "Malformed or oversized request")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"operation", "arguments"}:
            raise AccessError("invalid_arguments", "Invalid bridge request")
        encode_request(payload["operation"], payload["arguments"])
        result = asyncio.run(bridge_call(args.socket, payload["operation"], payload["arguments"], timeout=40))
        response = {"result": result}
    except AccessError as error:
        response = {"error": error.as_dict()}
    except Exception:
        response = {"error": {"code": "rpc_error", "message": "Invalid request or unavailable bridge; check send status before retrying writes"}}
    encoded = json.dumps(response, ensure_ascii=False).encode() + b"\n"
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = b'{"error":{"code":"response_too_large","message":"Request a smaller page"}}\n'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    if hasattr(signal, "alarm"):
        signal.alarm(0)


if __name__ == "__main__":
    main()
