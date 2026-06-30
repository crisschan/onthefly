"""construct_workflow (spec §4.3) — the CCW generator.

The output is a *Capability Construction Workflow* (CCW): a strictly
structured contract that tells a Code Agent exactly which files to
read, which files to create/update, which commands to run for
validation, and how to classify failures. It contains no natural
language instructions, no prompts, no execution-order hints.
"""
from __future__ import annotations

from typing import Any

from otf_mcp.core import workspace
from otf_mcp.core.schema import CONSTRUCT_WORKFLOW_SCHEMA, validate_or_raise
from otf_mcp.errors import InvalidParamsError, SystemNotFoundError


# Constraints are spec §5 / §14 distilled into a fixed list. They are
# ordered so that the most "load-bearing" rules come first — that way
# a Code Agent reading the list top-to-bottom sees the highest-priority
# rules immediately.
DEFAULT_CONSTRAINTS: list[str] = [
    "cli_spec_v1",
    "argparse",
    "stdout_json",
    "stderr_logs",
    "exit_code_contract",
    "support_help",
    "support_dry_run",
    "no_print_to_stdout",
    "no_hardcoded_tokens",
    "no_global_state",
]

# Failure categories from spec §13.
DEFAULT_FAILURE_CATEGORIES: list[str] = [
    "param_error",
    "contract_violation",
    "api_mismatch",
    "runtime_error",
]

# Input files a Code Agent must read to honour spec §10 step 1.
STANDARD_INPUTS: list[dict[str, str]] = [
    {"read": "otf_tools/{system}/onthefly.md"},
    {"read": "otf_tools/{system}/swagger.json"},
    {"read": "otf_tools/{system}/cli.md"},
]


def _command_name_must_be_safe(command: str) -> None:
    """Command names map to file names on disk.

    We reject anything that isn't a valid Python identifier so the
    generated CLI path (``cli/<command>.py``) can never escape the
    intended directory.
    """
    if not command or not command.replace("_", "").isalnum():
        raise InvalidParamsError(
            f"invalid command name: {command!r}",
            data={"hint": "use snake_case alphanumerics + underscore"},
        )
    if command[0].isdigit():
        raise InvalidParamsError(
            f"command name must not start with a digit: {command!r}",
        )


def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Generate a CCW for one CLI command of one system."""
    validate_or_raise(args, CONSTRUCT_WORKFLOW_SCHEMA, what="construct_workflow")
    system = args["system"]
    command = args["command"]
    max_attempts = int(args.get("max_attempts", 3))

    _command_name_must_be_safe(command)
    if not workspace.onthefly_file(system).is_file():
        raise SystemNotFoundError(
            f"system not found: {system!r}",
            data={"system": system},
        )

    doc = workspace.read_onthefly(system)
    api_version = doc.frontmatter.api_version if doc.frontmatter else ""

    cli_rel_path = f"otf_tools/{system}/cli/{command}.py"

    workflow: dict[str, Any] = {
        "workflow_type": "capability_construction",
        "version": 1,
        "system": system,
        "goal": {
            "artifact": "cli",
            "command": command,
        },
        "context": {
            "api_version": api_version,
        },
        "inputs": [
            {"read": tpl["read"].format(system=system)} for tpl in STANDARD_INPUTS
        ],
        "constraints": list(DEFAULT_CONSTRAINTS),
        "artifact": {
            "create": [cli_rel_path],
            "update": [f"otf_tools/{system}/cli.md"],
        },
        "validation_contract": {
            "type": "code_agent_execution",
            "steps": [
                {
                    "run": f"python cli/{command}.py --help",
                    "expect": {"exit_code": 0},
                },
                {
                    "run": f"python cli/{command}.py --dry-run",
                    "expect": {"exit_code": 0, "stdout_json": True},
                },
            ],
        },
        "retry_policy": {"max_attempts": max_attempts},
        "failure_model": {"categories": list(DEFAULT_FAILURE_CATEGORIES)},
    }

    return {"workflow": workflow}
