"""State file parsing and read-only registry helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

import yaml

from .errors import ToolError
from .workspace import otf_root

NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def validate_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not NAME_RE.match(value):
        raise ToolError("invalid_args", f"{label} must be a non-empty safe name")
    return value


def get_system_dir(system: str) -> Path:
    system_dir = otf_root() / system
    if not system_dir.is_dir():
        raise ToolError("system_not_found", f"System not found: {system}")
    return system_dir


def read_text(path: Path, state_name: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError("corrupted_state", f"Failed to read {state_name}: {path}") from exc


def parse_onthefly_summary(path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(read_text(path, "onthefly.md"))
    except yaml.YAMLError as exc:
        raise ToolError("corrupted_state", f"Failed to parse onthefly.md: {path}") from exc

    if not isinstance(parsed, dict):
        raise ToolError("corrupted_state", f"onthefly.md must be a YAML object: {path}")

    system = parsed.get("system")
    name = parsed.get("name")
    tags = parsed.get("tags", [])
    api_version = parsed.get("api_version")
    if not isinstance(system, str) or not isinstance(name, str) or not isinstance(api_version, str):
        raise ToolError("corrupted_state", f"onthefly.md is missing required string fields: {path}")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ToolError("corrupted_state", f"onthefly.md tags must be a string array: {path}")

    return {
        "system": system,
        "name": name,
        "tags": tags,
        "api_version": api_version,
    }


def load_swagger(system_dir: Path) -> tuple[str, dict[str, Any]]:
    path = system_dir / "swagger.json"
    raw = read_text(path, "swagger.json")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("corrupted_state", f"Failed to parse swagger.json: {path}") from exc
    if not isinstance(parsed, dict):
        raise ToolError("corrupted_state", f"swagger.json must be a JSON object: {path}")
    if not isinstance(parsed.get("version"), str):
        raise ToolError("corrupted_state", f"swagger.json must include a string version: {path}")
    return raw, parsed


def load_cli_registry(system_dir: Path, system: str) -> tuple[str, dict[str, Any]]:
    path = system_dir / "cli.md"
    if not path.exists():
        return "", {"system": system, "commands": []}

    raw = read_text(path, "cli.md")
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ToolError("corrupted_state", f"Failed to parse cli.md: {path}") from exc

    if parsed is None:
        parsed = {"system": system, "commands": []}
    if not isinstance(parsed, dict):
        raise ToolError("corrupted_state", f"cli.md must be a YAML object: {path}")
    commands = parsed.get("commands", [])
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        raise ToolError("corrupted_state", f"cli.md commands must be a list: {path}")
    for command in commands:
        if not isinstance(command, dict):
            raise ToolError("corrupted_state", f"cli.md command entries must be objects: {path}")
        if "name" in command and not isinstance(command["name"], str):
            raise ToolError("corrupted_state", f"cli.md command name must be a string: {path}")

    normalized = dict(parsed)
    normalized["system"] = parsed.get("system", system)
    normalized["commands"] = commands
    return raw, normalized


def write_cli_registry(system_dir: Path, registry: dict[str, Any]) -> None:
    path = system_dir / "cli.md"
    tmp_path = system_dir / f".cli.md.tmp.{os.getpid()}"
    text = yaml.safe_dump(registry, sort_keys=False, allow_unicode=True)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        raise ToolError("corrupted_state", f"Failed to write cli.md: {path}") from exc
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def find_command(cli_registry: dict[str, Any], command: str) -> dict[str, Any] | None:
    for entry in cli_registry.get("commands", []):
        if entry.get("name") == command:
            return entry
    return None


def current_api_version(swagger: dict[str, Any]) -> str:
    version = swagger.get("version")
    if not isinstance(version, str):
        raise ToolError("corrupted_state", "swagger.json must include a string version")
    return version


def is_command_stale(command_entry: dict[str, Any], swagger_version: str) -> bool:
    return command_entry.get("generated_from_api_version") != swagger_version


def registry_status(cli_registry: dict[str, Any], swagger_version: str) -> dict[str, int]:
    commands = cli_registry.get("commands", [])
    stale_count = sum(1 for command in commands if is_command_stale(command, swagger_version))
    needs_human_review_count = sum(1 for command in commands if command.get("needs_human_review") is True)
    return {
        "cli_count": len(commands),
        "stale_count": stale_count,
        "needs_human_review_count": needs_human_review_count,
    }
