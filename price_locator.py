"""Self-hosted automatic locator of the scalable price watch (phase 2).

For every watched product and active competitor a persistent job (``locate_jobs``) looks for the competitor's
product page without anyone typing URLs:

1. a verified URL already stored in ``competitor_product_urls`` is reused without any request;
2. the local, incremental ``discovery_index`` (pages already seen in sitemaps, brand/category listings, feeds
   or internal searches of that competitor) is matched by model and colour code;
3. only when the index has no candidate, the competitor's *authorised* discovery sources are refreshed
   (feed → sitemap → catalogue/brand pages → internal search → optional external hook) if their copy expired;
4. each candidate page is fetched once (``ficha``), its identity facts are cached in the index and reused for
   every other product, and it is classified with the agreed commercial rule: brand + model + colour code.
   Calibre never rejects a page; a published EAN is optional and a contradictory one goes to review.

Exact, unambiguous matches are confirmed automatically into ``competitor_product_urls`` (source
``auto_watch``) with the evidence; doubtful ones go to human review. Every HTTP request goes through
:class:`price_access.GovernedFetcher` (authorisation per method, budgets, pauses, CAPTCHA/403/429
incidents). Nothing is bypassed: when no authorised method exists the job is recorded as «localización no
disponible por restricciones de acceso», never as a missing product. Monitoring prices is a separate phase.

Jobs are leased, so an interrupted run is recovered by the next one. The supervised pilot pairs are skipped.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
import time
from typing import Callable
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

from filelock import Timeout
import pandas as pd

from price_access import METHODS, AccessControl, AccessDenied, GovernedFetcher, external_search_enabled
from price_comparator import PriceComparatorStore
from price_connectors import ConnectorBlocked, ConnectorError
from price_references import EXACT, PROBABLE, REJECTED, classify, key, model_key, normalize_caliber, tokens
from price_web import (_LOC, _PRODUCT_SITEMAP, Outcome, WebPriceSearch, WebSearchError, _same_site,
                       product_search_urls, validate_competitor_url)
from price_web_extract import ProductPage, extract_product, parse_html
from price_web_fetch import MAX_SITEMAP_BYTES, PageGone, RobotsDenied, _requests_transport

JOB_STATES = {
    "pending": "Pendiente",
    "running": "En curso",
    "waiting": "En espera (límites o pausa del sitio)",
    "done": "Ficha verificada",
    "review": "Revisión manual",
    "unavailable": "Localización no disponible por restricciones de acceso",
    "not_found": "Sin ficha localizada (se reintentará)",
    "blocked": "Bloqueado (requiere revisión humana)",
    "failed": "Fallido tras varios intentos",
    "pilot": "Gestionado por el piloto supervisado",
}
CLAIMABLE = ("pending", "waiting", "not_found", "unavailable", "blocked")
SETTLED = ("done", "review", "not_found", "unavailable", "pilot")
DISCOVERY_ORDER = ("feed", "sitemap", "catalog", "site_search", "external")
SOURCE_METHODS = ("catalog", "feed")
UNAVAILABLE_TEXT = "Localización no disponible por restricciones de acceso"
LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RETRY_NOT_FOUND = timedelta(days=7)
RETRY_UNAVAILABLE = timedelta(days=1)
RETRY_ERROR = timedelta(minutes=30)
FACTS_TTL = timedelta(days=30)
SITEMAP_REFRESH_HOURS = 168.0
MAX_SITEMAP_FILES = 20
MAX_VISITS_PER_JOB = 6
MAX_SPAN = 4
PRIORITY_NEW, PRIORITY_RELOCATE, PRIORITY_RETRY = 10, 20, 50
# Discovery routes known to be allowed by robots.txt. A source is configuration, not an authorisation:
# the ``catalog`` and ``ficha`` methods must still be authorised for the competitor.
DEFAULT_SOURCES = {
    "Domanci": [("catalog", "https://domanci.com/es/marca/{brand}",
                 "Páginas de marca permitidas por robots.txt (revisado el 2026-11-03; /es/busqueda prohibida). "
                 "Requiere autorizar «catalog» y «ficha» según sus condiciones de uso.")],
}


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def signature(url: str, label: str = "") -> str:
    """Identity tokens of a link: URL path and anchor/feed text, normalised (case, accents, separators)."""
    return " ".join(tokens(unquote(urlparse(url).path) + " " + label))


def _spans(words: list[str]):
    for start in range(len(words)):
        joined = ""
        for end in range(start, min(start + MAX_SPAN, len(words))):
            joined += key(words[end])
            yield start, end, joined


def link_matches(sig: str, model: str, color: str) -> bool:
    """Model and colour code as separate, contiguous token runs (``pr-27zs-16k08z``, ``0PR 27ZS``, ``FT1044S``).

    Only a pre-filter: the page itself is classified before anything is accepted."""
    wanted_model, wanted_color = model_key(model), key(color)
    if not wanted_model or not wanted_color:
        return False
    spans = list(_spans(sig.split()))
    models = [(a, b) for a, b, value in spans
              if model_key(value) == wanted_model or (value.startswith("0") and model_key(value[1:]) == wanted_model)]
    colors = [(a, b) for a, b, value in spans if value == wanted_color]
    return any(cb < ma or ca > mb for ma, mb in models for ca, cb in colors)


def _next_page(current: str, links: list[tuple[str, str]]) -> str:
    here = urlparse(current)
    params = dict(parse_qsl(here.query))
    value = params.get("page") or params.get("p") or "1"
    number = int(value) if value.isdigit() else 1
    for href, _ in links:
        target = urlparse(urljoin(current, href))
        if target.netloc.lower() != here.netloc.lower() or target.path != here.path:
            continue
        query = dict(parse_qsl(target.query))
        for name in ("page", "p"):
            if query.get(name, "").isdigit() and int(query[name]) == number + 1:
                return target._replace(fragment="").geturl()
    return ""


@dataclass
class RunReport:
    processed: int = 0
    states: Counter = field(default_factory=Counter)
    requests: int = 0
    stopped: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = ", ".join(f"{JOB_STATES.get(state, state).lower()}: {count}" for state, count in self.states.items())
        return f"{self.processed} trabajos procesados ({parts or 'ninguno'}); {self.requests} peticiones HTTP"


@dataclass
class _Result:
    state: str
    url: str = ""
    method: str = ""
    detail: str = ""


def _expired(deadline_seconds: float | None, started: float, monotonic: Callable[[], float]) -> bool:
    return deadline_seconds is not None and monotonic() - started > deadline_seconds


class _Stop(Exception):
    """The competitor cannot be queried now (budget, 429, CAPTCHA/403): stop it, keep the job."""

    def __init__(self, state: str, detail: str, retry_at: datetime | None = None) -> None:
        super().__init__(detail)
        self.state, self.detail, self.retry_at = state, detail, retry_at


class PriceLocator:
    def __init__(self, store: PriceComparatorStore, *, transport=_requests_transport,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], datetime] = _utc,
                 external_search: Callable[[dict, dict], list[str]] | None = None,
                 external_provider: str = "external") -> None:
        self.store = store
        self.access = AccessControl(store, clock=clock)
        self.search = WebPriceSearch(store, clock=clock)
        self._transport, self._sleep, self._now = transport, sleep, clock
        # Optional extension point (product, config) -> URLs; refused unless PRICE_EXTERNAL_SEARCH_ENABLED.
        self.external_search, self.external_provider = external_search, external_provider

    # queue ------------------------------------------------------------------------------------
    def enqueue(self) -> dict[str, int]:
        """Create the missing product × competitor jobs (idempotent) and reopen what needs another look:
        dead verified pages (relocation) and pairs previously without an authorised method."""
        now = _iso(self._now())
        self.search.ensure_config()
        with self.store._connect() as connection, connection:
            created = connection.execute("""
                INSERT INTO locate_jobs(ean,competitor_id,kind,state,priority,detail,created_at,updated_at)
                SELECT w.ean,c.id,'locate',
                       CASE WHEN EXISTS(SELECT 1 FROM competitor_product_urls u WHERE u.competitor_id=c.id
                                        AND u.ean=w.ean AND u.status='verified') THEN 'done' ELSE 'pending' END,
                       ?, '', ?, ?
                FROM watch_products w CROSS JOIN competitors c WHERE w.active=1 AND c.active=1
                ON CONFLICT(competitor_id,ean) DO NOTHING""", (PRIORITY_NEW, now, now)).rowcount
            relocate = connection.execute("""
                UPDATE locate_jobs SET kind='relocate',state='pending',priority=?,attempts=0,not_before=NULL,
                    detail='La ficha verificada dejó de responder: se busca su nueva dirección',updated_at=?
                WHERE state='done' AND EXISTS(SELECT 1 FROM competitor_product_urls u WHERE
                        u.competitor_id=locate_jobs.competitor_id AND u.ean=locate_jobs.ean
                        AND u.status='needs_relocation')
                  AND NOT EXISTS(SELECT 1 FROM competitor_product_urls u WHERE
                        u.competitor_id=locate_jobs.competitor_id AND u.ean=locate_jobs.ean AND u.status='verified')
                """, (PRIORITY_RELOCATE, now)).rowcount
            reopened = connection.execute("""UPDATE locate_jobs SET not_before=NULL,updated_at=?
                WHERE state='unavailable' AND not_before IS NOT NULL""", (now,)).rowcount
        return {"created": created, "relocate": relocate, "reopened": reopened}

    def _claimable_sql(self) -> str:
        return f"""j.competitor_id=? AND EXISTS(SELECT 1 FROM watch_products w WHERE w.ean=j.ean AND w.active=1)
            AND ((j.state IN {CLAIMABLE} AND (j.not_before IS NULL OR j.not_before<=?))
                 OR (j.state='running' AND j.lease_until<=?))"""

    def _claim(self, competitor_id: str) -> dict | None:
        now = self._now()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                while True:
                    row = connection.execute(f"""SELECT j.* FROM locate_jobs j WHERE {self._claimable_sql()}
                        ORDER BY j.priority,j.id LIMIT 1""", (competitor_id, _iso(now), _iso(now))).fetchone()
                    if row is None:
                        connection.commit()
                        return None
                    if row["attempts"] >= MAX_ATTEMPTS:
                        connection.execute("""UPDATE locate_jobs SET state='failed',lease_until=NULL,updated_at=?,
                            detail='Interrumpido o con error en varios intentos: revisar' WHERE id=?""",
                                           (_iso(now), row["id"]))
                        self._event(connection, row["id"], "failed", "", "", "Demasiados intentos", 0)
                        continue
                    connection.execute("""UPDATE locate_jobs SET state='running',attempts=attempts+1,lease_until=?,
                        updated_at=? WHERE id=?""", (_iso(now + LEASE), _iso(now), row["id"]))
                    connection.commit()
                    return dict(row)
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def _finish(self, job: dict, result: _Result, *, requests: int = 0, not_before: datetime | None = None,
                refund: bool = False) -> None:
        now = self._now()
        if not_before is None:
            not_before = {"not_found": now + RETRY_NOT_FOUND, "unavailable": now + RETRY_UNAVAILABLE,
                          "pending": now + RETRY_ERROR}.get(result.state)
        with self.store._connect() as connection, connection:
            connection.execute("""UPDATE locate_jobs SET state=?,url=?,method=?,detail=?,not_before=?,lease_until=NULL,
                attempts=CASE WHEN ? THEN MAX(attempts-1,0) WHEN ? THEN 0 ELSE attempts END,updated_at=? WHERE id=?""",
                               (result.state, result.url, result.method, result.detail[:1000],
                                _iso(not_before) if not_before else None, int(refund),
                                int(result.state in SETTLED), _iso(now), job["id"]))
            self._event(connection, job["id"], result.state, result.method, result.url, result.detail, requests)

    def _event(self, connection, job_id: int, state: str, method: str, url: str, detail: str, requests: int) -> None:
        connection.execute("""INSERT INTO locate_events(job_id,at,state,method,url,detail,requests)
            VALUES(?,?,?,?,?,?,?)""", (job_id, _iso(self._now()), state, method, url[:500], detail[:1000], requests))

    def _settle_all(self, competitor_id: str, state: str, detail: str, not_before: datetime | None,
                    pilot: set[str]) -> int:
        """Bulk transition without any request (no authorised method, or the whole competitor is suspended)."""
        now = _iso(self._now())
        excluded = sorted(pilot)
        marks = ",".join("?" * len(excluded))
        with self.store._connect() as connection, connection:
            if excluded:
                connection.execute(f"""UPDATE locate_jobs SET state='pilot',lease_until=NULL,updated_at=?,
                    detail='Par incluido en el piloto supervisado: se gestiona allí'
                    WHERE competitor_id=? AND ean IN ({marks}) AND state<>'pilot'""", (now, competitor_id, *excluded))
            return connection.execute(f"""UPDATE locate_jobs SET state=?,detail=?,not_before=?,lease_until=NULL,
                updated_at=? WHERE id IN (SELECT j.id FROM locate_jobs j WHERE {self._claimable_sql()}
                {f'AND j.ean NOT IN ({marks})' if excluded else ''})""",
                                      (state, detail[:1000], _iso(not_before) if not_before else None, now,
                                       competitor_id, now, now, *excluded)).rowcount

    # run --------------------------------------------------------------------------------------
    def run(self, competitor_ids: list[str] | None = None, *, max_jobs: int = 200,
            deadline_seconds: float | None = None, monotonic: Callable[[], float] = time.monotonic) -> RunReport:
        """Process a bounded batch. Competitors are taken one after another, each under the shared lock."""
        from price_pilot import pilot_scope

        report = RunReport()
        scope = pilot_scope(self.store)
        started = monotonic()
        with self.store._connect() as connection:
            ids = competitor_ids or [row[0] for row in connection.execute("""SELECT c.id FROM competitors c
                WHERE c.active=1 AND EXISTS(SELECT 1 FROM locate_jobs j WHERE j.competitor_id=c.id) ORDER BY c.name""")]
        for competitor_id in ids:
            if report.processed >= max_jobs or _expired(deadline_seconds, started, monotonic):
                break
            pilot = set(scope["eans"]) if scope and competitor_id in scope["competitors"] else set()
            try:
                with self.search._lock(competitor_id):
                    self._run_competitor(competitor_id, pilot, report, max_jobs, deadline_seconds, started, monotonic)
            except Timeout:
                report.stopped[competitor_id] = "Otra ejecución está consultando a este competidor"
        return report

    def _run_competitor(self, competitor_id: str, pilot: set[str], report: RunReport, max_jobs: int,
                        deadline_seconds: float | None, started: float, monotonic) -> None:
        session = _Session(self, competitor_id)
        gate = self.access.check(competitor_id, "ficha")
        if not gate.allowed and not gate.waitable and gate.code not in ("not_authorized", "prohibited"):
            state = "blocked" if gate.code == "blocked_manual" else "waiting"
            report.states[state] += self._settle_all(competitor_id, state, gate.message, gate.retry_at, pilot)
            report.stopped[competitor_id] = gate.message
            return
        if "ficha" not in session.methods:
            detail = f"{UNAVAILABLE_TEXT}: consulta de fichas no autorizada para este competidor"
            report.states["unavailable"] += self._settle_all(competitor_id, "unavailable", detail,
                                                             self._now() + RETRY_UNAVAILABLE, pilot)
            return
        try:
            while report.processed < max_jobs and not _expired(deadline_seconds, started, monotonic):
                job = self._claim(competitor_id)
                if job is None:
                    break
                report.processed += 1
                before = session.requests
                try:
                    result = (_Result("pilot", detail="Par incluido en el piloto supervisado: se gestiona allí")
                              if job["ean"] in pilot else session.process(job))
                    self._finish(job, result, requests=session.requests - before)
                    report.states[result.state] += 1
                except _Stop as stop:
                    self._finish(job, _Result(stop.state, detail=stop.detail), requests=session.requests - before,
                                 not_before=stop.retry_at, refund=True)
                    report.states[stop.state] += 1
                    # The whole competitor is suspended, not just this job: no request is attempted for the rest.
                    report.states[stop.state] += self._settle_all(competitor_id, stop.state, stop.detail,
                                                                  stop.retry_at, pilot)
                    report.stopped[competitor_id] = stop.detail
                    break
                except (ConnectorError, WebSearchError, ValueError) as exc:
                    self._finish(job, _Result("pending", detail=f"Error temporal: {exc}"),
                                 requests=session.requests - before)
                    report.states["pending"] += 1
        finally:
            report.requests += session.requests

    # human review -----------------------------------------------------------------------------
    def review(self, ean: str, competitor_id: str, url: str, *, accept: bool, actor: str, note: str) -> None:
        """Decide a doubtful candidate. A rejection keeps it out of future automatic checks."""
        actor, note = actor.strip(), note.strip()
        if not actor or not note:
            raise ValueError("Indique quién revisa y el motivo")
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            row = connection.execute("""SELECT status FROM competitor_product_urls WHERE competitor_id=? AND ean=?
                AND url=?""", (competitor_id, ean, url)).fetchone()
            if row is None or row["status"] not in ("probable", "pending_verification"):
                raise ValueError("No hay ningún candidato pendiente con esa URL")
            connection.execute("""UPDATE competitor_product_urls SET status=?,source=?,note=?,verified_at=?,
                match_status=CASE WHEN ? THEN 'exact_match' ELSE match_status END
                WHERE competitor_id=? AND ean=? AND url=?""",
                               ("verified" if accept else "rejected", "user" if accept else "auto_watch",
                                f"Revisión de {actor}: {note}", now, int(accept), competitor_id, ean, url))
            job = connection.execute("SELECT id FROM locate_jobs WHERE competitor_id=? AND ean=?",
                                     (competitor_id, ean)).fetchone()
            if job is not None:
                state = "done" if accept else "pending"
                connection.execute("""UPDATE locate_jobs SET state=?,url=?,detail=?,priority=?,not_before=NULL,
                    attempts=0,updated_at=? WHERE id=?""",
                                   (state, url if accept else "", f"Revisión de {actor}: {note}",
                                    PRIORITY_RETRY, now, job["id"]))
                self._event(connection, job["id"], state, "review", url, f"{actor}: {note}", 0)

    # discovery sources ------------------------------------------------------------------------
    def seed_sources(self) -> int:
        added = 0
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            for name, sources in DEFAULT_SOURCES.items():
                row = connection.execute("SELECT id FROM competitors WHERE name=?", (name,)).fetchone()
                if row is None:
                    continue
                for method, template, note in sources:
                    added += connection.execute("""INSERT OR IGNORE INTO discovery_sources(competitor_id,method,
                        url_template,note,created_by,created_at) VALUES(?,?,?,?,'system',?)""",
                                                (row["id"], method, template, note, now)).rowcount
        return added

    def save_source(self, competitor_id: str, method: str, url_template: str, *, actor: str, note: str = "",
                    refresh_hours: float = 168.0, max_pages: int = 10, active: bool = True) -> None:
        if method not in SOURCE_METHODS:
            raise ValueError("Tipo de fuente no válido: catálogo/marca o feed")
        if not actor.strip():
            raise ValueError("Indique quién registra la fuente")
        if not 1 <= refresh_hours <= 24 * 90 or not 1 <= max_pages <= 200:
            raise ValueError("Caducidad entre 1 h y 90 días; entre 1 y 200 páginas por fuente")
        template = url_template.strip()
        if re.search(r"[{}]", template.replace("{brand}", "")):
            raise ValueError("La única variable permitida es {brand}")
        config = self.search.config(competitor_id)
        try:
            validate_competitor_url(template.replace("{brand}", "marca"), config["domain"])
        except WebSearchError as exc:
            raise ValueError(str(exc)) from exc
        with self.store._connect() as connection, connection:
            connection.execute("""INSERT INTO discovery_sources(competitor_id,method,url_template,refresh_hours,
                max_pages,active,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(competitor_id,url_template) DO UPDATE SET method=excluded.method,
                refresh_hours=excluded.refresh_hours,max_pages=excluded.max_pages,active=excluded.active,
                note=excluded.note""", (competitor_id, method, template, float(refresh_hours), int(max_pages),
                                        int(active), note.strip(), actor.strip(), _iso(self._now())))

    # reporting (no HTTP) ----------------------------------------------------------------------
    def status(self):
        with self.store._connect() as connection:
            return pd.read_sql_query("""SELECT c.name AS competitor,j.state,COUNT(*) AS jobs FROM locate_jobs j
                JOIN competitors c ON c.id=j.competitor_id GROUP BY c.name,j.state ORDER BY c.name""", connection)

    def jobs(self, *, states: tuple[str, ...] | None = None, limit: int = 500):
        query = """SELECT j.id,j.ean,p.brand,p.model,p.color,c.name AS competitor,j.competitor_id,j.kind,j.state,
                j.attempts,j.not_before,j.url,j.method,j.detail,j.updated_at FROM locate_jobs j
                JOIN products p ON p.ean=j.ean JOIN competitors c ON c.id=j.competitor_id"""
        params: list = []
        if states:
            query += f" WHERE j.state IN ({','.join('?' * len(states))})"
            params += list(states)
        with self.store._connect() as connection:
            return pd.read_sql_query(query + " ORDER BY j.updated_at DESC LIMIT ?", connection,
                                     params=(*params, limit))

    def index_summary(self):
        with self.store._connect() as connection:
            return pd.read_sql_query("""SELECT c.name AS competitor,
                (SELECT COUNT(*) FROM discovery_index i WHERE i.competitor_id=c.id) AS indexed,
                (SELECT COUNT(*) FROM discovery_index i WHERE i.competitor_id=c.id AND i.check_state='product')
                    AS checked,
                (SELECT COUNT(*) FROM discovery_pages d WHERE d.competitor_id=c.id) AS pages,
                (SELECT MAX(fetched_at) FROM discovery_pages d WHERE d.competitor_id=c.id) AS last_refresh,
                (SELECT COUNT(*) FROM discovery_sources s WHERE s.competitor_id=c.id AND s.active=1) AS sources
                FROM competitors c ORDER BY c.name""", connection)

    def sources(self):
        with self.store._connect() as connection:
            return pd.read_sql_query("""SELECT s.*,c.name AS competitor FROM discovery_sources s
                JOIN competitors c ON c.id=s.competitor_id ORDER BY c.name,s.id""", connection)


class _Session:
    """One competitor during one run: authorised methods, governed fetchers (robots.txt shared) and caches."""

    def __init__(self, locator: PriceLocator, competitor_id: str) -> None:
        self.locator, self.store, self.competitor_id = locator, locator.store, competitor_id
        self.config = locator.search.config(competitor_id)
        self.methods = {method for method in METHODS if method != "external"
                        and locator.access.method_status(competitor_id, method) == "authorized"}
        if (external_search_enabled() and locator.external_search is not None
                and locator.access.method_status(competitor_id, "external") == "authorized"):
            self.methods.add("external")
        self._fetchers: dict[str, GovernedFetcher] = {}
        self._robots: dict = {}
        self._robots_state: dict = {}
        self._blocked: dict = {}
        self._visited: set[str] = set()
        self._sitemaps_done = False
        with self.store._connect() as connection:
            self._first_request = connection.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]

    @property
    def requests(self) -> int:
        """Requests actually reserved (and logged) for this competitor during the session."""
        with self.store._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM access_requests WHERE competitor_id=? AND id>?",
                                      (self.competitor_id, self._first_request)).fetchone()[0]

    def _now(self) -> datetime:
        return self.locator._now()

    def fetcher(self, method: str, ean: str = "") -> GovernedFetcher:
        if method not in self._fetchers:
            fetcher = GovernedFetcher(self.locator.access, self.competitor_id, method, purpose="locate",
                                      transport=self.locator._transport, sleep=self.locator._sleep,
                                      pause_seconds=0.0, max_requests=1_000_000,
                                      timeout=float(self.config["timeout_seconds"] or 15),
                                      retries=int(self.config["retries"] or 0))
            # One robots.txt read and one block memory per competitor, whatever the method.
            fetcher._robots, fetcher._robots_state, fetcher.blocked = self._robots, self._robots_state, self._blocked
            self._fetchers[method] = fetcher
        self._fetchers[method].ean = ean
        return self._fetchers[method]

    def _get(self, method: str, url: str, ean: str = "", **kwargs):
        return self._guarded(method, lambda: self.fetcher(method, ean).get(url, **kwargs))

    def _guarded(self, method: str, call):
        try:
            return call()
        except AccessDenied as exc:
            decision = exc.decision
            if decision.code in ("not_authorized", "prohibited", "external_disabled"):
                raise
            state = "blocked" if decision.code == "blocked_manual" else "waiting"
            raise _Stop(state, decision.message, decision.retry_at) from exc
        except ConnectorBlocked as exc:
            gate = self.locator.access.check(self.competitor_id, method)
            if gate.code == "blocked_wait":
                raise _Stop("waiting", f"{exc}. Se reanudará tras el plazo indicado por el sitio",
                            gate.retry_at) from exc
            raise _Stop("blocked", f"{exc}. Requiere revisión humana; no se intenta superar") from exc

    # job --------------------------------------------------------------------------------------
    def process(self, job: dict) -> _Result:
        cid, ean = self.competitor_id, job["ean"]
        with self.store._connect() as connection:
            product = dict(connection.execute("SELECT ean,brand,model,color,caliber FROM products WHERE ean=?",
                                              (ean,)).fetchone())
            known = connection.execute("SELECT url,status FROM competitor_product_urls WHERE competitor_id=? AND ean=?",
                                       (cid, ean)).fetchall()
            discarded = {row[0] for row in connection.execute("""SELECT url FROM pilot_url_candidates
                WHERE competitor_id=? AND ean=? AND state IN ('discarded','rejected')""", (cid, ean))}
        verified = [row["url"] for row in known if row["status"] == "verified"]
        if verified:
            return _Result("done", verified[0], "known", "Ficha verificada ya registrada: se reutiliza sin consultas")
        pending = [row["url"] for row in known if row["status"] in ("probable", "pending_verification")]
        if pending:
            return _Result("review", pending[0], "known", "Candidato pendiente de revisión manual")
        excluded = {row["url"] for row in known} | discarded
        result = self._match(product, excluded)
        if result:
            return result
        executed, restrictions = [], []
        for method in DISCOVERY_ORDER:
            if method not in self.methods:
                if method != "external":
                    restrictions.append(f"{METHODS[method]}: sin autorizar")
                continue
            outcome = self._discover(method, product)
            if outcome == "ran":
                executed.append(METHODS[method])
                result = self._match(product, excluded)
                if result:
                    return result
            else:
                restrictions.append(f"{METHODS[method]}: {outcome}")
        if not executed:
            return _Result("unavailable", detail=f"{UNAVAILABLE_TEXT} ({'; '.join(restrictions)})")
        return _Result("not_found", detail=f"Sin ficha de {product['brand']} {product['model']} {product['color']} en: "
                                           f"{', '.join(executed)}. No implica que el producto no exista; "
                                           "se reintentará cuando caduquen las fuentes")

    # matching against the local index ---------------------------------------------------------
    def _match(self, product: dict, excluded: set[str]) -> _Result | None:
        wanted_model, wanted_color = model_key(product["model"]), key(product["color"])
        with self.store._connect() as connection:
            rows = connection.execute("""SELECT * FROM discovery_index WHERE competitor_id=?
                AND instr(replace(signature,' ',''),?)>0 AND instr(replace(signature,' ',''),?)>0""",
                                      (self.competitor_id, wanted_model, wanted_color)).fetchall()
        lens = normalize_caliber(product.get("caliber")).partition("-")[0]
        candidates = [dict(row) for row in rows if row["url"] not in excluded
                      and link_matches(row["signature"], product["model"], product["color"])]
        # Calibre never decides identity; it only orders equivalent pages (the same lens first).
        candidates.sort(key=lambda row: (not (lens and lens in row["signature"].split()), row["first_seen"]))
        visits, review = 0, None
        for row in candidates:
            page, fresh = self._facts(row, product["ean"], visits < MAX_VISITS_PER_JOB)
            visits += fresh
            if page is None:
                continue
            url = page.url or row["url"]
            if url in excluded:
                continue
            match = classify(product, page.candidate())
            status = "manual_review" if page.ambiguous and match.status == EXACT else match.status
            if page.ambiguous and match.status == EXACT:
                match.reasons.append("Ficha ambigua (varias variantes u ofertas): revisión manual")
            state = {EXACT: "exact", PROBABLE: "probable", REJECTED: "rejected"}.get(status, "probable")
            source = METHODS.get(row["method"], row["method"])
            note = (f"{'Confirmación automática' if state == 'exact' else 'Candidato automático'} · regla marca + "
                    f"modelo + código de color (calibre y EAN publicado no exigidos) · descubierta mediante {source} "
                    f"en {row['source_url'] or url} · comprobada {row['checked_at'] or _iso(self._now())} · "
                    + "; ".join(match.reasons + match.observations))[:1000]
            outcome = Outcome(product["ean"], self.competitor_id, self.config["name"], state, url=url,
                              method=f"locator:{row['method']}", page=page,
                              match_status=EXACT if state == "exact" else status)
            self.locator.search._upsert_index(product, self.config, outcome, source="auto_watch", note=note)
            excluded.add(url)
            if state == "exact":
                self.locator.search._supersede(self.competitor_id, product["ean"], url, replace=False)
                return _Result("done", url, row["method"], note)
            if state == "probable" and review is None:
                review = _Result("review", url, row["method"], note)
        return review

    def _facts(self, row: dict, ean: str, may_fetch: bool) -> tuple[ProductPage | None, int]:
        """Identity facts of an indexed page: cached for every product, fetched (``ficha``) at most once."""
        checked = row["checked_at"] and datetime.fromisoformat(row["checked_at"]) > self._now() - FACTS_TTL
        if checked and row["check_state"] == "product":
            return ProductPage(**json.loads(row["facts"])), 0
        if checked or row["check_state"] == "robots" or not may_fetch:
            return None, 0
        state, facts, page = "product", "", None
        try:
            fetched = self._get("ficha", row["url"], ean)
            final = validate_competitor_url(fetched.url, self.config["domain"])
            page = extract_product(fetched.text, final, price_selector=self.config["price_selector"],
                                   availability_selector=self.config["availability_selector"],
                                   tax_policy=self.config["tax_policy"])
            if page is None:
                state = "no_product"
            else:
                page.url = final
                facts = json.dumps(asdict(page), ensure_ascii=False)
        except PageGone:
            state = "gone"
        except RobotsDenied:
            state = "robots"
        with self.store._connect() as connection, connection:
            connection.execute("""UPDATE discovery_index SET checked_at=?,check_state=?,facts=?
                WHERE competitor_id=? AND url=?""", (_iso(self._now()), state, facts, self.competitor_id, row["url"]))
        return page, 1

    # discovery --------------------------------------------------------------------------------
    def _discover(self, method: str, product: dict) -> str:
        """Refresh one authorised source if its copy expired. Returns 'ran' or why it could not be used."""
        if method == "sitemap":
            return self._sitemaps()
        if method == "site_search":
            return self._site_search(product)
        if method == "external":
            return self._external(product)
        with self.store._connect() as connection:
            sources = connection.execute("""SELECT * FROM discovery_sources WHERE competitor_id=? AND method=?
                AND active=1 ORDER BY id""", (self.competitor_id, method)).fetchall()
        if not sources:
            return "sin fuentes configuradas"
        brand_slug = "-".join(tokens(product["brand"])).lower()
        outcomes = [self._crawl(method, source["url_template"].replace("{brand}", brand_slug),
                                source["refresh_hours"], source["max_pages"]) for source in sources]
        return "ran" if "ran" in outcomes else outcomes[0]

    def _page(self, url: str, hours: float):
        with self.store._connect() as connection:
            row = connection.execute("SELECT * FROM discovery_pages WHERE competitor_id=? AND url=?",
                                     (self.competitor_id, url)).fetchone()
        if row is None or datetime.fromisoformat(row["fetched_at"]) <= self._now() - timedelta(hours=hours):
            return None
        return row

    def _record_page(self, url: str, method: str, status: str, links: int = 0, next_url: str = "",
                     detail: str = "") -> None:
        with self.store._connect() as connection, connection:
            connection.execute("""INSERT INTO discovery_pages(competitor_id,url,method,fetched_at,status,links,
                next_url,detail) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(competitor_id,url) DO UPDATE SET
                method=excluded.method,fetched_at=excluded.fetched_at,status=excluded.status,links=excluded.links,
                next_url=excluded.next_url,detail=excluded.detail""",
                               (self.competitor_id, url, method, _iso(self._now()), status, links, next_url,
                                detail[:300]))

    def _index(self, items: list[tuple[str, str]], method: str, source_url: str) -> int:
        now = _iso(self._now())
        rows, seen = [], set()
        for raw, label in items:
            try:
                url = validate_competitor_url(raw, self.config["domain"])
            except WebSearchError:
                continue
            sig = signature(url, label)
            if url in seen or not any(char.isdigit() for char in sig):
                continue
            seen.add(url)
            rows.append((self.competitor_id, url, label[:300], sig, method, source_url, now, now))
        with self.store._connect() as connection, connection:
            connection.executemany("""INSERT INTO discovery_index(competitor_id,url,label,signature,method,source_url,
                first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(competitor_id,url) DO UPDATE SET
                last_seen=excluded.last_seen,
                label=CASE WHEN excluded.label<>'' THEN excluded.label ELSE discovery_index.label END,
                signature=CASE WHEN excluded.label<>'' THEN excluded.signature ELSE discovery_index.signature END""",
                                   rows)
        return len(rows)

    def _crawl(self, method: str, start: str, refresh_hours: float, max_pages: int) -> str:
        """Listing pages (brand, category, feed) with pagination; pages still fresh are not fetched again."""
        try:
            url = validate_competitor_url(start, self.config["domain"])
        except WebSearchError as exc:
            return f"fuente no válida ({exc})"
        pages, outcome = 0, "ran"
        while url and pages < max_pages and url not in self._visited:
            self._visited.add(url)
            pages += 1
            cached = self._page(url, refresh_hours)
            if cached is not None:
                url = cached["next_url"] if cached["status"] == "ok" else ""
                continue
            try:
                fetched = self._get(method, url)
            except RobotsDenied:
                self._record_page(url, method, "robots", detail="robots.txt no permite esta página")
                return "robots.txt no lo permite" if pages == 1 else outcome
            except PageGone as exc:
                self._record_page(url, method, "gone", detail=str(exc))
                return outcome
            if method == "feed":
                items, following = _feed_items(fetched.text), ""
            else:
                links = parse_html(fetched.text).links
                here = urlparse(fetched.url).path
                items = [(urljoin(fetched.url, href), text) for href, text in links
                         if urlparse(urljoin(fetched.url, href)).path != here
                         and _same_site(urljoin(fetched.url, href), self.config["domain"])]
                following = _next_page(fetched.url, links)
            count = self._index(items, method, url)
            self._record_page(url, method, "ok", count, following)
            url = following
        return outcome

    def _sitemaps(self) -> str:
        if self._sitemaps_done:
            return "ran"
        self._sitemaps_done = True
        base = self.config["site_url"]
        root = urljoin(base, "/sitemap.xml")
        rules = self._guarded("sitemap", lambda: self.fetcher("sitemap").rules(base))
        pending = list(rules.sitemaps) or [root]
        if self._page(pending[0], SITEMAP_REFRESH_HOURS) is not None:
            return "ran"
        files = 0
        while pending and files < MAX_SITEMAP_FILES:
            sitemap = pending.pop(0)
            if not _same_site(sitemap, self.config["domain"]) or sitemap in self._visited:
                continue
            self._visited.add(sitemap)
            files += 1
            try:
                body = self._get("sitemap", sitemap, max_bytes=MAX_SITEMAP_BYTES).text
            except RobotsDenied:
                self._record_page(sitemap, "sitemap", "robots")
                continue
            except PageGone as exc:
                self._record_page(sitemap, "sitemap", "gone", detail=str(exc))
                continue
            locs = _LOC.findall(body)
            if "<sitemapindex" in body.lower():
                pending.extend([loc for loc in locs if _PRODUCT_SITEMAP.search(loc)] or locs)
                self._record_page(sitemap, "sitemap", "ok", len(locs), detail="índice de sitemaps")
                continue
            count = self._index([(loc, "") for loc in locs], "sitemap", sitemap)
            self._record_page(sitemap, "sitemap", "ok", count)
        return "ran" if files else "sin sitemap accesible"

    def _site_search(self, product: dict) -> str:
        if not self.config.get("search_url_template"):
            return "sin plantilla de búsqueda configurada"
        try:
            urls = product_search_urls(product, self.config, modes=["model_color", "ean"])
        except WebSearchError as exc:
            return f"plantilla no válida ({exc})"
        if not urls:
            return "búsqueda interna sin verificar"
        ran = False
        for _, url in urls:
            if self._page(url, SITEMAP_REFRESH_HOURS) is not None:
                ran = True
                continue
            try:
                fetched = self._get("site_search", url, product["ean"])
            except RobotsDenied:
                self._record_page(url, "site_search", "robots")
                return "robots.txt no lo permite"
            items = [(urljoin(fetched.url, href), text) for href, text in parse_html(fetched.text).links]
            self._record_page(url, "site_search", "ok", self._index(items, "site_search", url))
            ran = True
        return "ran" if ran else "sin resultados"

    def _external(self, product: dict) -> str:
        """Optional extension point, off by default. Spends the provider budget, never the competitor's."""
        access = self.locator.access
        try:
            request_id = access.reserve(self.competitor_id, "external", purpose="locate", ean=product["ean"],
                                        provider=self.locator.external_provider)
        except AccessDenied as exc:
            return exc.decision.message
        urls = self.locator.external_search(product, self.config) or []
        access.finish(request_id, f"{len(urls)} resultados")
        self._index([(url, "") for url in urls if _same_site(url, self.config["domain"])], "external",
                    f"proveedor:{self.locator.external_provider}")
        return "ran"


def _feed_items(body: str) -> list[tuple[str, str]]:
    """Generic product feed (RSS/Atom/Google Merchant): link plus title, brand, MPN and colour as the label."""
    items = []
    for block in re.split(r"<(?:item|entry)\b", body, flags=re.I)[1:]:
        link = re.search(r"<(?:g:)?link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</(?:g:)?link>", block, re.I | re.S) \
            or re.search(r"<link[^>]*href=\"([^\"]+)\"", block, re.I)
        if not link:
            continue
        label = " ".join(match.strip() for name in ("title", "g:brand", "g:mpn", "g:color")
                         for match in re.findall(rf"<{name}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{name}>", block,
                                                 re.I | re.S))
        items.append((link.group(1).strip(), label))
    return items


def main(argv: list[str] | None = None) -> int:
    """Manual commands. Nothing here schedules or loops: periodic execution is a deployment decision."""
    parser = argparse.ArgumentParser(description="Localización automática de fichas de productos en vigilancia.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("enqueue", help="Crea los trabajos pendientes (no hace peticiones)")
    sub.add_parser("status", help="Estado de la cola (no hace peticiones)")
    run = sub.add_parser("run", help="Procesa un lote acotado con los métodos autorizados")
    run.add_argument("--competitor", action="append", default=[], help="Nombre; repetible. Por defecto, todos")
    run.add_argument("--max-jobs", type=int, default=100)
    run.add_argument("--deadline", type=float, default=600.0, help="Segundos máximos de la ejecución")
    args = parser.parse_args(argv)
    store = PriceComparatorStore()
    store.seed_defaults()
    locator = PriceLocator(store)
    locator.seed_sources()
    if args.command == "enqueue":
        print(locator.enqueue())
        return 0
    if args.command == "status":
        print(locator.status().to_string(index=False))
        return 0
    ids = None
    if args.competitor:
        names = {name.lower(): cid for cid, name in store.competitors()[["id", "name"]].itertuples(index=False)}
        ids = [names[name.lower()] for name in args.competitor]
    locator.enqueue()
    print(locator.run(ids, max_jobs=args.max_jobs, deadline_seconds=args.deadline).summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
