#!/usr/bin/env python3
"""Minimal stdio MCP server for the DSH MCP spike (phase 1.5).

Pure stdlib, newline-delimited JSON-RPC 2.0. One tool: pong(n) -> "PONG <n>".
Proves that DSH's dsh-mcp-client plugin loads from a profile patch layer and
that its tools (mcp__spike__pong) are callable from a headless worker run.
"""
import json
import sys

TOOLS = [{
    "name": "pong",
    "description": "Return the string 'PONG <n>' for the given n.",
    "inputSchema": {
        "type": "object",
        "properties": {"n": {"type": "string", "description": "value to echo"}},
    },
}]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "spike", "version": "0.1.0"},
            }
        elif method.startswith("notifications/"):
            continue
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            n = (params.get("arguments") or {}).get("n", "?")
            result = {"content": [{"type": "text", "text": f"PONG {n}"}], "isError": False}
        else:
            if mid is not None:
                out = {"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32601, "message": f"unknown method {method}"}}
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
            continue
        if mid is not None:
            out = {"jsonrpc": "2.0", "id": mid, "result": result}
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
