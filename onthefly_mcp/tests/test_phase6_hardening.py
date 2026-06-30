"""Phase 6: schema hardening + edge cases."""
from __future__ import annotations

import pytest

from otf_mcp.core import workspace
from otf_mcp.core.schema import validate, validate_or_raise
from otf_mcp.errors import InvalidParamsError
from otf_mcp.server import OtfServer


# ---- mini schema validator: hardening --------------------------------


@pytest.mark.parametrize(
    "value,expect_ok",
    [
        ({"a": 1}, True),
        ({}, True),
        ([], True),
        ("s", True),
        (1, True),
        (True, True),
        (False, True),
        ("s", True),
        # wrong types
        (1, "object"),
        ({"a": 1}, "array"),
        ([1, 2], "string"),
        ({"a": "x"}, "integer"),
        ("x", "integer"),
        (True, "integer"),  # bool is not integer
        (1, "boolean"),  # int is not boolean
    ],
)
def test_type_checker(value, expect_ok):
    if expect_ok is True:
        assert validate(value, {"type": _type_name(value)}) == []
    elif expect_ok is False:
        assert validate(value, {"type": "object"}) != []
    else:
        assert validate(value, {"type": expect_ok}) != []


def _type_name(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def test_required_enforced():
    errs = validate({}, {"type": "object", "required": ["a"]})
    assert errs and "$.a" in errs[0]


def test_enum_and_const_enforced():
    assert validate("a", {"enum": ["a", "b"]}) == []
    assert validate("c", {"enum": ["a", "b"]})
    assert validate(1, {"const": 1}) == []
    assert validate(2, {"const": 1})


def test_array_items_validated():
    errs = validate([1, "x", 3], {"type": "array", "items": {"type": "integer"}})
    assert errs and "$[1]" in errs[0]


def test_additional_properties_disallowed():
    errs = validate({"a": 1, "b": 2}, {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False})
    assert errs and "$.b" in errs[0]


def test_validate_or_raise_wraps_errors():
    with pytest.raises(InvalidParamsError) as exc:
        validate_or_raise({"system": 1}, {"type": "object", "properties": {"system": {"type": "string"}}}, what="x")
    assert exc.value.data["errors"]


# ---- CCW schema corner cases -----------------------------------------


def test_ccw_rejects_unknown_workflow_type():
    errs = validate(
        {"workflow_type": "other", "version": 1},
        {"type": "object", "properties": {"workflow_type": {"const": "capability_construction"}}, "required": ["workflow_type"]},
    )
    assert errs


def test_ccw_rejects_non_int_version():
    errs = validate(
        {"workflow_type": "capability_construction", "version": "1"},
        {"type": "object", "properties": {"version": {"type": "integer"}}, "required": ["version"]},
    )
    assert errs


# ---- Server edge cases -----------------------------------------------


def _tools_call(srv, name, arguments):
    return srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )


def test_auto_init_is_idempotent(otf_tmp):
    srv = OtfServer()
    _tools_call(srv, "discover_system", {})
    _tools_call(srv, "discover_system", {})
    _tools_call(srv, "discover_system", {})
    assert (otf_tmp / "otf_tools").is_dir()


def test_unicode_in_system_name_rejected(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "get_system_context", {"system": "系统"})
    assert resp["error"]["code"] == InvalidParamsError.code


def test_non_dict_arguments_rejected(otf_tmp):
    srv = OtfServer()
    resp = srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "discover_system", "arguments": "no"}}
    )
    assert resp["error"]["code"] == InvalidParamsError.code


def test_unknown_notification_silently_accepted(otf_tmp):
    srv = OtfServer()
    msg = {"jsonrpc": "2.0", "method": "notifications/something_weird"}
    assert srv.handle_message(msg) is None


def test_missing_method_rejected(otf_tmp):
    srv = OtfServer()
    resp = srv.handle_message({"jsonrpc": "2.0", "id": 1})
    assert resp["error"]["code"] == -32600


def test_invalid_params_must_be_object(otf_tmp):
    srv = OtfServer()
    resp = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2, 3]})
    assert resp["error"]["code"] == -32602


def test_workspace_path_traversal_blocked(otf_tmp):
    with pytest.raises(InvalidParamsError):
        workspace.onthefly_file("../etc")


def test_cli_registry_tolerates_garbage(otf_tmp):
    text = "---\nnot_a_dict: 1\n---\n"
    reg = workspace.parse_cli_registry("ci", text)
    assert reg.commands == []


def test_cli_registry_handles_missing_status():
    text = "system: ci\n\ncommands:\n  - name: foo\n    cli: cli/foo.py\n    generated_from_api_version: \"1.0.0\"\n"
    reg = workspace.parse_cli_registry("ci", text)
    assert reg.commands[0].status == "draft"
