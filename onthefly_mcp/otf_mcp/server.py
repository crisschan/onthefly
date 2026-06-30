"""Top-level MCP server: router + stdio loop.

Public surface:
  * :class:`OtfServer` — high-level, in-process dispatcher (used by tests).
  * :func:`run_stdio` — production entry point that reads MCP-framed
    JSON-RPC messages from stdin and writes responses to stdout.

The router is a thin map of ``tool name → callable(args) -> dict``. The
real validation lives in each tool function so that errors surface
through the same :class:`OtfError` hierarchy.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from otf_mcp import protocol
from otf_mcp.core import workspace
from otf_mcp.errors import (
    InternalError,
    InvalidParamsError,
    MethodNotFoundError,
    OtfError,
    ProtocolError,
)
from otf_mcp.tools import (
    construct_workflow as tw_construct_workflow,
)
from otf_mcp.tools import (
    discover_system as tw_discover_system,
)
from otf_mcp.tools import (
    get_system_context as tw_get_system_context,
)
from otf_mcp.tools import (
    validate_contract as tw_validate_contract,
)

log = logging.getLogger("otf_mcp.server")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


class OtfServer:
    """In-process dispatcher for the On-the-Fly MCP server.

    Why an explicit class instead of a bare dict?

    * It owns the *single* ``otf_tools/`` init flag, so we honour
      spec §7: init runs on the first tool call, not at start-up.
    * It owns the ``initialized`` flag for the MCP lifecycle.
    * It's trivially testable: feed a list of messages, get a list
      of responses.
    """

    def __init__(self) -> None:
        self._initialized = False
        self._otf_initialized = False
        self._tools: dict[str, ToolFn] = {
            "discover_system": tw_discover_system.handle,
            "get_system_context": tw_get_system_context.handle,
            "construct_workflow": tw_construct_workflow.handle,
            "validate_contract": tw_validate_contract.handle,
        }

    # -- introspection ---------------------------------------------------

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    # -- lifecycle -------------------------------------------------------

    def _ensure_otf(self) -> None:
        """Spec §7: init otf_tools/ on the first tool call."""
        if self._otf_initialized:
            return
        workspace.ensure_otf_tools()
        self._otf_initialized = True

    # -- request dispatch ----------------------------------------------

    def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Process one incoming message; return a response or ``None``.

        Returns ``None`` for notifications (which never get a reply)
        and for malformed envelopes where there's no ``id`` to reply to.
        """
        if not isinstance(msg, dict):
            return protocol.make_error(None, ProtocolError.code, "request must be an object")

        # JSON-RPC envelope sanity checks
        if "jsonrpc" in msg and msg["jsonrpc"] != protocol.JSONRPC_VERSION:
            return protocol.make_error(
                msg.get("id"),
                ProtocolError.code,
                f"unsupported jsonrpc version: {msg.get('jsonrpc')!r}",
            )

        method = msg.get("method")
        if not isinstance(method, str) or not method:
            return protocol.make_error(
                msg.get("id"),
                ProtocolError.code,
                "missing or invalid 'method'",
            )

        req_id = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return protocol.make_error(
                req_id,
                InvalidParamsError.code,
                "'params' must be an object",
            )

        # MCP lifecycle methods
        if method == "initialize":
            self._initialized = True
            return protocol.make_result(req_id, protocol.server_capabilities())

        if method == "ping":
            return protocol.make_result(req_id, {"pong": True})

        if method == "tools/list":
            return protocol.make_result(req_id, {"tools": protocol.server_capabilities()["tools"]})

        if method == "tools/call":
            return self._handle_tool_call(req_id, params)

        # Anything else is unknown at the protocol layer
        if method.startswith("notifications/"):
            # unknown notification — accept silently
            return None
        return protocol.make_error(req_id, MethodNotFoundError.code, f"unknown method: {method}")

    def _handle_tool_call(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return protocol.make_error(req_id, InvalidParamsError.code, "missing tool 'name'")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return protocol.make_error(req_id, InvalidParamsError.code, "'arguments' must be an object")

        # Lazy init of otf_tools/ on first tool call (spec §7)
        try:
            self._ensure_otf()
        except OtfError as exc:
            return protocol.make_error(req_id, exc.code, exc.message, exc.data)

        fn = self._tools.get(name)
        if fn is None:
            return protocol.make_error(req_id, MethodNotFoundError.code, f"unknown tool: {name}")

        try:
            result = fn(args)
        except OtfError as exc:
            return protocol.make_error(req_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("tool %s crashed", name)
            return protocol.make_error(
                req_id,
                InternalError.code,
                f"internal error in {name}: {exc.__class__.__name__}: {exc}",
            )

        # Standard MCP tools/call envelope: { content: [...], isError: bool }
        return protocol.make_result(
            req_id,
            {"content": [{"type": "json", "data": result}], "isError": False},
        )


# ---------------------------------------------------------------------------
# Stdio loop
# ---------------------------------------------------------------------------


def run_stdio(in_stream=None, out_stream=None) -> None:
    """Production entry point: blocking stdio JSON-RPC loop."""
    in_stream = in_stream if in_stream is not None else sys.stdin
    out_stream = out_stream if out_stream is not None else sys.stdout

    server = OtfServer()

    while True:
        try:
            msg = protocol.read_message(in_stream)
        except protocol.FramingError as exc:
            log.warning("framing error: %s", exc)
            continue
        except EOFError:
            return
        # ``read_message`` returns None on EOF in some platforms
        if msg is None:
            return

        try:
            resp = server.handle_message(msg)
        except OtfError as exc:
            resp = protocol.make_error(msg.get("id"), exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unhandled exception in handle_message")
            resp = protocol.make_error(msg.get("id"), InternalError.code, str(exc))

        if resp is not None:
            protocol.write_message(resp, out_stream)


def main() -> None:
    """Console-script entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    run_stdio()


if __name__ == "__main__":  # pragma: no cover
    main()
