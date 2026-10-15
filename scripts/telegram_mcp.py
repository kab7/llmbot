#!/usr/bin/env python3
"""Local MCP stdio server. Never run inside the production bot container."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_mcp import create_server
from telegram_ssh import REMOTE_SOCKET, SSHBridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--ssh-host", help="SSH user@host; one connection per tool call")
    target.add_argument("--socket", help="Local Unix socket (development/testing only)")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--container", default="llmbot")
    parser.add_argument("--remote-socket", default=REMOTE_SOCKET)
    args = parser.parse_args()
    transport = None
    if args.ssh_host:
        transport = SSHBridge(args.ssh_host, port=args.ssh_port,
                              container=args.container, socket_path=args.remote_socket)
    create_server(args.socket, transport=transport).run(transport="stdio")


if __name__ == "__main__":
    main()
