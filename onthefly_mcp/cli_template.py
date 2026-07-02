"""Reference CLI template renderer for Code Agent side generation."""

from __future__ import annotations

import re


def render_cli_template(system: str, command: str, description: str = "CLI command") -> str:
    env_system = re.sub(r"[^A-Z0-9]+", "_", system.upper()).strip("_")
    token_env_var = f"OTF_{env_system}_API_TOKEN"

    return f'''import argparse
import json
import os
import sys


COMMAND = {command!r}
DESCRIPTION = {description!r}
TOKEN_ENV_VAR = {token_env_var!r}


def parse_args():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        token = os.getenv(TOKEN_ENV_VAR)

        if args.dry_run:
            print(json.dumps({{
                "success": True,
                "dry_run": True,
                "preview": {{
                    "command": COMMAND,
                    "token_env_var": TOKEN_ENV_VAR,
                    "token_present": bool(token),
                }},
            }}))
            return 0

        result = {{
            "success": True,
        }}

        print(json.dumps(result))
        return 0

    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
'''

