from __future__ import annotations

from pathlib import Path


class SyncScopeFilter:
    def __init__(self, *, sync_only: bool):
        self.sync_only = bool(sync_only)

    def filter(self, record) -> bool:
        has_sync_run_id = bool(getattr(record, "sync_run_id", None))
        return has_sync_run_id if self.sync_only else not has_sync_run_id


def build_logging_config(
    *,
    base_dir: Path,
    log_dir: Path | None = None,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> dict:
    resolved_base_dir = Path(base_dir)
    resolved_log_dir = Path(log_dir) if log_dir is not None else resolved_base_dir / "logs"
    resolved_log_dir.mkdir(parents=True, exist_ok=True)

    app_log = resolved_log_dir / "app.log"
    error_log = resolved_log_dir / "error.log"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_lines": {
                "format": "%(message)s",
            },
        },
        "filters": {
            "non_sync_scope": {
                "()": "config.logging_config.SyncScopeFilter",
                "sync_only": False,
            },
            "sync_scope": {
                "()": "config.logging_config.SyncScopeFilter",
                "sync_only": True,
            },
        },
        "handlers": {
            "app_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "json_lines",
                "filename": str(app_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "filters": ["non_sync_scope"],
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "json_lines",
                "filename": str(error_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "filters": ["non_sync_scope"],
            },
            "sync_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "json_lines",
                "filename": str(resolved_log_dir / "sync.log"),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "filters": ["sync_scope"],
            },
            "sync_error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "json_lines",
                "filename": str(resolved_log_dir / "sync.error.log"),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "filters": ["sync_scope"],
            },
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "json_lines",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "django.request": {
                "handlers": [],
                "level": "ERROR",
                "propagate": False,
            },
            "django.server": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["app_file", "error_file", "sync_file", "sync_error_file"],
            "level": level,
        },
    }
