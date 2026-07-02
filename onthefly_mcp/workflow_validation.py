"""CCW workflow validation helpers."""

from __future__ import annotations

from typing import Any

FAILURE_CATEGORIES = {"contract_violation", "api_mismatch", "runtime_error"}
TOP_LEVEL_REQUIRED = [
    "workflow_type",
    "version",
    "system",
    "goal",
    "context",
    "inputs",
    "constraints",
    "artifact",
    "validation_contract",
    "retry_policy",
    "failure_model",
]


def validate_ccw_schema(workflow: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing_fields: list[str] = []
    warnings: list[str] = []

    for field in TOP_LEVEL_REQUIRED:
        _require_key(workflow, field, missing_fields)

    if workflow.get("workflow_type") != "capability_construction":
        warnings.append("workflow_type must be capability_construction")
    if workflow.get("version") != 1:
        warnings.append("version must be 1")
    if not isinstance(workflow.get("system"), str):
        warnings.append("system must be a string")

    goal = _object_field(workflow, "goal", warnings)
    if goal is not None:
        _require_key(goal, "artifact", missing_fields, "goal.artifact")
        _require_key(goal, "command", missing_fields, "goal.command")
        _require_key(goal, "mode", missing_fields, "goal.mode")
        if goal.get("artifact") != "cli":
            warnings.append("goal.artifact must be cli")
        if not isinstance(goal.get("command"), str):
            warnings.append("goal.command must be a string")
        if goal.get("mode") not in {"build", "reuse"}:
            warnings.append("goal.mode must be build or reuse")

    context = _object_field(workflow, "context", warnings)
    if context is not None:
        _require_key(context, "api_version", missing_fields, "context.api_version")
        if not isinstance(context.get("api_version"), str):
            warnings.append("context.api_version must be a string")

    inputs = workflow.get("inputs")
    if not isinstance(inputs, list):
        warnings.append("inputs must be a list")
    else:
        for index, item in enumerate(inputs):
            if not isinstance(item, dict) or not isinstance(item.get("read"), str):
                warnings.append(f"inputs[{index}] must include a read string")

    constraints = workflow.get("constraints")
    if not isinstance(constraints, list) or not all(isinstance(item, str) for item in constraints):
        warnings.append("constraints must be a string list")

    artifact = _object_field(workflow, "artifact", warnings)
    if artifact is not None:
        _require_key(artifact, "create", missing_fields, "artifact.create")
        if not isinstance(artifact.get("create"), list):
            warnings.append("artifact.create must be a list")
        if "update" in artifact and not isinstance(artifact["update"], list):
            warnings.append("artifact.update must be a list")

    validation_contract = _object_field(workflow, "validation_contract", warnings)
    if validation_contract is not None:
        _require_key(validation_contract, "type", missing_fields, "validation_contract.type")
        _require_key(validation_contract, "steps", missing_fields, "validation_contract.steps")
        if validation_contract.get("type") != "code_agent_execution":
            warnings.append("validation_contract.type must be code_agent_execution")
        steps = validation_contract.get("steps")
        if not isinstance(steps, list) or not steps:
            warnings.append("validation_contract.steps must be a non-empty list")
        else:
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    warnings.append(f"validation_contract.steps[{index}] must be an object")
                    continue
                _require_key(step, "run", missing_fields, f"validation_contract.steps[{index}].run")
                _require_key(step, "expect", missing_fields, f"validation_contract.steps[{index}].expect")
                if not isinstance(step.get("run"), str):
                    warnings.append(f"validation_contract.steps[{index}].run must be a string")
                expect = step.get("expect")
                if not isinstance(expect, dict):
                    warnings.append(f"validation_contract.steps[{index}].expect must be an object")
                elif "exit_code" in expect and not isinstance(expect["exit_code"], int):
                    warnings.append(f"validation_contract.steps[{index}].expect.exit_code must be an integer")

    retry_policy = _object_field(workflow, "retry_policy", warnings)
    if retry_policy is not None:
        _require_key(retry_policy, "max_attempts", missing_fields, "retry_policy.max_attempts")
        if not isinstance(retry_policy.get("max_attempts"), int):
            warnings.append("retry_policy.max_attempts must be an integer")

    failure_model = _object_field(workflow, "failure_model", warnings)
    if failure_model is not None:
        _require_key(failure_model, "categories", missing_fields, "failure_model.categories")
        categories = failure_model.get("categories")
        if not isinstance(categories, list) or not all(item in FAILURE_CATEGORIES for item in categories):
            warnings.append("failure_model.categories must use supported categories")

    return missing_fields, warnings


def _require_key(container: dict[str, Any], key: str, missing_fields: list[str], label: str | None = None) -> None:
    if key not in container:
        missing_fields.append(label or key)


def _object_field(container: dict[str, Any], key: str, warnings: list[str]) -> dict[str, Any] | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        warnings.append(f"{key} must be an object")
        return None
    return value

