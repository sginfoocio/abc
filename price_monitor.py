"""Verified-URL monitoring, independent of discovery. No network on import or when opening the UI.

Persistent leases and extracted receipts allow restart without re-searching or re-reading a saved sample.
Offers use the existing importer/history; alerts and job completion commit together. Periodic execution
requires PRICE_MONITOR_SCHEDULE_ENABLED and an explicitly enabled competitor (both off by default).
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from numbers import Number
import os
import time
from typing import Callable

from filelock import FileLock, Timeout
import pandas as pd

from price_access import AccessControl, AccessDenied, GovernedFetcher
from price_comparator import PriceComparatorStore
from price_connectors import ConnectorBlocked, ConnectorError
from price_locator import LEASE, MAX_ATTEMPTS, PriceLocator
from price_references import EXACT, classify
from price_watch import PriceWatch
from price_web import Outcome, WebPriceSearch, WebSearchError, comparability_gap, validate_competitor_url
from price_web_extract import ProductPage, extract_product
from price_web_fetch import PageGone, RobotsDenied, _requests_transport
from vytria_price_sync import SyncInProgress, VytriaPriceSync

SCHEDULE_ENV = "PRICE_MONITOR_SCHEDULE_ENABLED"
STATES = {"pending": "Pendiente", "running": "En ejecución", "done": "Terminada",
          "waiting": "En espera de presupuesto / pausa", "blocked": "Bloqueada",
          "unavailable": "Sin autorización", "review": "Revisión manual",
          "gone": "Ficha retirada", "inactive": "Fuera de vigilancia", "failed": "Error: revisar"}
ALERTS = {"price_down": "Bajada de precio", "price_up": "Subida de precio",
          "undercut": "Más barato que Vytria", "availability": "Cambio de disponibilidad",
          "gone": "Ficha desaparecida", "stale": "Precio caducado"}


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


def schedule_enabled() -> bool:
    return os.environ.get(SCHEDULE_ENV, "").strip().lower() in {"1", "true", "yes"}


@dataclass
class MonitorReport:
    processed: int = 0
    requests: int = 0
    states: Counter = field(default_factory=Counter)
    stopped: dict[str, str] = field(default_factory=dict)

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

    def enqueue(self, competitor_ids: list[str] | None = None, eans: list[str] | None = None) -> int:
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
                seen.add((cid, ean))
                interval = self.watch.monitor_interval_hours(ean, cid)
                if interval is None:
                    continue
                job = con.execute("SELECT * FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                                  (cid, ean)).fetchone()
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
                if changed or (job["state"] == "done" and due <= now):
                    con.execute("""UPDATE monitor_jobs SET url=?,state='pending',not_before=NULL,lease_until=NULL,
                        attempts=0,previous_sample=CASE WHEN sample<>'' THEN sample ELSE previous_sample END,
                        sample='',sampled_at=NULL,eligible=CASE WHEN ? THEN 0 ELSE eligible END,
                        detail='',updated_at=? WHERE id=?""",
                                (row["url"], int(changed), _iso(now), job["id"]))
                elif job["state"] == "done":
                    valid_until = (_iso(datetime.fromisoformat(job["last_ok_at"]) + timedelta(hours=interval))
                                   if job["last_ok_at"] else None)
                    con.execute("UPDATE monitor_jobs SET not_before=?,valid_until=? WHERE id=?",
                                (_iso(due), valid_until, job["id"]))
        self.check_stale(competitor_ids, eans)
        return added

    def _claim(self, cid: str, eans: list[str] | None = None) -> dict | None:
        now = _iso(self._now())
        selected = "" if eans is None else f" AND j.ean IN ({','.join('?' for _ in eans)})"
        with self.store._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(f"""SELECT j.* FROM monitor_jobs j
                JOIN watch_products w ON w.ean=j.ean AND w.active=1
                JOIN competitors c ON c.id=j.competitor_id AND c.active=1
                WHERE j.competitor_id=? {selected} AND
                  ((j.state IN ('pending','waiting','blocked','unavailable') AND
                    (j.not_before IS NULL OR j.not_before<=?)) OR (j.state='running' AND j.lease_until<=?))
                ORDER BY j.not_before,j.id LIMIT 1""", (cid, *(eans or []), now, now)).fetchone()
            if row is None:
                con.commit()
                return None
            con.execute("""UPDATE monitor_jobs SET state='running',attempts=attempts+1,lease_until=?,
                updated_at=? WHERE id=?""", (_iso(self._now() + LEASE), now, row["id"]))
            con.commit()
            return dict(row)

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
            deadline_seconds: float = 900, eans: list[str] | None = None) -> MonitorReport:
        """An explicitly requested bounded batch. Does not enable periodic work or search for any URL."""
        if not 1 <= max_jobs <= 5000 or not 1 <= deadline_seconds <= 14400:
            raise ValueError("Lote de 1-5000 trabajos y plazo de 1-14400 segundos")
        report = MonitorReport()
        started = time.monotonic()
        self.enqueue(competitor_ids, eans)
        summary, _ = self.store.dashboard(now=self._now())
        own = dict(summary[["ean", "own_price_eur"]].itertuples(index=False, name=None)) if not summary.empty else {}
        with self.store._connect() as con:
            ids = competitor_ids if competitor_ids is not None else [
                r[0] for r in con.execute("SELECT DISTINCT competitor_id FROM monitor_jobs ORDER BY competitor_id")]
        for cid in ids:
            if report.processed >= max_jobs or time.monotonic() - started >= deadline_seconds:
                break
            try:
                with self.search._lock(cid):
                    config = self.search.config(cid)
                    fetcher = GovernedFetcher(
                        self.access, cid, "ficha", purpose="monitor", transport=self._transport, sleep=self._sleep,
                        pause_seconds=0, retries=0, max_requests=1_000_000,
                        timeout=float(config["timeout_seconds"] or 15))
                    with self.store._connect() as con:
                        first = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]
                    try:
                        while report.processed < max_jobs and time.monotonic() - started < deadline_seconds:
                            job = self._claim(cid, eans)
                            if job is None:
                                break
                            report.processed += 1
                            stop = self._process(job, config, fetcher, own.get(job["ean"]))
                            with self.store._connect() as con:
                                state = con.execute("SELECT state FROM monitor_jobs WHERE id=?",
                                                    (job["id"],)).fetchone()[0]
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
        self.check_stale(competitor_ids, eans)
        return report

    def _process(self, job: dict, config: dict, fetcher: GovernedFetcher, own_price: float | None) -> str:
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
                    shared = con.execute("""SELECT sample FROM monitor_jobs
                        WHERE competitor_id=? AND url=? AND state='done'
                        AND sample<>'' AND last_ok_at IS NOT NULL AND not_before>?
                        ORDER BY last_ok_at DESC LIMIT 1""", (cid, url, _iso(self._now()))).fetchone()
                if shared:
                    shared_sample = json.loads(shared["sample"])
                    page = ProductPage(**shared_sample["page"])
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
                match = classify(product, page.candidate()) if page else None
                if page is None or match is None or match.status != EXACT or page.ambiguous:
                    detail = ("; ".join(match.reasons) if match else "Sin datos legibles de producto")
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE competitor_product_urls
                            SET status='probable',match_status='manual_review',
                            note=substr(? || ' · ' || note,1,1000)
                            WHERE competitor_id=? AND ean=? AND url=?""", (detail, cid, ean, url))
                        if page:
                            con.execute("""UPDATE discovery_index SET facts=?,checked_at=? WHERE competitor_id=?
                                AND url=?""", (json.dumps(asdict(page), ensure_ascii=False), sampled_at, cid, url))
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
            self._commit_sample(job, config, sample, own_price)
        except PageGone as exc:
            PriceLocator(self.store, clock=self._now).retire(cid, url, str(exc))
            with self.store._connect() as con, con:
                self._alert(con, job, "gone", job.get("last_ok_at") or url, url, None, str(exc))
            self._finish(job, "gone", str(exc))
            PriceLocator(self.store, clock=self._now).enqueue()
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

    def _commit_sample(self, job: dict, config: dict, sample: dict, own_price: float | None) -> None:
        page = ProductPage(**sample["page"])
        at = datetime.fromisoformat(sample["at"])
        outcome = Outcome(job["ean"], job["competitor_id"], config["name"], "exact",
                          url=job["url"], page=page, match_status=EXACT, method="web:monitor",
                          detail="Identidad revalidada: marca + modelo + color; calibre no vinculante")
        # A receipt is replayed with its original timestamp, so the existing importer is idempotent.
        problem = WebPriceSearch(self.store, clock=lambda: at).record(outcome)
        if problem:
            raise ValueError(f"No se pudo registrar la observación: {problem}")
        hours = self.watch.monitor_interval_hours(job["ean"], job["competitor_id"]) or 24
        eligible = (not sample["gap"] and page.includes_tax is True
                    and page.availability == "available" and not page.stock_conflict)
        with self.store._connect() as con, con:
            previous = json.loads(job["previous_sample"]) if job["previous_sample"] else None
            self._changes(con, job, previous, sample, eligible, own_price)
            offer = con.execute("""SELECT id FROM offers WHERE ean=? AND competitor_id=? AND url=?
                ORDER BY captured_at DESC,id DESC LIMIT 1""",
                                (job["ean"], job["competitor_id"], job["url"])).fetchone()
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

    def inventory(self) -> pd.DataFrame:
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
