#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.sqlite_postgres_migration import (
    MigrationState,
    copy_sqlite_backups,
    default_backup_dir,
    dump_business_data,
    state_file_path,
    stop_matching_processes,
    write_state,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stop local Django writer processes, back up SQLite files, and export business data.",
    )
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Project root directory. Defaults to current directory.",
    )
    parser.add_argument(
        "--backup-dir",
        help="Backup directory. Defaults to /tmp/dikong/<timestamp>.",
    )
    parser.add_argument(
        "--postgres-port",
        default="5432",
        help="Host port for the future PostgreSQL container. Defaults to 5432.",
    )
    parser.add_argument(
        "--postgres-container",
        default="dikong-postgres",
        help="PostgreSQL container name. Defaults to dikong-postgres.",
    )
    parser.add_argument(
        "--postgres-volume",
        default="dikong_pgdata",
        help="PostgreSQL docker volume name. Defaults to dikong_pgdata.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended actions without making changes.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    root_dir = Path(args.root_dir).resolve()
    backup_dir = Path(args.backup_dir).expanduser().resolve() if args.backup_dir else default_backup_dir().resolve()
    state = MigrationState(
        root_dir=str(root_dir),
        backup_dir=str(backup_dir),
        dump_path=str(backup_dir / "business-data.json"),
        postgres_container=args.postgres_container,
        postgres_db="dikong",
        postgres_user="postgres",
        postgres_host="127.0.0.1",
        postgres_port=str(args.postgres_port),
        postgres_volume=args.postgres_volume,
        django_settings_module="config.settings",
    )

    print(f"root_dir={root_dir}")
    print(f"backup_dir={backup_dir}")
    stop_matching_processes(
        [
            "manage.py runserver",
            "run_dji_sync_scheduler",
        ],
        dry_run=args.dry_run,
    )
    copied = copy_sqlite_backups(root_dir, backup_dir, dry_run=args.dry_run)
    dump_business_data(state, dry_run=args.dry_run)
    if args.dry_run:
        print(f"(dry-run) would write state file: {state_file_path(backup_dir)}")
    else:
        write_state(state, state_file_path(backup_dir))

    print("sqlite backups:")
    for path in copied:
        print(f"- {path}")
    print(f"state file: {state_file_path(backup_dir)}")
    print(f"dump file: {state.dump_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
