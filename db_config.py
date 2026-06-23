from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def load_db_config() -> DBConfig:
    return DBConfig(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", ""),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
    )
