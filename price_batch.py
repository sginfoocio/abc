"""Supervised watch batches over existing queues. Preview is local-only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

import pandas as pd

from price_comparator import PriceComparatorStore
from price_locator import PriceLocator, link_matches
from price_monitor import PriceMonitor, _inventory_datetime
from price_pilot import pilot_scope
from price_url_candidates import CandidatePlan, candidate_plan


class WatchBatch:
    def __init__(self, store: PriceComparatorStore, *, locator: PriceLocator | None = None,
                 monitor: PriceMonitor | None = None) -> None:
        self.store = store
        self.locator = locator or PriceLocator(store)
        self.monitor = monitor or PriceMonitor(store)
        self.access = self.monitor.access

    def preview(self, eans: list[str], competitors: list[str]) -> pd.DataFrame:
        if not eans or not competitors:
            raise ValueError("Seleccione productos y competidores")
        eans, competitors = list(dict.fromkeys(eans)), list(dict.fromkeys(competitors))
        with self.store._connect() as con:
            watched = {r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")}
            active = {r[0] for r in con.execute("SELECT id FROM competitors WHERE active=1")}
            pending = {(r[0], r[1]) for r in con.execute("""SELECT ean,competitor_id FROM competitor_product_urls
                WHERE status IN ('probable','pending_verification')""")}
            marks = ",".join("?" for _ in competitors)
            index = con.execute(f"""SELECT competitor_id,signature FROM discovery_index
                                    WHERE competitor_id IN ({marks})""", competitors).fetchall()
            products = {r["ean"]: dict(r) for r in con.execute("SELECT * FROM products")}
            domains = {r["id"]: r["domain"] for r in con.execute("SELECT id,domain FROM competitors")}
            access_states = {r["competitor_id"]: r["access_status"] for r in con.execute(
                "SELECT competitor_id,access_status FROM competitor_web_config")}
        if not set(eans) <= watched or not set(competitors) <= active:
            raise ValueError("La selección contiene productos no vigilados o competidores inactivos")
        frame = self.monitor.inventory()
        frame = frame[frame["ean"].isin(eans) & frame["competitor_id"].isin(competitors)].copy()
        if len(frame) != len(eans) * len(competitors):
            raise ValueError("La vigilancia ha cambiado durante la lectura; vuelva a seleccionar el lote")
        methods = self.access.methods()
        sources = self.locator.sources()
        scope = pilot_scope(self.store)
        now = self.monitor._now()
        jobs = self.monitor.jobs()
        jobs = {(r["ean"], r["competitor_id"]): r for r in jobs.to_dict("records")}
        rows = []
        policies = {}
        for cid in competitors:
            gate = self.access.check(cid, "ficha")
            authorized = methods[(methods["competitor_id"] == cid) & (methods["status"] == "authorized")]
            search_methods = set(authorized["method"]) - {"ficha", "external"}
            has_source = not sources[(sources["competitor_id"] == cid) & sources["active"].eq(1) &
                                     sources["method"].isin(search_methods)].empty
            discovery = bool(search_methods & {"sitemap", "site_search"} or
                             (search_methods & {"feed", "catalog"} and has_source))
            budget = self.access.budget("competitor", cid)
            remaining = max(0, budget["daily_limit"] - self.access.usage("competitor", cid))
            signatures = [r["signature"] for r in index if r["competitor_id"] == cid]
            policies[cid] = gate, discovery, remaining, signatures
        for row in frame.to_dict("records"):
            cid, ean = row["competitor_id"], row["ean"]
            gate, discovery, remaining, signatures = policies[cid]
            indexed = any(link_matches(sig, row["model"], row["color"]) for sig in signatures)
            plan = candidate_plan(products[ean], domains[cid]) if not row["located"] else CandidatePlan()
            pilot = bool(scope and cid in scope["competitors"] and ean in scope["eans"] and not row["located"])
            job = jobs.get((ean, cid), {})
            sampled = job.get("sampled_at")
            missing = sampled is None or pd.isna(sampled) or sampled == ""
            due = job.get("state") != "done" or missing or (
                _inventory_datetime(sampled) + timedelta(hours=row["interval_hours"]) <= now)
            if (job.get("state") in {"review", "failed"} or row["locate_state"] in {"review", "failed"} or
                    (not row["located"] and (ean, cid) in pending)):
                action = "Revisión / recuperación"
            elif pilot:
                action = "Pendiente en piloto"
            elif row["located"]:
                action = "Monitorizar" if due else "Reutilizar precio vigente"
            else:
                action = "Localizar y monitorizar"
            needs_http = action in {"Monitorizar", "Localizar y monitorizar"}
            allowed = gate.allowed or gate.waitable
            if not row["located"] and not (discovery or indexed or plan.runnable):
                allowed = False
            reason = gate.message if not allowed else ""
            if not row["located"] and not (discovery or indexed or plan.runnable):
                reason = "Sin fuente y método de localización autorizados; revisar índice/configuración"
                if plan.reason:
                    reason += f" · {plan.reason}"
            if access_states.get(cid) in {"blocked", "robots_restricted"}:
                allowed = False
                reason = self.locator.search._gate({"access_status": access_states[cid]})
            rows.append({**row, "action": action, "ready": bool(allowed and needs_http),
                         "access": reason or ("Autorizado; pausas aplicables" if allowed else "Revisión necesaria"),
                         "candidate_count": len(plan.runnable), "candidate_reason": plan.reason,
                         "candidate_reason_code": plan.reason_code,
                         "local_candidates": [candidate.provenance() for candidate in plan.candidates],
                         "remaining_http": remaining, "minimum_http": int(needs_http),
                         "estimate": ("1 ficha + robots si no está en caché" if row["located"] else
                                      "Variable: listados, paginación y ficha; limitado por presupuesto")})
        return pd.DataFrame(rows)

    def prepare(self, eans: list[str], competitors: list[str]) -> pd.DataFrame:
        self.preview(eans, competitors)  # validate before any write
        self.locator.enqueue(competitors, eans)
        self.monitor.enqueue(competitors, eans)
        return self.preview(eans, competitors)

    def execute(self, eans: list[str], competitors: list[str], *, approved: bool,
                sync_vytria: bool = True, max_jobs: int = 300) -> dict:
        if not approved:
            raise ValueError("El lote requiere confirmación explícita")
        if not 1 <= max_jobs <= 5000:
            raise ValueError("El lote debe contener de 1 a 5000 trabajos")
        preview = self.preview(eans, competitors)  # permissions and budgets may have changed
        result = {"vytria": None, "runs": [], "deferred": int(preview["ready"].sum()),
                  "skipped": preview.loc[~preview["ready"], ["ean", "competitor", "action", "access"]].to_dict("records")}
        if sync_vytria:
            result["vytria"] = asdict(self.monitor.vytria.run(trigger="watch:batch", watched_only=True))
        self.prepare(eans, competitors)
        remaining = max_jobs
        for cid in competitors:
            selected = preview[(preview["competitor_id"] == cid) & preview["ready"]]["ean"].tolist()[:remaining]
            if not selected or remaining <= 0:
                continue
            located = self.locator.run([cid], eans=selected, max_jobs=remaining, deadline_seconds=300)
            monitored = self.monitor.run([cid], eans=selected, max_jobs=remaining, deadline_seconds=300)
            remaining -= len(selected)
            result["deferred"] -= len(selected)
            result["runs"].append({"competitor": cid, "location": located.summary,
                                   "monitor": monitored.summary,
                                   "errors": {**located.stopped, **monitored.stopped}})
        return result


def observation_export(store: PriceComparatorStore) -> pd.DataFrame:
    """All stored observations, including pilot and unmonitored history; never an HTTP refresh."""
    with store._connect() as con:
        return pd.read_sql_query("""SELECT o.id AS observation_id,o.ean,p.brand,p.model,p.color,
            c.name AS competitor,o.url,o.price,o.promotional_price,o.currency,o.market,o.availability,
            o.price_includes_tax,o.vat_rate,o.captured_at,
            COALESCE(k.last_ok_at,j.last_ok_at,o.captured_at) AS checked_at,o.match_status,
            o.method,u.source AS url_source,j.state AS monitor_state,j.valid_until,
            CASE WHEN EXISTS(SELECT 1 FROM pilot_url_candidates pc
                WHERE pc.ean=o.ean AND pc.competitor_id=o.competitor_id AND pc.url=o.url
                AND pc.state='confirmed') THEN 'Piloto: ficha reutilizada'
                WHEN u.source='auto_watch' THEN 'Motor de vigilancia'
                ELSE 'Ficha registrada / importación' END AS provenance
            FROM offers o JOIN products p ON p.ean=o.ean JOIN competitors c ON c.id=o.competitor_id
            LEFT JOIN offer_checks k ON k.last_offer_id=o.id
            LEFT JOIN competitor_product_urls u ON u.ean=o.ean AND u.competitor_id=o.competitor_id AND u.url=o.url
            LEFT JOIN monitor_jobs j ON j.ean=o.ean AND j.competitor_id=o.competitor_id AND j.last_offer_id=o.id
            ORDER BY o.captured_at DESC,o.id DESC""", con)
