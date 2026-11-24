"""Browser regression against an isolated Streamlit auth harness."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright

from auth_session import create_auth_token, derive_session_signing_key


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser_server(tmp_path_factory):
    state = tmp_path_factory.mktemp("auth-browser") / "auth.sqlite3"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["AUTH_TEST_STATE"] = str(state)
    env["PYTHONPATH"] = str(ROOT)
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "tests" / "fixtures" / "auth_browser_harness.py"),
         "--server.address", "127.0.0.1", "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                pytest.fail("Isolated Streamlit server exited before becoming ready")
            try:
                with urlopen(url + "/_stcore/health", timeout=1) as response:
                    if response.read() == b"ok":
                        break
            except (URLError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                pytest.fail("Isolated Streamlit server did not become ready")
            time.sleep(0.1)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=15)


def test_remembered_session_navigation_logout_and_isolation(browser_server):
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = playwright.chromium.launch(headless=True)
        first = browser.new_context()
        second = browser.new_context()
        page = first.new_page()
        page.goto(browser_server)
        page.get_by_label("Usuario").fill("admin", timeout=30000)
        page.get_by_label("Contraseña").fill("synthetic-password")
        page.get_by_label("Recordarme durante 30 días").check()
        page.get_by_role("button", name="Entrar").click()
        page.get_by_role("heading", name="Authenticated test page").wait_for(timeout=30000)

        cookies = [c for c in first.cookies() if c["name"] == "abc_auth_session"]
        assert len(cookies) == 1
        cookie = cookies[0]
        assert cookie["secure"] is True
        assert cookie["httpOnly"] is False
        assert cookie["sameSite"] == "Strict"
        assert cookie["path"] == "/"
        assert cookie["domain"] == "127.0.0.1"
        assert cookie["expires"] > time.time()
        for destination in ("Home", "Providers", "Luxottica"):
            page.get_by_label("Test navigation").click()
            page.get_by_role("option", name=destination, exact=True).click()
            page.reload()
            page.get_by_role("heading", name="Authenticated test page").wait_for(timeout=30000)

        other = second.new_page()
        other.goto(browser_server)
        other.get_by_label("Usuario").fill("other", timeout=30000)
        other.get_by_label("Contraseña").fill("other-synthetic-password")
        other.get_by_role("button", name="Entrar").click()
        other.get_by_text("Sesión: other").wait_for(timeout=30000)

        page.get_by_role("button", name="Cerrar sesión").click()
        page.get_by_text("Acceso restringido").wait_for(timeout=30000)
        page.reload()
        page.get_by_text("Acceso restringido").wait_for(timeout=30000)
        first.add_cookies([cookie])
        page.reload()
        page.get_by_text("Acceso restringido").wait_for(timeout=30000)
        other.reload()
        other.get_by_text("Sesión: other").wait_for(timeout=30000)
        browser.close()


def test_expired_cookie_does_not_restore_session(browser_server):
    key = derive_session_signing_key("synthetic-secret-" * 4, "admin", "synthetic-password")
    expired = create_auth_token("admin", key, days=1, now=1)
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies([{
            "name": "abc_auth_session", "value": expired,
            "domain": "127.0.0.1", "path": "/", "secure": True, "sameSite": "Strict",
        }])
        page = context.new_page()
        page.goto(browser_server)
        page.get_by_text("Acceso restringido").wait_for(timeout=30000)
        browser.close()
