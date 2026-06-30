"""Phase 4: construct_workflow (CCW generator)."""
from __future__ import annotations

import pytest

from otf_mcp.core.schema import CCW_SCHEMA, validate
from otf_mcp.errors import InvalidParamsError, SystemNotFoundError
from otf_mcp.server import OtfServer


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


def test_ccw_matches_spec_example(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "create_pipeline"})
    wf = resp["result"]["content"][0]["data"]["workflow"]

    # spec §4.3 expects exactly this shape
    assert wf["workflow_type"] == "capability_construction"
    assert wf["version"] == 1
    assert wf["system"] == "ci"
    assert wf["goal"] == {"artifact": "cli", "command": "create_pipeline"}
    assert wf["context"] == {"api_version": "2.0.0"}
    assert wf["inputs"] == [
        {"read": "otf_tools/ci/onthefly.md"},
        {"read": "otf_tools/ci/swagger.json"},
        {"read": "otf_tools/ci/cli.md"},
    ]
    for c in ["cli_spec_v1", "argparse", "stdout_json", "stderr_logs", "exit_code_contract"]:
        assert c in wf["constraints"]
    assert wf["artifact"]["create"] == ["otf_tools/ci/cli/create_pipeline.py"]
    assert wf["artifact"]["update"] == ["otf_tools/ci/cli.md"]
    assert wf["validation_contract"]["type"] == "code_agent_execution"
    assert wf["validation_contract"]["steps"][0]["run"] == "python cli/create_pipeline.py --help"
    assert wf["validation_contract"]["steps"][1]["run"] == "python cli/create_pipeline.py --dry-run"
    assert wf["retry_policy"] == {"max_attempts": 3}
    assert set(wf["failure_model"]["categories"]) == {
        "param_error",
        "contract_violation",
        "api_mismatch",
        "runtime_error",
    }


def test_ccw_is_schema_valid(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "do_thing"})
    wf = resp["result"]["content"][0]["data"]["workflow"]
    assert validate(wf, CCW_SCHEMA) == []


def test_ccw_unknown_system_raises(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ghost", "command": "x"})
    assert resp["error"]["code"] == SystemNotFoundError.code


@pytest.mark.parametrize("bad", ["", "with space", "with/slash", "1starts_with_digit", "../escape"])
def test_ccw_rejects_bad_command_names(otf_tmp, bad):
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ci", "command": bad})
    assert resp["error"]["code"] == InvalidParamsError.code


def test_ccw_honours_max_attempts_override(otf_tmp):
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "x", "max_attempts": 5})
    wf = resp["result"]["content"][0]["data"]["workflow"]
    assert wf["retry_policy"] == {"max_attempts": 5}


def test_ccw_contains_no_natural_language_prompts(otf_tmp):
    """Spec §4.3 forbids natural language instructions in the workflow."""
    _seed(otf_tmp)
    srv = OtfServer()
    resp = _tools_call(srv, "construct_workflow", {"system": "ci", "command": "x"})
    wf = resp["result"]["content"][0]["data"]["workflow"]

    # The keys we allow at each level are explicitly enumerated.
    allowed_top = {"workflow_type", "version", "system", "goal", "context", "inputs",
                   "constraints", "artifact", "validation_contract", "retry_policy", "failure_model"}
    assert set(wf.keys()) == allowed_top

    # Every 'run' string is a precise shell command, not a sentence.
    for step in wf["validation_contract"]["steps"]:
        run = step["run"]
        assert " " in run or run.startswith("python "), run
        # Heuristic: no trailing period (English sentences have periods)
        assert not run.endswith(".")
