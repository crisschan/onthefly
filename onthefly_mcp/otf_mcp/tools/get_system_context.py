"""get_system_context (spec §4.2).

Returns the full text of every artifact a Code Agent might need to read
when constructing a CLI for the system:

  * ``onthefly``  — the markdown source-of-truth document
  * ``cli_registry`` — the cli.md YAML registry (parsed)
  * ``swagger``   — the swagger.json body
  * ``status``    — counts derived from cli.md
"""
from __future__ import annotations

from typing import Any

from otf_mcp.core import workspace
from otf_mcp.core.schema import GET_SYSTEM_CONTEXT_SCHEMA, validate_or_raise
from otf_mcp.errors import SystemNotFoundError


def _system_must_exist(system: str) -> None:
    if not workspace.onthefly_file(system).is_file():
        raise SystemNotFoundError(
            f"system not found: {system!r}",
            data={"system": system, "expected": str(workspace.onthefly_file(system))},
        )


def handle(args: dict[str, Any]) -> dict[str, Any]:
    validate_or_raise(args, GET_SYSTEM_CONTEXT_SCHEMA, what="get_system_context")
    system = args["system"]
    _system_must_exist(system)

    onthefly_text = workspace.read_text(workspace.onthefly_file(system))
    cli_text = workspace.read_text(workspace.cli_registry_file(system))
    registry = workspace.parse_cli_registry(system, cli_text)
    swagger_text = workspace.read_text(workspace.swagger_file(system))

    return {
        "system": system,
        "onthefly": onthefly_text,
        "cli_registry": registry.to_dict(),
        "swagger": swagger_text,
        "status": {
            "cli_count": len(registry.commands),
            "stale_count": registry.stale_count,
        },
    }
