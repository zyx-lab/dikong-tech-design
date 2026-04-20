from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from tools.sqlite_postgres_migration import (
    MigrationState,
    build_postgres_container_run_command,
    build_postgres_env,
    build_runserver_command,
    copy_sqlite_backups,
    default_backup_dir,
    read_state,
    render_state_json,
    sqlite_backup_candidates,
    state_file_path,
    write_state,
)


class SQLitePostgresMigrationHelperTests(TestCase):
    def test_build_postgres_env_sets_expected_database_variables(self):
        state = MigrationState(
            root_dir="/repo",
            backup_dir="/tmp/backups",
            dump_path="/tmp/backups/business-data.json",
            postgres_container="dikong-postgres",
            postgres_db="dikong",
            postgres_user="postgres",
            postgres_host="127.0.0.1",
            postgres_port="15432",
            postgres_volume="dikong_pgdata",
            django_settings_module="config.settings",
        )

        env = build_postgres_env(state, password="secret")

        self.assertEqual(env["DJANGO_SETTINGS_MODULE"], "config.settings")
        self.assertEqual(env["DB_ENGINE"], "postgres")
        self.assertEqual(env["DB_HOST"], "127.0.0.1")
        self.assertEqual(env["DB_PORT"], "15432")
        self.assertEqual(env["DB_NAME"], "dikong")
        self.assertEqual(env["DB_USER"], "postgres")
        self.assertEqual(env["DB_PASSWORD"], "secret")

    def test_build_postgres_container_run_command_uses_host_port_mapping(self):
        state = MigrationState(
            root_dir="/repo",
            backup_dir="/tmp/backups",
            dump_path="/tmp/backups/business-data.json",
            postgres_container="dikong-postgres",
            postgres_db="dikong",
            postgres_user="postgres",
            postgres_host="127.0.0.1",
            postgres_port="15432",
            postgres_volume="dikong_pgdata",
            django_settings_module="config.settings",
        )

        cmd = build_postgres_container_run_command(state, "secret")

        self.assertEqual(cmd[:3], ["docker", "run", "-d"])
        self.assertIn("--name", cmd)
        self.assertIn("dikong-postgres", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("127.0.0.1:15432:5432", cmd)
        self.assertIn("-v", cmd)
        self.assertIn("dikong_pgdata:/var/lib/postgresql/data", cmd)
        self.assertIn("postgres:16-alpine", cmd)

    def test_build_runserver_command_points_at_manage_py(self):
        cmd = build_runserver_command(Path("/repo"), "0.0.0.0", "8000")

        self.assertEqual(cmd[1], "/repo/manage.py")
        self.assertEqual(cmd[2:], ["runserver", "0.0.0.0:8000"])

    def test_state_round_trip_preserves_apps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / "backups"
            state = MigrationState(
                root_dir="/repo",
                backup_dir=str(backup_dir),
                dump_path=str(backup_dir / "business-data.json"),
                postgres_container="dikong-postgres",
                postgres_db="dikong",
                postgres_user="postgres",
                postgres_host="127.0.0.1",
                postgres_port="5432",
                postgres_volume="dikong_pgdata",
                django_settings_module="config.settings",
                apps=("access", "dji_bff"),
            )
            path = state_file_path(backup_dir)
            write_state(state, path)

            loaded = read_state(path)

            self.assertEqual(loaded.root_dir, "/repo")
            self.assertEqual(loaded.backup_dir, str(backup_dir))
            self.assertEqual(loaded.dump_path, str(backup_dir / "business-data.json"))
            self.assertEqual(loaded.apps, ("access", "dji_bff"))

    def test_sqlite_backup_candidates_only_include_existing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "db.sqlite3").write_text("main", encoding="utf-8")
            (root / "db.sqlite3-wal").write_text("wal", encoding="utf-8")
            (root / "unrelated.txt").write_text("ignore", encoding="utf-8")

            candidates = sqlite_backup_candidates(root)

            self.assertEqual([path.name for path in candidates], ["db.sqlite3", "db.sqlite3-wal"])

    def test_copy_sqlite_backups_copies_existing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            backup = Path(tmpdir) / "backup"
            root.mkdir()
            (root / "db.sqlite3").write_text("main", encoding="utf-8")
            (root / "db.sqlite3-journal").write_text("journal", encoding="utf-8")

            copied = copy_sqlite_backups(root, backup)

            self.assertEqual([path.name for path in copied], ["db.sqlite3", "db.sqlite3-journal"])
            self.assertEqual((backup / "db.sqlite3").read_text(encoding="utf-8"), "main")
            self.assertEqual((backup / "db.sqlite3-journal").read_text(encoding="utf-8"), "journal")

    def test_default_backup_dir_uses_tmp_dikong_root(self):
        backup_dir = default_backup_dir()

        self.assertEqual(backup_dir.parent, Path("/tmp/dikong"))
        self.assertRegex(backup_dir.name, r"^\d{4}-\d{2}-\d{2}_\d{6}$")

    def test_render_state_json_outputs_pretty_json(self):
        state = MigrationState(
            root_dir="/repo",
            backup_dir="/tmp/dikong/2026-04-20_164000",
            dump_path="/tmp/dikong/2026-04-20_164000/business-data.json",
            postgres_container="dikong-postgres",
            postgres_db="dikong",
            postgres_user="postgres",
            postgres_host="127.0.0.1",
            postgres_port="5432",
            postgres_volume="dikong_pgdata",
            django_settings_module="config.settings",
            apps=("access", "dji_bff"),
        )

        text = render_state_json(state)

        self.assertTrue(text.endswith("\n"))
        self.assertIn('"backup_dir": "/tmp/dikong/2026-04-20_164000"', text)
        self.assertIn('"apps": [\n    "access",\n    "dji_bff"\n  ]', text)
