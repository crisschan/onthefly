"""Tool router and placeholder handlers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import yaml

from .errors import ToolError, error_response, not_implemented
from .state import (
    current_api_version,
    extract_api_version,
    extract_baseurl,
    extract_auth_types,
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
from .workspace import otf_root, workspace_root, set_workspace_root
from .workflow_validation import validate_ccw_schema

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
MAX_ATTEMPTS = 3
FAILURE_CATEGORIES = {"contract_violation", "api_mismatch", "runtime_error"}


def init_system(args: dict[str, Any]) -> dict[str, Any]:
    """Bootstrap a new system from an existing swagger.json inside the workspace.

    ``output_dir`` (optional) specifies where ``otf_tools/<system>/`` is created.
    When omitted, the output directory defaults to ``<swagger.json parent>/otf_tools/``.
    """

    system = validate_name(args.get("system"), "system")

    swagger_path = args.get("swagger_path")
    if not isinstance(swagger_path, str) or not swagger_path:
        raise ToolError("invalid_args", "swagger_path must be a non-empty string")

    output_dir = args.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        raise ToolError("invalid_args", "output_dir must be a string if provided")

    base_url = args.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise ToolError("invalid_args", "base_url must be a string if provided")

    name = args.get("name")
    tags = args.get("tags")
    description = args.get("description")

    if name is not None and not isinstance(name, str):
        raise ToolError("invalid_args", "name must be a string")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ToolError("invalid_args", "tags must be a list of strings")
    if description is not None and not isinstance(description, str):
        raise ToolError("invalid_args", "description must be a string")

    # 1. Resolve swagger_path
    source_file = (workspace_root() / swagger_path).resolve()
    if not source_file.is_file():
        raise ToolError("invalid_args", f"swagger_path not found: {swagger_path}")

    # 2. Determine system output directory and sync workspace root
    if output_dir:
        out_root = Path(output_dir).resolve()
        system_dir = out_root / system
        # Ensure subsequent tools (construct_workflow, register_cli, etc.)
        # can discover and operate on systems created under this output_dir.
        set_workspace_root(out_root.parent)
    else:
        # Legacy: otf_tools/ as sibling of swagger.json
        set_workspace_root(source_file.parent)
        system_dir = otf_root() / system

    # 3. Reject if system directory already exists
    if system_dir.is_dir():
        raise ToolError("system_already_exists", f"System already exists: {system}")

    # 4. Create directory skeleton
    system_dir.mkdir(parents=True)
    (system_dir / "cli").mkdir(parents=True)

    # 5. Copy swagger.json and extract api_version
    try:
        source_raw = source_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError("invalid_args", f"Failed to read swagger_path: {swagger_path}") from exc

    try:
        swagger_parsed = json.loads(source_raw)
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_args", f"swagger_path is not valid JSON: {swagger_path}") from exc

    if not isinstance(swagger_parsed, dict):
        raise ToolError("invalid_args", f"swagger_path must be a JSON object: {swagger_path}")

    api_version = extract_api_version(swagger_parsed)

    # Extract base URL and auth types from swagger
    detected_baseurl = extract_baseurl(swagger_parsed)
    auth_types = extract_auth_types(swagger_parsed)

    (system_dir / "swagger.json").write_text(source_raw, encoding="utf-8")

    # 6. Generate onthefly.md
    onthefly_data: dict[str, Any] = {
        "system": system,
        "name": name if name else system,
        "api_version": api_version,
    }
    # base_url priority: explicit arg > swagger-extracted > absent
    effective_baseurl = base_url or detected_baseurl
    if effective_baseurl:
        onthefly_data["baseurl"] = effective_baseurl
    if auth_types:
        onthefly_data["auth_types"] = auth_types
    if tags is not None:
        onthefly_data["tags"] = tags
    if description:
        onthefly_data["description"] = description

    (system_dir / "onthefly.md").write_text(
        yaml.safe_dump(onthefly_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # 7. Generate empty cli.md
    (system_dir / "cli.md").write_text(
        yaml.safe_dump({"commands": []}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # 8. Detect which config items are still missing and need human input
    missing_config: list[str] = []
    if not effective_baseurl:
        missing_config.append("baseurl")
    if not auth_types:
        missing_config.append("auth")

    result: dict[str, Any] = {
        "system": system,
        "created": True,
        "path": str(system_dir.relative_to(workspace_root())) if not output_dir else str(system_dir),
    }

    if missing_config:
        result["missing_config"] = missing_config
        result["hint"] = (
            "The swagger.json did not contain baseurl and/or auth information. "
            "Call configure_system to provide them. "
            "Example: base_url must be set for API calls; "
            "auth credentials will be stored and you will be instructed to set the OTF_{SYSTEM} environment variable."
        ).replace("{SYSTEM}", system.upper().replace("-", "_"))

    return result


def configure_system(args: dict[str, Any]) -> dict[str, Any]:
    """Configure a system with baseurl and/or auth credentials provided by the user.

    ``baseurl`` is written into onthefly.md.
    ``auth_json`` is written to a ``.auth.json`` file in the system directory
    as a reference, and the return value includes the exact command the user
    must run to set the corresponding ``OTF_<SYSTEM>`` environment variable.
    ``auth_types`` is also persisted into onthefly.md for workflow generation.
    """
    system = validate_name(args.get("system"), "system")
    system_dir = get_system_dir(system)

    baseurl = args.get("baseurl")
    auth_json = args.get("auth_json")
    auth_types = args.get("auth_types")

    if baseurl is not None and not isinstance(baseurl, str):
        raise ToolError("invalid_args", "baseurl must be a string if provided")
    if auth_json is not None and not isinstance(auth_json, str):
        raise ToolError("invalid_args", "auth_json must be a JSON string if provided")
    if auth_types is not None and (not isinstance(auth_types, list) or not all(isinstance(t, str) for t in auth_types)):
        raise ToolError("invalid_args", "auth_types must be a list of strings if provided")

    if baseurl is None and auth_json is None and auth_types is None:
        raise ToolError("invalid_args", "At least one of baseurl, auth_json, or auth_types must be provided")

    env_var_name = _auth_env_var(system)
    result: dict[str, Any] = {"system": system, "updated": []}

    # 1. Update onthefly.md
    onthefly_path = system_dir / "onthefly.md"
    onthefly_data = parse_onthefly_summary(onthefly_path)

    if baseurl:
        onthefly_data["baseurl"] = baseurl
        result["updated"].append("baseurl")

    if auth_types:
        onthefly_data["auth_types"] = auth_types
        result["updated"].append("auth_types")

    # Write back onthefly.md preserving all fields we read
    onthefly_path.write_text(
        yaml.safe_dump(onthefly_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # 2. Store auth credentials
    if auth_json:
        # Validate it's parseable JSON
        try:
            json.loads(auth_json)
        except json.JSONDecodeError as exc:
            raise ToolError("invalid_args", f"auth_json is not valid JSON: {exc}") from exc

        auth_file = system_dir / ".auth.json"
        auth_file.write_text(auth_json, encoding="utf-8")
        result["updated"].append("auth_json")
        result["auth_file"] = str(auth_file.relative_to(workspace_root()))
        result["env_var_instruction"] = (
            f"Run this command in your terminal to set the auth environment variable:\n"
            f"  set {env_var_name}={auth_json}       (Windows CMD)\n"
            f"  $env:{env_var_name}='{auth_json}'    (Windows PowerShell)\n"
            f"  export {env_var_name}='{auth_json}'  (Linux / macOS)\n"
            f"Or add it to your shell profile / system environment variables for persistence."
        )

    return result


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

    # Read baseurl and auth_types from onthefly.md
    onthefly_data = parse_onthefly_summary(system_dir / "onthefly.md")
    baseurl = onthefly_data.get("baseurl", "")

    return _build_ccw(system, command, swagger_version, mode, baseurl, str(workspace_root()))


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
        # Verify the CLI artifact actually exists on disk
        cli_file_path = system_dir / entry.get("cli", f"cli/{command}.py")
        if not cli_file_path.is_file():
            raise ToolError(
                "artifact_not_found",
                f"Cannot register success: CLI file does not exist at {cli_file_path}. "
                f"You MUST create the file before calling register_cli with outcome=success.",
            )
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
    "init_system": init_system,
    "configure_system": configure_system,
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


def _auth_env_var(system: str) -> str:
    """Compute the auth env-var name for a system: OTF_{SYSTEM_UPPER}.

    Hyphens in the system name are replaced with underscores.
    """
    return "OTF_" + system.upper().replace("-", "_")


_LOAD_CREDENTIALS_TEMPLATE = """\
def load_credentials(system: str) -> dict:
    \"\"\"
    统一的凭证读取入口。环境变量 OTF_{SYSTEM} 的值为 JSON 字符串，
    内部字段由该 system 的鉴权方式决定（如 app_key，或 access_key + secret_key）。
    所有 CLI 必须通过本函数读取凭证，禁止直接调用 os.environ 后自行拼接解析逻辑。
    \"\"\"
    env_name = f"OTF_{system.upper()}"
    raw = os.environ.get(env_name)
    if raw is None:
        print(f"missing credential env var: {env_name}", file=sys.stderr)
        sys.exit(3)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"invalid JSON in {env_name}: {e}", file=sys.stderr)
        sys.exit(3)\
"""


def _build_ccw(system: str, command: str, api_version: str, mode: str, baseurl: str = "", workspace_root_str: str = "") -> dict[str, Any]:
    # Derive all paths relative to workspace_root from otf_root() instead of hardcoding "otf_tools/"
    ws_root = Path(workspace_root_str) if workspace_root_str else workspace_root()
    system_dir_rel = str((otf_root() / system).relative_to(ws_root))

    cli_path = f"{system_dir_rel}/cli/{command}.py"
    cli_md_path = f"{system_dir_rel}/cli.md"
    onthefly_md_path = f"{system_dir_rel}/onthefly.md"
    swagger_path = f"{system_dir_rel}/swagger.json"

    create = [cli_path] if mode == "build" else []
    update = [cli_md_path] if mode == "build" else []

    context: dict[str, Any] = {"api_version": api_version, "workspace_root": workspace_root_str}
    if baseurl:
        context["baseurl"] = baseurl

    artifact_paths_md = "\n".join(f"  - {p}" for p in create + update)
    return {
        "workflow_type": "capability_construction",
        "version": 1,
        "system": system,
        "goal": {
            "artifact": "cli",
            "command": command,
            "mode": mode,
        },
        "context": context,
        "auth": {
            "env_var": _auth_env_var(system),
            "format": "json",
            "error_exit_code": 3,
            "helper_template": _LOAD_CREDENTIALS_TEMPLATE,
            "rule": "Every CLI MUST include the helper_template function verbatim. "
            "Read credentials exclusively via load_credentials(system). "
            "Never call os.environ directly for auth values. "
            "No CLI argument for tokens or secrets.",
        },
        "inputs": [
            {"read": onthefly_md_path},
            {"read": swagger_path},
            {"read": cli_md_path},
        ],
        "constraints": [
            "cli_spec_v1",
            "argparse",
            "stdout_json",
            "stderr_logs",
            "exit_code_contract",
            "env_auth_only",
            "auth_json_format",
            "use_load_credentials",
        ],
        "artifact": {
            "create": create,
            "update": update,
        },
        "validation_contract": {
            "type": "code_agent_execution",
            "working_directory": workspace_root_str,
            "steps": [
                {
                    "run": f"python {cli_path} --help",
                    "expect": {
                        "exit_code": 0,
                    },
                },
                {
                    "run": f"python {cli_path} --dry-run",
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
        "mandate": {
            "summary": (
                f"YOU MUST create/update exactly these files under workspace_root={workspace_root_str}:\n"
                f"{artifact_paths_md}\n"
                "Do NOT write one-off scripts, do NOT delete files after testing, do NOT use a different path."
            ),
            "steps": [
                (
                    f"1. Read the three inputs (onthefly.md for config, swagger.json for API schema, "
                    f"cli.md for command registry) to understand the system."
                ),
                (
                    f"2. Write the CLI file to {cli_path}. "
                    f"Use argparse, embed load_credentials(), output JSON to stdout, logs to stderr."
                ),
                (f"3. Update {cli_md_path} to register the new command entry."),
                (
                    f"4. Run validation: python {cli_path} --help (expect exit 0), "
                    f"then python {cli_path} --dry-run (expect exit 0 with JSON)."
                ),
                (
                    f"5. Call register_cli with outcome=success AND the actual api_version. "
                    f"The file MUST exist on disk at {cli_path} before calling register_cli."
                ),
            ],
        },
    }
