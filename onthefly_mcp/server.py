"""JSON-lines stdio server loop for On-the-Fly MCP tools.

The server accepts both the project-local test envelope:

    {"tool": "discover_system", "args": {}}

and the MCP JSON-RPC methods used by real MCP clients:

    initialize, notifications/initialized, tools/list, tools/call
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .errors import error_response
from .tools import TOOLS, dispatch

SERVER_NAME = "on-the-fly"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "discover_system": {
        "description": "Discover systems under otf_tools/*/onthefly.md.",
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
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _mcp_error(request_id, -32602, "Invalid MCP params")

    if method == "initialize":
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

    if method == "tools/call":
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
