"""Workspace model.

The MCP server treats ``os.getcwd()`` as the single engineering boundary.
This module centralises every filesystem access so tools can stay free of
path manipulation and edge-case handling (missing dirs, missing files,
unicode-safe I/O, etc.).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# -- paths ----------------------------------------------------------------


def project_root() -> Path:
    """Return the project's working directory.

    Per spec §2.1, ``os.getcwd()`` is the *only* engineering context.
    Tests may override this through the ``OTF_ROOT`` environment
    variable so they can run against a scratch directory.
    """
    override = os.environ.get("OTF_ROOT")
    return Path(override).resolve() if override else Path(os.getcwd()).resolve()


def otf_root() -> Path:
    """Path to ``otf_tools/`` inside the project root."""
    return project_root() / "otf_tools"


def otf_hidden() -> Path:
    """Path to ``otf_tools/.otf/`` for runtime hidden state."""
    return otf_root() / ".otf"


def system_dir(system: str) -> Path:
    """Path to ``otf_tools/<system>/``."""
    _validate_system_name(system)
    return otf_root() / system


def onthefly_file(system: str) -> Path:
    """Path to ``otf_tools/<system>/onthefly.md``."""
    return system_dir(system) / "onthefly.md"


def swagger_file(system: str) -> Path:
    return system_dir(system) / "swagger.json"


def cli_registry_file(system: str) -> Path:
    return system_dir(system) / "cli.md"


def cli_dir(system: str) -> Path:
    return system_dir(system) / "cli"


# -- system name validation ----------------------------------------------

_SYSTEM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def _validate_system_name(system: str) -> None:
    """System names are short, URL-safe identifiers.

    Refusing path traversal here is a defence-in-depth measure; the
    primary defence is that ``otf_tools/`` is rooted inside the
    project directory.
    """
    if not isinstance(system, str) or not _SYSTEM_NAME_RE.match(system):
        from otf_mcp.errors import InvalidParamsError

        raise InvalidParamsError(
            f"invalid system name: {system!r}",
            data={"pattern": _SYSTEM_NAME_RE.pattern},
        )


# -- auto-init -----------------------------------------------------------


def ensure_otf_tools() -> tuple[Path, Path]:
    """Ensure ``otf_tools/`` and ``otf_tools/.otf/`` exist.

    Per spec §7, this is invoked on the *first* tool call, not at server
    start-up. The :mod:`otf_mcp.server` keeps a process-wide flag so the
    idempotent check runs at most once per process lifetime.

    Returns:
        A tuple ``(otf_root, otf_hidden)`` with both directories
        guaranteed to exist on disk.
    """
    root = otf_root()
    hidden = otf_hidden()
    root.mkdir(parents=True, exist_ok=True)
    hidden.mkdir(parents=True, exist_ok=True)
    return root, hidden


# -- file I/O helpers ----------------------------------------------------


def read_text(path: Path) -> str:
    """Read a UTF-8 text file or return an empty string when missing.

    Returning ``""`` rather than raising keeps the discoverable surface
    small: ``get_system_context`` simply reports an empty body for
    optional artefacts (e.g. a system that exists but has no
    ``swagger.json`` yet).
    """
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def read_yaml(path: Path) -> Any:
    """Load a YAML document; return ``None`` for empty/missing files."""
    text = read_text(path)
    if not text.strip():
        return None
    return yaml.safe_load(text)


def read_json(path: Path) -> Any:
    """Load a JSON document; return ``None`` for empty/missing files."""
    text = read_text(path)
    if not text.strip():
        return None
    return json.loads(text)


def list_systems() -> list[str]:
    """Enumerate ``system`` identifiers under ``otf_tools/``.

    A directory counts as a system iff it has an ``onthefly.md`` file
    inside it (spec §3: each system owns its own ``onthefly.md``).
    """
    root = otf_root()
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):  # skip .otf and friends
            continue
        if (entry / "onthefly.md").is_file():
            out.append(entry.name)
    return out


# -- onthefly.md parsing -----------------------------------------------


@dataclass(frozen=True)
class OntheflyFrontmatter:
    """The small YAML front-matter embedded in ``onthefly.md``.

    ``onthefly.md`` is a markdown document with YAML metadata at the
    top. We only need a handful of fields for ``discover_system`` and
    ``construct_workflow``; the full body is preserved verbatim in
    :class:`OntheflyDocument`.
    """

    name: str
    tags: list[str]
    api_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tags": list(self.tags), "api_version": self.api_version}


@dataclass(frozen=True)
class OntheflyDocument:
    """Parsed ``onthefly.md`` payload."""

    frontmatter: OntheflyFrontmatter | None
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "frontmatter": self.frontmatter.to_dict() if self.frontmatter else None,
            "body": self.body,
        }


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_onthefly_md(text: str) -> OntheflyDocument:
    """Split ``onthefly.md`` into YAML front-matter + markdown body.

    The split is intentionally forgiving: a document that begins without
    the ``---`` fence is treated as having no front-matter, which makes
    the parser useful for early bootstrap files where the user is still
    authoring the metadata.
    """
    if not text:
        return OntheflyDocument(frontmatter=None, body="")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return OntheflyDocument(frontmatter=None, body=text)
    yaml_block = m.group(1)
    body = text[m.end():]
    try:
        raw = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return OntheflyDocument(frontmatter=None, body=text)
    if not isinstance(raw, dict):
        return OntheflyDocument(frontmatter=None, body=text)
    fm = OntheflyFrontmatter(
        name=str(raw.get("name", "")),
        tags=[str(t) for t in (raw.get("tags") or [])],
        api_version=str(raw.get("api_version", "")),
    )
    return OntheflyDocument(frontmatter=fm, body=body)


def read_onthefly(system: str) -> OntheflyDocument:
    return parse_onthefly_md(read_text(onthefly_file(system)))


# -- cli.md parsing ---------------------------------------------------


@dataclass(frozen=True)
class CliCommandEntry:
    name: str
    cli: str
    status: str
    generated_from_api_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cli": self.cli,
            "status": self.status,
            "generated_from_api_version": self.generated_from_api_version,
        }


@dataclass(frozen=True)
class CliRegistry:
    system: str
    commands: list[CliCommandEntry]

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "commands": [c.to_dict() for c in self.commands],
        }

    @property
    def stale_count(self) -> int:
        return sum(1 for c in self.commands if c.status == "stale")


def parse_cli_registry(system: str, text: str) -> CliRegistry:
    """Parse ``cli.md`` into a list of commands.

    The format is YAML front-matter + a markdown command table per
    spec §6. We keep the parser tolerant: missing or malformed files
    produce an empty registry so that downstream tools (e.g.
    ``get_system_context``) can still report status counts.
    """
    if not text.strip():
        return CliRegistry(system=system, commands=[])
    parsed = parse_onthefly_md(text)
    body = parsed.body
    # If the file did not have front-matter, treat whole body as YAML.
    if parsed.frontmatter is None:
        body = text
    try:
        raw = yaml.safe_load(body) or {}
    except yaml.YAMLError:
        return CliRegistry(system=system, commands=[])
    if not isinstance(raw, dict):
        return CliRegistry(system=system, commands=[])
    cmds_raw = raw.get("commands") or []
    if not isinstance(cmds_raw, list):
        return CliRegistry(system=system, commands=[])
    out: list[CliCommandEntry] = []
    for c in cmds_raw:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", ""))
        if not name:
            continue
        out.append(
            CliCommandEntry(
                name=name,
                cli=str(c.get("cli", f"cli/{name}.py")),
                status=str(c.get("status", "draft")),
                generated_from_api_version=str(c.get("generated_from_api_version", "")),
            )
        )
    return CliRegistry(system=system, commands=out)
