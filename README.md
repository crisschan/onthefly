# On-the-Fly MCP Server (Python stdio)

A stateless *Capability Construction Orchestrator* that turns a
project's ``otf_tools/`` directory into structured
**Capability Construction Workflows (CCW)** for Code Agents.

The server does not execute business logic, does not write code, does
not call LLMs. Its only job is to plan.

## Architecture

```
Codex / Claude Code ──MCP stdio──▶ On-the-Fly MCP Server (Python) ──▶ Project cwd
                                   ├─ discover_system
                                   ├─ get_system_context
                                   ├─ construct_workflow  → CCW (machine-readable)
                                   └─ validate_contract
```

Every tool call uses `os.getcwd()` as the engineering boundary; the
server auto-creates `otf_tools/` + `otf_tools/.otf/` on the first tool
call (spec §7).

## Running

### 1. Console script

```bash
pip install -e .
onthefly-mcp               # speaks MCP on stdin/stdout
python -m otf_mcp          # equivalent
```

### 2. Wire it up in your MCP client

```jsonc
{
  "mcpServers": {
    "onthefly": {
      "command": "python",
      "args": ["-m", "otf_mcp"],
      "cwd": "/abs/path/to/your/project"
    }
  }
}
```

The server uses the project's working directory as the only context;
no extra configuration required.

## Tools

| Tool               | Input              | Output summary                                 |
|--------------------|--------------------|------------------------------------------------|
| `discover_system`  | `{}`               | `{"systems": [{system,name,tags,api_version}]}`|
| `get_system_context` | `{system}`       | full texts of onthefly.md, cli.md, swagger.json|
| `construct_workflow` | `{system, command, max_attempts?}` | a CCW (validated against schema v1) |
| `validate_contract` | `{system, workflow}` | `{valid, warnings, stale_cli, missing_fields}` |

See `On-the-Fly MCP Server Spec（v1）.md` (at the repo root) for the
full semantics of the CCW (JSON-Schema v1, success judgement, retry
loop, …).

## Tests

```bash
cd onthefly_mcp
python3 -m pytest -q
```

74 tests cover the six phases of the spec implementation. Each phase
file (`tests/test_phaseN_*.py`) maps to the corresponding milestone in
the original request.

### Smoke test

```bash
python3 smoke_test.py     # exercises every tool end-to-end
```

## Project layout

```
onthefly_mcp/
├── otf_mcp/
│   ├── errors.py                # typed exceptions + JSON-RPC codes
│   ├── protocol.py              # MCP framing + JSON-RPC envelope
│   ├── server.py                # OtfServer + run_stdio entrypoint
│   ├── core/
│   │   ├── schema.py            # CCW JSON-Schema + tiny validator
│   │   └── workspace.py         # paths, init, onthefly.md / cli.md parsing
│   └── tools/
│       ├── discover_system.py
│       ├── get_system_context.py
│       ├── construct_workflow.py
│       └── validate_contract.py
└── tests/
    └── test_phase1_skeleton.py … test_phase6_hardening.py
```

## Design decisions worth noting

* **No `jsonschema` runtime dependency.** The host cannot reach PyPI.
  `core/schema.py` implements just enough of the JSON-Schema draft-07
  subset that the CCW v1 contract needs (type / required / enum / const
  / items / additionalProperties) without pulling a package.

* **Idempotent init.** `OtfServer` keeps one boolean flag so the
  `otf_tools/` directory is created at most once per process even if
  the caller hammers the discovery endpoint.

* **System-name allowlist.** Names are matched against
  `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` so a malicious caller cannot use
  `../etc` to escape the project boundary; the primary defence is that
  every path is rooted under `otf_tools/`, the allowlist is
  defence-in-depth.

* **CCW is *always* schema-validated before being returned.** If you
  tweak `tools/construct_workflow.py` and accidentally drop a required
  key, the test suite fails immediately.

* **No code generation.** The server only emits the CCW; the
  `cli/*.py` template lives in the spec, not in this codebase, by
  deliberate design boundary (capability compiler, not capability
  runtime).
