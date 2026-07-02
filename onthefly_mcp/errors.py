"""Shared error envelope helpers."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Expected tool failure that should be returned as a JSON error envelope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def error_response(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def not_implemented(tool: str) -> dict[str, Any]:
    return error_response("not_implemented", f"Tool is not implemented yet: {tool}")
