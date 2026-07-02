# On-the-Fly MCP Server

On-the-Fly MCP Server is a Python stdio server that acts as a Capability Construction Orchestrator. It does not execute business logic or generate business code directly. Instead, it returns structured Capability Construction Workflows (CCW) and maintains the authoritative CLI status ledger in `otf_tools/{system}/cli.md`.

## What It Does

- Scans workspace-local systems under `otf_tools/`.
- Returns system context from `onthefly.md`, `swagger.json`, and `cli.md`.
- Generates build or reuse CCW contracts for CLI construction.
- Validates CCW structure and stale CLI state.
- Records CLI validation results through `register_cli`.
- Resolves human-review locks through `resolve_escalation`.

The server is build-time only. Runtime skills should call generated stable CLI scripts directly.

## Install With uv

```bash
uv sync
uv run onthefly-mcp
```

You can also run the module directly:

```bash
uv run python -m onthefly_mcp.server
```

## MCP Server Configuration

Configure the MCP host to start this server with `uv`. The tested Codex configuration is:

```toml
[mcp_servers.onthefly]
command = "uv"
args = ["--directory", "/Users/<your-local>/codex_space/onthefly", "run", "python", "-m", "onthefly_mcp.server"]
startup_timeout_sec = 30
```

For MCP clients that use JSON config:

```json
{
  "mcpServers": {
    "onthefly": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/<your-local>/codex_space/onthefly",
        "run",
        "python",
        "-m",
        "onthefly_mcp.server"
      ],
      "startup_timeout_sec": 30
    }
  }
}
```

Replace `/Users/<your-local>/codex_space/onthefly` with the project directory where this command succeeds:

```bash
uv --directory /Users/<your-local>/codex_space/onthefly run python -m onthefly_mcp.server
```

Do not start the file directly with `python onthefly_mcp/server.py`; the server uses package-relative imports and must be started with `python -m onthefly_mcp.server`.

## Install With pip

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 -m onthefly_mcp.server
```

## Protocol

The server uses JSON-lines over stdio. Each request is one JSON object per line:

```json
{"tool": "discover_system", "args": {}}
```

Each response is one JSON object per line. Errors use the shared envelope:

```json
{
  "error": {
    "code": "system_not_found",
    "message": "human readable message"
  }
}
```

## Tools

- `discover_system`
- `get_system_context`
- `construct_workflow`
- `validate_workflow`
- `register_cli`
- `resolve_escalation`

## Workspace Layout

The server always uses the current working directory injected by the MCP host. It creates and reads workspace-local state under:

```plain
otf_tools/
├── .otf/
│   └── .init.lock
└── ci/
    ├── onthefly.md
    ├── swagger.json
    ├── cli.md
    └── cli/
```

`register_cli` and `resolve_escalation` are the only tools that write `cli.md`. Generated CLI scripts under `otf_tools/{system}/cli/` remain Code Agent outputs, not MCP Server outputs.

## Development Check

```bash
python3 -m compileall onthefly_mcp
```
