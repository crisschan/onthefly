"""Phase 3: get_system_context."""
from __future__ import annotations

import pytest

from otf_mcp.errors import SystemNotFoundError
from otf_mcp.server import OtfServer


def _tools_call(srv, name, arguments):
    return srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )


def _seed(otf_tmp, system="ci"):
    d = otf_tmp / "otf_tools" / system
    d.mkdir(parents=True)
    (d / "onthefly.md").write_text(
        '---\nname: CI\napi_version: "2.0.0"\ntags: [build]\n---\n\n# CI body\n',
        encoding="utf-8",
    )
    (d / "swagger.json").write_text('{"openapi": "3.0.0"}\n', encoding="utf-8")
    (d / "cli.md").write_text(
        "system: ci\n\ncommands:\n  - name: a\n    cli: cli/a.py\n    status: stable\n    generated_from_api_version: \"2.0.0\"\n  - name: b\n    cli: cli/b.py\n    status: stale\n    generated_from_api_version: \"1.0.0\"\n",
        encoding="utf-8",
    )


def test_get_context_returns_full_payload(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "get_system_context", {"system": "ci"})
    data = resp["result"]["content"][0]["data"]
    assert data["system"] == "ci"
    assert "CI body" in data["onthefly"]
    assert data["swagger"].startswith("{")
    assert data["cli_registry"]["system"] == "ci"
    assert [c["name"] for c in data["cli_registry"]["commands"]] == ["a", "b"]
    assert data["status"] == {"cli_count": 2, "stale_count": 1}


def test_get_context_unknown_system_raises(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "get_system_context", {"system": "ghost"})
    assert resp["error"]["code"] == SystemNotFoundError.code


def test_get_context_missing_swagger_reports_empty_string(otf_tmp):
    _seed(otf_tmp)
    (otf_tmp / "otf_tools" / "ci" / "swagger.json").unlink()
    srv = OtfServer()
    resp = _tools_call(srv, "get_system_context", {"system": "ci"})
    assert resp["result"]["content"][0]["data"]["swagger"] == ""


def test_get_context_rejects_missing_system_param(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "get_system_context", {})
    from otf_mcp.errors import InvalidParamsError
    assert resp["error"]["code"] == InvalidParamsError.code
