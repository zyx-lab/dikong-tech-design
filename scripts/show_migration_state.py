#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.sqlite_postgres_migration import read_state, render_state_json, require_state_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Show the saved migration_state.json for a migration backup directory.",
    )
    parser.add_argument(
        "--backup-dir",
        required=True,
        help="Backup directory that contains migration_state.json.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a short human-readable summary instead of raw JSON.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    backup_dir = Path(args.backup_dir).expanduser().resolve()
    state_path = require_state_file(backup_dir)
    state = read_state(state_path)

    if args.summary:
        print(f"backup_dir={state.backup_dir}")
        print(f"root_dir={state.root_dir}")
        print(f"dump_path={state.dump_path}")
        print(f"postgres_container={state.postgres_container}")
        print(f"postgres_db={state.postgres_db}")
        print(f"postgres_host={state.postgres_host}")
        print(f"postgres_port={state.postgres_port}")
        print(f"postgres_volume={state.postgres_volume}")
        print(f"django_settings_module={state.django_settings_module}")
        print("apps=" + ",".join(state.apps))
        return 0

    sys.stdout.write(render_state_json(state))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
