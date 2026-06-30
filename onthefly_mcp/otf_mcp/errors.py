"""Typed errors for the On-the-Fly MCP server.

The protocol layer wraps everything as JSON-RPC 2.0; these classes are
raised inside tools and translated by :mod:`otf_mcp.server` into
JSON-RPC error objects.
"""
from __future__ import annotations

from typing import Any


class OtfError(Exception):
    """Base class for On-the-Fly MCP errors.

    Subclasses set a stable ``code`` that is propagated to the JSON-RPC
    ``error.code`` field so callers can branch on the failure category
    instead of parsing strings.
    """

    code: int = -32000  # JSON-RPC "server error" range
    message: str = "On-the-Fly server error"

    def __init__(self, message: str | None = None, *, data: Any = None) -> None:
        if message is not None:
            self.message = message
        self.data = data
        super().__init__(self.message)

    def to_rpc(self) -> dict[str, Any]:
        """Serialize to a JSON-RPC 2.0 ``error`` object."""
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class ProtocolError(OtfError):
    """Malformed input that violates the JSON-RPC envelope."""

    code = -32600  # JSON-RPC Invalid Request
    message = "Invalid Request"


class MethodNotFoundError(OtfError):
    """Tool name is not registered with the router."""

    code = -32601  # JSON-RPC Method not found
    message = "Method not found"


class InvalidParamsError(OtfError):
    """Tool arguments fail schema/validation."""

    code = -32602  # JSON-RPC Invalid params
    message = "Invalid params"


class InternalError(OtfError):
    """Unexpected server failure (treated as a bug)."""

    code = -32603  # JSON-RPC Internal error
    message = "Internal error"


class SystemNotFoundError(OtfError):
    """Referenced system has no ``onthefly.md`` under ``otf_tools/``."""

    code = -32001
    message = "system not found"


class ContractViolationError(OtfError):
    """CCW workflow failed validation in :func:`validate_contract`."""

    code = -32002
    message = "contract violation"
