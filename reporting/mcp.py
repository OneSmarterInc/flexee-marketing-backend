"""The connector's MCP side. Stateless JSON-RPC over HTTP POST (Streamable HTTP, answered as
JSON): initialize, ping, tools/list and tools/call. Every tool is read-only and takes no arguments.
Each call is logged with its tool, status, row count and time, never its rows."""
from __future__ import annotations

import json
import logging
import time

from . import oauth, reports

log = logging.getLogger("flexee.reporting")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def error(id_, code: int, message: str):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def tools() -> list[dict]:
    out = []
    for name, tool in reports.TOOLS.items():
        columns = "\n".join(f"{c}: {d}" for c, d in tool["columns"].items())
        out.append({
            "name": name, "title": tool["title"],
            "description": f"{tool['description']}\n\nColumns:\n{columns}",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}},
                             "required": ["rows"]},
            "annotations": {"title": tool["title"], "readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        })
    return out


def handle(message: dict, connection):
    """The reply to one JSON-RPC message, or None for a notification."""
    id_ = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return error(id_, -32600, "Invalid request.")
    if "id" not in message:
        return None
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    method = message["method"]
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else ""
        return _result(id_, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "flexee-marketing-management-reporting",
                           "title": f"{oauth.PRODUCT_NAME} reporting", "version": reports.CONTRACT},
            "instructions": (
                f"Read-only operational reporting for {reports.SIM} (Flexee family reporting contract {reports.CONTRACT}). "
                'Each tool returns {"rows": [...]}; every row starts with "sim". Times are UTC (ISO 8601), money is US dollars. '
                "In Marketing a course is a game and a quarter is a round; rounds before the takeover round are the "
                "listening period (warm_up). There are no names of people, no student work and no team results."),
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": tools()})
    if method == "tools/call":
        return _call(id_, params, connection)
    return error(id_, -32601, f"Method not found: {method}")


def _call(id_, params: dict, connection):
    name = params.get("name") if isinstance(params.get("name"), str) else ""
    if name not in reports.TOOLS:
        return error(id_, -32602, f"Unknown tool: {name}")
    started = time.monotonic()
    try:
        rows = reports.run(name)
    except Exception:
        log.exception("[reporting] %s failed", name)
        oauth.log_call(connection, name, "error", 0, int((time.monotonic() - started) * 1000))
        return _result(id_, {"content": [{"type": "text", "text": "The report couldn't be worked out just now. Try again shortly."}],
                             "isError": True})
    oauth.log_call(connection, name, "ok", len(rows), int((time.monotonic() - started) * 1000))
    return _result(id_, {"content": [{"type": "text", "text": json.dumps({"rows": rows})}],
                         "structuredContent": {"rows": rows}, "isError": False})
