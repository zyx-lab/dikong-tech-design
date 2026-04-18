from __future__ import annotations

from pathlib import Path


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
        "handlers": {
            "app_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "json_lines",
                "filename": str(app_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "json_lines",
                "filename": str(error_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
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
            "handlers": ["app_file", "error_file"],
            "level": level,
        },
    }
