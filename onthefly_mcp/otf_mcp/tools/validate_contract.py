"""validate_contract (spec §4.4).

Performs four orthogonal checks against the system on disk:

  1. Workflow conforms to the CCW JSON-Schema (spec §12).
  2. ``cli.md`` does not contain any command whose ``generated_from_api_version``
     mismatches the system's current ``api_version`` (= stale).
  3. ``swagger.json`` exists when ``context.api_version`` is set.
  4. The workflow's CLI artifact path lives under ``otf_tools/<system>/cli/``.

Returns ``{valid, warnings, stale_cli, missing_fields}``; never raises
for ordinary mismatches. The only exception is malformed input, which
the router turns into an ``InvalidParamsError``.
"""
from __future__ import annotations

from typing import Any

from otf_mcp.core import workspace
from otf_mcp.core.schema import CCW_SCHEMA, VALIDATE_CONTRACT_SCHEMA, validate
from otf_mcp.errors import SystemNotFoundError


def handle(args: dict[str, Any]) -> dict[str, Any]:
    errors = validate(args, VALIDATE_CONTRACT_SCHEMA)
    if errors:
        from otf_mcp.errors import InvalidParamsError

        raise InvalidParamsError(
            "validate_contract: bad arguments",
            data={"errors": errors},
        )

    system = args["system"]
    workflow = args["workflow"]

    warnings: list[str] = []
    stale_cli: list[dict[str, Any]] = []
    missing_fields: list[str] = []

    # (0) System existence --------------------------------------------------
    if not workspace.onthefly_file(system).is_file():
        raise SystemNotFoundError(
            f"system not found: {system!r}",
            data={"system": system},
        )

    # (1) Schema check --------------------------------------------------------
    schema_errors = validate(workflow, CCW_SCHEMA)
    if schema_errors:
        warnings.append("workflow_schema_invalid")
        missing_fields.extend(schema_errors)
        # If the schema is broken there's nothing else we can usefully say.
        return {
            "valid": False,
            "warnings": warnings,
            "stale_cli": stale_cli,
            "missing_fields": missing_fields,
        }

    # (2) cli.md staleness ---------------------------------------------------

    cli_text = workspace.read_text(workspace.cli_registry_file(system))
    registry = workspace.parse_cli_registry(system, cli_text)

    current_api_version = str(workflow.get("context", {}).get("api_version") or "")
    if current_api_version:
        for c in registry.commands:
            if c.generated_from_api_version and c.generated_from_api_version != current_api_version:
                stale_cli.append(
                    {
                        "name": c.name,
                        "generated_from_api_version": c.generated_from_api_version,
                        "current_api_version": current_api_version,
                    }
                )

    # (3) swagger.json presence ---------------------------------------------
    swagger_path = workspace.swagger_file(system)
    if not swagger_path.is_file():
        warnings.append("swagger_missing")
        missing_fields.append("otf_tools/{}/swagger.json".format(system))

    # (4) CLI artifact path containment -------------------------------------
    cli_artifact_rel = (workflow.get("artifact") or {}).get("create") or []
    expected_prefix = f"otf_tools/{system}/cli/"
    for path in cli_artifact_rel:
        if not isinstance(path, str) or not path.startswith(expected_prefix):
            warnings.append(f"cli_path_out_of_system: {path!r}")
            missing_fields.append(f"artifact.create must start with {expected_prefix!r}")

    # (5) validation_contract coverage of help + dry-run -------------------
    steps = (workflow.get("validation_contract") or {}).get("steps") or []
    runs = [str(s.get("run", "")) for s in steps if isinstance(s, dict)]
    if not any("--help" in r for r in runs):
        warnings.append("missing_help_validation")
        missing_fields.append("validation_contract.steps[].run containing --help")
    if not any("--dry-run" in r for r in runs):
        warnings.append("missing_dry_run_validation")
        missing_fields.append("validation_contract.steps[].run containing --dry-run")

    valid = not warnings and not stale_cli and not missing_fields
    return {
        "valid": valid,
        "warnings": warnings,
        "stale_cli": stale_cli,
        "missing_fields": missing_fields,
    }
