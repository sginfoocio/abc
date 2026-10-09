from __future__ import annotations

import re
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

from kering_images import (
    PORTAL_URL, InterventionRequired, LoginFailed, PortalFailure, PortalTimeout,
    ProductNotFound, SessionExpired,
)


TIMEOUT_MS = 30000
PRODUCT_LIMIT = 40
ORIGINAL_INDEX = {"perspectiva": 0, "frontal": 1, "detalle": 2}
PRODUCT_SNAPSHOT = """() => {
    const fields = {};
    for (const label of document.querySelectorAll('.characteristics-title')) {
        fields[label.textContent.trim()] = label.nextElementSibling?.textContent.trim() || '';
    }
    const images = [...document.querySelectorAll('#imageModal .itemModal img')]
        .map(img => ({url: img.src, reference: img.alt}));
    const size = [...document.querySelectorAll('span')]
        .find(el => el.children.length === 0 && /^\\s*TALLA\\s/.test(el.textContent));
    return {ean: fields.EAN || '', upc: fields.UPC || '',
            reference: images[0]?.reference || '', size: size?.textContent.trim() || '', images};
}"""


def allowed_url(url: str, media: bool = False) -> bool:
    parsed = urlsplit(url)
    hosts = {"picture.kecdn.net"} if media else {"my.keringeyewear.com"}
    return (parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
            and not parsed.username and not parsed.password)


def exact_product(snapshot: dict, ean: str) -> bool:
    return (ean in (snapshot["ean"], snapshot["upc"])
            and bool(re.fullmatch(r".+-\d{3}", snapshot["reference"])) and bool(snapshot["size"]))


class KeringPortal:
    def __init__(self, config: dict):
        self._config = dict(config)
        self._manager = None
        self._browser = None
        self._context = None
        self._page = None
        self.identities = {}

    def __repr__(self):
        return "<KeringPortal>"

    def _start(self):
        if self._page is not None:
            return
        url = self._config.get("url", PORTAL_URL)
        if not allowed_url(url) or not self._config.get("username") or not self._config.get("password"):
            raise LoginFailed() from None
        self._manager = sync_playwright().start()
        self._browser = self._manager.chromium.launch(headless=True)
        self._context = self._browser.new_context(viewport={"width": 1440, "height": 1000})
        self._page = self._context.new_page()
        self._page.set_default_timeout(TIMEOUT_MS)
        self._page.set_default_navigation_timeout(TIMEOUT_MS)
        self._page.goto(url, wait_until="domcontentloaded")
        if not allowed_url(self._page.url):
            raise LoginFailed() from None
        self._login()

    def _intervention(self):
        selectors = 'input[autocomplete="one-time-code"], iframe[src*="recaptcha"], iframe[src*="hcaptcha"]'
        if self._page.locator(selectors).filter(visible=True).count():
            raise InterventionRequired() from None
        challenge = self._page.get_by_text(re.compile(
            r"^(Introduce el c[oó]digo de verificaci[oó]n|Verifica tu identidad|Enter verification code)", re.I))
        if challenge.count() and challenge.first.is_visible():
            raise InterventionRequired() from None

    def _authenticated(self):
        self._intervention()
        return self._page.locator('a[href="/es/logout"]').count() > 0

    def _login(self):
        if self._authenticated():
            return
        reject = self._page.get_by_role("button", name="Rechazarlas todas", exact=True)
        if reject.count() and reject.is_visible():
            reject.click()
        self._page.get_by_placeholder("MAIL", exact=True).fill(self._config["username"])
        self._page.get_by_placeholder("CONTRASEÑA", exact=True).fill(self._config["password"])
        with self._page.expect_navigation(wait_until="domcontentloaded", timeout=TIMEOUT_MS):
            self._page.get_by_role("button", name="Iniciar Sesión", exact=True).click()
        if not self._authenticated():
            raise LoginFailed() from None

    def _check_session(self):
        if not self._authenticated():
            raise SessionExpired() from None

    def _find_product(self, ean):
        self._check_session()
        self._page.locator('.showSearchBar').first.evaluate("el => el.click()")
        search = self._page.locator('#hard-js-site-search-input')
        search.fill(ean)
        with self._page.expect_navigation(wait_until="domcontentloaded", timeout=TIMEOUT_MS):
            search.press("Enter")
        self._check_session()
        links = self._page.locator('.plp-products-container .product-item a[href]').evaluate_all(
            "elements => [...new Set(elements.map(el => el.href).filter(href => /\\/p\\/\\d+$/.test(href)))]")
        for url in links[:PRODUCT_LIMIT]:
            if not allowed_url(url):
                continue
            self._page.goto(url, wait_until="domcontentloaded")
            self._check_session()
            snapshot = self._page.evaluate(PRODUCT_SNAPSHOT)
            if exact_product(snapshot, ean):
                reference, color = snapshot["reference"].rsplit("-", 1)
                self.identities[ean] = {"ean": snapshot["ean"], "upc": snapshot["upc"],
                                        "model": reference, "color": color, "size": snapshot["size"]}
                return snapshot
        raise ProductNotFound() from None

    def _download(self, snapshot, pending, collected):
        requested = set(pending)
        if "lateral" in requested:
            requested.remove("lateral")
            requested.add("detalle")
        for view in requested - collected.keys():
            index = ORIGINAL_INDEX[view]
            if index >= len(snapshot["images"]):
                continue
            image = snapshot["images"][index]
            if image["reference"] != snapshot["reference"] or not allowed_url(image["url"], media=True):
                continue
            content = self._read_image(image["url"])
            if content is not None:
                collected[view] = content

    def _read_image(self, url):
        response = self._context.request.get(url, timeout=TIMEOUT_MS, max_redirects=0)
        try:
            if response.status in (401, 403) or 300 <= response.status < 400:
                raise SessionExpired() from None
            mime = response.headers.get("content-type", "").split(";", 1)[0]
            if not response.ok or mime not in {"image/jpeg", "image/png", "image/webp"}:
                return None
            if int(response.headers.get("content-length", "0")) > 30 * 1024 * 1024:
                return None
            content = response.body()
            return content if len(content) <= 30 * 1024 * 1024 else None
        finally:
            response.dispose()

    def fetch(self, ean: str, pending: tuple[str, ...]) -> dict[str, bytes]:
        collected = {}
        try:
            for attempt in range(2):
                try:
                    self._start()
                    snapshot = self._find_product(ean)
                    self._download(snapshot, pending, collected)
                    return collected
                except SessionExpired:
                    self.close()
                    if attempt:
                        raise
        except BrowserTimeout:
            self._intervention_if_open()
            if collected:
                return collected
            raise PortalTimeout() from None
        except (PortalFailure, InterventionRequired):
            raise
        except Exception:
            raise PortalFailure() from None

    def _intervention_if_open(self):
        if self._page is not None:
            self._intervention()

    def check_access(self) -> dict:
        try:
            self._start()
            self._check_session()
            return {"ok": True, "code": "acceso_autenticado"}
        except InterventionRequired:
            return {"ok": False, "code": "intervencion_captcha_o_mfa"}
        except BrowserTimeout:
            return {"ok": False, "code": "timeout_portal"}
        except PortalFailure as error:
            return {"ok": False, "code": error.code}
        except Exception:
            return {"ok": False, "code": "fallo_portal"}
        finally:
            self.close()

    def close(self):
        for resource, operation in ((self._browser, "close"), (self._manager, "stop")):
            try:
                if resource is not None:
                    getattr(resource, operation)()
            except Exception:
                pass
        self._page = self._context = self._browser = self._manager = None