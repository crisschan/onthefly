"""discover_system (spec §4.1).

Scans ``otf_tools/*/onthefly.md`` and returns a normalised summary per
system. Each entry contains ``system`` (dir name), ``name`` (from
front-matter), ``tags``, and ``api_version``.

This tool does not require any arguments.
"""
from __future__ import annotations

from typing import Any

from otf_mcp.core import workspace
from otf_mcp.core.schema import EMPTY_OBJECT_SCHEMA, validate_or_raise


def handle(args: dict[str, Any]) -> dict[str, Any]:
    validate_or_raise(args, EMPTY_OBJECT_SCHEMA, what="discover_system")

    systems: list[dict[str, Any]] = []
    for system in workspace.list_systems():
        doc = workspace.read_onthefly(system)
        fm = doc.frontmatter
        if fm is None:
            # Tolerate onthefly.md that exists but has no front-matter
            # yet: report the bare minimum so downstream tools can keep
            # working during early bootstrap.
            systems.append(
                {
                    "system": system,
                    "name": system,
                    "tags": [],
                    "api_version": "",
                }
            )
            continue
        systems.append(
            {
                "system": system,
                "name": fm.name or system,
                "tags": fm.tags,
                "api_version": fm.api_version,
            }
        )

    return {"systems": systems}
