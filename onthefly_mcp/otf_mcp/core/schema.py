"""CCW (Capability Construction Workflow) schema.

We don't depend on ``jsonschema`` (the environment can't reach PyPI), so
this module implements just enough of JSON-Schema's *draft-07* subset to
validate a CCW document against the contract in spec §12.

This is intentionally a tiny validator:
  * ``type`` is enforced for: object, array, string, integer, boolean.
  * ``required`` lists are checked.
  * ``properties`` allow extra keys (no ``additionalProperties: false``).
  * ``enum`` and ``const`` are checked.
  * ``items`` validates every array element.

That is sufficient for the CCW contract, which uses no ``oneOf``,
``allOf``, ``anyOf``, ``$ref``, or pattern keywords.
"""
from __future__ import annotations

from typing import Any

from otf_mcp.errors import InvalidParamsError


# ---------------------------------------------------------------------------
# CCW v1 schema (spec §12)
# ---------------------------------------------------------------------------

CCW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
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
    ],
    "properties": {
        "workflow_type": {"type": "string", "const": "capability_construction"},
        "version": {"type": "integer", "const": 1},
        "system": {"type": "string"},
        "goal": {
            "type": "object",
            "required": ["artifact", "command"],
            "properties": {
                "artifact": {"type": "string", "enum": ["cli"]},
                "command": {"type": "string"},
            },
        },
        "context": {
            "type": "object",
            "required": ["api_version"],
            "properties": {"api_version": {"type": "string"}},
        },
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["read"],
                "properties": {"read": {"type": "string"}},
            },
        },
        "constraints": {"type": "array", "items": {"type": "string"}},
        "artifact": {
            "type": "object",
            "required": ["create"],
            "properties": {
                "create": {"type": "array", "items": {"type": "string"}},
                "update": {"type": "array", "items": {"type": "string"}},
            },
        },
        "validation_contract": {
            "type": "object",
            "required": ["type", "steps"],
            "properties": {
                "type": {"type": "string", "enum": ["code_agent_execution"]},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["run", "expect"],
                        "properties": {
                            "run": {"type": "string"},
                            "expect": {
                                "type": "object",
                                "properties": {
                                    "exit_code": {"type": "integer"},
                                    "stdout_json": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
            },
        },
        "retry_policy": {
            "type": "object",
            "required": ["max_attempts"],
            "properties": {"max_attempts": {"type": "integer"}},
        },
        "failure_model": {
            "type": "object",
            "required": ["categories"],
            "properties": {"categories": {"type": "array", "items": {"type": "string"}}},
        },
    },
}


# ---------------------------------------------------------------------------
# Tool argument schemas (used to validate inputs before dispatch)
# ---------------------------------------------------------------------------

EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

GET_SYSTEM_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["system"],
    "properties": {"system": {"type": "string"}},
}

CONSTRUCT_WORKFLOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["system", "command"],
    "properties": {
        "system": {"type": "string"},
        "command": {"type": "string"},
        # Optional overrides for callers that want to tweak defaults
        # without rebuilding the whole workflow.
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 10},
    },
}

VALIDATE_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["system", "workflow"],
    "properties": {
        "system": {"type": "string"},
        "workflow": {"type": "object"},
    },
}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
}


def _check_type(value: Any, expected: str) -> str | None:
    """Return a JSON-pointer-ish error string or ``None`` if ok.

    ``bool`` is a subclass of ``int`` in Python, so we special-case it
    before checking ``integer``.
    """
    py_type = _TYPE_MAP.get(expected)
    if py_type is None:
        return f"unknown schema type: {expected}"
    if expected == "integer" and isinstance(value, bool):
        return f"expected integer, got boolean"
    if expected == "boolean" and not isinstance(value, bool):
        return f"expected boolean, got {type(value).__name__}"
    if not isinstance(value, py_type):
        return f"expected {expected}, got {type(value).__name__}"
    return None


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``value`` against ``schema``; return list of error strings.

    An empty list means the value is valid. Errors are reported with
    dotted JSON-pointer-ish paths (``$.foo.bar``) so the caller can
    surface them verbatim in an :class:`InvalidParamsError`.
    """
    errors: list[str] = []

    schema_type = schema.get("type")
    if schema_type is not None:
        err = _check_type(value, schema_type)
        if err is not None:
            errors.append(f"{path}: {err}")
            return errors  # no point descending further

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in enum {schema['enum']!r}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value {value!r} != const {schema['const']!r}")

    if schema_type == "object":
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for k in value.keys():
                if k not in allowed:
                    errors.append(f"{path}.{k}: additional property not allowed")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errors.extend(validate(value[k], sub, f"{path}.{k}"))

    elif schema_type == "array":
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(value):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    return errors


def validate_or_raise(value: Any, schema: dict[str, Any], *, what: str) -> None:
    """Validate and convert the first error into an :class:`InvalidParamsError`."""
    errors = validate(value, schema)
    if errors:
        raise InvalidParamsError(
            f"{what} failed schema validation",
            data={"errors": errors, "schema_id": schema.get("title") or what},
        )
