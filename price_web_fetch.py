"""Polite, permission-aware HTTP access to public competitor pages.

Rules enforced here (not left to callers): robots.txt is honoured for every URL, requests are paced per
host and capped per run, the agent identifies itself, and a 401/403/407/429 response or a CAPTCHA
stops all further requests to that host. There is no proxy, IP rotation, CAPTCHA solving or evasion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import gzip
import os
import re
import time
from typing import Callable, Protocol
from urllib.parse import urljoin, urlparse

from price_connectors import BLOCK_STATUSES, ConnectorBlocked, ConnectorError, _assert_public_host

AGENT_TOKEN = "Cloud-PriceComparator"
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_SITEMAP_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
_CAPTCHA = re.compile(r"captcha|are you a robot|verify you are human|cf-challenge|access denied|"
                      r"unusual traffic|just a moment\.\.\.", re.I)
Transport = Callable[[str, dict, float], "tuple[int, dict, bytes]"]


def user_agent(agent_token: str = AGENT_TOKEN) -> str:
    contact = os.getenv("PRICE_WEB_CONTACT", "").strip()
    return f"{agent_token}/1.0 (comparador de precios de Diagonal Eyewear" + (
        f"; {contact})" if contact else ")")


def _http_datetime(value: str) -> datetime:
    parsed = parsedate_to_datetime(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class RobotsCache(Protocol):
    def get(self, origin: str, agent: str) -> tuple[int, str] | None: ...
    def put(self, origin: str, agent: str, status: int, headers: dict, body: bytes) -> None: ...


class PersistentRobotsCache:
    """SQLite cache for successful robots responses, scoped to an origin and user-agent."""

    def __init__(self, store, *, clock: Callable[[], datetime], ttl: timedelta = timedelta(hours=24)) -> None:
        self.store, self._now, self.ttl = store, clock, ttl

    def get(self, origin: str, agent: str) -> tuple[int, str] | None:
        now = self._now().astimezone(timezone.utc)
        with self.store._connect() as con:
            row = con.execute("""SELECT status,rules_text,expires_at FROM price_robots_cache
                WHERE origin=? AND agent=?""", (origin, agent)).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            with self.store._connect() as con, con:
                con.execute("DELETE FROM price_robots_cache WHERE origin=? AND agent=?", (origin, agent))
            return None
        return int(row["status"]), row["rules_text"]

    def put(self, origin: str, agent: str, status: int, headers: dict, body: bytes) -> None:
        if status not in {200, 404, 410}:
            return
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        directives = [part.strip().lower() for part in normalized.get("cache-control", "").split(",")]
        directive_names = {part.partition("=")[0].strip() for part in directives}
        vary = {part.strip().lower() for part in normalized.get("vary", "").split(",")}
        if ({"no-store", "no-cache", "private"} & directive_names or "*" in vary):
            with self.store._connect() as con, con:
                con.execute("DELETE FROM price_robots_cache WHERE origin=? AND agent=?", (origin, agent))
            return

        now = self._now().astimezone(timezone.utc)
        expires_at = now + self.ttl
        age_directive = None
        for name in ("s-maxage", "max-age"):
            age_directive = next((match for value in directives if value.startswith(name)
                                  for match in [re.fullmatch(r'(?:s-maxage|max-age)\s*=\s*"?(\d+)"?', value)]
                                  if match), None)
            if age_directive:
                break
        if age_directive:
            response_age = normalized.get("age", "").strip()
            try:
                age = max(0, int(response_age)) if response_age else 0
            except ValueError:
                age = 0
            expires_at = min(expires_at, now + timedelta(seconds=max(0, int(age_directive.group(1)) - age)))
        expires = normalized.get("expires")
        if expires:
            try:
                expires_at = min(expires_at, _http_datetime(expires))
            except (TypeError, ValueError, OverflowError):
                pass
        if expires_at <= now:
            with self.store._connect() as con, con:
                con.execute("DELETE FROM price_robots_cache WHERE origin=? AND agent=?", (origin, agent))
            return
        with self.store._connect() as con, con:
            con.execute("""INSERT INTO price_robots_cache(origin,agent,status,rules_text,fetched_at,expires_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(origin,agent) DO UPDATE SET
                status=excluded.status,rules_text=excluded.rules_text,fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at""",
                        (origin, agent, status, body.decode("utf-8", errors="replace"),
                         now.isoformat(), expires_at.isoformat()))


class RobotsDenied(ConnectorError):
    """robots.txt does not allow this URL for our agent (or could not be read)."""


class PageGone(ConnectorError):
    """404/410: the stored URL no longer exists and must be located again."""


@dataclass
class RobotsRules:
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    allow_all: bool = False
    deny_all: bool = False

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        rules = cls()
        agents: list[str] = []
        entries: list[tuple[bool, str]] = []
        collecting_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            name, value = (part.strip() for part in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if not collecting_agents and agents:
                    rules.groups.append((agents, entries))
                    agents, entries = [], []
                agents.append(value.lower())
                collecting_agents = True
                continue
            collecting_agents = False
            if name == "sitemap" and value:
                rules.sitemaps.append(value)
            elif name in {"allow", "disallow"} and agents:
                entries.append((name == "allow", value))
        if agents:
            rules.groups.append((agents, entries))
        return rules

    def _entries(self, agent_token: str = AGENT_TOKEN) -> list[tuple[bool, str]]:
        token = agent_token.lower()
        specific = [entries for agents, entries in self.groups if any(a != "*" and a in token for a in agents)]
        generic = [entries for agents, entries in self.groups if "*" in agents]
        return [item for group in (specific or generic) for item in group]

    @staticmethod
    def _matches(pattern: str, target: str) -> bool:
        if not pattern:
            return False
        regex = re.escape(pattern).replace(r"\*", ".*")
        regex = regex[:-2] + "$" if regex.endswith(r"\$") else regex
        return re.match(regex, target) is not None

    def allowed(self, url: str, agent_token: str = AGENT_TOKEN) -> bool:
        if self.deny_all:
            return False
        if self.allow_all:
            return True
        parsed = urlparse(url)
        target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        best_length, verdict = -1, True
        for allow, pattern in self._entries(agent_token):
            if self._matches(pattern, target) and (len(pattern) > best_length
                                                   or (len(pattern) == best_length and allow)):
                best_length, verdict = len(pattern), allow
        return verdict


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes
    requested_url: str = ""
    redirects: list[dict[str, str | int]] = field(default_factory=list)

    @property
    def trace(self) -> dict:
        if not self.requested_url:
            return {"final_url": self.url}
        return {"requested_url": self.requested_url, "final_url": self.url, "redirects": self.redirects}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _requests_transport(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    import requests

    _assert_public_host(urlparse(url).hostname or "")
    try:
        response = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
    except requests.RequestException as exc:
        raise ConnectorError(f"Error de conexión: {type(exc).__name__}") from exc
    with response:
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > MAX_SITEMAP_BYTES:
                raise ConnectorError("La respuesta supera el tamaño máximo permitido")
        return response.status_code, dict(response.headers), bytes(body)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class WebFetcher:
    """One instance per competitor run: owns the request budget, the pacing and the block memory."""

    def __init__(self, *, transport: Transport = _requests_transport, pause_seconds: float = 3.0,
                 max_requests: int = 10, timeout: float = 15.0, retries: int = 1,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 agent_token: str = AGENT_TOKEN, robots_cache: RobotsCache | None = None) -> None:
        self._transport, self._sleep, self._clock = transport, sleep, clock
        self.agent_token, self.robots_cache = agent_token, robots_cache
        self.pause_seconds, self.max_requests, self.timeout, self.retries = pause_seconds, max_requests, timeout, retries
        self.requests = 0
        self.blocked: dict[str, str] = {}
        self._robots: dict[str, RobotsRules] = {}
        self._robots_state: dict[str, str] = {}
        self._last_request = 0.0

    def _request(self, url: str, *, max_bytes: int) -> tuple[int, dict, bytes]:
        host = _host(url)
        if host in self.blocked:
            raise ConnectorBlocked(f"Consultas detenidas en {host}: {self.blocked[host]}")
        if self.requests >= self.max_requests:
            raise ConnectorError("Límite de consultas de la ejecución alcanzado")
        headers = {"User-Agent": user_agent(self.agent_token),
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                   "Accept-Language": "es-ES,es;q=0.9"}
        attempt = 0
        while True:
            wait = self.pause_seconds - (self._clock() - self._last_request)
            if self._last_request and wait > 0:
                self._sleep(wait)
            self.requests += 1
            self._last_request = self._clock()
            try:
                status, response_headers, body = self._transport(url, headers, self.timeout)
            except ConnectorBlocked:
                raise
            except ConnectorError:
                attempt += 1
                if attempt > self.retries or self.requests >= self.max_requests:
                    raise
                continue
            if status in BLOCK_STATUSES:
                self.blocked[host] = f"HTTP {status}"
                raise ConnectorBlocked(f"El sitio rechazó o limitó la petición (HTTP {status}); no se reintenta")
            if len(body) > max_bytes:
                raise ConnectorError("La respuesta supera el tamaño máximo permitido")
            return status, response_headers, body

    def rules(self, url: str) -> RobotsRules:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            agent = user_agent(self.agent_token)
            cached = self.robots_cache.get(origin, agent) if self.robots_cache else None
            if cached:
                status, text = cached
                body = text.encode("utf-8")
                state_suffix = " (caché vigente)"
            else:
                status, headers, body = self._request(origin + "/robots.txt", max_bytes=MAX_PAGE_BYTES)
                if self.robots_cache:
                    self.robots_cache.put(origin, agent, status, headers, body)
                state_suffix = ""
            if status == 200:
                rules, state = RobotsRules.parse(body.decode("utf-8", errors="replace")), "robots.txt leído"
            elif status in {404, 410}:
                rules, state = RobotsRules(allow_all=True), "sin robots.txt (sin restricciones publicadas)"
            else:
                rules, state = RobotsRules(deny_all=True), f"robots.txt no disponible (HTTP {status}): se asume denegado"
            self._robots[origin], self._robots_state[origin] = rules, state + state_suffix
        return self._robots[origin]

    def robots_state(self, url: str) -> str:
        parsed = urlparse(url)
        return self._robots_state.get(f"{parsed.scheme}://{parsed.netloc}", "")

    def inspect_robots(self, site_url: str) -> RobotsRules:
        """Configuration-only read: one HTTPS /robots.txt, no retries, redirects or product requests."""
        parsed = urlparse(site_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None \
                or parsed.password is not None or parsed.port not in (None, 443):
            raise ConnectorError("La comprobación de robots.txt requiere un origen HTTPS sin credenciales")
        origin = f"https://{parsed.netloc}"
        probe = WebFetcher(transport=self._transport, pause_seconds=self.pause_seconds,
                           max_requests=min(1, max(0, self.max_requests - self.requests)),
                           timeout=self.timeout, retries=0, sleep=self._sleep, clock=self._clock,
                           agent_token=self.agent_token)
        probe._last_request = self._last_request
        probe.blocked = dict(self.blocked)
        try:
            status, headers, body = probe._request(origin + "/robots.txt", max_bytes=MAX_PAGE_BYTES)
            text = body.decode("utf-8", errors="replace")
            content_type = headers.get("Content-Type") or headers.get("content-type") or ""
            html = bool("text/html" in content_type.lower() or
                        re.search(r"<(?:!doctype\s+html|html|head|body|script)\b", text[:1000], re.I))
            if _CAPTCHA.search(text[:20000]) and (html or not re.search(r"^user-agent\s*:", text, re.I | re.M)):
                probe.blocked[_host(origin)] = "CAPTCHA o control antibot"
                raise ConnectorBlocked("robots.txt presenta un control antibot/CAPTCHA; no se intenta superarlo")
            if status != 200:
                raise ConnectorError(f"robots.txt no disponible (HTTP {status}); no se siguen redirecciones")
            if html:
                raise ConnectorError("robots.txt no disponible: el servidor devolvió HTML en lugar de reglas")
            rules = RobotsRules.parse(text)
            self._robots[origin] = rules
            self._robots_state[origin] = "robots.txt leído"
            return rules
        finally:
            # Preserve the caller's budget/block memory when a manual URL verification shares a fetcher.
            self.requests += probe.requests
            self._last_request = probe._last_request
            self.blocked.update(probe.blocked)

    def get(self, url: str, *, max_bytes: int = MAX_PAGE_BYTES) -> Fetched:
        if urlparse(url).scheme != "https":
            raise ConnectorError("Solo se consultan páginas HTTPS")
        requested_url = url
        redirects: list[dict[str, str | int]] = []
        origin_host = _host(url)
        for _ in range(MAX_REDIRECTS + 1):
            if not self.rules(url).allowed(url, self.agent_token):
                raise RobotsDenied("robots.txt no permite consultar esta ruta")
            status, headers, body = self._request(url, max_bytes=max_bytes)
            if status in {301, 302, 303, 307, 308}:
                target = urljoin(url, headers.get("Location") or headers.get("location") or "")
                if _host(target) != origin_host:
                    raise ConnectorError("Redirección a otro dominio: no se sigue")
                redirects.append({"url": url, "status": status, "location": target})
                url = target
                continue
            if status in {404, 410}:
                raise PageGone(f"La página ya no existe (HTTP {status})")
            if status >= 400:
                raise ConnectorError(f"El sitio respondió HTTP {status}")
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)[:max_bytes]
            if _CAPTCHA.search(body[:20000].decode("utf-8", errors="ignore")) and b"application/ld+json" not in body:
                self.blocked[origin_host] = "CAPTCHA o control antibot"
                raise ConnectorBlocked("El sitio presenta un control antibot/CAPTCHA; no se intenta superarlo")
            return Fetched(url, status, body, requested_url, redirects)
        raise ConnectorError("Demasiadas redirecciones")
