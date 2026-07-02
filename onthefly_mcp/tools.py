"""Tool router and placeholder handlers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .errors import ToolError, error_response, not_implemented
from .state import (
    current_api_version,
    find_command,
    get_system_dir,
    is_command_stale,
    load_cli_registry,
    load_swagger,
    parse_onthefly_summary,
    read_text,
    registry_status,
    validate_name,
    write_cli_registry,
)
from .workspace import ensure_otf_tools, file_lock, otf_root
from .workflow_validation import validate_ccw_schema

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
MAX_ATTEMPTS = 3
FAILURE_CATEGORIES = {"contract_violation", "api_mismatch", "runtime_error"}


def discover_system(args: dict[str, Any]) -> dict[str, Any]:
    if args:
        return error_response("invalid_args", "discover_system does not accept arguments")

    systems = []
    for system_dir in sorted(path for path in otf_root().iterdir() if path.is_dir()):
        if system_dir.name == ".otf":
            continue
        onthefly_path = system_dir / "onthefly.md"
        if not onthefly_path.exists():
            continue
        systems.append(parse_onthefly_summary(onthefly_path))

    return {"systems": systems}


def get_system_context(args: dict[str, Any]) -> dict[str, Any]:
    system = validate_name(args.get("system"), "system")
    system_dir = get_system_dir(system)

    onthefly_path = system_dir / "onthefly.md"
    if not onthefly_path.exists():
        raise ToolError("corrupted_state", f"Missing onthefly.md for system: {system}")
    onthefly_raw = read_text(onthefly_path, "onthefly.md")

    cli_raw, cli_registry = load_cli_registry(system_dir, system)
    swagger_raw, swagger = load_swagger(system_dir)
    swagger_version = current_api_version(swagger)

    return {
        "system": system,
        "onthefly": onthefly_raw,
        "cli_registry": cli_raw,
        "swagger": swagger_raw,
        "status": registry_status(cli_registry, swagger_version),
    }


def construct_workflow(args: dict[str, Any]) -> dict[str, Any]:
    system = validate_name(args.get("system"), "system")
    command = validate_name(args.get("command"), "command")
    system_dir = get_system_dir(system)

    _, swagger = load_swagger(system_dir)
    swagger_version = current_api_version(swagger)
    _, cli_registry = load_cli_registry(system_dir, system)
    command_entry = find_command(cli_registry, command)

    if command_entry and command_entry.get("needs_human_review") is True:
        raise ToolError(
            "blocked_pending_human_review",
            f"Command is blocked pending human review: {system}.{command}",
        )

    mode = "build"
    if (
        command_entry
        and command_entry.get("status") == "stable"
        and not is_command_stale(command_entry, swagger_version)
    ):
        mode = "reuse"

    return _build_ccw(system, command, swagger_version, mode)


def validate_workflow(args: dict[str, Any]) -> dict[str, Any]:
    system = validate_name(args.get("system"), "system")
    workflow = args.get("workflow")
    if not isinstance(workflow, dict):
        raise ToolError("invalid_args", "workflow must be an object")

    system_dir = get_system_dir(system)
    _, swagger = load_swagger(system_dir)
    swagger_version = current_api_version(swagger)
    _, cli_registry = load_cli_registry(system_dir, system)

    missing_fields, warnings = validate_ccw_schema(workflow)
    stale_cli = []

    if workflow.get("system") != system:
        warnings.append("workflow.system must match input system")

    workflow_api_version = workflow.get("context", {}).get("api_version") if isinstance(workflow.get("context"), dict) else None
    if workflow_api_version != swagger_version:
        warnings.append("workflow context api_version does not match current swagger version")

    goal = workflow.get("goal")
    command_name = goal.get("command") if isinstance(goal, dict) else None
    if isinstance(command_name, str):
        command_entry = find_command(cli_registry, command_name)
        if command_entry and is_command_stale(command_entry, swagger_version):
            stale_cli.append(command_name)

    return {
        "valid": not missing_fields and not warnings and not stale_cli,
        "warnings": warnings,
        "stale_cli": stale_cli,
        "missing_fields": missing_fields,
    }


def register_cli(args: dict[str, Any]) -> dict[str, Any]:
    system = validate_name(args.get("system"), "system")
    command = validate_name(args.get("command"), "command")
    outcome = args.get("outcome")
    attempts = args.get("attempts")
    failure_category = args.get("failure_category")
    api_version = args.get("api_version")

    if outcome not in {"success", "failure"}:
        raise ToolError("invalid_args", "outcome must be success or failure")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ToolError("invalid_args", "attempts must be a positive integer")
    if failure_category is not None and failure_category not in FAILURE_CATEGORIES:
        raise ToolError("invalid_args", "failure_category is not supported")
    if not isinstance(api_version, str) or not api_version:
        raise ToolError("invalid_args", "api_version must be a non-empty string")

    system_dir = get_system_dir(system)
    with file_lock(system_dir / ".cli.md.lock"):
        _, cli_registry = load_cli_registry(system_dir, system)
        entry = find_command(cli_registry, command)
        if entry is None:
            entry = {
                "name": command,
                "cli": f"cli/{command}.py",
                "history": [],
            }
            cli_registry["commands"].append(entry)

        if outcome == "success":
            status = "stable"
            needs_human_review = False
        elif attempts < MAX_ATTEMPTS:
            status = "draft"
            needs_human_review = False
        else:
            status = "draft"
            needs_human_review = True

        entry["status"] = status
        entry["generated_from_api_version"] = api_version
        entry["needs_human_review"] = needs_human_review
        history = _history_list(entry)
        history.append(
            {
                "timestamp": _utc_now(),
                "outcome": outcome,
                "attempts": attempts,
                "failure_category": failure_category,
                "source": "machine",
            }
        )
        entry["history"] = history
        write_cli_registry(system_dir, cli_registry)

    return {
        "system": system,
        "command": command,
        "status": status,
        "needs_human_review": needs_human_review,
    }


def resolve_escalation(args: dict[str, Any]) -> dict[str, Any]:
    system = validate_name(args.get("system"), "system")
    command = validate_name(args.get("command"), "command")
    resolution_note = args.get("resolution_note")
    if not isinstance(resolution_note, str) or not resolution_note.strip():
        raise ToolError("invalid_args", "resolution_note must be a non-empty string")

    system_dir = get_system_dir(system)
    _, swagger = load_swagger(system_dir)
    resolved_at = _utc_now()

    with file_lock(system_dir / ".cli.md.lock"):
        _, cli_registry = load_cli_registry(system_dir, system)
        entry = find_command(cli_registry, command)
        if entry is None:
            raise ToolError("command_not_found", f"Command not found: {command}")

        entry["status"] = "stable"
        entry["generated_from_api_version"] = current_api_version(swagger)
        entry["needs_human_review"] = False
        history = _history_list(entry)
        history.append(
            {
                "timestamp": resolved_at,
                "outcome": "success",
                "resolution_note": resolution_note,
                "source": "human",
            }
        )
        entry["history"] = history
        write_cli_registry(system_dir, cli_registry)

    return {
        "system": system,
        "command": command,
        "status": "stable",
        "resolved_by": "human",
        "resolved_at": resolved_at,
    }


TOOLS: dict[str, ToolHandler] = {
    "discover_system": discover_system,
    "get_system_context": get_system_context,
    "construct_workflow": construct_workflow,
    "validate_workflow": validate_workflow,
    "register_cli": register_cli,
    "resolve_escalation": resolve_escalation,
}


def dispatch(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOLS.get(tool)
    if handler is None:
        return error_response("invalid_args", f"Unknown tool: {tool}")
    try:
        ensure_otf_tools()
        return handler(args)
    except ToolError as exc:
        return error_response(exc.code, exc.message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _history_list(entry: dict[str, Any]) -> list[dict[str, Any]]:
    history = entry.get("history", [])
    if history is None:
        history = []
    if not isinstance(history, list):
        raise ToolError("corrupted_state", "cli.md history must be a list")
    return history


def _build_ccw(system: str, command: str, api_version: str, mode: str) -> dict[str, Any]:
    cli_path = f"otf_tools/{system}/cli/{command}.py"
    create = [cli_path] if mode == "build" else []
    update = [f"otf_tools/{system}/cli.md"] if mode == "build" else []

    return {
        "workflow_type": "capability_construction",
        "version": 1,
        "system": system,
        "goal": {
            "artifact": "cli",
            "command": command,
            "mode": mode,
        },
        "context": {
            "api_version": api_version,
        },
        "inputs": [
            {"read": f"otf_tools/{system}/onthefly.md"},
            {"read": f"otf_tools/{system}/swagger.json"},
            {"read": f"otf_tools/{system}/cli.md"},
        ],
        "constraints": [
            "cli_spec_v1",
            "argparse",
            "stdout_json",
            "stderr_logs",
            "exit_code_contract",
        ],
        "artifact": {
            "create": create,
            "update": update,
        },
        "validation_contract": {
            "type": "code_agent_execution",
            "steps": [
                {
                    "run": f"python cli/{command}.py --help",
                    "expect": {
                        "exit_code": 0,
                    },
                },
                {
                    "run": f"python cli/{command}.py --dry-run",
                    "expect": {
                        "exit_code": 0,
                        "stdout_json": True,
                    },
                },
            ],
        },
        "retry_policy": {
            "max_attempts": MAX_ATTEMPTS,
        },
        "failure_model": {
            "categories": sorted(FAILURE_CATEGORIES),
        },
    }
