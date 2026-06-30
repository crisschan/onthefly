"""End-to-end smoke: drive the server through its in-process API,
hitting every tool in spec §4.1–4.4 against a scratch OTF_ROOT."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sandbox OTF_ROOT BEFORE importing the workspace module.
scratch = Path(tempfile.mkdtemp(prefix="otf_smoke_"))
os.environ["OTF_ROOT"] = str(scratch)

from otf_mcp.server import OtfServer  # noqa: E402


def call(srv, name, args):
    return srv.handle_message(
        {"jsonrpc": "2.0", "id": name, "method": "tools/call",
         "params": {"name": name, "arguments": args}}
    )


def main() -> int:
    srv = OtfServer()

    # 1. initialize (handshake)
    print("== initialize ==")
    resp = srv.handle_message({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    print(json.dumps(resp, ensure_ascii=False)[:120], "…")
    assert resp["result"]["serverInfo"]["name"] == "onthefly-mcp"

    # 2. discover_system on empty project (triggers auto-init)
    print("\n== discover_system (empty) ==")
    resp = call(srv, "discover_system", {})
    print(resp)
    assert (scratch / "otf_tools").is_dir(), "otf_tools should be auto-created"
    assert (scratch / "otf_tools" / ".otf").is_dir(), ".otf/ should be auto-created"

    # 3. seed a system
    sys_dir = scratch / "otf_tools" / "demo"
    sys_dir.mkdir()
    (sys_dir / "onthefly.md").write_text(
        '---\nname: Demo\napi_version: "1.2.3"\ntags: [sample, test]\n---\n\n# Demo\n',
        encoding="utf-8",
    )
    (sys_dir / "swagger.json").write_text('{"openapi": "3.0.0"}\n', encoding="utf-8")
    (sys_dir / "cli.md").write_text(
        "system: demo\n\ncommands:\n  - name: hello\n    cli: cli/hello.py\n    status: stable\n    generated_from_api_version: \"1.2.3\"\n",
        encoding="utf-8",
    )

    # 4. discover_system finds it
    print("\n== discover_system ==")
    resp = call(srv, "discover_system", {})
    print(json.dumps(resp["result"]["content"][0]["data"], indent=2))

    # 5. get_system_context
    print("\n== get_system_context ==")
    resp = call(srv, "get_system_context", {"system": "demo"})
    data = resp["result"]["content"][0]["data"]
    print(json.dumps({k: v if k != "onthefly" else v[:30] + "…"
                      for k, v in data.items()}, indent=2))
    assert data["status"] == {"cli_count": 1, "stale_count": 0}

    # 6. construct_workflow
    print("\n== construct_workflow ==")
    resp = call(srv, "construct_workflow", {"system": "demo", "command": "hello"})
    wf = resp["result"]["content"][0]["data"]["workflow"]
    print(json.dumps(wf, indent=2))

    # 7. validate_contract against the just-built workflow
    print("\n== validate_contract ==")
    resp = call(srv, "validate_contract", {"system": "demo", "workflow": wf})
    print(json.dumps(resp["result"]["content"][0]["data"], indent=2))

    # 8. validate_contract with deliberately bogus workflow (schema break)
    print("\n== validate_contract (schema break) ==")
    resp = call(srv, "validate_contract", {"system": "demo", "workflow": {"workflow_type": "x"}})
    print(json.dumps(resp["result"]["content"][0]["data"], indent=2))

    # 9. unknown tool -> method not found
    print("\n== tools/call unknown_tool ==")
    resp = call(srv, "nope", {})
    print(resp)
    assert resp["error"]["code"] == -32601

    print("\nALL SMOKE CHECKS PASSED ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
