"""JSON-RPC 2.0 framing for the MCP stdio transport.

The MCP protocol layers its own envelope on top of JSON-RPC, but the
wire format we speak on stdin/stdout is the standard
``Content-Length``-delimited message framing defined in
`the MCP transport spec <https://modelcontextprotocol.io>`_.

This module owns *only* the framing + JSON-RPC envelope. Tool dispatch
and request lifecycle live in :mod:`otf_mcp.server`.
"""
from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Iterator, Union


CONTENT_LENGTH = "Content-Length"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "onthefly-mcp"
SERVER_VERSION = "0.1.0"

JSONRPC_VERSION = "2.0"

# Default cap on a single frame. 64 MiB is generous for tool args but
# still small enough to keep a malformed peer from exhausting memory.
DEFAULT_MAX_FRAME_BYTES = 64 * 1024 * 1024

Stream = Union[BinaryIO, Any]  # any IO with .read/.readline/.write/.flush


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FramingError(Exception):
    """Raised when stdin doesn't look like an MCP framed message."""


# ---------------------------------------------------------------------------
# Stream-mode helpers
# ---------------------------------------------------------------------------


def _is_binary(stream: Any) -> bool:
    mode = getattr(stream, "mode", "")
    if mode:
        # TextIOWrapper reports mode like 'rb' or 'rt'
        return "b" in mode
    # stdin/stdout in CPython are text streams whose .readline yields str.
    return not hasattr(stream, "encoding")


def _read_line(stream: Stream) -> bytes:
    """Read one CRLF-terminated line as bytes."""
    if _is_binary(stream):
        return stream.readline()
    # text mode: encode back to bytes so the rest of the parser stays in bytes
    line = stream.readline()
    if isinstance(line, str):
        return line.encode("ascii", errors="replace")
    return line


def _read_n(stream: Stream, n: int) -> bytes:
    if n == 0:
        return b""
    if _is_binary(stream):
        return stream.read(n)
    raw = stream.read(n)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return raw


# ---------------------------------------------------------------------------
# Read/write raw MCP messages
# ---------------------------------------------------------------------------


def _read_headers(stream: Stream, *, max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> dict[str, str]:
    headers: dict[str, str] = {}
    bytes_read = 0
    while True:
        line = _read_line(stream)
        bytes_read += len(line)
        if bytes_read > max_bytes:
            raise FramingError("header section too large")
        if not line:
            raise FramingError("unexpected EOF in headers")
        line = line.rstrip(b"\r\n")
        if line == b"":
            return headers
        if b":" not in line:
            raise FramingError(f"malformed header: {line!r}")
        k, _, v = line.partition(b":")
        headers[k.decode("ascii", errors="replace").strip().lower()] = v.decode("ascii", errors="replace").strip()
    raise FramingError("unreachable")  # pragma: no cover


def read_message(stream: Stream | None = None) -> dict[str, Any]:
    """Read one MCP framed message from ``stream`` (default: stdin).

    Raises :class:`FramingError` on malformed frames. EOF raises EOFError
    so the server loop can distinguish a clean shutdown from a parse
    failure.
    """
    stream = stream if stream is not None else sys.stdin
    headers = _read_headers(stream)
    if not headers:
        raise FramingError("empty frame")
    length_str = headers.get(CONTENT_LENGTH.lower())
    if length_str is None:
        raise FramingError(f"missing {CONTENT_LENGTH}: header")
    try:
        length = int(length_str)
    except ValueError as exc:
        raise FramingError(f"bad Content-Length: {length_str!r}") from exc
    if length < 0 or length > DEFAULT_MAX_FRAME_BYTES:
        raise FramingError(f"implausible Content-Length: {length}")
    raw = _read_n(stream, length)
    if len(raw) != length:
        raise FramingError(f"truncated body: expected {length}, got {len(raw)}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FramingError(f"non-UTF8 body: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FramingError(f"invalid JSON body: {exc}") from exc


def write_message(message: dict[str, Any], stream: Stream | None = None) -> None:
    """Write a single framed message to ``stream`` (default: stdout)."""
    stream = stream if stream is not None else sys.stdout
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"{CONTENT_LENGTH}: {len(body)}\r\n\r\n".encode("ascii")
    if _is_binary(stream):
        stream.write(header)
        stream.write(body)
    else:
        stream.write(header.decode("ascii"))
        stream.write(body.decode("utf-8"))
    stream.flush()


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def is_request(msg: dict[str, Any]) -> bool:
    return "method" in msg and "id" in msg


def is_notification(msg: dict[str, Any]) -> bool:
    return "method" in msg and "id" not in msg


def is_response(msg: dict[str, Any]) -> bool:
    return "result" in msg or "error" in msg


def make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# Notifications: server -> client
# ---------------------------------------------------------------------------


def make_log(level: str, data: Any) -> dict[str, Any]:
    """Build a ``notifications/message`` JSON-RPC notification."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "notifications/message",
        "params": {"level": level, "data": data},
    }


# ---------------------------------------------------------------------------
# Handshake: capabilities advertised to the client
# ---------------------------------------------------------------------------


def server_capabilities() -> dict[str, Any]:
    """The fixed set of tools advertised in ``initialize``."""
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {"tools": {"listChanged": False}},
        "tools": [
            {
                "name": "discover_system",
                "description": "List systems registered under otf_tools/.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_system_context",
                "description": "Read the full onthefly.md + cli.md + swagger.json for a system.",
                "inputSchema": {
                    "type": "object",
                    "required": ["system"],
                    "properties": {"system": {"type": "string"}},
                },
            },
            {
                "name": "construct_workflow",
                "description": "Build a CCW (Capability Construction Workflow) for one CLI command.",
                "inputSchema": {
                    "type": "object",
                    "required": ["system", "command"],
                    "properties": {
                        "system": {"type": "string"},
                        "command": {"type": "string"},
                        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                },
            },
            {
                "name": "validate_contract",
                "description": "Validate a CCW workflow against schema + cli.md + swagger.json.",
                "inputSchema": {
                    "type": "object",
                    "required": ["system", "workflow"],
                    "properties": {
                        "system": {"type": "string"},
                        "workflow": {"type": "object"},
                    },
                },
            },
        ],
    }


# ---------------------------------------------------------------------------
# Iterable reader for tests / repls that pre-load messages from a list.
# ---------------------------------------------------------------------------


def iter_messages(messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Iterate a static list of messages as if they came off stdin."""
    yield from messages
