"""Phase 5: validate_contract."""
from __future__ import annotations

import pytest

from otf_mcp.server import OtfServer
from otf_mcp.tools import construct_workflow


def _tools_call(srv, name, arguments):
    return srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )


def _seed(otf_tmp, system="ci", api_version="2.0.0"):
    d = otf_tmp / "otf_tools" / system
    d.mkdir(parents=True)
    (d / "onthefly.md").write_text(
        f'---\nname: CI\napi_version: "{api_version}"\ntags: []\n---\n', encoding="utf-8"
    )
    (d / "swagger.json").write_text('{"openapi": "3.0.0"}\n', encoding="utf-8")
    (d / "cli.md").write_text(
        "system: ci\n\ncommands:\n  - name: create_pipeline\n    cli: cli/create_pipeline.py\n    status: stable\n    generated_from_api_version: \"2.0.0\"\n",
        encoding="utf-8",
    )


def test_validate_clean_workflow_is_valid(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    ccw = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "create_pipeline"})
    wf = ccw["result"]["content"][0]["data"]["workflow"]
    resp = _tools_call(srv, "validate_contract", {"system": "ci", "workflow": wf})
    out = resp["result"]["content"][0]["data"]
    assert out == {"valid": True, "warnings": [], "stale_cli": [], "missing_fields": []}


def test_validate_detects_stale_cli(otf_tmp):
    _seed(otf_tmp, api_version="3.0.0")
    srv = OtfServer()
    ccw = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "create_pipeline"})
    wf = ccw["result"]["content"][0]["data"]["workflow"]
    resp = _tools_call(srv, "validate_contract", {"system": "ci", "workflow": wf})
    out = resp["result"]["content"][0]["data"]
    assert out["valid"] is False
    assert out["stale_cli"][0]["name"] == "create_pipeline"
    assert out["stale_cli"][0]["generated_from_api_version"] == "2.0.0"
    assert out["stale_cli"][0]["current_api_version"] == "3.0.0"


def test_validate_flags_missing_swagger(otf_tmp):
    _seed(otf_tmp)
    (otf_tmp / "otf_tools" / "ci" / "swagger.json").unlink()
    srv = OtfServer()
    ccw = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "x"})
    wf = ccw["result"]["content"][0]["data"]["workflow"]
    resp = _tools_call(srv, "validate_contract", {"system": "ci", "workflow": wf})
    out = resp["result"]["content"][0]["data"]
    assert "swagger_missing" in out["warnings"]


def test_validate_flags_cli_path_outside_system(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    wf = construct_workflow.handle({"system": "ci", "command": "x"}).get("workflow") if False else None  # noqa
    # build by hand
    wf = {
        "workflow_type": "capability_construction",
        "version": 1,
        "system": "ci",
        "goal": {"artifact": "cli", "command": "x"},
        "context": {"api_version": "2.0.0"},
        "inputs": [{"read": "otf_tools/ci/onthefly.md"}],
        "constraints": ["cli_spec_v1"],
        "artifact": {"create": ["cli/x.py"], "update": ["otf_tools/ci/cli.md"]},
        "validation_contract": {
            "type": "code_agent_execution",
            "steps": [
                {"run": "python cli/x.py --help", "expect": {"exit_code": 0}},
                {"run": "python cli/x.py --dry-run", "expect": {"exit_code": 0, "stdout_json": True}},
            ],
        },
        "retry_policy": {"max_attempts": 3},
        "failure_model": {"categories": ["param_error"]},
    }
    resp = _tools_call(srv, "validate_contract", {"system": "ci", "workflow": wf})
    out = resp["result"]["content"][0]["data"]
    assert any(w.startswith("cli_path_out_of_system") for w in out["warnings"])


def test_validate_catches_schema_break(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    bad = {"workflow_type": "capability_construction", "version": 1, "system": "ci"}  # missing fields
    resp = _tools_call(srv, "validate_contract", {"system": "ci", "workflow": bad})
    out = resp["result"]["content"][0]["data"]
    assert out["valid"] is False
    assert "workflow_schema_invalid" in out["warnings"]
    assert out["missing_fields"]  # at least one required field missing


def test_validate_unknown_system_raises(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "validate_contract", {"system": "ghost", "workflow": {}})
    assert resp["error"]["code"] == -32001  # SystemNotFoundError
