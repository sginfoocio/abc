"""Isolated browser harness executing the application's actual auth functions."""
import ast
import hmac
import os
from pathlib import Path
import secrets
import time

import streamlit as st
import streamlit.components.v1 as components

from auth_session import (
    AuthStateStore,
    create_auth_token,
    derive_session_signing_key,
    login_bucket_keys,
    verify_auth_token,
)

APP_TITLE = "Auth test"
AUTH_COOKIE_NAME = "abc_auth_session"
AUTH_COOKIE_SECRET_ENV = "AUTH_COOKIE_SECRET"
AUTH_COOKIE_SECRET_MIN_LENGTH = 32
AUTH_LOGIN_MAX_ATTEMPTS_ENV = "AUTH_LOGIN_MAX_ATTEMPTS"
AUTH_LOGIN_MAX_ATTEMPTS_DEFAULT = 8
AUTH_LOGIN_WINDOW_SECONDS_ENV = "AUTH_LOGIN_WINDOW_SECONDS"
AUTH_LOGIN_WINDOW_SECONDS_DEFAULT = 900
_store = AuthStateStore(Path(os.environ["AUTH_TEST_STATE"]))


def _get_auth_users():
    return {
        "admin": {"password": "synthetic-password", "role": "admin"},
        "other": {"password": "other-synthetic-password", "role": "masterdata"},
    }


def _get_auth_cookie_secret():
    return "synthetic-secret-" * 4


def _get_remember_days():
    return 30


def _get_auth_login_limit(_name, default):
    return default


def _get_auth_state_store():
    return _store


source = Path(__file__).resolve().parents[2] / "app_enhanced.py"
_auth_cookie_component = components.declare_component(
    "abc_auth_cookie", path=str(source.parent / "auth_cookie_component")
)
module = ast.parse(source.read_text(encoding="utf-8"))
names = {
    "_make_auth_token", "_verify_auth_token", "_queue_auth_cookie",
    "_read_auth_cookie", "_render_login", "require_authentication",
    "_revoke_auth_session", "render_sidebar_shell",
}
functions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in names]
exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"))

require_authentication()
render_sidebar_shell("Test")
st.selectbox("Test navigation", ["Home", "Providers", "Luxottica"])
st.title("Authenticated test page")
