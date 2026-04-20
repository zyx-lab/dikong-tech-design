#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.sqlite_postgres_migration import (
    ensure_postgres_container,
    load_business_data,
    migrate_postgres_schema,
    read_state,
    reset_postgres_sequences,
    require_state_file,
    state_file_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ensure PostgreSQL container exists, migrate schema, import dumped data, and reset sequences.",
    )
    parser.add_argument(
        "--backup-dir",
        required=True,
        help="Backup directory created by precheck_backup_export.py.",
    )
    parser.add_argument(
        "--postgres-password",
        help="PostgreSQL password. If omitted, prompt securely.",
    )
    parser.add_argument(
        "--recreate-container",
        action="store_true",
        help="Remove and recreate the PostgreSQL container before import.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended actions without making changes.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    backup_dir = Path(args.backup_dir).expanduser().resolve()
    state = read_state(require_state_file(backup_dir))
    password = args.postgres_password or getpass.getpass("PostgreSQL password: ")

    ensure_postgres_container(
        state,
        password,
        dry_run=args.dry_run,
        recreate=args.recreate_container,
    )
    migrate_postgres_schema(state, password, dry_run=args.dry_run)
    load_business_data(state, password, dry_run=args.dry_run)
    reset_postgres_sequences(state, password, dry_run=args.dry_run)

    print(f"state file: {state_file_path(backup_dir)}")
    print("postgres schema and data import completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
