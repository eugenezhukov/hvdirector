"""Process configuration.

Only bootstrap values live outside the database: how to reach PostgreSQL and
the master key used to encrypt credentials stored in the database.  Everything
else (Hyper-V host addresses and credentials, ISO share, guacd address, ...)
is stored in PostgreSQL and managed through the provider portal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_CONFIG_FILE = "/etc/hvdirector/hvdirector.env"


def _load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing values."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    secret_key: str
    hyperv_driver: str
    frontend_dist: str | None
    cookie_secure: bool
    log_level: str
    fake_state_file: str
    worker_threads: int
    upload_chunk_mb: int


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    _load_env_file(os.environ.get("HVD_CONFIG_FILE", DEFAULT_CONFIG_FILE))
    database_url = os.environ.get(
        "HVD_DATABASE_URL", "postgresql+psycopg://hvdirector:hvdirector@127.0.0.1:5432/hvdirector"
    )
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
    secret_key = os.environ.get("HVD_SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError(
            "HVD_SECRET_KEY is not set. Generate one with 'hvdirector-manage gen-key' "
            f"and put it into {DEFAULT_CONFIG_FILE}"
        )
    return AppConfig(
        database_url=database_url,
        secret_key=secret_key,
        hyperv_driver=os.environ.get("HVD_HYPERV_DRIVER", "psrp").strip().lower(),
        frontend_dist=os.environ.get("HVD_FRONTEND_DIST") or None,
        cookie_secure=_bool(os.environ.get("HVD_COOKIE_SECURE"), True),
        log_level=os.environ.get("HVD_LOG_LEVEL", "INFO").upper(),
        fake_state_file=os.environ.get("HVD_FAKE_STATE", "/var/lib/hvdirector/fake-hyperv.json"),
        worker_threads=int(os.environ.get("HVD_WORKER_THREADS", "8")),
        upload_chunk_mb=int(os.environ.get("HVD_UPLOAD_CHUNK_MB", "16")),
    )
