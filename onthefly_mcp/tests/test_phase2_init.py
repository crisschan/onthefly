"""Phase 2: otf_tools/ auto-init + discover_system."""
from __future__ import annotations

import pytest

from otf_mcp.core import workspace
from otf_mcp.server import OtfServer
from otf_mcp.tools import discover_system


def _tools_call(srv, name, arguments):
    return srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )


def test_ensure_otf_creates_dirs(otf_tmp):
    root, hidden = workspace.ensure_otf_tools()
    assert root.is_dir()
    assert hidden.is_dir()
    assert root == otf_tmp / "otf_tools"
    assert hidden == otf_tmp / "otf_tools" / ".otf"


def test_auto_init_triggers_on_first_tool_call(otf_tmp):
    srv = OtfServer()
    assert (otf_tmp / "otf_tools").exists() is False
    resp = _tools_call(srv, "discover_system", {})
    assert resp["result"]["content"][0]["data"] == {"systems": []}
    assert (otf_tmp / "otf_tools").is_dir()
    assert (otf_tmp / "otf_tools" / ".otf").is_dir()


def test_discover_empty_returns_empty_list(otf_tmp):
    srv = OtfServer()
    resp = _tools_call(srv, "discover_system", {})
    assert resp["result"]["content"][0]["data"] == {"systems": []}


def _seed(otf_tmp, system, *, name=None, tags=None, api_version="1.0.0"):
    d = otf_tmp / "otf_tools" / system
    d.mkdir(parents=True)
    md = otf_tmp / "otf_tools" / system / "onthefly.md"
    fm = "---\n"
    if name:
        fm += f"name: {name}\n"
    if tags is not None:
        fm += f"tags: [{', '.join(tags)}]\n"
    if api_version:
        fm += f"api_version: \"{api_version}\"\n"
    fm += "---\n\n# body\n"
    md.write_text(fm, encoding="utf-8")


def test_discover_finds_front_matter(otf_tmp):
    _seed(otf_tmp, "ci", name="CI Pipeline", tags=["build", "deploy"], api_version="2.0.0")
    srv = OtfServer()
    resp = _tools_call(srv, "discover_system", {})
    data = resp["result"]["content"][0]["data"]
    assert data == {
        "systems": [
            {"system": "ci", "name": "CI Pipeline", "tags": ["build", "deploy"], "api_version": "2.0.0"}
        ]
    }


def test_discover_falls_back_when_no_front_matter(otf_tmp):
    d = otf_tmp / "otf_tools" / "plain"
    d.mkdir(parents=True)
    (d / "onthefly.md").write_text("# no front matter\n", encoding="utf-8")
    srv = OtfServer()
    resp = _tools_call(srv, "discover_system", {})
    data = resp["result"]["content"][0]["data"]
    assert data["systems"] == [{"system": "plain", "name": "plain", "tags": [], "api_version": ""}]


def test_direct_discover_skips_dirs_without_onthefly_md(otf_tmp):
    # bare dir -> ignored
    (otf_tmp / "otf_tools" / "bare").mkdir(parents=True)
    # dot-dir -> ignored
    (otf_tmp / "otf_tools" / ".hidden").mkdir(parents=True)
    _seed(otf_tmp, "ci", name="CI", tags=[], api_version="1.0.0")
    systems = workspace.list_systems()
    assert systems == ["ci"]


def test_system_name_rejects_path_traversal(otf_tmp):
    from otf_mcp.errors import InvalidParamsError

    with pytest.raises(InvalidParamsError):
        workspace.system_dir("../escape")
    with pytest.raises(InvalidParamsError):
        workspace.system_dir("")
    with pytest.raises(InvalidParamsError):
        workspace.system_dir("with/slash")
    with pytest.raises(InvalidParamsError):
        workspace.system_dir("a" * 100)


def test_onthefly_parse_handles_empty():
    doc = workspace.parse_onthefly_md("")
    assert doc.frontmatter is None
    assert doc.body == ""


def test_onthefly_parse_handles_no_front_matter():
    doc = workspace.parse_onthefly_md("# just a header\n")
    assert doc.frontmatter is None
    assert doc.body == "# just a header\n"


def test_onthefly_parse_extracts_front_matter():
    doc = workspace.parse_onthefly_md('---\nname: x\napi_version: "1"\ntags: [a]\n---\n# body\n')
    assert doc.frontmatter is not None
    assert doc.frontmatter.name == "x"
    assert doc.frontmatter.tags == ["a"]
    assert doc.frontmatter.api_version == "1"
    assert doc.body.startswith("# body")


def test_onthefly_parse_tolerates_garbage_yaml():
    doc = workspace.parse_onthefly_md("---\n: : :\n---\nbody\n")
    assert doc.frontmatter is None
    assert "body" in doc.body
