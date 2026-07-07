from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def load_env_file(env_path: str | Path = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file into process environment."""
    path = Path(env_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue

        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def load_db_config() -> DBConfig:
    load_env_file()
    return DBConfig(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", ""),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
    )
