from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

DEFAULT_DUMP_APPS: tuple[str, ...] = (
    "access",
    "iam_v2",
    "resource_v2",
    "inspection_v2",
    "system_v2",
)

DEFAULT_SQLITE_BACKUP_FILES: tuple[str, ...] = (
    "db.sqlite3",
    "db.sqlite3-wal",
    "db.sqlite3-shm",
    "db.sqlite3-journal",
)


@dataclass(slots=True)
class MigrationState:
    root_dir: str
    backup_dir: str
    dump_path: str
    postgres_container: str = "dikong-postgres"
    postgres_db: str = "dikong"
    postgres_user: str = "postgres"
    postgres_host: str = "127.0.0.1"
    postgres_port: str = "5432"
    postgres_volume: str = "dikong_pgdata"
    django_settings_module: str = "config.settings"
    apps: tuple[str, ...] = DEFAULT_DUMP_APPS

    @property
    def root_path(self) -> Path:
        return Path(self.root_dir)

    @property
    def backup_path(self) -> Path:
        return Path(self.backup_dir)

    @property
    def dump_file(self) -> Path:
        return Path(self.dump_path)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["apps"] = list(self.apps)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MigrationState":
        data = dict(payload)
        data["apps"] = tuple(data.get("apps") or DEFAULT_DUMP_APPS)
        return cls(**data)


def default_backup_dir() -> Path:
    return Path("/tmp/dikong") / datetime.now().strftime("%Y-%m-%d_%H%M%S")


def state_file_path(backup_dir: Path) -> Path:
    return backup_dir / "migration_state.json"


def require_state_file(backup_dir: Path) -> Path:
    path = state_file_path(backup_dir)
    if path.exists():
        return path
    raise FileNotFoundError(
        f"migration state file not found: {path}. "
        f"Run `python scripts/precheck_backup_export.py --backup-dir {backup_dir}` first, "
        f"and make sure the later scripts use the same --backup-dir."
    )


def write_state(state: MigrationState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_state_json(state: MigrationState) -> str:
    return json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"


def read_state(path: Path) -> MigrationState:
    return MigrationState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def quote_command(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    stdout=None,
    stderr=None,
    check: bool = True,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [str(part) for part in cmd]
    print("+", quote_command(command))
    if dry_run:
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        input=input_text,
        text=True,
        stdout=stdout,
        stderr=stderr,
        check=check,
    )


def ensure_tool_exists(tool_name: str) -> None:
    if shutil.which(tool_name) is None:
        raise RuntimeError(f"required tool not found on PATH: {tool_name}")


def build_manage_command(root_dir: Path, args: Sequence[str]) -> list[str]:
    return [sys.executable, str(root_dir / "manage.py"), *args]


def build_runserver_command(root_dir: Path, host: str, port: str) -> list[str]:
    return [sys.executable, str(root_dir / "manage.py"), "runserver", f"{host}:{port}"]


def build_sqlite_env(state: MigrationState) -> dict[str, str]:
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = state.django_settings_module
    env["DB_ENGINE"] = "sqlite"
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        env.pop(key, None)
    return env


def build_postgres_env(state: MigrationState, *, password: str) -> dict[str, str]:
    if not password:
        raise ValueError("postgres password cannot be empty")
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = state.django_settings_module
    env["DB_ENGINE"] = "postgres"
    env["DB_HOST"] = state.postgres_host
    env["DB_PORT"] = state.postgres_port
    env["DB_NAME"] = state.postgres_db
    env["DB_USER"] = state.postgres_user
    env["DB_PASSWORD"] = password
    return env


def sqlite_backup_candidates(root_dir: Path) -> list[Path]:
    return [root_dir / name for name in DEFAULT_SQLITE_BACKUP_FILES if (root_dir / name).exists()]


def copy_sqlite_backups(root_dir: Path, backup_dir: Path, *, dry_run: bool = False) -> list[Path]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    candidates = sqlite_backup_candidates(root_dir)
    if not (root_dir / "db.sqlite3").exists():
        raise FileNotFoundError(f"SQLite database not found: {root_dir / 'db.sqlite3'}")

    copied: list[Path] = []
    for source in candidates:
        target = backup_dir / source.name
        print(f"copy {source} -> {target}")
        if not dry_run:
            shutil.copy2(source, target)
        copied.append(target)
    return copied


def stop_matching_processes(patterns: Sequence[str], *, dry_run: bool = False) -> None:
    for pattern in patterns:
        if dry_run:
            print(f"(dry-run) would stop processes matching: {pattern}")
            continue

        result = run_command(["pkill", "-f", pattern], check=False)
        if result.returncode == 0:
            print(f"stopped processes matching: {pattern}")
        elif result.returncode == 1:
            print(f"no processes matched: {pattern}")
        else:
            raise subprocess.CalledProcessError(result.returncode, result.args)

    if dry_run:
        return

    lingering: list[str] = []
    for pattern in patterns:
        check = subprocess.run(
            ["pgrep", "-af", pattern],
            text=True,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0 and check.stdout.strip():
            lingering.extend(line for line in check.stdout.splitlines() if line.strip())

    if lingering:
        raise RuntimeError("some writer processes are still running:\n" + "\n".join(lingering))


def dump_business_data(state: MigrationState, *, dry_run: bool = False) -> Path:
    dump_path = state.dump_file
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_manage_command(state.root_path, ["dumpdata", *state.apps, "--indent", "2"])
    env = build_sqlite_env(state)
    print(f"writing sqlite dump to: {dump_path}")
    if dry_run:
        return dump_path
    with dump_path.open("w", encoding="utf-8") as handle:
        run_command(cmd, cwd=state.root_path, env=env, stdout=handle)
    return dump_path


def build_postgres_container_run_command(state: MigrationState, password: str) -> list[str]:
    if not password:
        raise ValueError("postgres password cannot be empty")
    return [
        "docker",
        "run",
        "-d",
        "--name",
        state.postgres_container,
        "--restart",
        "unless-stopped",
        "-e",
        f"POSTGRES_DB={state.postgres_db}",
        "-e",
        f"POSTGRES_USER={state.postgres_user}",
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-p",
        f"127.0.0.1:{state.postgres_port}:5432",
        "-v",
        f"{state.postgres_volume}:/var/lib/postgresql/data",
        "postgres:16-alpine",
    ]


def docker_container_is_running(container_name: str) -> bool | None:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip().lower() == "true"
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if "no such object" in output:
        return None
    raise RuntimeError(
        f"docker inspect failed for {container_name}: {(result.stderr or result.stdout or '').strip()}"
    )


def wait_for_postgres_ready(state: MigrationState, password: str, *, timeout_seconds: int = 60, dry_run: bool = False) -> None:
    if dry_run:
        print(f"(dry-run) would wait for PostgreSQL container {state.postgres_container} to become ready")
        return

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"PGPASSWORD={password}",
                "-i",
                state.postgres_container,
                "psql",
                "-h",
                state.postgres_host,
                "-U",
                state.postgres_user,
                "-d",
                state.postgres_db,
                "-c",
                "SELECT 1;",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"PostgreSQL container is ready: {state.postgres_container}")
            return
        last_error = (result.stderr or result.stdout or "").strip()
        time.sleep(1)

    raise RuntimeError(
        f"PostgreSQL container did not become ready within {timeout_seconds}s: {last_error}"
    )


def _is_docker_port_conflict(output: str, *, host_port: str) -> bool:
    normalized = output.lower()
    return (
        "port is already allocated" in normalized
        or "address already in use" in normalized
        or f"bind for 0.0.0.0:{host_port}" in normalized
        or f"bind host port 127.0.0.1:{host_port}" in normalized
    )


def _port_conflict_error_message(state: MigrationState) -> str:
    return (
        f"host port {state.postgres_port} is already in use, so PostgreSQL container "
        f"`{state.postgres_container}` could not start. "
        f"Free that port first, or rerun `python scripts/precheck_backup_export.py --backup-dir {state.backup_dir} "
        f"--postgres-port <free-port>` and then rerun the later migration steps with the same --backup-dir."
    )


def _run_docker_container_command(
    command: Sequence[str],
    *,
    state: MigrationState,
    cleanup_container_name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", quote_command(command))
    result = subprocess.run(
        [str(part) for part in command],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode == 0:
        return result

    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if _is_docker_port_conflict(output, host_port=state.postgres_port):
        if cleanup_container_name:
            run_command(["docker", "rm", "-f", cleanup_container_name], check=False)
        raise RuntimeError(_port_conflict_error_message(state))

    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def ensure_postgres_container(
    state: MigrationState,
    password: str,
    *,
    dry_run: bool = False,
    recreate: bool = False,
) -> None:
    if dry_run:
        action = "recreate" if recreate else "ensure"
        print(f"(dry-run) would {action} PostgreSQL container: {state.postgres_container}")
        return

    ensure_tool_exists("docker")
    running = docker_container_is_running(state.postgres_container)

    if running is True and not recreate:
        print(f"PostgreSQL container already running: {state.postgres_container}")
        wait_for_postgres_ready(state, password, dry_run=dry_run)
        return

    if running is False and not recreate:
        _run_docker_container_command(["docker", "start", state.postgres_container], state=state)
        wait_for_postgres_ready(state, password, dry_run=dry_run)
        return

    if running is None and not recreate:
        run_command(["docker", "volume", "create", state.postgres_volume], dry_run=dry_run)
        _run_docker_container_command(
            build_postgres_container_run_command(state, password),
            state=state,
            cleanup_container_name=state.postgres_container,
        )
        wait_for_postgres_ready(state, password, dry_run=dry_run)
        return

    if running is not None and recreate:
        run_command(["docker", "rm", "-f", state.postgres_container], check=False, dry_run=dry_run)

    run_command(["docker", "volume", "create", state.postgres_volume], dry_run=dry_run)
    _run_docker_container_command(
        build_postgres_container_run_command(state, password),
        state=state,
        cleanup_container_name=state.postgres_container,
    )
    wait_for_postgres_ready(state, password, dry_run=dry_run)


def migrate_postgres_schema(state: MigrationState, password: str, *, dry_run: bool = False) -> None:
    env = build_postgres_env(state, password=password)
    run_command(build_manage_command(state.root_path, ["migrate"]), cwd=state.root_path, env=env, dry_run=dry_run)
    run_command(build_manage_command(state.root_path, ["check"]), cwd=state.root_path, env=env, dry_run=dry_run)


def load_business_data(state: MigrationState, password: str, *, dry_run: bool = False) -> None:
    env = build_postgres_env(state, password=password)
    run_command(
        build_manage_command(state.root_path, ["loaddata", str(state.dump_file)]),
        cwd=state.root_path,
        env=env,
        dry_run=dry_run,
    )


def reset_postgres_sequences(state: MigrationState, password: str, *, dry_run: bool = False) -> None:
    if dry_run:
        print("(dry-run) would reset postgres sequences")
        return

    env = build_postgres_env(state, password=password)
    result = run_command(
        build_manage_command(state.root_path, ["sqlsequencereset", *state.apps]),
        cwd=state.root_path,
        env=env,
        stdout=subprocess.PIPE,
        dry_run=dry_run,
    )
    sequence_sql = (result.stdout or "").strip()
    if not sequence_sql:
        print("no sequences to reset")
        return

    run_command(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={password}",
            state.postgres_container,
            "psql",
            "-h",
            state.postgres_host,
            "-U",
            state.postgres_user,
            "-d",
            state.postgres_db,
        ],
        input_text=sequence_sql + "\n",
        dry_run=dry_run,
    )


def dump_postgres_data(state: MigrationState, password: str, *, output_path: Path | None = None, dry_run: bool = False) -> Path:
    env = build_postgres_env(state, password=password)
    target = output_path or (state.backup_path / "business-data-postgres.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"writing postgres dump to: {target}")
    if dry_run:
        return target
    with target.open("w", encoding="utf-8") as handle:
        run_command(
            build_manage_command(state.root_path, ["dumpdata", *state.apps, "--indent", "2"]),
            cwd=state.root_path,
            env=env,
            stdout=handle,
        )
    return target


def compare_dump_files(expected: Path, actual: Path, *, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"(dry-run) would compare {expected} and {actual}")
        return True

    result = subprocess.run(
        ["diff", "-u", str(expected), str(actual)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        print(f"data dumps match: {expected} == {actual}")
        return True

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return False


def restore_sqlite_backups(state: MigrationState, *, dry_run: bool = False) -> list[Path]:
    restored: list[Path] = []
    for name in DEFAULT_SQLITE_BACKUP_FILES:
        source = state.backup_path / name
        target = state.root_path / name
        if not source.exists():
            continue
        print(f"restore {source} -> {target}")
        if not dry_run:
            shutil.copy2(source, target)
        restored.append(target)
    return restored


def start_background_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_file: Path,
    dry_run: bool = False,
) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"+ {quote_command(command)} > {log_file}")
    if dry_run:
        return 0

    with log_file.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=str(cwd),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process.pid
