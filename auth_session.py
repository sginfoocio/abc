from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class AuthTokenClaims:
    username: str
    expires_at: int
    session_id: str


def derive_session_signing_key(secret: str, username: str, password: str) -> str:
    credential = f"{username}\0{password}".encode()
    return hmac.new(secret.encode(), credential, hashlib.sha256).hexdigest()


def create_auth_token(
    username: str,
    secret: str,
    days: int,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at + days * 86400
    session_id = secrets.token_urlsafe(24)
    payload = f"{username}:{expires_at}:{session_id}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_auth_token(
    token: str,
    expected_user: str,
    secret: str,
    now: int | None = None,
) -> AuthTokenClaims | None:
    try:
        username, expiry_text, session_id, signature = token.rsplit(":", 3)
        expires_at = int(expiry_text)
    except (AttributeError, ValueError):
        return None

    payload = f"{username}:{expiry_text}:{session_id}"
    expected_signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    if not hmac.compare_digest(username, expected_user):
        return None
    current_time = int(time.time()) if now is None else int(now)
    if expires_at <= current_time:
        return None
    return AuthTokenClaims(username, expires_at, session_id)


def login_bucket_keys(username: str, remote_ip: str | None) -> tuple[str, str]:
    ip_digest = hashlib.sha256((remote_ip or "unknown").encode()).hexdigest()
    identity = f"{username.strip().casefold()}\0{ip_digest}"
    identity_digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"ip:{ip_digest}", f"identity:{identity_digest}"


class AuthStateStore:
    """Persistent session revocation and login throttling for a single host."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS revoked_sessions (
                    session_id TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket_key TEXT PRIMARY KEY,
                    window_started INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                );
                """
            )

    def is_revoked(self, session_id: str, now: int | None = None) -> bool:
        current_time = int(time.time()) if now is None else int(now)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            row = connection.execute(
                "SELECT 1 FROM revoked_sessions WHERE session_id = ? AND expires_at >= ?",
                (session_id, current_time),
            ).fetchone()
        return row is not None

    def revoke_session(
        self,
        session_id: str,
        expires_at: int,
        now: int | None = None,
    ) -> None:
        current_time = int(time.time()) if now is None else int(now)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (current_time,))
            connection.execute(
                "INSERT OR REPLACE INTO revoked_sessions (session_id, expires_at) VALUES (?, ?)",
                (session_id, int(expires_at)),
            )

    def allow_login_attempt(
        self,
        bucket_keys: tuple[str, ...],
        maximum_attempts: int,
        window_seconds: int,
        now: int | None = None,
    ) -> bool:
        current_time = int(time.time()) if now is None else int(now)
        cutoff = current_time - window_seconds
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM login_attempts WHERE window_started <= ?",
                (cutoff,),
            )
            existing: dict[str, int] = {}
            for bucket_key in bucket_keys:
                row = connection.execute(
                    "SELECT window_started, attempts FROM login_attempts WHERE bucket_key = ?",
                    (bucket_key,),
                ).fetchone()
                if row is not None:
                    existing[bucket_key] = int(row[1])

            if any(attempts >= maximum_attempts for attempts in existing.values()):
                return False

            for bucket_key in bucket_keys:
                previous_attempts = existing.get(bucket_key)
                if previous_attempts is None:
                    connection.execute(
                        "INSERT INTO login_attempts (bucket_key, window_started, attempts) VALUES (?, ?, 1)",
                        (bucket_key, current_time),
                    )
                else:
                    connection.execute(
                        "UPDATE login_attempts SET attempts = ? WHERE bucket_key = ?",
                        (previous_attempts + 1, bucket_key),
                    )
            return True

    def reset_login_attempts(self, bucket_keys: tuple[str, ...]) -> None:
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.executemany(
                "DELETE FROM login_attempts WHERE bucket_key = ?",
                ((bucket_key,) for bucket_key in bucket_keys),
            )


class PostgresAuthStateStore:
    """Shared session revocation and login throttling for multiple app replicas."""

    def __init__(self, database_url: str) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+")):
            raise ValueError("AUTH_STATE_DATABASE_URL debe ser una URL PostgreSQL.")
        self._engine = create_engine(database_url, pool_pre_ping=True)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS cloud_auth_revoked_sessions (
                        session_id TEXT PRIMARY KEY,
                        expires_at BIGINT NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS cloud_auth_login_attempts (
                        bucket_key TEXT PRIMARY KEY,
                        window_started BIGINT NOT NULL,
                        attempts INTEGER NOT NULL
                    )
                    """
                )
            )

    @staticmethod
    def _lock_buckets(
        connection: Connection,
        bucket_keys: tuple[str, ...],
    ) -> None:
        for bucket_key in sorted(set(bucket_keys)):
            lock_key = int.from_bytes(
                hashlib.sha256(bucket_key.encode()).digest()[:8],
                "big",
                signed=True,
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )

    def is_revoked(self, session_id: str, now: int | None = None) -> bool:
        current_time = int(time.time()) if now is None else int(now)
        with self._engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT 1 FROM cloud_auth_revoked_sessions
                    WHERE session_id = :session_id AND expires_at >= :now
                    """
                ),
                {"session_id": session_id, "now": current_time},
            )
            return result.first() is not None

    def revoke_session(
        self,
        session_id: str,
        expires_at: int,
        now: int | None = None,
    ) -> None:
        current_time = int(time.time()) if now is None else int(now)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM cloud_auth_revoked_sessions WHERE expires_at < :now"
                ),
                {"now": current_time},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO cloud_auth_revoked_sessions (session_id, expires_at)
                    VALUES (:session_id, :expires_at)
                    ON CONFLICT (session_id) DO UPDATE
                    SET expires_at = EXCLUDED.expires_at
                    """
                ),
                {"session_id": session_id, "expires_at": int(expires_at)},
            )

    def allow_login_attempt(
        self,
        bucket_keys: tuple[str, ...],
        maximum_attempts: int,
        window_seconds: int,
        now: int | None = None,
    ) -> bool:
        current_time = int(time.time()) if now is None else int(now)
        cutoff = current_time - window_seconds
        with self._engine.begin() as connection:
            self._lock_buckets(connection, bucket_keys)
            connection.execute(
                text(
                    """
                    WITH expired AS (
                        SELECT bucket_key
                        FROM cloud_auth_login_attempts
                        WHERE window_started <= :cutoff
                        ORDER BY window_started
                        LIMIT 1000
                        FOR UPDATE SKIP LOCKED
                    )
                    DELETE FROM cloud_auth_login_attempts AS attempts
                    USING expired
                    WHERE attempts.bucket_key = expired.bucket_key
                    """
                ),
                {"cutoff": cutoff},
            )
            existing: dict[str, int] = {}
            for bucket_key in bucket_keys:
                row = connection.execute(
                    text(
                        """
                        SELECT window_started, attempts FROM cloud_auth_login_attempts
                        WHERE bucket_key = :bucket_key FOR UPDATE
                        """
                    ),
                    {"bucket_key": bucket_key},
                ).first()
                if row is not None:
                    if int(row[0]) <= cutoff:
                        connection.execute(
                            text(
                                """
                                DELETE FROM cloud_auth_login_attempts
                                WHERE bucket_key = :bucket_key
                                """
                            ),
                            {"bucket_key": bucket_key},
                        )
                    else:
                        existing[bucket_key] = int(row[1])

            if any(attempts >= maximum_attempts for attempts in existing.values()):
                return False

            for bucket_key in bucket_keys:
                previous_attempts = existing.get(bucket_key)
                if previous_attempts is None:
                    connection.execute(
                        text(
                            """
                            INSERT INTO cloud_auth_login_attempts
                                (bucket_key, window_started, attempts)
                            VALUES (:bucket_key, :window_started, 1)
                            """
                        ),
                        {"bucket_key": bucket_key, "window_started": current_time},
                    )
                else:
                    connection.execute(
                        text(
                            """
                            UPDATE cloud_auth_login_attempts
                            SET attempts = :attempts
                            WHERE bucket_key = :bucket_key
                            """
                        ),
                        {
                            "bucket_key": bucket_key,
                            "attempts": previous_attempts + 1,
                        },
                    )
            return True

    def reset_login_attempts(self, bucket_keys: tuple[str, ...]) -> None:
        with self._engine.begin() as connection:
            self._lock_buckets(connection, bucket_keys)
            for bucket_key in bucket_keys:
                connection.execute(
                    text(
                        "DELETE FROM cloud_auth_login_attempts WHERE bucket_key = :bucket_key"
                    ),
                    {"bucket_key": bucket_key},
                )
