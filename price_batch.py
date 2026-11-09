"""Supervised watch batches over existing queues. Preview is local-only."""
from __future__ import annotations

from dataclasses import asdict
from contextlib import nullcontext
from datetime import timedelta
import json

import pandas as pd

from price_comparator import PriceComparatorStore
from price_locator import PriceLocator, job_deferral, link_matches
from price_monitor import PriceMonitor, _inventory_datetime
from price_pilot import PricePilot, pilot_scope, stamp
from price_url_candidates import CandidatePlan, candidate_plan


class WatchBatch:
    def __init__(self, store: PriceComparatorStore, *, locator: PriceLocator | None = None,
                 monitor: PriceMonitor | None = None) -> None:
        self.store = store
        self.locator = locator or PriceLocator(store)
        self.monitor = monitor or PriceMonitor(store)
        self.access = self.monitor.access

    def _prepare_snapshot(self, preview: pd.DataFrame) -> None:
        for cid in preview["competitor_id"].drop_duplicates().tolist():
            selected = preview.loc[preview["competitor_id"].eq(cid) & preview["ready"], "ean"].tolist()
            if selected:
                self.locator.enqueue([cid], selected)
                self.monitor.enqueue([cid], selected)

    def _unclaimed_reason(self, cid: str, ean: str, *, monitoring: bool,
                          point_check: bool, pilot_authorized: bool) -> str:
        with self.store._connect() as con:
            if not con.execute("SELECT 1 FROM watch_products WHERE ean=? AND active=1", (ean,)).fetchone():
                return "Ningún trabajo reclamado: el producto ya no está en vigilancia"
            if not con.execute("SELECT 1 FROM competitors WHERE id=? AND active=1", (cid,)).fetchone():
                return "Ningún trabajo reclamado: el competidor ya no está activo"
            monitoring = monitoring or bool(con.execute("""SELECT 1 FROM competitor_product_urls
                WHERE competitor_id=? AND ean=? AND status='verified' LIMIT 1""", (cid, ean)).fetchone())
            table = "monitor_jobs" if monitoring else "locate_jobs"
            row = con.execute(f"SELECT * FROM {table} WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone()
        if row is None:
            return "Ningún trabajo reclamado: no existe trabajo en la cola correspondiente"
        job = dict(row)
        reason = job_deferral(job, self.monitor._now(), point_check=point_check, monitoring=monitoring,
                              pilot_authorized=pilot_authorized)
        if reason:
            return f"Ningún trabajo reclamado: {reason}"
        detail = f"; detalle: {job['detail']}" if job.get("detail") else ""
        return f"Ningún trabajo reclamado: estado actual {job['state']}{detail}"

    def preview(self, eans: list[str], competitors: list[str], *, point_check: bool = False) -> pd.DataFrame:
        from price_trial import active_execution
        supervised_trial = active_execution(self.store) is not None
        if not eans or not competitors:
            raise ValueError("Seleccione productos y competidores")
        eans, competitors = list(dict.fromkeys(eans)), list(dict.fromkeys(competitors))
        with self.store._connect() as con:
            watched = {r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")}
            active = {r[0] for r in con.execute("SELECT id FROM competitors WHERE active=1")}
            pending = {(r[0], r[1]) for r in con.execute("""SELECT ean,competitor_id FROM competitor_product_urls
                WHERE status IN ('probable','pending_verification')""")}
            pending.update((r[0], r[1]) for r in con.execute("""SELECT ean,competitor_id FROM pilot_url_candidates
                WHERE state IN ('candidate_found','pending_validation')"""))
            marks = ",".join("?" for _ in competitors)
            index = con.execute(f"""SELECT competitor_id,signature FROM discovery_index
                                    WHERE competitor_id IN ({marks})""", competitors).fetchall()
            products = {r["ean"]: dict(r) for r in con.execute("SELECT * FROM products")}
            domains = {r["id"]: r["domain"] for r in con.execute("SELECT id,domain FROM competitors")}
            access_states = {r["competitor_id"]: r["access_status"] for r in con.execute(
                "SELECT competitor_id,access_status FROM competitor_web_config")}
            locate_jobs = {(r["ean"], r["competitor_id"]): dict(r) for r in con.execute("SELECT * FROM locate_jobs")}
            monitor_jobs = {(r["ean"], r["competitor_id"]): dict(r) for r in con.execute("SELECT * FROM monitor_jobs")}
            pilot_active = con.execute("SELECT id FROM pilot_runs WHERE status IN ('running','interrupted')").fetchone()
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
            pilot_member = bool(scope and cid in scope["competitors"] and ean in scope["eans"])
            pilot = pilot_member and not row["located"]
            job = monitor_jobs.get((ean, cid), {})
            locate_job = locate_jobs.get((ean, cid), {})
            sampled = job.get("sampled_at")
            missing = sampled is None or pd.isna(sampled) or sampled == ""
            due = job.get("state") != "done" or missing or (
                _inventory_datetime(sampled) + timedelta(hours=row["interval_hours"]) <= now)
            if (job.get("state") in {"review", "failed"} or row["locate_state"] in {"review", "failed"} or
                    (not row["located"] and (ean, cid) in pending)):
                action = "Revisión / recuperación"
            elif pilot and not point_check and not supervised_trial:
                action = "Pendiente en piloto"
            elif row["located"]:
                action = "Monitorizar" if due or point_check else "Reutilizar precio vigente"
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
            queue = job if row["located"] else locate_job
            # Normal monitor.enqueue reopens completed samples whose frequency is due.
            eligibility_job = {} if row["located"] and due and job.get("state") == "done" else queue
            deferred = job_deferral(eligibility_job, now, point_check=point_check,
                                    monitoring=bool(row["located"]),
                                    pilot_authorized=pilot and (point_check or supervised_trial))
            if pilot_member and (point_check or supervised_trial) and (not scope["enabled"] or pilot_active):
                deferred = "Piloto desactivado o con ejecución pendiente: no se inicia otra"
            if action == "Revisión / recuperación":
                deferred = "Revisión pendiente: no se acepta ni se reintenta automáticamente"
            elif action == "Pendiente en piloto":
                deferred = "Requiere comprobación puntual explícita del piloto supervisado"
            if not allowed:
                deferred = reason
            from price_trial import pair_reason
            trial_reason = pair_reason(self.store, cid, ean, now)
            if trial_reason:
                deferred = trial_reason
            queue_not_before = queue.get("not_before")
            valid_until = row.get("valid_until")
            access_retry_at = gate.retry_at.isoformat() if gate.retry_at else None
            if action == "Reutilizar precio vigente":
                next_check_at = valid_until or queue_not_before
            else:
                next_check_at = access_retry_at or queue_not_before
            if pd.isna(next_check_at):
                next_check_at = None
            elif hasattr(next_check_at, "isoformat"):
                next_check_at = next_check_at.isoformat()
            elif next_check_at is not None:
                next_check_at = str(next_check_at)
            rows.append({**row, "action": action, "ready": bool(allowed and needs_http and not deferred),
                         "access": reason or ("Autorizado; pausas aplicables" if allowed else "Revisión necesaria"),
                         "defer_reason": deferred, "queue_state": queue.get("state", "sin preparar"),
                         "not_before": queue_not_before, "next_check_at": next_check_at,
                         "access_retry_at": access_retry_at, "attempts": queue.get("attempts", 0),
                         "pilot_route": bool(pilot_member and (point_check or supervised_trial)),
                         "candidate_count": len(plan.runnable), "candidate_reason": plan.reason,
                         "candidate_reason_code": plan.reason_code,
                         "local_candidates": [candidate.provenance() for candidate in plan.candidates],
                         "remaining_http": remaining, "minimum_http": int(needs_http),
                         "estimate": ("1 ficha + robots si no está en caché" if row["located"] else
                                      "Variable: listados, paginación y ficha; limitado por presupuesto")})
        return pd.DataFrame(rows)

    def prepare(self, eans: list[str], competitors: list[str], *, point_check: bool = False) -> pd.DataFrame:
        preview = self.preview(eans, competitors, point_check=point_check)
        self._prepare_snapshot(preview)
        return self.preview(eans, competitors, point_check=point_check)

    def execute(self, eans: list[str], competitors: list[str], *, approved: bool,
                sync_vytria: bool = True, max_jobs: int = 300, point_check: bool = False) -> dict:
        if not approved:
            raise ValueError("El lote requiere confirmación explícita")
        if not 1 <= max_jobs <= 5000:
            raise ValueError("El lote debe contener de 1 a 5000 trabajos")
        preview = self.preview(eans, competitors, point_check=point_check)
        result = {"vytria": None, "runs": [], "deferred": int(preview["ready"].sum()),
                  "processed_pairs": 0, "pairs": [], "point_check": point_check,
                  "skipped": preview.loc[~preview["ready"],
                      ["ean", "competitor", "action", "access", "defer_reason"]].to_dict("records")}
        if sync_vytria:
            result["vytria"] = asdict(self.monitor.vytria.run(trigger="watch:batch", watched_only=True))
        self._prepare_snapshot(preview)
        remaining = max_jobs
        for cid in competitors:
            selected = preview[(preview["competitor_id"] == cid) & preview["ready"]]["ean"].tolist()[:remaining]
            if not selected or remaining <= 0:
                continue
            legacy = [(cid, r["ean"]) for r in preview.to_dict("records")
                      if r["competitor_id"] == cid and r["ean"] in selected and r["pilot_route"]]
            context = PricePilot(self.store).watch_check(legacy, approved=approved) if legacy else nullcontext(None)
            started_at = stamp()
            with context as pilot_run_id:
                located = self.locator.run([cid], eans=selected, max_jobs=len(selected), deadline_seconds=300,
                                           **({"point_check": point_check, "pilot_run_id": pilot_run_id}
                                              if point_check or pilot_run_id is not None else {}))
                # A store-level block must stop monitoring as well as discovery.
                from price_monitor import MonitorReport
                monitored = (MonitorReport() if located.stopped else self.monitor.run(
                    [cid], eans=selected, max_jobs=len(selected), deadline_seconds=300,
                    **({"point_check": point_check, "pilot_run_id": pilot_run_id}
                       if point_check or pilot_run_id is not None else {})))
                processed = {r["ean"] for r in located.outcomes + monitored.outcomes}
                remaining -= len(processed)
                result["deferred"] -= len(processed)
                result["processed_pairs"] += len(processed)
                run = {"competitor": cid, "location": located.summary, "monitor": monitored.summary,
                       "location_processed": located.processed, "monitor_processed": monitored.processed,
                       "requests": located.requests + monitored.requests,
                       "processed_pairs": len(processed), "pilot_run_id": pilot_run_id,
                       "location_deferred": located.deferred_reason,
                       "monitor_deferred": monitored.deferred_reason,
                       "started_at": started_at, "finished_at": stamp(),
                       "errors": {**located.stopped, **monitored.stopped}}
                result["runs"].append(run)
                if pilot_run_id is not None:
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE pilot_budgets SET requests=?,blocked=?
                            WHERE run_id=? AND competitor_id=?""",
                            (run["requests"], int(bool(run["errors"])), pilot_run_id, cid))
                for ean in selected:
                    outcomes = [r for r in located.outcomes + monitored.outcomes if r["ean"] == ean]
                    requests = sum(r["requests"] for r in outcomes)
                    checked = any(r.get("commercial_requests") or r.get("current_source") for r in outcomes)
                    reused = bool(outcomes and outcomes[-1]["state"] in {"done", "not_found"})
                    preview_row = preview.loc[(preview["competitor_id"] == cid) &
                                              (preview["ean"] == ean)].iloc[0]
                    pair = {"ean": ean, "competitor_id": cid, "processed": ean in processed,
                            "category": "checked_now" if checked else "reused" if reused else "not_executed",
                            "requests": requests, "outcomes": outcomes,
                            "reason": (outcomes[-1]["detail"] if outcomes else
                                       run["errors"].get(cid) or located.deferred_reason or monitored.deferred_reason
                                       or self._unclaimed_reason(
                                           cid, ean, monitoring=bool(preview_row["located"]),
                                           point_check=point_check,
                                           pilot_authorized=bool(preview_row["pilot_route"])))}
                    result["pairs"].append(pair)
                    if pilot_run_id is not None:
                        with self.store._connect() as con, con:
                            con.execute("""UPDATE pilot_jobs SET status='done',outcome=?,detail=?,observation=?,
                                checked_at=? WHERE run_id=? AND competitor_id=? AND ean=?""",
                                (pair["category"], pair["reason"], json.dumps(pair, ensure_ascii=False), stamp(),
                                 pilot_run_id, cid, ean))
                with self.store._connect() as con, con:
                    con.execute("""INSERT INTO locate_runs(competitor_id,trigger,started_at,finished_at,status,
                        processed,requests,summary) VALUES(?,?,?,?,?,?,?,?)""",
                        (cid, "watch:point_check" if point_check else "watch:batch", started_at, run["finished_at"],
                         "stopped" if run["errors"] else "ok", len(processed), run["requests"],
                         json.dumps({"run": run, "pairs": [p for p in result["pairs"] if p["competitor_id"] == cid]},
                                    ensure_ascii=False)))
        for row in preview.loc[~preview["ready"]].to_dict("records"):
            result["pairs"].append({"ean": row["ean"], "competitor_id": row["competitor_id"],
                                    "processed": False,
                                    "category": "reused" if row["action"] == "Reutilizar precio vigente"
                                    else "not_executed", "requests": 0, "outcomes": [],
                                    "reason": row["defer_reason"] or row["action"]})
        included = {(p["competitor_id"], p["ean"]) for p in result["pairs"]}
        for row in preview.to_dict("records"):
            if (row["competitor_id"], row["ean"]) not in included:
                result["pairs"].append({"ean": row["ean"], "competitor_id": row["competitor_id"],
                                        "processed": False, "category": "not_executed",
                                        "requests": 0, "outcomes": [], "reason": "Límite del lote"})
        return result


def batch_pairs_export(pairs: list[dict]) -> pd.DataFrame:
    """Keep nested execution outcomes readable and machine-parseable in CSV exports."""
    rows = []
    for pair in pairs:
        row = dict(pair)
        if isinstance(row.get("outcomes"), (list, dict)):
            row["outcomes"] = json.dumps(row["outcomes"], ensure_ascii=False, sort_keys=True)
        rows.append(row)
    return pd.DataFrame(rows)


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
