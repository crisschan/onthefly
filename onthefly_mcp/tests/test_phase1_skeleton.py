"""Phase 1: stdio skeleton + router."""
from __future__ import annotations

import io
import json

import pytest

from otf_mcp import protocol
from otf_mcp.errors import (
    InternalError,
    InvalidParamsError,
    MethodNotFoundError,
    OtfError,
)
from otf_mcp.server import OtfServer


def _msg(req_id, method, params=None):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


def test_initialize_returns_capabilities():
    srv = OtfServer()
    resp = srv.handle_message(_msg(1, "initialize"))
    assert resp["id"] == 1
    caps = resp["result"]
    assert caps["serverInfo"]["name"] == "onthefly-mcp"
    names = {t["name"] for t in caps["tools"]}
    assert names == {"discover_system", "get_system_context", "construct_workflow", "validate_contract"}


def test_tools_list_returns_registered_tools():
    srv = OtfServer()
    resp = srv.handle_message(_msg(2, "tools/list"))
    assert resp["id"] == 2
    assert {t["name"] for t in resp["result"]["tools"]} == {
        "discover_system",
        "get_system_context",
        "construct_workflow",
        "validate_contract",
    }


def test_ping_round_trip():
    srv = OtfServer()
    resp = srv.handle_message(_msg(3, "ping"))
    assert resp["result"] == {"pong": True}


def test_unknown_method_returns_method_not_found():
    srv = OtfServer()
    resp = srv.handle_message(_msg(4, "nope/not-a-method"))
    assert resp["error"]["code"] == MethodNotFoundError.code


def test_invalid_envelope_returns_protocol_error():
    srv = OtfServer()
    resp = srv.handle_message({"jsonrpc": "1.0", "id": 5, "method": "ping"})
    assert resp["error"]["code"] == -32600


def test_unknown_tool_returns_method_not_found():
    srv = OtfServer()
    resp = srv.handle_message(
        _msg(6, "tools/call", {"name": "nope", "arguments": {}})
    )
    assert resp["error"]["code"] == MethodNotFoundError.code


def test_tool_call_with_missing_name_returns_invalid_params():
    srv = OtfServer()
    resp = srv.handle_message(_msg(7, "tools/call", {"arguments": {}}))
    assert resp["error"]["code"] == InvalidParamsError.code


def test_notification_has_no_response():
    srv = OtfServer()
    msg = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
    assert srv.handle_message(msg) is None


def test_router_catches_internal_exception():
    class Boom(OtfError):
        code = -32099

    srv = OtfServer()
    srv._tools["explode"] = lambda _a: (_ for _ in ()).throw(Boom("boom"))
    resp = srv.handle_message(_msg(8, "tools/call", {"name": "explode", "arguments": {}}))
    assert resp["error"]["code"] == -32099


def test_framing_round_trip():
    """End-to-end: encode/decode one message through stdio framing."""
    body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode("utf-8")
    raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    in_stream = io.BytesIO(raw)
    msg = protocol.read_message(in_stream)
    out_stream = io.BytesIO()
    protocol.write_message(msg, out_stream)

    out_stream.seek(0)
    header_line = out_stream.readline().decode("ascii").rstrip("\r\n")
    assert header_line.startswith("Content-Length: ")
    length = int(header_line.split(":", 1)[1].strip())
    payload = out_stream.read().decode("utf-8")
    assert json.loads(payload)["method"] == "ping"


def test_framing_rejects_missing_length():
    from otf_mcp.protocol import FramingError, read_message

    in_stream = io.BytesIO(b"\r\n")
    with pytest.raises(FramingError):
        read_message(in_stream)


def test_internal_error_code_path():
    class Crash:
        def __call__(self, _a):
            raise RuntimeError("boom")

    srv = OtfServer()
    srv._tools["crash"] = Crash()
    resp = srv.handle_message(_msg(10, "tools/call", {"name": "crash", "arguments": {}}))
    assert resp["error"]["code"] == InternalError.code
    assert "boom" in resp["error"]["message"]
