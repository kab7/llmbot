#!/usr/bin/env python3
"""Run behind SSH + docker exec -i. Stdout is reserved for MCP JSON-RPC."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_mcp import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, help="Bot-owned Unix socket path")
    args = parser.parse_args()
    create_server(args.socket).run(transport="stdio")


if __name__ == "__main__":
    main()
