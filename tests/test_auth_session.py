import ast
from concurrent.futures import ThreadPoolExecutor
import hmac
import os
from pathlib import Path
import time

import pytest

from auth_session import (
    AuthStateStore,
    PostgresAuthStateStore,
    create_auth_token,
    derive_session_signing_key,
    login_bucket_keys,
    verify_auth_token,
)


def _load_app_function(
    name: str, namespace: dict[str, object] | None = None
) -> dict[str, object]:
    source = Path(__file__).resolve().parents[1] / "app_enhanced.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    function_namespace = namespace if namespace is not None else {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        function_namespace,
    )
    return function_namespace


def test_signed_token_expires_and_rejects_tampering() -> None:
    signing_key = derive_session_signing_key("s" * 40, "admin", "old-password")
    token = create_auth_token("admin", signing_key, days=1, now=1000)

    claims = verify_auth_token(token, "admin", signing_key, now=1001)

    assert claims is not None
    assert claims.username == "admin"
    assert verify_auth_token(token, "admin", "wrong-secret", now=1001) is None
    assert verify_auth_token(token, "admin", signing_key, now=87400) is None
    assert verify_auth_token(token, "admin", signing_key, now=87401) is None
    changed_password_key = derive_session_signing_key("s" * 40, "admin", "new-password")
    assert verify_auth_token(token, "admin", changed_password_key, now=1001) is None
    rotated_secret_key = derive_session_signing_key("r" * 40, "admin", "old-password")
    assert verify_auth_token(token, "admin", rotated_secret_key, now=1001) is None


def test_remembered_token_creation_requires_independent_secret() -> None:
    namespace = {
        "AUTH_COOKIE_SECRET_ENV": "AUTH_COOKIE_SECRET",
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "create_auth_token": create_auth_token,
    }
    _load_app_function("_make_auth_token", namespace)

    with pytest.raises(ValueError, match="AUTH_COOKIE_SECRET"):
        namespace["_make_auth_token"]("admin", "", "password", 30)
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECRET"):
        namespace["_make_auth_token"]("admin", "short", "password", 30)

    token = namespace["_make_auth_token"]("admin", "s" * 32, "password", 30)
    assert verify_auth_token(
        token,
        "admin",
        derive_session_signing_key("s" * 32, "admin", "password"),
    ) is not None


@pytest.mark.parametrize("secret", ["", "short"])
def test_normal_login_succeeds_without_valid_remembered_session_secret(
    secret: str,
) -> None:
    class Rerun(Exception):
        pass

    class Form:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class StreamlitStub:
        context = type("Context", (), {"ip_address": "192.0.2.10"})()

        def __init__(self):
            self.session_state = {}
            self.infos = []
            self.checkbox_options = {}

        def title(self, _message):
            pass

        def subheader(self, _message):
            pass

        def info(self, _message):
            self.infos.append(_message)

        def error(self, _message):
            raise AssertionError(_message)

        def form(self, *_args, **_kwargs):
            return Form()

        def text_input(self, label, **_kwargs):
            return "admin" if label == "Usuario" else "correct-password"

        def checkbox(self, label, **kwargs):
            self.checkbox_options[label] = kwargs
            return False

        def form_submit_button(self, _label):
            return True

        def rerun(self):
            raise Rerun

        def stop(self):
            raise AssertionError("Successful login should not stop")

    class AuthStoreStub:
        def allow_login_attempt(self, *_args):
            return True

        def reset_login_attempts(self, *_args):
            pass

    class CookieControllerStub:
        def __init__(self):
            self.removed = []
            self.set_values = []

        def get(self, _name):
            return None

        def remove(self, name):
            self.removed.append(name)

        def set(self, *args, **kwargs):
            self.set_values.append((args, kwargs))

    streamlit = StreamlitStub()
    cookies = CookieControllerStub()
    namespace = {
        "st": streamlit,
        "APP_TITLE": "Cloud Diagonal",
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "AUTH_LOGIN_MAX_ATTEMPTS_ENV": "AUTH_LOGIN_MAX_ATTEMPTS",
        "AUTH_LOGIN_MAX_ATTEMPTS_DEFAULT": 8,
        "AUTH_LOGIN_WINDOW_SECONDS_ENV": "AUTH_LOGIN_WINDOW_SECONDS",
        "AUTH_LOGIN_WINDOW_SECONDS_DEFAULT": 900,
        "AUTH_COOKIE_NAME": "abc_auth_session",
        "AUTH_REMEMBER_DAYS_DEFAULT": 30,
        "AUTH_REMEMBER_DAYS_ENV": "AUTH_REMEMBER_DAYS",
        "AUTH_COOKIE_SECRET_ENV": "AUTH_COOKIE_SECRET",
        "hmac": hmac,
        "login_bucket_keys": login_bucket_keys,
        "derive_session_signing_key": derive_session_signing_key,
        "verify_auth_token": verify_auth_token,
        "_get_auth_users": lambda: {
            "admin": {"password": "correct-password", "role": "admin"}
        },
        "_get_remember_days": lambda: 30,
        "_get_auth_cookie_secret": lambda: secret,
        "_get_auth_state_store": lambda: AuthStoreStub(),
        "_get_auth_login_limit": lambda _name, default: default,
        "_get_cookie_controller": lambda: cookies,
        "_remove_auth_cookie": lambda: cookies.remove("abc_auth_session"),
        "_make_auth_token": lambda *_args: pytest.fail(
            "Remembered token creation must be disabled"
        ),
    }
    _load_app_function("_render_login", namespace)

    with pytest.raises(Rerun):
        namespace["_render_login"]()

    assert streamlit.session_state["authenticated"] is True
    assert streamlit.session_state["auth_role"] == "admin"
    assert "AUTH_COOKIE_SECRET" in streamlit.infos[0]
    assert streamlit.checkbox_options[
        "Recordarme durante 30 días"
    ]["disabled"]
    assert cookies.set_values == []
    assert cookies.removed == ["abc_auth_session"]


def test_remembered_tokens_from_password_fallback_are_rejected() -> None:
    legacy_secret = "old-password-fallback"
    now = int(time.time())
    token = create_auth_token(
        "admin",
        derive_session_signing_key(legacy_secret, "admin", legacy_secret),
        days=30,
        now=now,
    )
    namespace = {
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "verify_auth_token": verify_auth_token,
        "_get_auth_state_store": lambda: None,
    }
    _load_app_function("_verify_auth_token", namespace)

    verify = namespace["_verify_auth_token"]
    assert not verify(token, "admin", legacy_secret, legacy_secret)
    assert not verify(
        token,
        "admin",
        legacy_secret,
        "new-independent-secret-" + "x" * 16,
    )


def test_revocation_store_unavailable_fails_closed() -> None:
    import sqlite3

    now = int(time.time())
    token = create_auth_token(
        "admin",
        derive_session_signing_key("s" * 40, "admin", "password"),
        days=30,
        now=now,
    )

    class BrokenStore:
        def is_revoked(self, _session_id):
            raise sqlite3.OperationalError("database is unavailable")

    namespace = {
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "verify_auth_token": verify_auth_token,
        "_get_auth_state_store": BrokenStore,
    }
    _load_app_function("_verify_auth_token", namespace)

    with pytest.raises(
        sqlite3.OperationalError, match="database is unavailable"
    ):
        namespace["_verify_auth_token"](token, "admin", "password", "s" * 40)


def test_masterdata_role_cannot_access_admin_guarded_routes() -> None:
    class AccessDenied(Exception):
        pass

    class StreamlitStub:
        session_state = {"auth_role": "masterdata"}
        errors: list[str] = []

        def error(self, message: str) -> None:
            self.errors.append(message)

        def stop(self) -> None:
            raise AccessDenied

    streamlit = StreamlitStub()
    namespace = {"st": streamlit}
    _load_app_function("require_admin_access", namespace)

    with pytest.raises(AccessDenied):
        namespace["require_admin_access"]()
    assert streamlit.errors == ["Este apartado requiere un usuario administrador."]


def test_direct_price_comparator_route_requires_admin() -> None:
    class AccessDenied(Exception):
        pass

    def deny_admin_access() -> None:
        raise AccessDenied

    calls = []
    namespace = {
        "require_admin_access": deny_admin_access,
        "render_price_comparator_page": lambda: calls.append("rendered"),
    }
    _load_app_function("render_price_comparator_route", namespace)

    with pytest.raises(AccessDenied):
        namespace["render_price_comparator_route"]()
    assert calls == []


def test_price_comparator_route_renders_after_admin_authorization() -> None:
    calls = []
    namespace = {
        "require_admin_access": lambda: calls.append("authorized"),
        "render_price_comparator_page": lambda: calls.append("rendered"),
    }
    _load_app_function("render_price_comparator_route", namespace)

    namespace["render_price_comparator_route"]()

    assert calls == ["authorized", "rendered"]


def test_revoked_remembered_token_is_rejected_by_authentication(tmp_path) -> None:
    store = AuthStateStore(tmp_path / "auth-state.sqlite3")
    signing_key = derive_session_signing_key("s" * 40, "admin", "password")
    token = create_auth_token("admin", signing_key, days=1)
    claims = verify_auth_token(token, "admin", signing_key)
    assert claims is not None
    store.revoke_session(claims.session_id, claims.expires_at)

    namespace = {
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "verify_auth_token": verify_auth_token,
        "_get_auth_state_store": lambda: store,
    }
    _load_app_function("_verify_auth_token", namespace)

    assert not namespace["_verify_auth_token"](
        token, "admin", "password", "s" * 40
    )


def test_remembered_session_restores_after_app_recreation(tmp_path) -> None:
    secret = "persistent-independent-cookie-secret-" + "x" * 16
    state_path = tmp_path / "persistent-auth-state.sqlite3"
    credential = "synthetic-admin-credential"
    token_namespace = {
        "AUTH_COOKIE_SECRET_ENV": "AUTH_COOKIE_SECRET",
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "create_auth_token": create_auth_token,
    }
    _load_app_function("_make_auth_token", token_namespace)
    cookie = token_namespace["_make_auth_token"](
        "admin", secret, credential, 30
    )
    initial_store = AuthStateStore(state_path)

    class CookieControllerStub:
        def get(self, name):
            assert name == "abc_auth_session"
            return cookie

    class StreamlitStub:
        session_state = {}

    recreated_store = AuthStateStore(state_path)
    streamlit = StreamlitStub()
    namespace = {
        "st": streamlit,
        "AUTH_COOKIE_NAME": "abc_auth_session",
        "AUTH_COOKIE_SECRET_MIN_LENGTH": 32,
        "derive_session_signing_key": derive_session_signing_key,
        "verify_auth_token": verify_auth_token,
        "time": time,
        "_get_auth_users": lambda: {
            "admin": {"password": credential, "role": "admin"}
        },
        "_get_auth_cookie_secret": lambda: secret,
        "_get_auth_state_store": lambda: recreated_store,
        "_get_cookie_controller": lambda: CookieControllerStub(),
        "_render_login": lambda: pytest.fail(
            "A valid persistent cookie should restore the session"
        ),
    }
    _load_app_function("_verify_auth_token", namespace)
    _load_app_function("require_authentication", namespace)

    namespace["require_authentication"]()

    assert initial_store.database_path == recreated_store.database_path
    assert streamlit.session_state["authenticated"] is True
    assert streamlit.session_state["auth_user"] == "admin"
    assert streamlit.session_state["auth_role"] == "admin"
    assert streamlit.session_state["auth_session_id"]


def test_logout_revokes_persistent_remembered_session(tmp_path) -> None:
    store = AuthStateStore(tmp_path / "auth-state.sqlite3")
    session_id = "session-to-revoke"
    expires_at = int(time.time()) + 3600

    class StreamlitStub:
        session_state = {
            "auth_session_id": session_id,
            "auth_session_expires_at": expires_at,
        }

    namespace = {
        "st": StreamlitStub(),
        "_get_auth_state_store": lambda: store,
    }
    _load_app_function("_revoke_auth_session", namespace)

    namespace["_revoke_auth_session"]()

    assert store.is_revoked(session_id)


def test_auth_state_store_uses_postgres_when_configured(monkeypatch) -> None:
    database_url = "postgresql+psycopg2://db.example.test/cloud_auth"
    monkeypatch.setenv("AUTH_STATE_DATABASE_URL", database_url)

    class StreamlitStub:
        session_state = {}

    class StoreStub:
        def __init__(self, value):
            self.value = value

    namespace = {
        "AUTH_STATE_DATABASE_URL_ENV": "AUTH_STATE_DATABASE_URL",
        "AUTH_STATE_PATH_ENV": "AUTH_STATE_PATH",
        "AuthStateStore": StoreStub,
        "PostgresAuthStateStore": StoreStub,
        "_create_postgres_auth_state_store": StoreStub,
        "__file__": str(Path(__file__).resolve().parents[1] / "app_enhanced.py"),
        "Path": Path,
        "os": os,
        "st": StreamlitStub(),
    }
    _load_app_function("_get_auth_state_store", namespace)

    store = namespace["_get_auth_state_store"]()

    assert store.value == database_url


def test_auth_state_store_keeps_persistent_sqlite_fallback(
    monkeypatch, tmp_path
) -> None:
    state_path = tmp_path / "auth-state.sqlite3"
    monkeypatch.delenv("AUTH_STATE_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTH_STATE_PATH", str(state_path))

    class StreamlitStub:
        session_state = {}

    class StoreStub:
        def __init__(self, value):
            self.value = value

    namespace = {
        "AUTH_STATE_DATABASE_URL_ENV": "AUTH_STATE_DATABASE_URL",
        "AUTH_STATE_PATH_ENV": "AUTH_STATE_PATH",
        "AuthStateStore": StoreStub,
        "PostgresAuthStateStore": StoreStub,
        "_create_postgres_auth_state_store": StoreStub,
        "__file__": str(Path(__file__).resolve().parents[1] / "app_enhanced.py"),
        "Path": Path,
        "os": os,
        "st": StreamlitStub(),
    }
    _load_app_function("_get_auth_state_store", namespace)

    store = namespace["_get_auth_state_store"]()

    assert store.value == state_path


def test_postgres_auth_store_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="URL PostgreSQL"):
        PostgresAuthStateStore("sqlite:///not-a-shared-database.sqlite3")


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


def _load_auth_cookie_remover(controller):
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app_enhanced.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_remove_auth_cookie")
    namespace = {"_get_cookie_controller": lambda: controller, "AUTH_COOKIE_NAME": "synthetic_auth_cookie"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_remove_auth_cookie"]


def test_remove_auth_cookie_is_idempotent_with_real_controller(monkeypatch):
    import streamlit_cookies_controller.cookie_controller as cookies_module

    commands = []
    monkeypatch.setattr(cookies_module, "_cookie_controller", lambda **kwargs: commands.append(kwargs))
    controller = cookies_module.CookieController.__new__(cookies_module.CookieController)
    controller._CookieController__cookies = {}
    remove = _load_auth_cookie_remover(controller)

    remove()
    controller._CookieController__cookies["synthetic_auth_cookie"] = "synthetic_token"
    remove()
    remove()

    assert controller._CookieController__cookies == {}
    assert len(commands) == 3
    assert all(command["method"] == "remove" and command["name"] == "synthetic_auth_cookie" for command in commands)


def test_remove_auth_cookie_does_not_hide_unrelated_errors():
    import pytest

    class BrokenController:
        def remove(self, name):
            raise KeyError("unrelated_key")

    remove = _load_auth_cookie_remover(BrokenController())
    with pytest.raises(KeyError, match="unrelated_key"):
        remove()
