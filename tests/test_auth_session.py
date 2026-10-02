from auth_session import (
    AuthStateStore,
    create_auth_token,
    derive_session_signing_key,
    login_bucket_keys,
    verify_auth_token,
)
from concurrent.futures import ThreadPoolExecutor


def test_signed_token_expires_and_rejects_tampering() -> None:
    signing_key = derive_session_signing_key("s" * 40, "admin", "old-password")
    token = create_auth_token("admin", signing_key, days=1, now=1000)

    claims = verify_auth_token(token, "admin", signing_key, now=1001)

    assert claims is not None
    assert claims.username == "admin"
    assert verify_auth_token(token, "admin", "wrong-secret", now=1001) is None
    assert verify_auth_token(token, "admin", signing_key, now=87401) is None
    changed_password_key = derive_session_signing_key("s" * 40, "admin", "new-password")
    assert verify_auth_token(token, "admin", changed_password_key, now=1001) is None


def test_revocation_is_persistent_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "auth-state.sqlite3"
    store = AuthStateStore(database_path)
    token = create_auth_token("admin", "s" * 40, days=1, now=1000)
    claims = verify_auth_token(token, "admin", "s" * 40, now=1001)
    assert claims is not None

    store.revoke_session(claims.session_id, claims.expires_at, now=1001)

    reopened_store = AuthStateStore(database_path)
    assert reopened_store.is_revoked(claims.session_id, now=1002)
    assert not reopened_store.is_revoked(claims.session_id, now=claims.expires_at + 1)


def test_login_attempts_are_limited_by_ip_and_identity(tmp_path) -> None:
    store = AuthStateStore(tmp_path / "auth-state.sqlite3")
    bucket_keys = login_bucket_keys("admin", "192.0.2.4")

    assert store.allow_login_attempt(bucket_keys, maximum_attempts=2, window_seconds=60, now=100)
    assert store.allow_login_attempt(bucket_keys, maximum_attempts=2, window_seconds=60, now=101)
    assert not store.allow_login_attempt(bucket_keys, maximum_attempts=2, window_seconds=60, now=102)
    assert store.allow_login_attempt(bucket_keys, maximum_attempts=2, window_seconds=60, now=161)

    store.reset_login_attempts(bucket_keys)
    assert store.allow_login_attempt(bucket_keys, maximum_attempts=2, window_seconds=60, now=162)


def test_concurrent_login_attempts_cannot_exceed_limit(tmp_path) -> None:
    store = AuthStateStore(tmp_path / "auth-state.sqlite3")
    bucket_keys = login_bucket_keys("admin", "192.0.2.8")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: store.allow_login_attempt(
                    bucket_keys,
                    maximum_attempts=3,
                    window_seconds=60,
                    now=100,
                ),
                range(8),
            )
        )

    assert sum(results) == 3
