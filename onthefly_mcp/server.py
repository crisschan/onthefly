"""JSON-lines stdio server loop for On-the-Fly MCP tools.

The server accepts both the project-local test envelope:

    {"tool": "discover_system", "args": {}}

and the MCP JSON-RPC methods used by real MCP clients:

    initialize, notifications/initialized, tools/list, tools/call
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlparse

from .errors import ToolError, error_response
from .tools import TOOLS, dispatch
from .workspace import ensure_otf_tools, set_workspace_root

SERVER_NAME = "on-the-fly"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "init_system": {
        "description": "Bootstrap a new system from an existing swagger.json in the workspace.",
        "inputSchema": {
            "type": "object",
            "required": ["system", "swagger_path"],
            "properties": {
                "system": {
                    "type": "string",
                    "description": "System directory name, chosen by the Code Agent.",
                },
                "swagger_path": {
                    "type": "string",
                    "description": "Relative path inside workspace to an existing swagger.json file.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Optional. Absolute path to the directory where otf_tools/<system>/ is created. When omitted, defaults to <swagger.json parent>/otf_tools/.",
                },
                "base_url": {
                    "type": "string",
                    "description": "Optional. Override the base URL for API calls (e.g. https://api.example.com). When omitted, auto-extracted from swagger.json servers/host fields.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags list for onthefly.md.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for onthefly.md.",
                },
            },
            "additionalProperties": False,
        },
    },
    "configure_system": {
        "description": "Provide missing config (baseurl, auth credentials, auth_types) for a system. Call this when init_system returns missing_config or when the user supplies credentials.",
        "inputSchema": {
            "type": "object",
            "required": ["system"],
            "properties": {
                "system": {
                    "type": "string",
                    "description": "System directory name.",
                },
                "baseurl": {
                    "type": "string",
                    "description": "Optional. The base URL for API calls (e.g. https://api.example.com). Persisted into onthefly.md.",
                },
                "auth_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. List of auth scheme names (e.g. [\"api_key\"], [\"bearer\"]). Persisted into onthefly.md.",
                },
                "auth_json": {
                    "type": "string",
                    "description": "Optional. A JSON string containing auth credentials (e.g. '{\"access_key\":\"abc\",\"secret_key\":\"xyz\"}'). Saved to .auth.json in the system dir. The return value includes the exact env-var command the user must run.",
                },
            },
            "additionalProperties": False,
        },
    },
    "discover_system": {
        "description": "Discover systems in the workspace under otf_tools/*/onthefly.md.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "get_system_context": {
        "description": "Return onthefly.md, cli.md, swagger.json, and realtime status for a system.",
        "inputSchema": {
            "type": "object",
            "required": ["system"],
            "properties": {
                "system": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "construct_workflow": {
        "description": "Construct a build or reuse Capability Construction Workflow for a command.",
        "inputSchema": {
            "type": "object",
            "required": ["system", "command"],
            "properties": {
                "system": {"type": "string"},
                "command": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "validate_workflow": {
        "description": "Validate a Capability Construction Workflow against schema and current state.",
        "inputSchema": {
            "type": "object",
            "required": ["system", "workflow"],
            "properties": {
                "system": {"type": "string"},
                "workflow": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    "register_cli": {
        "description": "Register a Code Agent validation outcome and update cli.md state.",
        "inputSchema": {
            "type": "object",
            "required": ["system", "command", "outcome", "attempts", "api_version"],
            "properties": {
                "system": {"type": "string"},
                "command": {"type": "string"},
                "outcome": {"type": "string", "enum": ["success", "failure"]},
                "attempts": {"type": "integer", "minimum": 1},
                "failure_category": {
                    "anyOf": [
                        {"type": "string", "enum": ["contract_violation", "api_mismatch", "runtime_error"]},
                        {"type": "null"},
                    ]
                },
                "api_version": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "resolve_escalation": {
        "description": "Resolve a human-review lock and restore a command to stable state.",
        "inputSchema": {
            "type": "object",
            "required": ["system", "command", "resolution_note"],
            "properties": {
                "system": {"type": "string"},
                "command": {"type": "string"},
                "resolution_note": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}


def handle_request(request: Any) -> dict[str, Any] | None:
    
    try:
        ensure_otf_tools()
    except ToolError as exc:
        return error_response(exc.code, exc.message)
    if not isinstance(request, dict):
        return error_response("invalid_args", "Request must be a JSON object")

    if "jsonrpc" in request or "method" in request:
        return handle_mcp_request(request)



    tool = request.get("tool")
    args = request.get("args", {})
    if not isinstance(tool, str) or not tool:
        return error_response("invalid_args", "Request must include a non-empty tool")
    if not isinstance(args, dict):
        return error_response("invalid_args", "Request args must be an object")
    return dispatch(tool, args)


def handle_mcp_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if not isinstance(method, str) or not method:
        return _mcp_error(request_id, -32600, "Invalid MCP request: missing method")

    if method == "tools/call":
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _mcp_error(request_id, -32602, "Invalid MCP params")

        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name:
            return _mcp_error(request_id, -32602, "tools/call requires params.name")
        if not isinstance(arguments, dict):
            return _mcp_error(request_id, -32602, "tools/call params.arguments must be an object")

        result = dispatch(tool_name, arguments)
        is_error = "error" in result
        return _mcp_result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False),
                    }
                ],
                "isError": is_error,
            },
        )

    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _mcp_error(request_id, -32602, "Invalid MCP params")

    if method == "initialize":
        _apply_workspace_from_initialize(params)
        requested_version = params.get("protocolVersion")
        protocol_version = requested_version if isinstance(requested_version, str) else DEFAULT_PROTOCOL_VERSION
        return _mcp_result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _mcp_result(request_id, {})

    if method == "tools/list":
        tools = [
            {
                "name": name,
                "description": TOOL_DEFINITIONS[name]["description"],
                "inputSchema": TOOL_DEFINITIONS[name]["inputSchema"],
            }
            for name in TOOLS
        ]
        return _mcp_result(request_id, {"tools": tools})

    return _mcp_error(request_id, -32601, f"Method not found: {method}")


def _mcp_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _mcp_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _apply_workspace_from_initialize(params: dict[str, Any]) -> None:
    """Extract workspace root from MCP initialize params and apply it.

    Supports both ``rootUri`` (URI format, preferred) and ``rootPath`` (legacy).
    Falls back to ``Path.cwd()`` if neither is present.
    """
    root_uri: str | None = params.get("rootUri") if isinstance(params.get("rootUri"), str) else None
    root_path: str | None = params.get("rootPath") if isinstance(params.get("rootPath"), str) else None

    local_path: str | None = None

    if root_uri is not None:
        # rootUri is a file:// URI, e.g. "file:///d:/some/workspace"
        parsed = urlparse(root_uri)
        if parsed.scheme in ("file", ""):
            # On Windows, parsed.path might be "/d:/some/workspace"
            local_path = unquote(parsed.path)
            # Strip leading slash on Windows drive-letter paths: "/D:/..." -> "D:/..."
            if os.name == "nt" and len(local_path) > 2 and local_path[0] == "/" and local_path[2] == ":":
                local_path = local_path[1:]
    elif root_path is not None:
        local_path = root_path

    if local_path:
        set_workspace_root(local_path)


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except json.JSONDecodeError as exc:
            response = error_response("invalid_args", f"Invalid JSON request: {exc.msg}")
        except Exception as exc:  # Keep protocol clients from seeing broken pipes.
            response = error_response("internal_error", str(exc))

        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
