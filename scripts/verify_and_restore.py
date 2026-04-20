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
    build_postgres_env,
    build_runserver_command,
    build_sqlite_env,
    compare_dump_files,
    dump_postgres_data,
    read_state,
    restore_sqlite_backups,
    start_background_command,
    state_file_path,
    stop_matching_processes,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify PostgreSQL dump matches SQLite dump, then start Django against PostgreSQL or restore SQLite backups.",
    )
    parser.add_argument(
        "--backup-dir",
        required=True,
        help="Backup directory created by precheck_backup_export.py.",
    )
    parser.add_argument(
        "--postgres-password",
        help="PostgreSQL password. Required for verification or start modes that target PostgreSQL.",
    )
    parser.add_argument(
        "--mode",
        choices=("verify", "start-postgres", "rollback"),
        default="verify",
        help="verify: compare dumps only; start-postgres: compare then start runserver with PostgreSQL; rollback: restore SQLite files and start runserver with SQLite.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="runserver host. Defaults to 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        default="8000",
        help="runserver port. Defaults to 8000.",
    )
    parser.add_argument(
        "--log-file",
        help="Path for the restarted runserver output. Defaults to <backup-dir>/runserver.log.",
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
    state = read_state(state_file_path(backup_dir))
    log_file = Path(args.log_file).expanduser().resolve() if args.log_file else backup_dir / "runserver.log"

    if args.mode == "rollback":
        stop_matching_processes(
            [
                "manage.py runserver",
                "run_dji_sync_scheduler",
            ],
            dry_run=args.dry_run,
        )
        restore_sqlite_backups(state, dry_run=args.dry_run)
        env = build_sqlite_env(state)
        pid = start_background_command(
            build_runserver_command(state.root_path, args.host, args.port),
            cwd=state.root_path,
            env=env,
            log_file=log_file,
            dry_run=args.dry_run,
        )
        print(f"sqlite rollback complete; runserver pid={pid}")
        print(f"log file: {log_file}")
        return 0

    password = args.postgres_password or getpass.getpass("PostgreSQL password: ")
    postgres_dump = dump_postgres_data(state, password, dry_run=args.dry_run)
    if not compare_dump_files(state.dump_file, postgres_dump, dry_run=args.dry_run):
        return 1

    if args.mode == "verify":
        print("verification completed")
        print(f"postgres dump: {postgres_dump}")
        return 0

    stop_matching_processes(
        [
            "manage.py runserver",
            "run_dji_sync_scheduler",
        ],
        dry_run=args.dry_run,
    )
    env = build_postgres_env(state, password=password)
    pid = start_background_command(
        build_runserver_command(state.root_path, args.host, args.port),
        cwd=state.root_path,
        env=env,
        log_file=log_file,
        dry_run=args.dry_run,
    )
    print(f"postgres runserver pid={pid}")
    print(f"log file: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
