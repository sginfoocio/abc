"""Access governance for the scalable price-watch engine: who may be queried, how, how often.

Every automated request of the watch engine goes through :class:`AccessControl`:

* **Authorisation per competitor and method** (``ficha``, ``site_search``, ``sitemap``, ``catalog``,
  ``feed``, ``external``). Nothing is authorised by default; a public page or a permissive robots.txt is not
  an authorisation. robots.txt is still enforced per URL by :class:`price_web_fetch.WebFetcher`.
  ``external`` is an optional extension point that stays refused unless ``PRICE_EXTERNAL_SEARCH_ENABLED``.
* **Budgets** per competitor (and per optional external provider) (rolling 24 h, per minute, pause),
  editable in the database. The request is reserved *before* the I/O, so a crash never refunds it.
* **Blocking incidents** (CAPTCHA, 401/403/407, 429): the scope is suspended and recorded. A 429 waits
  for its Retry-After; anything else waits for a human decision. Nothing is bypassed or retried around.

The supervised pilot keeps its own budgets (``pilot_budgets``) and is not affected by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
import re
import time
from typing import Callable

import pandas as pd

from price_comparator import PriceComparatorStore
from price_connectors import BLOCK_STATUSES, ConnectorBlocked, ConnectorError
from price_web_fetch import WebFetcher, _host

METHODS = {
    "ficha": "Fichas de producto",
    "site_search": "Buscador interno",
    "sitemap": "Sitemap e índices",
    "catalog": "Catálogo, marcas y categorías",
    "feed": "Feed / API del competidor",
    "external": "Búsqueda externa (opcional)",
}
# The engine is self-hosted: third-party search is only an extension point, off unless explicitly enabled.
EXTERNAL_SEARCH_ENV = "PRICE_EXTERNAL_SEARCH_ENABLED"


def external_search_enabled() -> bool:
    return os.environ.get(EXTERNAL_SEARCH_ENV, "").strip().lower() in {"1", "true", "yes"}
METHOD_STATES = {"pending": "Sin revisar", "authorized": "Autorizado", "denied": "Prohibido"}
SCOPES = ("competitor", "provider")
INCIDENT_KINDS = {"captcha": "CAPTCHA / antibot", "http_401": "HTTP 401", "http_403": "HTTP 403",
                  "http_407": "HTTP 407", "http_429": "HTTP 429 (límite)", "other": "Otro bloqueo"}
# Initial values; every one of them is editable per competitor/provider without touching code.
DEFAULT_BUDGETS = {
    "competitor": {"daily_limit": 200, "per_minute_limit": 10, "pause_seconds": None},
    "provider": {"daily_limit": 100, "per_minute_limit": 10, "pause_seconds": 1.0},
}
DEFAULT_COMPETITOR_PAUSE = 3.0
DEFAULT_429_WAIT = timedelta(hours=1)
MAX_429_WAIT = timedelta(hours=24)
MAX_INLINE_WAIT_SECONDS = 60.0
_SCOPE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str = "ok"
    message: str = ""
    retry_at: datetime | None = None

    @property
    def waitable(self) -> bool:
        """Short, self-resolving waits (pause, per-minute window) as opposed to a refusal."""
        return self.code in {"pause", "rate_minute"}


class AccessDenied(ConnectorError):
    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.message)
        self.decision = decision


class AccessControl:
    def __init__(self, store: PriceComparatorStore, *, clock: Callable[[], datetime] = _utc) -> None:
        self.store = store
        self._now = clock

    # authorisations ---------------------------------------------------------------------------
    def ensure_defaults(self) -> int:
        """Record the known prohibitions (robots.txt forbids the internal search). Never authorises."""
        from competitor_requirements import CHECKED_ON, REQUIREMENTS

        added = 0
        with self.store._connect() as connection, connection:
            for row in connection.execute("SELECT id,name FROM competitors").fetchall():
                restriction = REQUIREMENTS.get(row["name"], ("", ""))[0]
                if "robots.txt prohíbe la búsqueda" in restriction:
                    added += connection.execute(
                        """INSERT OR IGNORE INTO access_methods(competitor_id,method,status,note,evidence,
                               decided_by,decided_at) VALUES(?,?,?,?,?,?,?)""",
                        (row["id"], "site_search", "denied", restriction, "robots.txt", "system",
                         CHECKED_ON)).rowcount
        return added

    def method_status(self, competitor_id: str, method: str) -> str:
        self._method(method)
        with self.store._connect() as connection:
            row = connection.execute("SELECT status FROM access_methods WHERE competitor_id=? AND method=?",
                                     (competitor_id, method)).fetchone()
        return row["status"] if row else "pending"

    def set_method(self, competitor_id: str, method: str, status: str, *, actor: str, note: str,
                   evidence: str = "") -> None:
        self._method(method)
        if status not in METHOD_STATES:
            raise ValueError("Estado de autorización no válido")
        actor, note = actor.strip(), note.strip()
        if not actor:
            raise ValueError("Indique quién toma la decisión")
        if status != "pending" and not note:
            raise ValueError("Justifique la decisión (condiciones de uso, acuerdo, robots.txt…)")
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            if connection.execute("SELECT 1 FROM competitors WHERE id=?", (competitor_id,)).fetchone() is None:
                raise ValueError("Competidor no registrado")
            previous = connection.execute("SELECT status FROM access_methods WHERE competitor_id=? AND method=?",
                                          (competitor_id, method)).fetchone()
            connection.execute("""
                INSERT INTO access_methods(competitor_id,method,status,note,evidence,decided_by,decided_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(competitor_id,method) DO UPDATE SET status=excluded.status,
                note=excluded.note,evidence=excluded.evidence,decided_by=excluded.decided_by,
                decided_at=excluded.decided_at""", (competitor_id, method, status, note, evidence.strip(), actor, now))
            self._audit(connection, "competitor", competitor_id, f"method:{method}",
                        previous["status"] if previous else "pending", status, actor, note, now)

    def methods(self) -> pd.DataFrame:
        """One row per competitor and method, including the implicit «pending» ones."""
        with self.store._connect() as connection:
            competitors = pd.read_sql_query("SELECT id AS competitor_id,name FROM competitors ORDER BY name",
                                            connection)
            stored = pd.read_sql_query("SELECT * FROM access_methods", connection)
        grid = competitors.merge(pd.DataFrame({"method": list(METHODS)}), how="cross")
        grid = grid.merge(stored, on=["competitor_id", "method"], how="left")
        grid["status"] = grid["status"].fillna("pending")
        for column in ("note", "evidence", "decided_by"):
            grid[column] = grid[column].fillna("")
        return grid

    @staticmethod
    def _method(method: str) -> None:
        if method not in METHODS:
            raise ValueError(f"Método desconocido: {method}")

    # budgets ----------------------------------------------------------------------------------
    def budget(self, scope: str, scope_id: str) -> dict:
        self._scope(scope, scope_id)
        with self.store._connect() as connection:
            row = connection.execute("SELECT * FROM access_budgets WHERE scope=? AND scope_id=?",
                                     (scope, scope_id)).fetchone()
            competitor_pause = None
            if scope == "competitor":
                web = connection.execute("SELECT pause_seconds FROM competitor_web_config WHERE competitor_id=?",
                                         (scope_id,)).fetchone()
                competitor_pause = web["pause_seconds"] if web else DEFAULT_COMPETITOR_PAUSE
        result = {"scope": scope, "scope_id": scope_id, **DEFAULT_BUDGETS[scope], "custom": row is not None}
        if row is not None:
            result.update(daily_limit=row["daily_limit"], per_minute_limit=row["per_minute_limit"],
                          pause_seconds=row["pause_seconds"])
        # Competitor pause: the stricter of the budget and the existing per-competitor web pause.
        result["effective_pause"] = max(result["pause_seconds"] or 0.0, competitor_pause or 0.0)
        return result

    def set_budget(self, scope: str, scope_id: str, *, daily_limit: int, per_minute_limit: int,
                   pause_seconds: float | None, actor: str) -> None:
        self._scope(scope, scope_id)
        if not actor.strip():
            raise ValueError("Indique quién modifica el presupuesto")
        if int(daily_limit) != daily_limit or not 0 <= daily_limit <= 100_000:
            raise ValueError("Consultas al día: entero entre 0 y 100 000 (0 detiene las consultas)")
        if int(per_minute_limit) != per_minute_limit or not 1 <= per_minute_limit <= 600:
            raise ValueError("Consultas por minuto: entero entre 1 y 600")
        if pause_seconds is not None and not 0 <= pause_seconds <= 3600:
            raise ValueError("La pausa debe estar entre 0 y 3600 segundos")
        before = self.budget(scope, scope_id)
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            if scope == "competitor" and connection.execute(
                    "SELECT 1 FROM competitors WHERE id=?", (scope_id,)).fetchone() is None:
                raise ValueError("Competidor no registrado")
            connection.execute("""
                INSERT INTO access_budgets(scope,scope_id,daily_limit,per_minute_limit,pause_seconds,updated_by,
                    updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope,scope_id) DO UPDATE SET
                daily_limit=excluded.daily_limit,per_minute_limit=excluded.per_minute_limit,
                pause_seconds=excluded.pause_seconds,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                               (scope, scope_id, int(daily_limit), int(per_minute_limit), pause_seconds,
                                actor.strip(), now))
            self._audit(connection, scope, scope_id, "budget",
                        f"{before['daily_limit']}/día {before['per_minute_limit']}/min pausa {before['pause_seconds']}",
                        f"{int(daily_limit)}/día {int(per_minute_limit)}/min pausa {pause_seconds}",
                        actor.strip(), "", now)

    def budgets(self) -> pd.DataFrame:
        """Competitors (always listed) and providers with a stored budget, with their 24 h usage."""
        with self.store._connect() as connection:
            competitors = [row[0] for row in connection.execute("SELECT id FROM competitors ORDER BY name")]
            providers = [row[0] for row in connection.execute(
                "SELECT scope_id FROM access_budgets WHERE scope='provider' ORDER BY scope_id")]
        rows = []
        for scope, ids in (("competitor", competitors), ("provider", providers)):
            for scope_id in ids:
                budget = self.budget(scope, scope_id)
                rows.append({**budget, "used_24h": self.usage(scope, scope_id)})
        return pd.DataFrame(rows)

    def usage(self, scope: str, scope_id: str, *, window: timedelta = timedelta(hours=24)) -> int:
        since = _iso(self._now() - window)
        with self.store._connect() as connection:
            return connection.execute("""SELECT COUNT(*) FROM access_requests
                WHERE scope=? AND scope_id=? AND reserved_at>?""", (scope, scope_id, since)).fetchone()[0]

    @staticmethod
    def _scope(scope: str, scope_id: str) -> None:
        if scope not in SCOPES:
            raise ValueError("Ámbito no válido")
        if not _SCOPE_ID.match(scope_id or ""):
            raise ValueError("Identificador no válido (minúsculas, cifras, punto, guion o guion bajo)")

    # gate -------------------------------------------------------------------------------------
    def check(self, competitor_id: str, method: str, *, provider: str | None = None) -> Decision:
        with self.store._connect() as connection:
            return self._check(connection, competitor_id, method, provider)

    def reserve(self, competitor_id: str, method: str, *, purpose: str, ean: str = "", url: str = "",
                provider: str | None = None) -> int:
        """Atomically check the gate and record the request before any I/O. Raises :class:`AccessDenied`."""
        scope, scope_id = ("provider", provider) if provider else ("competitor", competitor_id)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                decision = self._check(connection, competitor_id, method, provider)
                if not decision.allowed:
                    connection.rollback()
                    raise AccessDenied(decision)
                request_id = connection.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,
                    purpose,ean,url,reserved_at) VALUES(?,?,?,?,?,?,?,?)""",
                                                (scope, scope_id, competitor_id, method, purpose, ean, url[:500],
                                                 _iso(self._now()))).lastrowid
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return request_id

    def finish(self, request_id: int, result: str) -> None:
        with self.store._connect() as connection, connection:
            connection.execute("UPDATE access_requests SET result=? WHERE id=?", (result[:200], request_id))

    def _check(self, connection, competitor_id: str, method: str, provider: str | None) -> Decision:
        self._method(method)
        if provider and method != "external":
            raise ValueError("Solo la búsqueda externa consume presupuesto de proveedor")
        if method == "external" and not provider:
            raise ValueError("La búsqueda externa requiere indicar el proveedor")
        if method == "external" and not external_search_enabled():
            return Decision(False, "external_disabled",
                            "La búsqueda externa está desactivada: el motor usa solo métodos propios")
        row = connection.execute("SELECT status FROM access_methods WHERE competitor_id=? AND method=?",
                                 (competitor_id, method)).fetchone()
        status = row["status"] if row else "pending"
        label = METHODS[method].lower()
        if status == "denied":
            return Decision(False, "prohibited", f"Método prohibido para este competidor: {label}")
        if status != "authorized":
            return Decision(False, "not_authorized", f"Método sin autorizar para este competidor: {label}")
        now = self._now()
        scopes = [("competitor", competitor_id)] + ([("provider", provider)] if provider else [])
        for scope, scope_id in scopes:
            incident = self._open_incident(connection, scope, scope_id, now)
            if incident is not None:
                if incident["retry_after"] is None:
                    return Decision(False, "blocked_manual",
                                    f"Bloqueo registrado ({INCIDENT_KINDS.get(incident['kind'], incident['kind'])}):"
                                    " requiere revisión humana antes de reanudar")
                return Decision(False, "blocked_wait", "Esperando el plazo indicado por el sitio (HTTP 429)",
                                _parse(incident["retry_after"]))
        # Only the scope that receives the request spends budget: the provider for external searches.
        scope, scope_id = scopes[-1]
        budget = self.budget(scope, scope_id)
        recent = [_parse(value) for (value,) in connection.execute("""SELECT reserved_at FROM access_requests
            WHERE scope=? AND scope_id=? AND reserved_at>? ORDER BY reserved_at""",
                                                                   (scope, scope_id, _iso(now - timedelta(hours=24))))]
        if len(recent) >= budget["daily_limit"]:
            retry = recent[-budget["daily_limit"]] + timedelta(hours=24) if budget["daily_limit"] else None
            return Decision(False, "budget_daily", "Presupuesto diario agotado", retry)
        last_minute = [moment for moment in recent if moment > now - timedelta(minutes=1)]
        if len(last_minute) >= budget["per_minute_limit"]:
            return Decision(False, "rate_minute", "Límite de consultas por minuto alcanzado",
                            last_minute[-budget["per_minute_limit"]] + timedelta(minutes=1))
        if recent and budget["effective_pause"]:
            ready = recent[-1] + timedelta(seconds=budget["effective_pause"])
            if ready > now:
                return Decision(False, "pause", "Pausa entre consultas", ready)
        return Decision(True)

    def _open_incident(self, connection, scope: str, scope_id: str, now: datetime):
        row = connection.execute("""SELECT * FROM access_incidents WHERE scope=? AND scope_id=? AND status='open'
            ORDER BY id DESC LIMIT 1""", (scope, scope_id)).fetchone()
        if row is not None and row["retry_after"] is not None and _parse(row["retry_after"]) <= now:
            if connection.in_transaction:
                connection.execute("""UPDATE access_incidents SET status='expired',resolved_by='system',
                    resolved_at=?,resolution_note='Plazo de espera cumplido' WHERE id=?""", (_iso(now), row["id"]))
            return None
        return row

    # incidents --------------------------------------------------------------------------------
    def report_block(self, scope: str, scope_id: str, kind: str, *, method: str = "", url: str = "",
                     detail: str = "", retry_after: datetime | None = None) -> int:
        """Suspend the scope. Only a 429 resumes on its own (after Retry-After); the rest need a human."""
        self._scope(scope, scope_id)
        if kind not in INCIDENT_KINDS:
            kind = "other"
        now = self._now()
        if kind == "http_429":
            retry_after = min(retry_after or now + DEFAULT_429_WAIT, now + MAX_429_WAIT)
        else:
            retry_after = None
        with self.store._connect() as connection, connection:
            existing = connection.execute("""SELECT id FROM access_incidents WHERE scope=? AND scope_id=?
                AND status='open'""", (scope, scope_id)).fetchone()
            if existing is not None:
                return existing["id"]
            return connection.execute("""INSERT INTO access_incidents(scope,scope_id,method,kind,url,detail,
                detected_at,retry_after) VALUES(?,?,?,?,?,?,?,?)""",
                                      (scope, scope_id, method, kind, url[:500], detail[:500], _iso(now),
                                       _iso(retry_after) if retry_after else None)).lastrowid

    def resolve_incident(self, incident_id: int, *, actor: str, note: str) -> None:
        """Human decision to resume (e.g. access agreed with the competitor). It never bypasses a CAPTCHA."""
        actor, note = actor.strip(), note.strip()
        if not actor or not note:
            raise ValueError("Indique responsable y motivo para reanudar")
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            row = connection.execute("SELECT * FROM access_incidents WHERE id=? AND status='open'",
                                     (incident_id,)).fetchone()
            if row is None:
                raise ValueError("Incidencia inexistente o ya cerrada")
            connection.execute("""UPDATE access_incidents SET status='resolved',resolved_by=?,resolved_at=?,
                resolution_note=? WHERE id=?""", (actor, now, note, incident_id))
            self._audit(connection, row["scope"], row["scope_id"], "incident_resolved", row["kind"], "resolved",
                        actor, note, now)

    def incidents(self, *, open_only: bool = False) -> pd.DataFrame:
        query = "SELECT * FROM access_incidents" + (" WHERE status='open'" if open_only else "")
        with self.store._connect() as connection:
            return pd.read_sql_query(query + " ORDER BY id DESC LIMIT 500", connection)

    def audit(self, limit: int = 500) -> pd.DataFrame:
        with self.store._connect() as connection:
            return pd.read_sql_query("SELECT * FROM access_audit ORDER BY id DESC LIMIT ?", connection,
                                     params=(limit,))

    @staticmethod
    def _audit(connection, scope: str, scope_id: str, action: str, previous: str, current: str, actor: str,
               note: str, at: str) -> None:
        connection.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
            VALUES(?,?,?,?,?,?,?,?)""", (scope, scope_id, action, previous, current, actor, note, at))


def _retry_after(headers: dict, now: datetime) -> datetime | None:
    value = (headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return now + timedelta(seconds=int(value))
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class GovernedFetcher(WebFetcher):
    """WebFetcher whose every HTTP attempt (robots.txt included) passes the access gate and is logged.

    Short waits (pause, per-minute window) are honoured by sleeping; refusals raise :class:`AccessDenied`.
    Blocks open an incident for the competitor; the base fetcher already stops querying the host.
    """

    def __init__(self, access: AccessControl, competitor_id: str, method: str, *, purpose: str, ean: str = "",
                 sleep: Callable[[float], None] = time.sleep, **kwargs) -> None:
        super().__init__(sleep=sleep, **kwargs)
        self.access, self.competitor_id, self.method, self.purpose, self.ean = (
            access, competitor_id, method, purpose, ean)
        inner = self._transport

        def governed(url: str, headers: dict, timeout: float):
            request_id = self._reserve(url)
            try:
                status, response_headers, body = inner(url, headers, timeout)
            except ConnectorError as exc:
                self.access.finish(request_id, f"error: {type(exc).__name__}")
                raise
            self.access.finish(request_id, f"HTTP {status}")
            if status in BLOCK_STATUSES:
                self.access.report_block("competitor", competitor_id, f"http_{status}", method=method, url=url,
                                         detail=f"HTTP {status}",
                                         retry_after=_retry_after(response_headers, self.access._now()))
            return status, response_headers, body

        self._transport = governed

    def _reserve(self, url: str) -> int:
        while True:
            try:
                return self.access.reserve(self.competitor_id, self.method, purpose=self.purpose, ean=self.ean,
                                           url=url)
            except AccessDenied as exc:
                decision = exc.decision
                if not decision.waitable or decision.retry_at is None:
                    raise
                wait = (decision.retry_at - self.access._now()).total_seconds()
                if wait > MAX_INLINE_WAIT_SECONDS:
                    raise
                self._sleep(max(wait, 0.05))

    def get(self, url: str, **kwargs):
        try:
            return super().get(url, **kwargs)
        except ConnectorBlocked as exc:
            reason = self.blocked.get(_host(url), "")
            if reason.startswith("CAPTCHA"):
                self.access.report_block("competitor", self.competitor_id, "captcha", method=self.method, url=url,
                                         detail=str(exc))
            raise
