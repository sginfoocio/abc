"""Verified-URL monitoring, independent of discovery. No network on import or when opening the UI.

Persistent leases and extracted receipts allow restart without re-searching or re-reading a saved sample.
Offers use the existing importer/history; alerts and job completion commit together. Periodic execution
requires PRICE_MONITOR_SCHEDULE_ENABLED and an explicitly enabled competitor (both off by default).
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from numbers import Number
import os
import time
from typing import AbstractSet, Callable
from urllib.parse import parse_qsl, urlparse

from filelock import FileLock, Timeout
import pandas as pd

from price_access import AccessControl, AccessDenied, GovernedFetcher
from price_comparator import PriceComparatorStore
from price_connectors import ConnectorBlocked, ConnectorError
from price_locator import LEASE, MAX_ATTEMPTS, PriceLocator, job_deferral
from price_references import EXACT, classify
from price_watch import PriceWatch
from price_web import Outcome, WebPriceSearch, WebSearchError, comparability_gap, validate_competitor_url
from price_web_extract import ProductPage, extract_product, select_product_variant
from price_web_fetch import Fetched, PageGone, RobotsDenied, _requests_transport
from vytria_price_sync import SyncInProgress, VytriaPriceSync

SCHEDULE_ENV = "PRICE_MONITOR_SCHEDULE_ENABLED"
STATES = {"pending": "Pendiente", "running": "En ejecución", "done": "Terminada",
          "waiting": "En espera de presupuesto / pausa", "blocked": "Bloqueada",
          "unavailable": "Sin autorización", "review": "Revisión manual",
          "gone": "Ficha retirada", "inactive": "Fuera de vigilancia", "failed": "Error: revisar"}
ALERTS = {"price_down": "Bajada de precio", "price_up": "Subida de precio",
          "undercut": "Más barato que Vytria", "availability": "Cambio de disponibilidad",
          "gone": "Ficha desaparecida", "stale": "Precio caducado"}

_capture_observer: ContextVar[Callable[[str, str, Fetched, ProductPage | None, dict], None] | None] = (
    ContextVar("price_capture_observer", default=None))
_capture_url_guard: ContextVar[Callable[[str], None] | None] = ContextVar("price_capture_url_guard", default=None)


class ReviewRouteDenied(ValueError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@contextmanager
def capture_context(observer, url_guard):
    if _capture_observer.get() is not None:
        raise ValueError("Ya hay una captura activa en este contexto")
    observer_token = _capture_observer.set(observer)
    guard_token = _capture_url_guard.set(url_guard)
    try:
        yield
    finally:
        _capture_url_guard.reset(guard_token)
        _capture_observer.reset(observer_token)


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _inventory_datetime(value: object) -> datetime:
    """Read SQLite/pandas date scalars; legacy naive dates represent UTC."""
    if not pd.api.types.is_scalar(value) or isinstance(value, Number) or pd.isna(value):
        raise ValueError("Fecha de vigencia no válida")
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            raise ValueError("Fecha vacía")
        parsed = parsed.tz_localize("UTC") if parsed.tzinfo is None else parsed.tz_convert("UTC")
        return parsed.to_pydatetime(warn=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Fecha de vigencia no válida") from exc


def _done_job_restriction(job):
    if job["state"] != "done" or not job.get("not_before"):
        return None
    deadline = _inventory_datetime(job["not_before"])
    if (job.get("sampled_at") and job.get("last_ok_at") and job.get("valid_until")
            and _inventory_datetime(job["sampled_at"]) == _inventory_datetime(job["last_ok_at"])
            and deadline == _inventory_datetime(job["valid_until"])):
        return None
    return deadline


def _review_sample_matches(task: dict) -> bool:
    if not task.get("monitor_sample") or not task.get("monitor_sampled_at"):
        return False
    sampled = _inventory_datetime(task["monitor_sampled_at"])
    requested = _inventory_datetime(task["requested_at"])
    precise_request = "." in task["requested_at"].split("T", 1)[-1]
    if sampled <= requested or (not precise_request and sampled < requested + timedelta(seconds=1)):
        return False
    sample = json.loads(task["monitor_sample"])
    page = sample.get("page") or {}
    return (_inventory_datetime(sample["at"]) == sampled and page.get("url") == task["url"]
            and (page.get("url_trace") or {}).get("candidate_url") == task["url"])


def _review_sample_ambiguous(task: dict) -> bool:
    if not task.get("monitor_sample") or not task.get("monitor_sampled_at"):
        return False
    requested = _inventory_datetime(task["requested_at"])
    sampled = _inventory_datetime(task["monitor_sampled_at"])
    return ("." not in task["requested_at"].split("T", 1)[-1]
            and requested <= sampled < requested + timedelta(seconds=1))


def _accepted_edel_resolution(page: ProductPage, accepted_url: str, product: dict) -> bool:
    from price_comparator import normalize_gtin

    accepted, selected = urlparse(accepted_url), urlparse(page.url)
    original_query = parse_qsl(accepted.query, keep_blank_values=True)
    selected_query = parse_qsl(selected.query, keep_blank_values=True)
    if ((accepted.hostname or "").lower().removeprefix("www.") != "edel-optics.es"
            or accepted._replace(query="") != selected._replace(query="")
            or any(key == "selectedSize" for key, _ in original_query)
            or selected_query != [*original_query, ("selectedSize", page.variant_id)]
            or not page.variant_id.isdigit()
            or page.url_trace.get("candidate_url") != accepted_url
            or page.url_trace.get("final_url") != accepted_url
            or page.url_trace.get("selected_variant_url") != page.url
            or page.url_trace.get("offer_variant_url") != page.url
            or page.url_trace.get("selected_variant_source") != "checked_radio"
            or page.ambiguous or not page.ean_published):
        return False
    try:
        return normalize_gtin(page.ean_published) == normalize_gtin(product["ean"])
    except (ValueError, KeyError):
        return False


def _review_sample_policy_reason(con, task: dict, now: datetime, interval: float | None) -> str:
    if interval is None or _inventory_datetime(task["monitor_sampled_at"]) + timedelta(hours=interval) <= now:
        return "La muestra posterior a la solicitud ha caducado"
    sample = json.loads(task["monitor_sample"])
    page = ProductPage(**sample["page"])
    config = con.execute("""SELECT w.*,c.name,c.url AS site_url,c.domain,c.market,c.currency
        FROM competitor_web_config w JOIN competitors c ON c.id=w.competitor_id
        WHERE w.competitor_id=?""", (task["competitor_id"],)).fetchone()
    if not config:
        return "Falta la configuración comercial vigente"
    config = dict(config)
    if page.configured_tax_policy != config["tax_policy"]:
        return "Cambió la política de IVA desde la lectura"
    gap = comparability_gap(page, config)
    if (sample.get("gap") or "") != gap:
        return "Cambió la validación comercial desde la lectura"
    if task["monitor_state"] == "done":
        eligible = not gap and page.includes_tax is True and page.availability == "available" and not page.stock_conflict
        if bool(task["monitor_eligible"]) != eligible:
            return "La comparabilidad guardada ya no coincide con la lectura"
        if page.has_price and (not task["monitor_valid_until"] or
                               _inventory_datetime(task["monitor_valid_until"]) <= now or
                               not task["monitor_last_ok_at"] or
                               _inventory_datetime(task["monitor_last_ok_at"]) !=
                               _inventory_datetime(task["monitor_sampled_at"])):
            return "La vigencia guardada no acredita el precio de esta lectura"
    return ""


def _reconcile_review_requests(con, task_id: int) -> None:
    con.execute("""UPDATE manual_review_refreshes SET http_requests=MAX(http_requests,
        COALESCE((SELECT base_requests FROM manual_review_http_attribution WHERE refresh_id=?)
        + (SELECT COUNT(*) FROM access_requests WHERE purpose=?),http_requests)) WHERE id=?""",
                (task_id, f"manual_review:{task_id}", task_id))


def _review_identity(con, task: dict) -> dict | None:
    row = con.execute("""SELECT r.ean,r.competitor_id,r.url,r.locate_job_id,p.brand,p.model,p.color,
            u.model_code,u.color_code,u.ean_published,
            u.match_status,u.status,u.source,u.note,u.verified_at,c.name AS competitor_name,c.domain,
            (SELECT MAX(e.id) FROM locate_events e WHERE e.job_id=r.locate_job_id
              AND e.method IN ('review','review+price')) AS review_event_id
        FROM manual_review_refreshes r
        JOIN products p ON p.ean=r.ean
        JOIN competitors c ON c.id=r.competitor_id
        JOIN competitor_product_urls u ON u.competitor_id=r.competitor_id AND u.ean=r.ean AND u.url=r.url
        WHERE r.id=?""", (task["id"],)).fetchone()
    return dict(row) if row else None


def _review_identity_valid(con, task: dict) -> bool:
    from price_comparator import normalize_gtin
    from price_references import key, model_key

    identity = _review_identity(con, task)
    if not identity or identity["status"] != "verified" or identity["source"] != "user":
        return False
    if (identity["verified_at"] != task["requested_at"]
            or identity["note"] != f"Revisión de {task['actor']}: {task['reason']}"):
        return False
    if ((identity["model_code"] and model_key(identity["model_code"]) != model_key(identity["model"]))
            or (identity["color_code"] and key(identity["color_code"]) != key(identity["color"]))):
        return False
    if identity["ean_published"]:
        try:
            if normalize_gtin(identity["ean_published"]) != identity["ean"]:
                return False
        except ValueError:
            return False
    saved = con.execute("SELECT snapshot FROM manual_review_identity WHERE refresh_id=?", (task["id"],)).fetchone()
    if saved:
        return identity == json.loads(saved["snapshot"])
    product = con.execute("SELECT updated_at FROM products WHERE ean=?", (task["ean"],)).fetchone()
    if not product or _inventory_datetime(product["updated_at"]) >= _inventory_datetime(task["requested_at"]):
        return False
    events = con.execute("""SELECT id FROM locate_events WHERE job_id=? AND at=? AND method='review+price'
        AND url=? AND detail=?""", (task["locate_job_id"], task["requested_at"], task["url"],
                                   f"{task['actor']}: {task['reason']}"[:1000])).fetchall()
    return len(events) == 1 and events[0]["id"] == identity["review_event_id"]


def schedule_enabled() -> bool:
    return os.environ.get(SCHEDULE_ENV, "").strip().lower() in {"1", "true", "yes"}


@dataclass
class MonitorReport:
    processed: int = 0
    requests: int = 0
    states: Counter = field(default_factory=Counter)
    stopped: dict[str, str] = field(default_factory=dict)
    outcomes: list[dict] = field(default_factory=list)
    deferred_reason: str = ""

    @property
    def summary(self) -> str:
        counts = ", ".join(f"{STATES.get(k, k)}: {v}" for k, v in self.states.items())
        return f"{self.processed} trabajos · {self.requests} peticiones HTTP · {counts or 'sin trabajos vencidos'}"


class PriceMonitor:
    def __init__(self, store: PriceComparatorStore, *, transport=_requests_transport,
                 clock: Callable[[], datetime] = _utc, sleep: Callable[[float], None] = time.sleep,
                 vytria_sync: VytriaPriceSync | None = None) -> None:
        self.store, self._now, self._transport, self._sleep = store, clock, transport, sleep
        self.access = AccessControl(store, clock=clock)
        self.search = WebPriceSearch(store, clock=clock)
        self.watch = PriceWatch(store, clock=clock)
        self.vytria = vytria_sync or VytriaPriceSync(store, clock=clock, sleep=sleep)

    def _validate_review_continuity(self, task: dict, continuity_id: int) -> None:
        from price_recovery import _scope
        from price_trial import continuity_config

        try:
            continuity_config()
        except ValueError as exc:
            raise ReviewRouteDenied(f"Configuración de continuidad no válida: {exc}") from exc
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()
            trial = con.execute("SELECT status FROM price_trial WHERE id=1").fetchone()
            if not row or row["id"] != continuity_id:
                raise ReviewRouteDenied("La continuidad original ha cambiado")
            if row["status"] == "paused":
                raise ReviewRouteDenied("La continuidad está pausada", retryable=True)
            if row["status"] != "active":
                raise ReviewRouteDenied(f"La continuidad no está activa: {row['status']}")
            if not trial or trial["status"] not in {"expired", "stopped"}:
                raise ReviewRouteDenied("El piloto original no está cerrado")
            if not row["started_at"] or _inventory_datetime(row["started_at"]) > self._now():
                raise ReviewRouteDenied("La continuidad aún no ha comenzado", retryable=True)
            try:
                _scope(con, [{"continuity_id": continuity_id}])
            except ValueError as exc:
                raise ReviewRouteDenied(f"Manifiesto de continuidad no válido: {exc}") from exc
            pair = con.execute("""SELECT state FROM price_continuity_pairs
                WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                               (continuity_id, task["competitor_id"], task["ean"])).fetchone()
            if not pair:
                raise ReviewRouteDenied("Par fuera del manifiesto de continuidad")
            if pair["state"] == "running":
                raise ReviewRouteDenied("Par reservado por una lectura de continuidad", retryable=True)
            if pair["state"] not in {"pending", "review"}:
                raise ReviewRouteDenied(f"El par de continuidad requiere revisión: {pair['state']}")

    @contextmanager
    def _review_execution(self, task: dict):
        from price_trial import PriceTrial, continuity_row, trial_row

        if trial_row(self.store) is None:
            yield
            return
        service = PriceTrial(self.store, clock=self._now)
        with service.continuity_lock():
            row = continuity_row(self.store)
            if not row:
                raise ReviewRouteDenied("Par reservado al piloto; no existe continuidad activa")
            self._validate_review_continuity(task, row["id"])
            with service.execution(0, task["competitor_id"], task["ean"],
                                   program="continuity", program_id=row["id"]):
                yield

    def enqueue(self, competitor_ids: list[str] | None = None, eans: list[str] | None = None,
                *, exact_url: str | None = None) -> int:
        """One current job per watched pair, never a discovery request. Only verified URLs are eligible."""
        self.search.ensure_config()
        now = self._now()
        added = 0
        selected, params = "", []
        for column, values in (("ean", eans), ("competitor_id", competitor_ids)):
            if values is not None:
                selected += f" AND monitor_jobs.{column} IN ({','.join('?' for _ in values)})"
                params.extend(values)
        with self.store._connect() as con, con:
            con.execute(f"""UPDATE monitor_jobs SET state='inactive',eligible=0,lease_until=NULL,updated_at=?
                WHERE (NOT EXISTS(SELECT 1 FROM watch_products w WHERE w.ean=monitor_jobs.ean AND w.active=1)
                OR NOT EXISTS(SELECT 1 FROM competitors c WHERE c.id=monitor_jobs.competitor_id AND c.active=1))
                {selected}""", (_iso(now), *params))
            rows = con.execute("""SELECT u.* FROM competitor_product_urls u
                JOIN watch_products w ON w.ean=u.ean AND w.active=1
                JOIN competitors c ON c.id=u.competitor_id AND c.active=1
                WHERE u.status='verified' ORDER BY u.verified_at DESC,u.url""").fetchall()
            seen = set()
            for row in rows:
                cid, ean = row["competitor_id"], row["ean"]
                if (competitor_ids is not None and cid not in competitor_ids) or (cid, ean) in seen:
                    continue
                if eans is not None and ean not in eans:
                    continue
                if exact_url is not None and row["url"] != exact_url:
                    continue
                seen.add((cid, ean))
                interval = self.watch.monitor_interval_hours(ean, cid)
                if interval is None:
                    continue
                job = con.execute("SELECT * FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                                  (cid, ean)).fetchone()
                if job is not None and exact_url is not None and job["url"] != exact_url:
                    continue
                if job is None:
                    added += con.execute("""INSERT INTO monitor_jobs(ean,competitor_id,url,updated_at)
                        VALUES(?,?,?,?)""", (ean, cid, row["url"], _iso(now))).rowcount
                    continue
                if job["state"] == "running" and job["lease_until"] and (
                        datetime.fromisoformat(job["lease_until"]) > now):
                    continue
                if job["last_ok_at"]:
                    con.execute("UPDATE monitor_jobs SET valid_until=? WHERE id=?",
                                (_iso(datetime.fromisoformat(job["last_ok_at"]) + timedelta(hours=interval)),
                                 job["id"]))
                changed = job["url"] != row["url"] or job["state"] in ("inactive", "gone", "review")
                due = (datetime.fromisoformat(job["sampled_at"]) + timedelta(hours=interval)
                       if job["sampled_at"] else now)
                not_before = _done_job_restriction(dict(job))
                if ((changed or (job["state"] == "done" and due <= now))
                        and (not_before is None or not_before <= now)):
                    con.execute("""UPDATE monitor_jobs SET url=?,state='pending',not_before=NULL,lease_until=NULL,
                        attempts=0,previous_sample=CASE WHEN sample<>'' THEN sample ELSE previous_sample END,
                        sample='',sampled_at=NULL,eligible=CASE WHEN ? THEN 0 ELSE eligible END,
                        detail='',updated_at=? WHERE id=?""",
                                (row["url"], int(changed), _iso(now), job["id"]))
                elif job["state"] == "done":
                    valid_until = (_iso(datetime.fromisoformat(job["last_ok_at"]) + timedelta(hours=interval))
                                   if job["last_ok_at"] else None)
                    con.execute("UPDATE monitor_jobs SET not_before=?,valid_until=? WHERE id=?",
                                (_iso(max(due, not_before)) if not_before else _iso(due), valid_until, job["id"]))
        self.check_stale(competitor_ids, eans)
        return added

    def _claim(self, cid: str, eans: list[str] | None = None, *, point_check: bool = False,
               completed: AbstractSet[str] = frozenset(), exact_url: str | None = None,
               resume_sample: bool = False) -> dict | None:
        from price_trial import select_eans
        eans = select_eans(self.store, cid, eans, self._now())
        if eans == []:
            return None
        now = _iso(self._now())
        selected = "" if eans is None else f" AND j.ean IN ({','.join('?' for _ in eans)})"
        with self.store._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(f"""SELECT j.* FROM monitor_jobs j
                JOIN watch_products w ON w.ean=j.ean AND w.active=1
                JOIN competitors c ON c.id=j.competitor_id AND c.active=1
                WHERE j.competitor_id=? {selected}
                  AND (? IS NULL OR j.url=?)
                  AND j.state IN ('pending','waiting','blocked','unavailable','running'
                                  {",'done'" if point_check else ""})
                ORDER BY j.not_before,j.id""", (cid, *(eans or []), exact_url, exact_url)).fetchall()
            row = next((r for r in rows if r["ean"] not in completed and not job_deferral(
                dict(r), self._now(), monitoring=True, point_check=point_check,
                check_attempts=point_check)), None)
            if row is None:
                con.commit()
                return None
            con.execute("""UPDATE monitor_jobs SET state='running',attempts=attempts+1,lease_until=?,
                updated_at=? WHERE id=?""", (_iso(self._now() + LEASE), now, row["id"]))
            if point_check and not resume_sample:
                self._event(con, dict(row), row["state"],
                            f"Comprobación puntual aprobada; plazo anterior {row['not_before']}")
                con.execute("""UPDATE monitor_jobs SET
                    previous_sample=CASE WHEN sample<>'' THEN sample ELSE previous_sample END,
                    sample='',sampled_at=NULL WHERE id=?""", (row["id"],))
            con.commit()
            job = dict(row)
            if point_check and not resume_sample:
                job["previous_sample"] = job["sample"] or job["previous_sample"]
                job["sample"] = ""
                job["sampled_at"] = None
            return job

    def _finish(self, job: dict, state: str, detail: str, *, retry_at: datetime | None = None) -> None:
        with self.store._connect() as con, con:
            con.execute("""UPDATE monitor_jobs SET state=?,detail=?,not_before=?,lease_until=NULL,updated_at=?,
                eligible=CASE WHEN ? IN ('review','gone','blocked','unavailable') THEN 0 ELSE eligible END,
                attempts=CASE WHEN ? IN ('waiting','blocked','unavailable') THEN MAX(attempts-1,0) ELSE attempts END
                WHERE id=?""",
                        (state, detail[:1000], _iso(retry_at) if retry_at else None,
                         _iso(self._now()), state, state, job["id"]))
            self._event(con, job, state, detail)

    def _event(self, con, job: dict, state: str, detail: str) -> None:
        con.execute("INSERT INTO monitor_events(job_id,at,state,detail) VALUES(?,?,?,?)",
                    (job["id"], _iso(self._now()), state, detail[:1000]))

    def run(self, competitor_ids: list[str] | None = None, *, max_jobs: int = 200,
            deadline_seconds: float = 900, eans: list[str] | None = None,
            point_check: bool = False, pilot_run_id: int | None = None,
            exact_url: str | None = None, resume_sample: bool = False,
            review_id: int | None = None) -> MonitorReport:
        """An explicitly requested bounded batch. Does not enable periodic work or search for any URL."""
        if not 1 <= max_jobs <= 5000 or not 1 <= deadline_seconds <= 14400:
            raise ValueError("Lote de 1-5000 trabajos y plazo de 1-14400 segundos")
        report = MonitorReport()
        started = time.monotonic()
        if exact_url is not None and (not competitor_ids or len(competitor_ids) != 1
                                      or not eans or len(eans) != 1):
            raise ValueError("La URL exacta requiere un único competidor y EAN")
        if review_id is not None and exact_url is None:
            raise ValueError("La lectura de revisión requiere su URL exacta")
        if review_id is not None and pilot_run_id is not None:
            raise ValueError("La revisión manual no puede asumir una ejecución del piloto original")
        if resume_sample and review_id is None:
            raise ValueError("Solo una revisión identificada puede recuperar una muestra")
        self.enqueue(competitor_ids, eans, exact_url=exact_url)
        summary, _ = self.store.dashboard(now=self._now())
        own = dict(summary[["ean", "own_price_eur"]].itertuples(index=False, name=None)) if not summary.empty else {}
        with self.store._connect() as con:
            ids = competitor_ids if competitor_ids is not None else [
                r[0] for r in con.execute("SELECT DISTINCT competitor_id FROM monitor_jobs ORDER BY competitor_id")]
        for cid in ids:
            if report.processed >= max_jobs or time.monotonic() - started >= deadline_seconds:
                break
            from price_trial import select_eans
            selected_eans = select_eans(self.store, cid, eans, self._now())
            if selected_eans == []:
                report.stopped[cid] = "Par reservado a la continuidad vigente"
                continue
            try:
                with self.search._lock(cid):
                    config = self.search.config(cid)
                    guard = reserved = None
                    if pilot_run_id is not None:
                        from price_pilot import PricePilot
                        guard, reserved = PricePilot(self.store).watch_check_hooks(
                            pilot_run_id, cid, clock=self._now, sleep=self._sleep)
                    inherited_url_guard = _capture_url_guard.get()

                    def validate_review(con) -> None:
                        if review_id is not None:
                            from price_trial import active_execution
                            execution = active_execution(self.store)
                            if execution and execution.program == "continuity":
                                self._validate_review_continuity(
                                    {"competitor_id": cid, "ean": eans[0]}, execution.program_id)
                            valid = con.execute("""SELECT r.* FROM manual_review_refreshes r
                                    JOIN competitor_product_urls u ON u.competitor_id=r.competitor_id
                                      AND u.ean=r.ean AND u.url=r.url
                                    JOIN monitor_jobs j ON j.id=r.monitor_job_id AND j.url=r.url
                                    JOIN watch_products w ON w.ean=r.ean AND w.active=1
                                    JOIN competitors c ON c.id=r.competitor_id AND c.active=1
                                    WHERE r.id=? AND r.status='running' AND r.competitor_id=? AND r.ean=?
                                      AND r.url=? AND u.status='verified' AND u.source='user'
                                      AND u.verified_at=r.requested_at""",
                                                (review_id, cid, eans[0], exact_url)).fetchone()
                            valid = valid and _review_identity_valid(con, dict(valid))
                            if not valid:
                                raise ConnectorError("La ficha aceptada o el alcance cambió; no se consulta")

                    def url_guard(target: str) -> None:
                        if inherited_url_guard is not None:
                            inherited_url_guard(target)
                        if exact_url is None:
                            return
                        expected = urlparse(exact_url)
                        robots_url = f"{expected.scheme}://{expected.netloc}/robots.txt"
                        if target not in {exact_url, robots_url}:
                            raise ConnectorError("Destino distinto de la URL aceptada; no se sigue")
                        with self.store._connect() as con:
                            validate_review(con)

                    if review_id is not None:
                        def review_reserved(target: str, _ean: str) -> None:
                            url_guard(target)

                        reserved = review_reserved
                    with self.store._connect() as con:
                        first = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]

                    fetcher = GovernedFetcher(
                        self.access, cid, "ficha",
                        purpose=f"manual_review:{review_id}" if review_id is not None else "monitor",
                        transport=self._transport, sleep=self._sleep,
                        pause_seconds=0, request_guard=guard, request_reserved=reserved,
                        url_guard=url_guard,
                        retries=0, max_requests=1_000_000,
                        timeout=float(config["timeout_seconds"] or 15))
                    try:
                        completed: set[str] = set()
                        while report.processed < max_jobs and time.monotonic() - started < deadline_seconds:
                            job = self._claim(cid, selected_eans, point_check=point_check, completed=completed,
                                              exact_url=exact_url, resume_sample=resume_sample)
                            if job is None:
                                break
                            report.processed += 1
                            completed.add(job["ean"])
                            self._job_trace = {}
                            if point_check and job["state"] == "done" and not resume_sample:
                                job["sample"] = ""
                            with self.store._connect() as con:
                                before = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]
                            if review_id is not None:
                                stop = self._process(job, config, fetcher, own.get(job["ean"]),
                                                     fresh=True, validate=validate_review, review_id=review_id)
                            else:
                                stop = (self._process(job, config, fetcher, own.get(job["ean"]), fresh=True)
                                        if point_check else self._process(job, config, fetcher, own.get(job["ean"])))
                            with self.store._connect() as con:
                                saved = dict(con.execute("SELECT state,detail,url,sample FROM monitor_jobs WHERE id=?",
                                                         (job["id"],)).fetchone())
                                saved["sample"] = json.loads(saved["sample"]) if saved["sample"] else None
                                requests = con.execute("""SELECT COUNT(*) FROM access_requests
                                    WHERE competitor_id=? AND id>?""", (cid, before)).fetchone()[0]
                                urls = con.execute("""SELECT url FROM access_requests
                                    WHERE competitor_id=? AND id>?""", (cid, before)).fetchall()
                            state = saved["state"]
                            report.outcomes.append({"ean": job["ean"], **saved, "requests": requests,
                                                    "identity": self._job_trace,
                                                    "commercial_requests": sum(
                                                        urlparse(r["url"]).path != "/robots.txt" for r in urls)})
                            report.states[state] += 1
                            if stop:
                                report.stopped[cid] = stop
                                break
                    finally:
                        with self.store._connect() as con:
                            report.requests += con.execute("""SELECT COUNT(*) FROM access_requests
                                WHERE competitor_id=? AND id>?""", (cid, first)).fetchone()[0]
            except Timeout:
                report.stopped[cid] = "Otra ejecución está consultando a este competidor"
        if time.monotonic() - started >= deadline_seconds:
            report.deferred_reason = "Plazo de monitorización agotado; la cola pendiente continúa en otra pasada"
        elif report.processed >= max_jobs:
            report.deferred_reason = "Límite de trabajos de monitorización alcanzado"
        self.check_stale(competitor_ids, eans)
        return report

    def review_refresh_tick(self, *, max_jobs: int = 5, deadline_seconds: float = 180) -> dict:
        """Drain durable manual-review reads through the normal governed monitor engine."""
        if not 1 <= max_jobs <= 50 or not 1 <= deadline_seconds <= 900:
            raise ValueError("Lote de 1-50 lecturas y plazo de 1-900 segundos")
        started = time.monotonic()
        completed = 0
        deferred = 0
        errors = []
        try:
            queue_lock = FileLock(f"{self.store.database_path}.manual-review-refresh.lock", timeout=0)
            queue_lock.acquire()
        except Timeout:
            return {"skipped": "Otra instancia está procesando lecturas de revisión manual",
                    "processed": 0, "deferred": 0, "errors": []}
        try:
            while completed + deferred < max_jobs and time.monotonic() - started < deadline_seconds:
                now = self._now()
                now_text = _iso(now)
                with self.store._connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    row = con.execute("""SELECT r.*,j.state AS monitor_state,j.not_before AS monitor_not_before,
                            j.lease_until AS monitor_lease_until,j.attempts AS monitor_attempts,
                            j.eligible AS monitor_eligible,j.detail AS monitor_detail,j.url AS monitor_url,
                    j.last_ok_at AS monitor_last_ok_at,j.sampled_at AS monitor_sampled_at,
                    j.valid_until AS monitor_valid_until,j.sample AS monitor_sample,
                            j.previous_sample AS monitor_previous_sample,
                            EXISTS(SELECT 1 FROM competitor_product_urls u WHERE u.competitor_id=r.competitor_id
                                AND u.ean=r.ean AND u.url=r.url AND u.status='verified'
                                AND u.source='user' AND u.verified_at=r.requested_at) AS url_verified,
                            EXISTS(SELECT 1 FROM watch_products w WHERE w.ean=r.ean AND w.active=1)
                                AS watch_active,
                            EXISTS(SELECT 1 FROM competitors c WHERE c.id=r.competitor_id AND c.active=1)
                                AS competitor_active
                        FROM manual_review_refreshes r
                        LEFT JOIN monitor_jobs j ON j.id=r.monitor_job_id
                        WHERE ((r.status IN ('pending','deferred') AND
                                (r.retry_at IS NULL OR r.retry_at<=?))
                               OR (r.status='running' AND r.lease_until<=?))
                          AND (EXISTS(SELECT 1 FROM manual_review_identity i WHERE i.refresh_id=r.id)
                               OR EXISTS(SELECT 1 FROM manual_review_recovery_grants g WHERE g.refresh_id=r.id
                                   AND g.continuity_id=(SELECT id FROM price_continuity ORDER BY id DESC LIMIT 1))
                               OR NOT EXISTS(SELECT 1 FROM price_continuity_pairs p
                                   WHERE p.continuity_id=(SELECT id FROM price_continuity ORDER BY id DESC LIMIT 1)
                                     AND p.competitor_id=r.competitor_id AND p.ean=r.ean))
                        ORDER BY r.updated_at,CASE WHEN r.status='pending' THEN 0 ELSE 1 END,r.id
                        LIMIT 1""", (now_text, now_text)).fetchone()
                    if row is None:
                        con.commit()
                        break
                    task = dict(row)
                    invalid_reason = (
                        "El producto ya no está en vigilancia activa" if not task["watch_active"] else
                        "El competidor ya no está activo" if not task["competitor_active"] else
                        "La ficha aceptada cambió o dejó de estar verificada" if not task["url_verified"] else
                        "La URL del monitor no coincide con la ficha aceptada" if task["monitor_url"] != task["url"] else
                        "La identidad aceptada cambió o no puede acreditarse"
                        if not _review_identity_valid(con, task) else "")
                    if invalid_reason:
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (invalid_reason, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": invalid_reason})
                        continue
                    try:
                        sample_matches = _review_sample_matches(task)
                        sample_ambiguous = _review_sample_ambiguous(task)
                    except (KeyError, TypeError, ValueError) as exc:
                        detail = f"Muestra de monitorización ilegible: {type(exc).__name__}"
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    if sample_ambiguous:
                        detail = "No se puede acreditar el orden entre la muestra y la solicitud; requiere revisión"
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    if sample_matches:
                        interval = self.watch.monitor_interval_hours(task["ean"], task["competitor_id"])
                        try:
                            policy_reason = _review_sample_policy_reason(con, task, now, interval)
                        except (KeyError, TypeError, ValueError) as exc:
                            policy_reason = f"Muestra comercial ilegible: {type(exc).__name__}"
                        if policy_reason:
                            detail = f"{policy_reason}; requiere revisión"
                            con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                                lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                        (detail, now_text, now_text, task["id"]))
                            _reconcile_review_requests(con, task["id"])
                            con.commit()
                            errors.append({"id": task["id"], "detail": detail})
                            continue
                    if task["monitor_state"] == "done" and sample_matches:
                        try:
                            with self._review_execution(task):
                                pass
                        except (ReviewRouteDenied, Timeout) as exc:
                            retryable = isinstance(exc, Timeout) or exc.retryable
                            detail = "Bloqueo de continuidad ocupado" if isinstance(exc, Timeout) else str(exc)
                            con.execute("""UPDATE manual_review_refreshes SET status=?,detail=?,retry_at=?,
                                lease_until=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                        ("deferred" if retryable else "blocked", detail,
                                         _iso(now + timedelta(minutes=5)) if retryable else None,
                                         None if retryable else now_text, now_text, task["id"]))
                            con.commit()
                            if retryable:
                                deferred += 1
                            else:
                                errors.append({"id": task["id"], "detail": detail})
                            continue
                        sample = json.loads(task["monitor_sample"] or task["monitor_previous_sample"] or "{}")
                        page = sample.get("page", {})
                        if task["monitor_eligible"] and page.get("price") is not None:
                            detail = (f"Oferta comparable: {page['price']} {page.get('currency') or ''} · "
                                      f"{page.get('availability') or 'disponibilidad no indicada'} · "
                                      f"lectura original {sample['at']}")
                        else:
                            reason = sample.get("gap") or task["monitor_detail"] or page.get(
                                "availability") or "faltan datos comerciales"
                            detail = f"Lectura terminada; no comparable: {reason}"
                            detail += f" · lectura original {sample['at']}"
                        con.execute("""UPDATE manual_review_refreshes SET status='done',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail[:1000], now_text, now_text, task["id"]))
                        _reconcile_review_requests(con, task["id"])
                        con.commit()
                        completed += 1
                        continue
                    if task["status"] == "running" and task["started_at"] and not sample_matches:
                        since = _iso(_inventory_datetime(task["started_at"]) - timedelta(seconds=1))
                        uncertain = con.execute("""SELECT 1 FROM access_requests
                            WHERE competitor_id=? AND (ean=? OR ean='' OR ean IS NULL)
                              AND reserved_at>=? LIMIT 1""",
                                                (task["competitor_id"], task["ean"], since)).fetchone()
                        if uncertain:
                            detail = "Interrupción con petición o reserva sin muestra recuperable; revisión necesaria"
                            con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                                lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                        (detail, now_text, now_text, task["id"]))
                            _reconcile_review_requests(con, task["id"])
                            con.commit()
                            errors.append({"id": task["id"], "detail": detail})
                            continue
                    if task["monitor_state"] in {"failed", "gone", "inactive"}:
                        detail = f"El trabajo monitor está en estado {task['monitor_state']}; requiere resolución manual"
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    if task["monitor_state"] == "blocked" and not task["monitor_not_before"]:
                        detail = task["monitor_detail"] or "El trabajo monitor tiene un bloqueo sin fecha de reintento"
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail[:1000], now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    if int(task["monitor_attempts"] or 0) >= MAX_ATTEMPTS:
                        detail = "Máximo de intentos alcanzado; no se reinicia automáticamente"
                        con.execute("""UPDATE manual_review_refreshes SET status='blocked',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    retry_at = None
                    if task["monitor_state"] == "running" and task["monitor_lease_until"]:
                        lease = datetime.fromisoformat(task["monitor_lease_until"])
                        if lease > now:
                            retry_at = lease
                    elif task["monitor_state"] == "done":
                        monitor_row = {"state": "done", "not_before": task["monitor_not_before"],
                                       "sampled_at": task["monitor_sampled_at"],
                                       "last_ok_at": task["monitor_last_ok_at"],
                                       "valid_until": task["monitor_valid_until"]}
                        restriction = _done_job_restriction(monitor_row)
                        if restriction and restriction > now:
                            retry_at = restriction
                    elif task["monitor_not_before"]:
                        not_before = datetime.fromisoformat(task["monitor_not_before"])
                        if not_before > now:
                            retry_at = not_before
                    if retry_at is not None:
                        reason = ("Lectura en curso en otra ejecución" if task["monitor_state"] == "running"
                                  else task["monitor_detail"] or "Restricción vigente del monitor")
                        detail = f"{reason} · aplazada hasta {_iso(retry_at)}"
                        con.execute("""UPDATE manual_review_refreshes SET status='deferred',detail=?,
                            retry_at=?,lease_until=NULL,updated_at=? WHERE id=?""",
                                    (detail, _iso(retry_at), now_text, task["id"]))
                        con.commit()
                        deferred += 1
                        continue
                    if task["monitor_state"] == "review":
                        detail = "La verificación del monitor volvió a revisión; no se fuerza otra lectura"
                        con.execute("""UPDATE manual_review_refreshes SET status='error',detail=?,
                            lease_until=NULL,retry_at=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (detail, now_text, now_text, task["id"]))
                        con.commit()
                        errors.append({"id": task["id"], "detail": detail})
                        continue
                    lease_until = _iso(now + LEASE)
                    con.execute("""INSERT OR IGNORE INTO manual_review_http_attribution(refresh_id,base_requests)
                        VALUES(?,?)""", (task["id"], task["http_requests"]))
                    con.execute("""UPDATE manual_review_refreshes SET status='running',attempts=attempts+1,
                        started_at=COALESCE(started_at,?),lease_until=?,retry_at=NULL,updated_at=? WHERE id=?""",
                                (now_text, lease_until, now_text, task["id"]))
                    con.commit()

                try:
                    with self._review_execution(task):
                        report = self.run([task["competitor_id"]], max_jobs=1,
                                          deadline_seconds=max(1, min(120, deadline_seconds)),
                                          eans=[task["ean"]], point_check=True, exact_url=task["url"],
                                          resume_sample=sample_matches, review_id=task["id"])
                except (ReviewRouteDenied, Timeout) as exc:
                    retryable = isinstance(exc, Timeout) or exc.retryable
                    detail = "Bloqueo de continuidad ocupado" if isinstance(exc, Timeout) else str(exc)
                    status = "deferred" if retryable else "blocked"
                    finished = _iso(self._now())
                    retry = _iso(self._now() + timedelta(minutes=5)) if retryable else None
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE manual_review_refreshes SET status=?,detail=?,retry_at=?,
                            attempts=?,started_at=?,lease_until=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                    (status, detail, retry, task["attempts"], task["started_at"],
                                     None if retryable else finished, finished, task["id"]))
                        _reconcile_review_requests(con, task["id"])
                    if retryable:
                        deferred += 1
                    else:
                        completed += 1
                        errors.append({"id": task["id"], "detail": detail})
                    continue
                with self.store._connect() as con:
                    current = con.execute("""SELECT state,not_before,lease_until,attempts,eligible,detail,
                            last_ok_at,sampled_at,sample FROM monitor_jobs WHERE id=?""",
                                          (task["monitor_job_id"],)).fetchone()
                finished_at = _iso(self._now())
                retry_at = None
                status, detail = "pending", ""
                if current is None:
                    status, detail = "error", "Desapareció el trabajo de monitorización"
                else:
                    current = dict(current)
                    if current["state"] == "done":
                        done_after_request = _review_sample_matches({
                            **task, "monitor_sample": current["sample"], "monitor_sampled_at": current["sampled_at"]})
                        if report.processed or done_after_request:
                            status = "done"
                            sample = json.loads(current["sample"] or current.get("previous_sample") or "{}")
                            page = sample.get("page", {})
                            if current["eligible"] and page.get("price") is not None:
                                detail = (f"Oferta comparable: {page['price']} {page.get('currency') or ''} · "
                                          f"{page.get('availability') or 'disponibilidad no indicada'}")
                            else:
                                reason = sample.get("gap") or current["detail"] or page.get(
                                    "availability") or "faltan datos comerciales"
                                detail = f"Lectura terminada; no comparable: {reason}"
                        else:
                            status = "deferred"
                            retry_at = self._now() + timedelta(seconds=30)
                            detail = report.stopped.get(
                                task["competitor_id"], "Otra ejecución mantiene el bloqueo; se revisará más tarde")
                    elif current["state"] in {"waiting", "pending"}:
                        retry_at = (datetime.fromisoformat(current["not_before"])
                                    if current["not_before"] else self._now() + timedelta(seconds=30))
                        status = "deferred"
                        detail = report.stopped.get(task["competitor_id"]) or current["detail"] or (
                            "Aplazada por la cola de monitorización")
                    elif current["state"] == "running":
                        retry_at = (datetime.fromisoformat(current["lease_until"])
                                    if current["lease_until"] else self._now() + timedelta(seconds=30))
                        status = "deferred"
                        detail = "Lectura en curso en otra ejecución"
                    elif current["state"] in {"blocked", "unavailable"}:
                        not_before = (datetime.fromisoformat(current["not_before"])
                                      if current["not_before"] else None)
                        if not_before and not_before > self._now():
                            status, retry_at = "deferred", not_before
                            detail = current["detail"] or f"Acceso aplazado hasta {_iso(not_before)}"
                        else:
                            status, detail = "blocked", current["detail"] or "Acceso no disponible"
                    elif current["state"] in {"review", "gone", "failed", "inactive"}:
                        status, detail = "error", current["detail"] or f"Monitorización: {current['state']}"
                    else:
                        status, detail = "error", current["detail"] or f"Estado inesperado: {current['state']}"
                with self.store._connect() as con, con:
                    con.execute("""UPDATE manual_review_refreshes SET status=?,detail=?,
                        retry_at=?,lease_until=NULL,finished_at=?,updated_at=? WHERE id=?""",
                                (status, detail[:1000], _iso(retry_at) if retry_at else None,
                                 finished_at if status in {"done", "blocked", "error"} else None,
                                 finished_at, task["id"]))
                    _reconcile_review_requests(con, task["id"])
                if status in {"done", "blocked", "error"}:
                    completed += 1
                    if status != "done":
                        errors.append({"id": task["id"], "detail": detail})
                else:
                    deferred += 1
        finally:
            queue_lock.release()
        return {"skipped": "", "processed": completed, "deferred": deferred, "errors": errors}

    def _process(self, job: dict, config: dict, fetcher: GovernedFetcher, own_price: float | None,
                 *, fresh: bool = False, validate=None, review_id: int | None = None) -> str:
        cid, ean, url = job["competitor_id"], job["ean"], job["url"]
        with self.store._connect() as con:
            verified = con.execute("""SELECT 1 FROM competitor_product_urls
                WHERE competitor_id=? AND ean=? AND url=? AND status='verified'""", (cid, ean, url)).fetchone()
        if not verified:
            self._finish(job, "review", "La URL ya no está verificada; no se consulta")
            return ""
        if job["attempts"] >= MAX_ATTEMPTS:
            self._finish(job, "failed", "Varios intentos fallidos: revisar antes de reintentar")
            return ""
        try:
            if job["sample"]:
                sample = json.loads(job["sample"])
            else:
                validate_competitor_url(url, config["domain"])
                fetcher.ean = ean
                with self.store._connect() as con:
                    shared = con.execute("""SELECT ean,sample FROM monitor_jobs
                        WHERE competitor_id=? AND url=? AND state='done'
                        AND sample<>'' AND last_ok_at IS NOT NULL AND not_before>?
                        ORDER BY last_ok_at DESC LIMIT 1""", (cid, url, _iso(self._now()))).fetchone()
                shared_sample = json.loads(shared["sample"]) if shared and not fresh else None
                shared_page = ProductPage(**shared_sample["page"]) if shared_sample else None
                product = self.search._product(ean)
                shared_candidate = select_product_variant(shared_page, product) if shared_page else None
                shared_match = classify(product, shared_candidate.candidate()) if shared_candidate else None
                reuse_shared = bool(shared_candidate and shared_match and shared_match.status == EXACT
                                    and not shared_candidate.ambiguous)
                if reuse_shared:
                    page = shared_candidate
                    sampled_at = shared_sample["at"]
                else:
                    fetched = fetcher.get(url)
                    validate_competitor_url(fetched.url, config["domain"])
                    page = extract_product(fetched.text, fetched.url, price_selector=config["price_selector"],
                                           availability_selector=config["availability_selector"],
                                           tax_policy=config["tax_policy"],
                                           url_trace={**fetched.trace, "candidate_url": url})
                    sampled_at = _iso(self._now())
                    product = self.search._product(ean)
                    page = select_product_variant(page, product)
                match = classify(product, page.candidate()) if page else None
                from price_web_extract import product_page_rejection
                self._job_trace = {
                    "candidate_url": url,
                    "url_trace": page.url_trace if page else fetched.trace,
                    "identity_status": match.status if match else "no_product",
                    "identity_reasons": list(match.reasons) if match else [],
                    "rejection": product_page_rejection(fetched.text, fetched.url) if page is None else "",
                }
                if review_id is not None and not reuse_shared:
                    trace = page.url_trace if page else fetched.trace
                    hashed_urls = {name: hashlib.sha256(str(trace[name]).encode("utf-8")).hexdigest()
                                   for name in ("candidate_url", "final_url", "selected_variant_url",
                                                "offer_variant_url")
                                   if trace.get(name)}
                    if page:
                        hashed_urls["extracted_url"] = hashlib.sha256(page.url.encode("utf-8")).hexdigest()
                    with self.store._connect() as con, con:
                        receipt_ids = [r[0] for r in con.execute(
                            "SELECT id FROM access_requests WHERE purpose=? ORDER BY id",
                            (f"manual_review:{review_id}",))]
                        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                            VALUES('manual_review',?,'record_read_trace','',?,'worker','',?)""",
                                    (str(review_id), json.dumps({"url_sha256": hashed_urls,
                                                               "receipt_ids": receipt_ids,
                                                               "identity_status": self._job_trace["identity_status"],
                                                               "selected_variant_source":
                                                                   trace.get("selected_variant_source", "")}),
                                     _iso(self._now())))
                observer = _capture_observer.get()
                if not reuse_shared and observer is not None:
                    observer(cid, ean, fetched, page, self._job_trace)
                if validate is not None and page is not None and page.url != url:
                    if (match and match.status == EXACT and _accepted_edel_resolution(page, url, product)):
                        page.url = url
                    else:
                        self._finish(job, "review", "La variante extraída no corresponde a la URL aceptada")
                        return ""
                if page is None or match is None or match.status != EXACT or page.ambiguous:
                    detail = ("; ".join(match.reasons) if match else "Sin datos legibles de producto")
                    with self.store._connect() as con, con:
                        lower_detail = detail.casefold()
                        identity_conflict = any(token in lower_detail for token in ("ean", "gtin", "upc"))
                        if identity_conflict:
                            updated = con.execute("""UPDATE competitor_product_urls
                                SET status='probable',match_status='manual_review',
                                note=substr(? || ' · ' || note,1,1000)
                                WHERE competitor_id=? AND ean=? AND url=?""",
                                                  (detail, cid, ean, url))
                        elif review_id is not None:
                            updated = con.execute("""UPDATE competitor_product_urls
                                SET status='verified',match_status='exact_match'
                                WHERE competitor_id=? AND ean=? AND url=? AND source='user'""",
                                                  (cid, ean, url))
                        else:
                            updated = con.execute("""UPDATE competitor_product_urls
                                SET status='probable',match_status='manual_review',
                                note=substr(? || ' · ' || note,1,1000)
                                WHERE competitor_id=? AND ean=? AND url=?""",
                                                  (detail, cid, ean, url))
                        if page:
                            con.execute("""UPDATE discovery_index SET facts=?,checked_at=? WHERE competitor_id=?
                                AND url=?""", (json.dumps(asdict(page), ensure_ascii=False), sampled_at, cid, url))
                        if updated.rowcount:
                            con.execute("""UPDATE locate_jobs SET state='review',url=?,detail=?,updated_at=?
                                WHERE competitor_id=? AND ean=?""", (url, detail, _iso(self._now()), cid, ean))
                    self._finish(job, "review", detail or "Variantes ambiguas: revisar identidad")
                    return ""
                sample = {"page": asdict(page), "at": sampled_at,
                          "own_price": own_price if own_price is not None and pd.notna(own_price) else None,
                          "gap": comparability_gap(page, config)}
                with self.store._connect() as con, con:
                    con.execute("UPDATE monitor_jobs SET sample=?,sampled_at=? WHERE id=?",
                                (json.dumps(sample, ensure_ascii=False), sample["at"], job["id"]))
            if validate is not None:
                with self.store._connect() as con:
                    validate(con)
                self._commit_sample(job, config, sample, own_price, validate, review_id)
            else:
                self._commit_sample(job, config, sample, own_price)
        except PageGone as exc:
            PriceLocator(self.store, clock=self._now).retire(cid, url, str(exc))
            with self.store._connect() as con, con:
                self._alert(con, job, "gone", job.get("last_ok_at") or url, url, None, str(exc))
            self._finish(job, "gone", str(exc))
            PriceLocator(self.store, clock=self._now).enqueue([cid], [ean])
        except AccessDenied as exc:
            gate = exc.decision
            state = ("unavailable" if gate.code in ("not_authorized", "prohibited") else
                      "blocked" if gate.code == "blocked_manual" else "waiting")
            self._finish(job, state, gate.message, retry_at=gate.retry_at)
            return gate.message
        except ConnectorBlocked as exc:
            gate = self.access.check(cid, "ficha")
            self._finish(job, "waiting" if gate.retry_at else "blocked", str(exc), retry_at=gate.retry_at)
            return str(exc)
        except RobotsDenied as exc:
            self._finish(job, "unavailable", str(exc), retry_at=self._now() + timedelta(days=1))
            return str(exc)
        except (ConnectorError, WebSearchError, ValueError) as exc:
            self._finish(job, "pending", f"Error temporal: {exc}", retry_at=self._now() + timedelta(minutes=30))
        return ""

    def _commit_sample(self, job: dict, config: dict, sample: dict, own_price: float | None,
                       validate=None, review_id: int | None = None) -> None:
        page = ProductPage(**sample["page"])
        at = datetime.fromisoformat(sample["at"])
        outcome = Outcome(job["ean"], job["competitor_id"], config["name"], "exact",
                          url=job["url"], page=page, match_status=EXACT, method="web:monitor",
                          detail="Identidad revalidada: marca + modelo + color; calibre no vinculante")
        search = WebPriceSearch(self.store, clock=lambda: at)
        if review_id is not None:
            digest = hashlib.sha256(json.dumps(sample, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            problem = search.record(outcome, validate=validate, review_sample=(review_id, sample["at"], digest))
        else:
            problem = search.record(outcome, validate=validate) if validate is not None else search.record(outcome)
        if problem:
            raise ValueError(f"No se pudo registrar la observación: {problem}")
        hours = self.watch.monitor_interval_hours(job["ean"], job["competitor_id"]) or 24
        eligible = (not sample["gap"] and page.includes_tax is True
                    and page.availability == "available" and not page.stock_conflict)
        with self.store._connect() as con, con:
            if validate is not None:
                con.execute("BEGIN IMMEDIATE")
                validate(con)
            previous = json.loads(job["previous_sample"]) if job["previous_sample"] else None
            self._changes(con, job, previous, sample, eligible, own_price)
            offer_url = page.url_trace.get("selected_variant_url") or job["url"]
            offer = con.execute("""SELECT id FROM offers WHERE ean=? AND competitor_id=? AND url=?
                AND julianday(captured_at)<=julianday(?)
                ORDER BY captured_at DESC,id DESC LIMIT 1""",
                                (job["ean"], job["competitor_id"], offer_url, sample["at"])).fetchone()
            last_ok = sample["at"] if page.has_price else job["last_ok_at"]
            valid_until = _iso(at + timedelta(hours=hours)) if page.has_price else job["valid_until"]
            offer_id = offer["id"] if offer and page.has_price else job["last_offer_id"]
            con.execute("""UPDATE monitor_jobs SET state='done',attempts=0,lease_until=NULL,not_before=?,
                last_ok_at=?,valid_until=?,last_offer_id=?,eligible=?,detail=?,updated_at=? WHERE id=?""",
                        (_iso(at + timedelta(hours=hours)), last_ok, valid_until, offer_id, int(eligible),
                         sample["gap"] or page.availability, _iso(self._now()), job["id"]))
            con.execute("""UPDATE competitor_product_urls SET last_price=?,last_availability=?,last_ok_at=?,
                failures=0 WHERE competitor_id=? AND ean=? AND url=?""",
                        (page.price, page.availability, last_ok, job["competitor_id"], job["ean"], job["url"]))
            self._event(con, job, "done", sample["gap"] or "Precio y disponibilidad registrados")
        self.search._touch_query(job["competitor_id"], ok=page.has_price)

    def _alert(self, con, job: dict, kind: str, episode: str, old, new, detail: str = "") -> None:
        key = hashlib.sha256(json.dumps([job["id"], kind, episode]).encode()).hexdigest()
        con.execute("""INSERT OR IGNORE INTO price_alerts(
            event_key,ean,competitor_id,kind,at,old_value,new_value,detail)
            VALUES(?,?,?,?,?,?,?,?)""",
                    (key, job["ean"], job["competitor_id"], kind, _iso(self._now()),
                     json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), detail[:1000]))

    def _changes(self, con, job: dict, previous: dict | None, sample: dict, eligible: bool,
                 own_price: float | None) -> None:
        current = sample["page"]
        old = previous["page"] if previous else {}
        if previous and old["availability"] != current["availability"]:
            self._alert(con, job, "availability", sample["at"], old["availability"], current["availability"])
        threshold = float(self.store.get_setting("alert_threshold_pct"))
        prior_valid = previous and not previous["gap"] and old["includes_tax"] is True
        price_valid = not sample["gap"] and current["includes_tax"] is True
        price, before = current["price"], old.get("price")
        if price_valid and prior_valid and before and abs((price - before) / before * 100) >= threshold:
            self._alert(con, job, "price_up" if price > before else "price_down", sample["at"], before, price)
        prior_own = previous.get("own_price") if previous else None
        was_cheaper = bool(prior_valid and old["availability"] == "available" and not old.get("stock_conflict")
                           and prior_own is not None and before < prior_own)
        if eligible and own_price is not None and price < own_price and not was_cheaper:
            self._alert(con, job, "undercut", sample["at"], before, price, f"Vytria: {own_price:g} EUR")

    def check_stale(self, competitor_ids: list[str] | None = None, eans: list[str] | None = None) -> int:
        selected, params = "", []
        for column, values in (("ean", eans), ("competitor_id", competitor_ids)):
            if values is not None:
                selected += f" AND j.{column} IN ({','.join('?' for _ in values)})"
                params.extend(values)
        """Local-only check, idempotent per last successful sample. No HTTP or email."""
        with self.store._connect() as con, con:
            before = con.total_changes
            for row in con.execute(f"""SELECT j.* FROM monitor_jobs j JOIN watch_products w ON w.ean=j.ean
                JOIN competitors c ON c.id=j.competitor_id WHERE w.active=1 AND c.active=1
                AND j.valid_until<? AND j.last_offer_id IS NOT NULL {selected}""",
                                   (_iso(self._now()), *params)).fetchall():
                self._alert(con, dict(row), "stale", row["last_ok_at"], row["last_ok_at"], row["valid_until"])
            return con.total_changes - before

    def configure(self, cid: str, *, enabled: bool, interval_hours: float = 24, max_jobs: int = 200,
                  actor: str) -> None:
        if not actor.strip() or not 1 <= interval_hours <= 720 or not 1 <= max_jobs <= 5000:
            raise ValueError("Actor obligatorio; frecuencia de 1-720 h y lote de 1-5000")
        with self.store._connect() as con, con:
            if not con.execute("SELECT 1 FROM competitors WHERE id=?", (cid,)).fetchone():
                raise ValueError("Competidor desconocido")
            con.execute("INSERT OR IGNORE INTO competitor_schedule(competitor_id) VALUES(?)", (cid,))
            con.execute("UPDATE competitor_schedule SET interval_hours=? WHERE competitor_id=?", (interval_hours, cid))
            con.execute("""INSERT INTO monitor_schedule(competitor_id,enabled,max_jobs,updated_by,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(competitor_id) DO UPDATE SET enabled=excluded.enabled,
                max_jobs=excluded.max_jobs,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                        (cid, int(enabled), max_jobs, actor.strip(), _iso(self._now())))
            AccessControl._audit(con, "competitor", cid, "monitor_schedule", "", str(int(enabled)), actor,
                                 f"{interval_hours:g} h; {max_jobs} trabajos", _iso(self._now()))

    def tick(self) -> dict:
        if not schedule_enabled():
            return {"skipped": f"Planificación desactivada ({SCHEDULE_ENV})", "runs": []}
        try:
            with FileLock(f"{self.store.database_path}.monitor-tick.lock", timeout=0):
                with self.store._connect() as con:
                    plans = con.execute("""SELECT s.* FROM monitor_schedule s JOIN competitors c
                        ON c.id=s.competitor_id WHERE s.enabled=1 AND c.active=1""").fetchall()
                if not plans:
                    return {"skipped": "Sin competidores habilitados", "runs": []}
                if not self.vytria.auto_enabled:
                    return {"skipped": "Active primero la sincronización diaria de Vytria", "runs": []}
                try:
                    sync = self.vytria.run_watched_scheduled()
                except SyncInProgress as exc:
                    return {"skipped": str(exc), "runs": []}
                return {"vytria": asdict(sync) if sync else None,
                        "runs": [{"competitor": r["competitor_id"],
                                  "summary": self.run([r["competitor_id"]], max_jobs=r["max_jobs"]).summary}
                                 for r in plans]}
        except Timeout:
            return {"skipped": "Otra pasada de monitorización está en curso", "runs": []}

    def jobs(self) -> pd.DataFrame:
        with self.store._connect() as con:
            return pd.read_sql_query("""SELECT j.*,c.name AS competitor FROM monitor_jobs j
                JOIN competitors c ON c.id=j.competitor_id ORDER BY j.updated_at DESC,j.id DESC""", con)

    def review_refreshes(self, limit: int | None = 100) -> pd.DataFrame:
        if limit is not None and limit < 1:
            raise ValueError("El límite debe ser positivo o None")
        limit_sql = "" if limit is None else " LIMIT ?"
        with self.store._connect() as con:
            rows = pd.read_sql_query("""SELECT r.id,r.ean,p.brand,p.model,p.color,r.competitor_id,
                    c.name AS competitor,r.url,r.actor,r.reason,r.status,r.detail,r.http_requests,
                    r.requested_at,r.started_at,r.finished_at,r.retry_at,r.attempts,
                    j.state AS monitor_state,j.eligible,j.last_ok_at
                FROM manual_review_refreshes r JOIN products p ON p.ean=r.ean
                JOIN competitors c ON c.id=r.competitor_id
                LEFT JOIN monitor_jobs j ON j.id=r.monitor_job_id
                ORDER BY r.requested_at DESC,r.id DESC""" + limit_sql,
                                     con, params=() if limit is None else (limit,))
        if not rows.empty:
            rows["ean"] = rows["ean"].astype(str)
        return rows

    def retry_failed(self, cid: str, *, actor: str) -> int:
        if not actor.strip():
            raise ValueError("Indique quién solicita el reintento")
        with self.store._connect() as con, con:
            rows = con.execute("SELECT id FROM monitor_jobs WHERE competitor_id=? AND state='failed'",
                               (cid,)).fetchall()
            for row in rows:
                self._event(con, dict(row), "pending", f"Reintento solicitado por {actor.strip()}")
            return con.execute("""UPDATE monitor_jobs SET state='pending',attempts=0,not_before=NULL,
                lease_until=NULL,updated_at=? WHERE competitor_id=? AND state='failed'""",
                               (_iso(self._now()), cid)).rowcount

    def alerts(self, limit: int = 200) -> pd.DataFrame:
        with self.store._connect() as con:
            return pd.read_sql_query("""SELECT a.id,a.event_key,a.ean,a.competitor_id,a.kind,a.at,a.old_value,
                a.new_value,a.detail,c.name AS competitor,p.brand,p.model,p.color FROM price_alerts a
                JOIN competitors c ON c.id=a.competitor_id JOIN products p ON p.ean=a.ean
                UNION ALL SELECT a.id,'vytria:' || a.history_id,a.ean,NULL,a.kind,a.at,a.old_value,a.new_value,a.detail,
                'Vytria',p.brand,p.model,p.color FROM vytria_price_alerts a JOIN products p ON p.ean=a.ean
                ORDER BY at DESC,id DESC LIMIT ?""", con, params=(limit,))

    def email_summary(self, limit: int = 200) -> str:
        rows = self.alerts(limit)
        lines = ["Diagonal Cloud - resumen de vigilancia (envío automático desactivado)"]
        for row in rows.itertuples():
            lines.append(f"{row.at} | {row.ean} {row.brand} {row.model} {row.color} | {row.competitor} | "
                         f"{ALERTS.get(row.kind, row.kind)} | {row.old_value} -> {row.new_value}")
        return "\n".join(lines)

    def inventory(self, *, competitor_ids: list[str] | None = None,
                  eans: list[str] | None = None) -> pd.DataFrame:
        summary, _ = self.store.dashboard(now=self._now())
        own = summary.set_index("ean")["own_price_eur"].to_dict() if not summary.empty else {}
        own_details = summary.set_index("ean").to_dict("index") if not summary.empty else {}
        with self.store._connect() as con:
            rows = pd.read_sql_query("""SELECT w.ean,p.brand,p.model,p.color,c.id AS competitor_id,
                c.name AS competitor,COALESCE(u.url,j.url) AS url,u.source AS url_source,
                CASE WHEN EXISTS(SELECT 1 FROM pilot_url_candidates pc WHERE pc.ean=w.ean
                    AND pc.competitor_id=c.id AND pc.url=COALESCE(u.url,j.url) AND pc.state='confirmed')
                    THEN 'Piloto: ficha reutilizada'
                    WHEN u.source='auto_watch' THEN 'Motor de vigilancia'
                    WHEN u.url IS NOT NULL THEN 'Ficha registrada / importación'
                    ELSE 'Sin ficha verificada' END AS provenance,
                j.state,j.detail,l.state AS locate_state,l.detail AS locate_detail,j.sample,j.previous_sample,
                j.last_ok_at,j.valid_until,j.eligible,
                COALESCE(g.interval_hours,s.interval_hours,24) AS interval_hours,
                EXISTS(SELECT 1 FROM competitor_product_urls u WHERE u.ean=w.ean AND u.competitor_id=c.id
                    AND u.status='verified') AS located
                FROM watch_products w JOIN products p ON p.ean=w.ean
                JOIN watch_groups g ON g.name=w.group_name CROSS JOIN competitors c
                LEFT JOIN competitor_schedule s ON s.competitor_id=c.id
                LEFT JOIN monitor_jobs j ON j.ean=w.ean AND j.competitor_id=c.id
                LEFT JOIN locate_jobs l ON l.ean=w.ean AND l.competitor_id=c.id
                LEFT JOIN competitor_product_urls u ON u.rowid=(
                    SELECT x.rowid FROM competitor_product_urls x WHERE x.ean=w.ean AND x.competitor_id=c.id
                    AND x.status='verified' ORDER BY x.verified_at DESC,x.url LIMIT 1)
                WHERE w.active=1 AND c.active=1 ORDER BY p.brand,p.model,p.color,c.name""", con)
        # A supervised preview must not evaluate budgets for unrelated shops:
        # its execution context only authorizes the selected frozen scope.
        if competitor_ids is not None:
            rows = rows[rows["competitor_id"].isin(competitor_ids)]
        if eans is not None:
            rows = rows[rows["ean"].isin(eans)]
        if rows.empty:
            return rows
        now = _inventory_datetime(self._now())
        gates = {cid: self.access.check(cid, "ficha") for cid in rows["competitor_id"].unique()}
        data = []
        # pandas 3 string columns turn SQL NULL into NaN, which is truthy.
        for row in rows.astype(object).where(rows.notna(), None).to_dict("records"):
            raw = row["sample"] or row["previous_sample"]
            sample = json.loads(raw) if isinstance(raw, str) and raw else {}
            page = sample.get("page", {})
            validity = row["valid_until"]
            missing = validity is None or (isinstance(validity, str) and not validity.strip())
            valid_until = None
            date_issue = ""
            if missing:
                if row["last_ok_at"] or row["eligible"] == 1:
                    date_issue = "Sin fecha de vigencia"
            else:
                try:
                    valid_until = _inventory_datetime(validity)
                    valid_until = min(valid_until, _inventory_datetime(row["last_ok_at"])
                                      + timedelta(hours=row["interval_hours"]))
                except ValueError:
                    date_issue = "Fecha de vigencia inválida"
                    logging.getLogger(__name__).warning(
                        "Invalid monitor valid_until for ean=%s competitor=%s (type=%s)",
                        row["ean"], row["competitor_id"], type(validity).__name__)
            stale = valid_until is not None and valid_until < now
            comparable = row["eligible"] == 1 and valid_until is not None and not stale and row["state"] not in (
                "gone", "review", "blocked", "unavailable")
            gate = gates[row["competitor_id"]]
            restricted = gate.code in ("blocked_manual", "blocked_wait", "not_authorized", "prohibited")
            comparable = comparable and not restricted
            state = ("Bloqueado" if gate.code in ("blocked_manual", "blocked_wait") else
                     "Sin autorización" if gate.code in ("not_authorized", "prohibited") else
                     "Revisión de identidad" if not row["located"] and row["locate_state"] == "review" else
                     "Ficha localizada" if not row["state"] and row["located"] else
                     "Sin localizar" if not row["state"] else
                     "Ficha retirada" if row["state"] == "gone" else
                     "Bloqueado" if row["state"] in ("blocked", "unavailable") else
                     date_issue if date_issue else
                     "Precio caducado" if stale else
                     "Sin stock" if page.get("availability") == "unavailable" else
                     "Actualizado" if comparable else
                     "Sin precio" if row["state"] == "done" and page.get("price") is None else
                     "No comparable" if row["state"] == "done" else STATES.get(row["state"], row["state"]))
            price, vytria = page.get("price"), own.get(row["ean"])
            diff = vytria - price if comparable and vytria is not None and pd.notna(vytria) else None
            own_row = own_details.get(row["ean"], {})
            data.append({**row, "status": state, "date_issue": date_issue, "own_price_eur": vytria, "price": price,
                         "own_price_source": own_row.get("own_price_source", ""),
                         "own_price_checked_at": own_row.get("own_price_checked_at"),
                         "own_price_status": own_row.get("own_price_status", ""),
                         "currency": page.get("currency", ""), "includes_tax": page.get("includes_tax"),
                         "availability": page.get("availability", "unknown"), "comparable": comparable,
                         "difference_eur": diff, "difference_pct": diff / vytria * 100 if diff is not None
                         and vytria else None})
        frame = pd.DataFrame(data)
        best = frame[frame["comparable"]].groupby("ean")["price"].min()
        frame["best_price"] = frame["ean"].map(best)
        return frame.drop(columns=["sample", "previous_sample"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("command", choices=["enqueue", "run", "tick", "loop", "status", "email-summary"])
    parser.add_argument("--competitor", action="append")
    parser.add_argument("--max-jobs", type=int, default=200)
    parser.add_argument("--every", type=int, default=300)
    args = parser.parse_args(argv)
    monitor = PriceMonitor(PriceComparatorStore(args.db))
    if args.command == "enqueue":
        print(monitor.enqueue(args.competitor))
    elif args.command == "run":
        print(monitor.run(args.competitor, max_jobs=args.max_jobs).summary)
    elif args.command == "status":
        print(monitor.jobs().drop(columns=["sample", "previous_sample"]).to_string(index=False))
    elif args.command == "email-summary":
        print(monitor.email_summary())
    elif args.command == "tick":
        print(monitor.tick())
    else:
        if args.every < 30:
            parser.error("--every debe ser al menos 30 segundos")
        while True:
            print(monitor.tick(), flush=True)
            time.sleep(args.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
