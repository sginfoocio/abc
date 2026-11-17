"""Audited, explicit recovery of interrupted continuity pairs; never performs HTTP."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from filelock import FileLock

from competitor_requirements import COMPETITORS
from price_trial import manifest

REASON = "Trabajador interrumpido; no se repite una reserva incierta"
EXPECTED_COUNTS = {
    "congafasdesol-com": 64, "optica-optima-com": 43, "amevista-com": 35,
    "domanci-com": 25, "visiofactory-es": 14, "edel-optics-es": 1,
}


def _time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    return parsed.astimezone(timezone.utc)


def load_allowlist(path, report_path):
    """Validate the supplied historical snapshot, not the current database."""
    list_content = path if isinstance(path, bytes) else Path(path).read_bytes()
    data = json.loads(list_content.decode("utf-8-sig"))
    content = report_path if isinstance(report_path, bytes) else Path(report_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != data.get("source_report_sha256"):
        raise ValueError("La huella del informe histórico no coincide")
    report = json.loads(content.decode("utf-8-sig"))
    entries = data.get("pairs")
    if data.get("kind") != "offline_recovery_review_allowlist_not_execution_authorization" or not isinstance(entries, list):
        raise ValueError("Lista de recuperación no reconocida")
    if (len(entries) != 182 or len({e["ean"] for e in entries}) != 85
            or data.get("selected_pairs") != 1400 or data.get("technical_pairs") != 182
            or data.get("distinct_eans_in_technical_pairs") != 85):
        raise ValueError("La lista no contiene los 182 pares y 85 EAN esperados")
    counts = {cid: sum(e["competitor_id"] == cid for e in entries) for cid in EXPECTED_COUNTS}
    if (counts != EXPECTED_COUNTS or len({(e["continuity_id"], e["pair_id"]) for e in entries}) != 182
            or len({(e["competitor_id"], e["ean"]) for e in entries}) != 182
            or len({e["expected_latest_check_id"] for e in entries}) != 182):
        raise ValueError("Los pares de recuperación no coinciden con la distribución auditada")
    historic = {(p["continuity_id"], p["id"]): p for p in report["pairs"]}
    checks = {c["id"]: c for c in report["checks"]}
    for entry in entries:
        pair = historic.get((entry["continuity_id"], entry["pair_id"]))
        check = checks.get(entry["expected_latest_check_id"])
        if (not pair or not check or pair["ean"] != entry["ean"] or pair["competitor_id"] != entry["competitor_id"]
                or (pair.get("latest_check") or {}).get("id") != entry["expected_latest_check_id"]
                or check["ean"] != entry["ean"] or check["competitor_id"] != entry["competitor_id"]
                or check["continuity_id"] != entry["continuity_id"]
                or pair["state"] != "review" or pair["defer_reason"] != REASON
                or check["state"] != "review" or check["category"] != "interrupted" or check["defer_reason"] != REASON
                or any(check[key] != 0 for key in ("requests", "http_commercial", "http_robots", "unconfirmed_http"))
                or check["started_at"] != entry["attempt_started_at"] or check["finished_at"] != entry["closed_at"]
                or entry["expected_state"] != "review" or entry["expected_category"] != "interrupted"
                or entry["expected_reason"] != REASON):
            raise ValueError("La lista no coincide con las comprobaciones del informe histórico")
        receipt = json.loads(check["receipt"])
        if receipt != {"http_receipts": [], "closure_reason": REASON}:
            raise ValueError("El informe histórico no acredita un cierre sin recibos")
        _time(entry["attempt_started_at"])
        _time(entry["closed_at"])
    return data


def _scope(con, entries, *, allow_stopped=False):
    ids = {e["continuity_id"] for e in entries}
    if len(ids) != 1:
        raise ValueError("La lista debe pertenecer a una única continuidad")
    continuity_id = next(iter(ids))
    row = con.execute("SELECT * FROM price_continuity WHERE id=?", (continuity_id,)).fetchone()
    allowed = {"active", "paused", "stopped"} if allow_stopped else {"active", "paused"}
    if not row or row["status"] not in allowed:
        raise ValueError("Continuidad inexistente o no recuperable")
    frozen = json.loads(row["manifest"])
    expected_names = {name: (urlsplit(url).hostname or "").removeprefix("www.") for name, url in COMPETITORS}
    shops = {r["id"]: r["name"] for r in con.execute("SELECT id,name,domain FROM competitors")
             if expected_names.get(r["name"]) == r["domain"]}
    if (frozen["eans"] != manifest()["eans"] or len(frozen["competitors"]) != 14
            or frozen["competitors"] != shops or len(expected_names) != 14):
        raise ValueError("El manifiesto vigente no coincide con los 100 EAN y 14 competidores")
    expected_pairs = {(cid, ean) for cid in shops for ean in frozen["eans"]}
    declared = [(p["competitor_id"], p["ean"]) for p in frozen["pairs"]]
    actual = {(r["competitor_id"], r["ean"]) for r in con.execute(
        "SELECT competitor_id,ean FROM price_continuity_pairs WHERE continuity_id=?", (continuity_id,))}
    if len(declared) != 1400 or set(declared) != expected_pairs or actual != expected_pairs:
        raise ValueError("Los pares vigentes no coinciden con el manifiesto de 1.400 pares")
    return continuity_id


def _evaluate(con, entries):
    continuity_id = _scope(con, entries)
    result = []
    for entry in entries:
        cid, ean = entry["competitor_id"], entry["ean"]
        row = con.execute("SELECT * FROM price_continuity_pairs WHERE id=? AND continuity_id=?",
                          (entry["pair_id"], continuity_id)).fetchone()
        check = con.execute("""SELECT * FROM price_continuity_checks WHERE continuity_id=? AND competitor_id=?
            AND ean=? ORDER BY id DESC LIMIT 1""", (continuity_id, cid, ean)).fetchone()
        reason = ""
        if (not row or row["competitor_id"] != cid or row["ean"] != ean or row["state"] != "review"
                or row["defer_reason"] != REASON or row["started_at"] != entry["attempt_started_at"]
                or row["checked_at"] != entry["closed_at"]):
            reason = "El estado del par ha cambiado"
        elif (not check or check["id"] != entry["expected_latest_check_id"] or check["state"] != "review"
              or check["category"] != "interrupted" or check["defer_reason"] != REASON
              or check["started_at"] != entry["attempt_started_at"] or check["finished_at"] != entry["closed_at"]
              or any(check[key] != 0 for key in ("requests", "http_commercial", "http_robots", "unconfirmed_http"))):
            reason = "La comprobación esperada ha cambiado o contiene peticiones"
        else:
            try:
                receipt = json.loads(check["receipt"])
            except (TypeError, json.JSONDecodeError):
                reason = "Recibo de interrupción ilegible"
            else:
                if receipt != {"http_receipts": [], "closure_reason": REASON}:
                    reason = "Recibo de interrupción incompleto o no vacío"
        if not reason:
            requests = con.execute("""SELECT reserved_at FROM access_requests
                WHERE (competitor_id=? OR (scope='competitor' AND scope_id=?)
                       OR ((competitor_id='' OR competitor_id IS NULL)
                           AND (scope!='competitor' OR scope_id='' OR scope_id IS NULL)))
                  AND (ean=? OR ean='' OR ean IS NULL)""", (cid, cid, ean)).fetchall()
            try:
                if any(_time(r["reserved_at"]) >= _time(entry["attempt_started_at"]) for r in requests):
                    reason = "Petición o reserva posterior al inicio, incluso sin EAN"
            except (TypeError, ValueError):
                reason = "Fecha de petición no verificable"
        if not reason:
            for table in ("locate_jobs", "monitor_jobs"):
                job = con.execute(f"SELECT state FROM {table} WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone()
                if job and job["state"] in {"review", "failed", "running"}:
                    reason = f"Cola {table} requiere revisión o está en ejecución"
                    break
        if not reason and con.execute("""SELECT 1 FROM pilot_url_candidates WHERE competitor_id=? AND ean=?
            AND state IN ('candidate_found','pending_validation') LIMIT 1""", (cid, ean)).fetchone():
            reason = "Hay una candidata pendiente de revisión"
        if not reason and con.execute("""SELECT 1 FROM competitor_product_urls WHERE competitor_id=? AND ean=?
            AND status IN ('probable','pending_verification') LIMIT 1""", (cid, ean)).fetchone():
            reason = "Hay una ficha con identidad pendiente de revisión"
        if not reason:
            events = con.execute("""SELECT e.at FROM locate_events e JOIN locate_jobs j ON j.id=e.job_id
                WHERE j.competitor_id=? AND j.ean=? AND e.requests>0""", (cid, ean)).fetchall()
            try:
                if any(_time(e["at"]) >= _time(entry["attempt_started_at"]) for e in events):
                    reason = "Evento de localización posterior con peticiones"
            except (TypeError, ValueError):
                reason = "Fecha de evento no verificable"
        if not reason:
            sources = (
                ("SELECT captured_at FROM offers WHERE competitor_id=? AND ean=?", "captured_at"),
                ("SELECT captured_at FROM search_results WHERE competitor_id=? AND ean=?", "captured_at"),
                ("SELECT last_ok_at FROM offer_checks WHERE competitor_id=? AND ean=?", "last_ok_at"),
                ("SELECT verified_at FROM competitor_product_urls WHERE competitor_id=? AND ean=?", "verified_at"),
                ("SELECT last_ok_at FROM competitor_product_urls WHERE competitor_id=? AND ean=?", "last_ok_at"),
                ("SELECT sampled_at FROM monitor_jobs WHERE competitor_id=? AND ean=?", "sampled_at"),
                ("SELECT last_ok_at FROM monitor_jobs WHERE competitor_id=? AND ean=?", "last_ok_at"),
            )
            try:
                if any(_time(item[key]) >= _time(entry["attempt_started_at"])
                       for query, key in sources for item in con.execute(query, (cid, ean)) if item[key]):
                    reason = "Evidencia de oferta, ficha o consulta posterior al inicio"
            except (TypeError, ValueError):
                reason = "Fecha de evidencia no verificable"
        result.append({"pair_id": entry["pair_id"], "competitor_id": cid, "ean": ean,
                       "expected_latest_check_id": entry["expected_latest_check_id"],
                       "eligible": not reason, "reason": reason or "Cierre técnico sin peticiones acreditadas",
                       "previous": dict(row) if row else None})
    return continuity_id, result


def _connect(path, *, readonly):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("La base existente no está disponible")
    connection = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON" if readonly else "PRAGMA foreign_keys=ON")
    return connection


def preview(database, allowlist_path, report_path):
    allowlist = load_allowlist(allowlist_path, report_path)
    with closing(_connect(database, readonly=True)) as con:
        con.execute("BEGIN")
        _, rows = _evaluate(con, allowlist["pairs"])
        con.rollback()
    return rows


def _selected_entry(con, continuity_id, pair_id, check_id):
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (continuity_id, pair_id, check_id)):
        raise ValueError("Indique IDs positivos de continuidad, par y comprobación")
    pair = con.execute("SELECT * FROM price_continuity_pairs WHERE id=? AND continuity_id=?",
                       (pair_id, continuity_id)).fetchone()
    check = con.execute("SELECT * FROM price_continuity_checks WHERE id=? AND continuity_id=?",
                        (check_id, continuity_id)).fetchone()
    if not pair or not check or (pair["competitor_id"], pair["ean"]) != (check["competitor_id"], check["ean"]):
        raise ValueError("El par y la comprobación no corresponden a la continuidad seleccionada")
    return {"continuity_id": continuity_id, "pair_id": pair_id, "competitor_id": pair["competitor_id"],
            "ean": pair["ean"], "expected_latest_check_id": check_id,
            "attempt_started_at": check["started_at"], "closed_at": check["finished_at"]}


def preview_selected(database, *, continuity_id, pair_id, check_id):
    with closing(_connect(database, readonly=True)) as con:
        con.execute("BEGIN")
        entry = _selected_entry(con, continuity_id, pair_id, check_id)
        _, rows = _evaluate(con, [entry])
        con.rollback()
    return rows[0]


def apply_selected(database, *, continuity_id, pair_id, check_id, actor, note, build_commit, approved=False):
    if not approved or not actor.strip() or not note.strip():
        raise ValueError("Confirmación explícita, responsable y motivo obligatorios")
    if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
        raise ValueError("Commit válido de 40 caracteres requerido")
    path = Path(database).resolve()
    with FileLock(f"{path}.continuity.lock", timeout=0):
        with closing(_connect(path, readonly=True)) as con:
            entry = _selected_entry(con, continuity_id, pair_id, check_id)
        locked_competitor = entry["competitor_id"]
        with FileLock(f"{path}.competitor-{locked_competitor}.lock", timeout=0):
            with closing(_connect(path, readonly=False)) as con:
                con.execute("BEGIN IMMEDIATE")
                entry = _selected_entry(con, continuity_id, pair_id, check_id)
                if entry["competitor_id"] != locked_competitor:
                    raise ValueError("El competidor cambió antes de adquirir los bloqueos")
                _, rows = _evaluate(con, [entry])
                row = rows[0]
                if row["eligible"]:
                    con.execute("""UPDATE price_continuity_pairs SET state='pending',defer_reason=?
                        WHERE id=? AND continuity_id=? AND state='review'""",
                                ("Recuperación técnica auditada; sujeta a controles vigentes", pair_id, continuity_id))
                metadata = {"reason": note.strip(), "build_commit": build_commit, "pair_id": pair_id,
                            "expected_latest_check_id": check_id, "competitor_id": entry["competitor_id"],
                            "ean": entry["ean"], "eligible": row["eligible"], "evaluation": row["reason"]}
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'recover_selected_interrupted_pair',?,?,?,?,?)""",
                            (str(continuity_id), json.dumps(row["previous"], ensure_ascii=False),
                             json.dumps({"state": "pending" if row["eligible"] else "unchanged"},
                                        ensure_ascii=False),
                             actor.strip(), json.dumps(metadata, ensure_ascii=False),
                             datetime.now(timezone.utc).isoformat()))
                con.commit()
    return row


def apply(database, allowlist_path, report_path, *, actor, note, build_commit, approved=False):
    if not approved or not actor.strip() or not note.strip():
        raise ValueError("Confirmación explícita, responsable y motivo obligatorios")
    if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
        raise ValueError("Commit válido de 40 caracteres requerido")
    allowlist = load_allowlist(allowlist_path, report_path)
    path = Path(database).resolve()
    with FileLock(f"{path}.continuity.lock", timeout=0), ExitStack() as stack:
        for cid in sorted({e["competitor_id"] for e in allowlist["pairs"]}):
            stack.enter_context(FileLock(f"{path}.competitor-{cid}.lock", timeout=0))
        with closing(_connect(path, readonly=False)) as con:
            con.execute("BEGIN IMMEDIATE")
            continuity_id, rows = _evaluate(con, allowlist["pairs"])
            changed = []
            now = datetime.now(timezone.utc).isoformat()
            for row in rows:
                if not row["eligible"]:
                    continue
                con.execute("""UPDATE price_continuity_pairs SET state='pending',defer_reason=?
                    WHERE id=? AND continuity_id=? AND state='review'""",
                            ("Recuperación técnica auditada; sujeta a controles vigentes", row["pair_id"], continuity_id))
                changed.append(row)
            metadata = {"reason": note.strip(), "build_commit": build_commit,
                        "source_report_sha256": allowlist["source_report_sha256"],
                        "excluded": [{k: r[k] for k in ("pair_id", "competitor_id", "ean", "reason")}
                                     for r in rows if not r["eligible"]]}
            if changed:
                previous = [r["previous"] for r in changed]
                current = [{"id": r["pair_id"], "state": "pending", "next_check_at": r["previous"]["next_check_at"]}
                           for r in changed]
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'recover_interrupted_pairs',?,?,?,?,?)""",
                            (str(continuity_id), json.dumps(previous, ensure_ascii=False),
                             json.dumps(current, ensure_ascii=False), actor.strip(),
                             json.dumps(metadata, ensure_ascii=False), now))
            else:
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'recover_interrupted_pairs_noop','[]','[]',?,?,?)""",
                            (str(continuity_id), actor.strip(), json.dumps(metadata, ensure_ascii=False), now))
            con.commit()
    return rows


class _ReadOnlyStore:
    def __init__(self, path):
        self.database_path = Path(path).resolve()

    def _connect(self):
        return _connect(self.database_path, readonly=True)


def _manual_review_rows(con, path, now):
    from price_access import AccessControl
    from price_locator import MAX_ATTEMPTS, job_deferral
    from price_monitor import (_done_job_restriction, _review_identity_valid,
                               _review_sample_ambiguous, _review_sample_matches, _review_sample_policy_reason)
    from price_trial import PriceTrial, continuity_config
    from price_watch import PriceWatch
    from price_web_fetch import RobotsRules, user_agent

    continuity_config()
    continuity = con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()
    if not continuity:
        raise ValueError("No hay continuidad para validar el inventario")
    continuity_id = _scope(con, [{"continuity_id": continuity["id"]}], allow_stopped=True)
    trial = con.execute("SELECT status FROM price_trial WHERE id=1").fetchone()
    if not trial or trial["status"] not in {"expired", "stopped"}:
        raise ValueError("El piloto original no está cerrado")
    frozen = json.loads(continuity["manifest"])
    store = _ReadOnlyStore(path)
    access = AccessControl(store, clock=lambda: now)
    service = PriceTrial(store, clock=lambda: now)
    watch = PriceWatch(store, clock=lambda: now)
    rows = con.execute("""SELECT r.*,j.url AS monitor_url,j.state AS monitor_state,
            j.not_before AS monitor_not_before,j.lease_until AS monitor_lease_until,
            j.attempts AS monitor_attempts,j.eligible AS monitor_eligible,j.detail AS monitor_detail,
            j.last_ok_at AS monitor_last_ok_at,j.sampled_at AS monitor_sampled_at,
            j.valid_until AS monitor_valid_until,j.sample AS monitor_sample,
            w.active AS watch_active,c.active AS competitor_active,
            p.state AS continuity_pair_state,
            EXISTS(SELECT 1 FROM manual_review_identity i WHERE i.refresh_id=r.id) AS identity_captured,
            EXISTS(SELECT 1 FROM manual_review_recovery_grants g
                   WHERE g.refresh_id=r.id AND g.continuity_id=?) AS recovery_granted
        FROM manual_review_refreshes r
        LEFT JOIN monitor_jobs j ON j.id=r.monitor_job_id
        LEFT JOIN watch_products w ON w.ean=r.ean
        LEFT JOIN competitors c ON c.id=r.competitor_id
        LEFT JOIN price_continuity_pairs p ON p.continuity_id=? AND p.competitor_id=r.competitor_id AND p.ean=r.ean
        ORDER BY r.requested_at,r.id""", (continuity_id, continuity_id)).fetchall()
    result = []
    for record in rows:
        task = dict(record)
        cid, ean = task["competitor_id"], task["ean"]
        category, action, reason = "excluded", "", ""
        if continuity["status"] != "active":
            reason = f"Continuidad no activa: {continuity['status']}"
        elif not continuity["started_at"] or _time(continuity["started_at"]) > now:
            reason = "Continuidad con fecha de inicio futura"
        elif cid not in frozen["competitors"] or ean not in frozen["eans"] or not task["continuity_pair_state"]:
            reason = "Par fuera del manifiesto"
        elif not task["watch_active"] or not task["competitor_active"]:
            reason = "Producto o competidor inactivo"
        elif task["monitor_url"] != task["url"]:
            reason = "La URL del monitor no coincide con la ficha aceptada"
        else:
            try:
                identity_ok = _review_identity_valid(con, task)
                sample_matches = _review_sample_matches(task)
                ambiguous_sample = _review_sample_ambiguous(task)
            except (KeyError, TypeError, ValueError) as exc:
                reason = f"Identidad o muestra ilegible: {type(exc).__name__}"
                identity_ok = sample_matches = ambiguous_sample = False
            if not reason and not identity_ok:
                reason = "La identidad aceptada cambió o no puede acreditarse"
            if not reason and ambiguous_sample:
                reason = "No se puede acreditar el orden entre la muestra y la solicitud"
            if not reason and sample_matches and task["monitor_state"] == "done":
                interval = watch.monitor_interval_hours(ean, cid)
                try:
                    policy_reason = _review_sample_policy_reason(con, task, now, interval)
                except (KeyError, TypeError, ValueError) as exc:
                    policy_reason = f"Muestra comercial ilegible: {type(exc).__name__}"
                if not policy_reason:
                    category, reason = "already_resolved", "Lectura posterior y vigente de la misma ficha"
                    if (task["status"] == "pending" or
                            (task["status"] == "deferred" and
                             task["detail"] == "Par reservado al piloto de 72 h o ventana finalizada")):
                        action = "reconcile"
                else:
                    reason = policy_reason
            elif not reason and task["status"] == "done":
                reason = "Solicitud terminada sin muestra posterior verificable de la misma ficha"
            elif not reason and task["status"] not in {"pending", "deferred"}:
                reason = f"Estado de solicitud no recuperable: {task['status']}"
            elif (not reason and task["status"] == "deferred"
                  and task["detail"] != "Par reservado al piloto de 72 h o ventana finalizada"):
                reason = f"Aplazamiento ajeno al defecto: {task['detail']}"
            elif not reason and task["continuity_pair_state"] == "running":
                reason = "Lectura de continuidad en curso"
            elif not reason and task["continuity_pair_state"] not in {"pending", "review"}:
                reason = f"Restricción del par: {task['continuity_pair_state']}"
            elif not reason and task["monitor_state"] == "running":
                reason = "Lectura concurrente o interrupción sin resolver"
            elif not reason and task["monitor_state"] in {"review", "failed", "gone", "inactive"}:
                reason = f"El monitor requiere revisión: {task['monitor_state']}"
            elif not reason and int(task["monitor_attempts"] or 0) >= MAX_ATTEMPTS:
                reason = "Máximo de intentos del monitor alcanzado"
            elif not reason and task["monitor_state"] == "blocked" and not task["monitor_not_before"]:
                reason = task["monitor_detail"] or "Bloqueo de acceso sin fecha"
            if not reason:
                job = {"state": task["monitor_state"], "not_before": task["monitor_not_before"],
                       "lease_until": task["monitor_lease_until"], "attempts": task["monitor_attempts"],
                       "sampled_at": task["monitor_sampled_at"], "last_ok_at": task["monitor_last_ok_at"],
                       "valid_until": task["monitor_valid_until"]}
                restriction = _done_job_restriction(job) if job["state"] == "done" else None
                if restriction and restriction > now:
                    reason = f"Fecha futura del monitor: {restriction.isoformat()}"
                elif task["status"] == "pending" and task["retry_at"] and _time(task["retry_at"]) > now:
                    reason = f"Fecha futura de la solicitud: {task['retry_at']}"
                else:
                    reason = job_deferral(job, now, point_check=True, monitoring=True)
            if not reason:
                with service.execution(0, cid, ean, program="continuity", program_id=continuity_id):
                    decision = access.check(cid, "ficha")
                if not decision.allowed:
                    reason = decision.message
                    if decision.retry_at:
                        reason += f" hasta {decision.retry_at.isoformat()}"
            if not reason:
                parsed = urlsplit(task["url"])
                origin = f"{parsed.scheme}://{parsed.netloc}"
                robots = con.execute("""SELECT status,rules_text,expires_at FROM price_robots_cache
                    WHERE origin=? AND agent=?""", (origin, user_agent())).fetchone()
                if robots and _time(robots["expires_at"]) > now and robots["status"] == 200:
                    if not RobotsRules.parse(robots["rules_text"]).allowed(task["url"]):
                        reason = "robots.txt vigente prohíbe esta ruta"
            if not reason:
                category = "recoverable"
                action = ("requeue" if task["status"] == "deferred" else
                          "authorize" if not task["identity_captured"] and not task["recovery_granted"]
                          else "already_queued")
                reason = ("Aplazamiento de reserva acreditado" if action == "requeue"
                          else "Solicitud pendiente; requiere autorización auditada" if action == "authorize"
                          else "Solicitud pendiente; ya está en la cola")
        result.append({"id": task["id"], "ean": ean, "competitor_id": cid, "url": task["url"],
                       "category": category, "action": action, "reason": reason,
                       "status": task["status"], "requested_at": task["requested_at"],
                       "retry_at": task["retry_at"], "attempts": task["attempts"],
                       "sampled_at": task["monitor_sampled_at"], "detail": task["detail"]})
    return continuity_id, result


def _manual_summary(rows):
    return {name: sum(row["category"] == name for row in rows)
            for name in ("recoverable", "already_resolved", "excluded")}


def preview_manual_reviews(database, *, now=None):
    path = Path(database).resolve()
    now = _time(now or datetime.now(timezone.utc))
    with closing(_connect(path, readonly=True)) as con:
        con.execute("BEGIN")
        _, rows = _manual_review_rows(con, path, now)
        con.rollback()
    return {"counts": _manual_summary(rows), "requests": rows}


def apply_manual_reviews(database, *, actor, note, build_commit, approved=False, request_ids=None, now=None):
    if not approved or not actor.strip() or not note.strip():
        raise ValueError("Confirmación explícita, responsable y motivo obligatorios")
    if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
        raise ValueError("Commit válido de 40 caracteres requerido")
    if request_ids is not None and (not request_ids or any(not isinstance(i, int) or i < 1 for i in request_ids)):
        raise ValueError("Los IDs seleccionados deben ser enteros positivos")
    path = Path(database).resolve()
    now = _time(now or datetime.now(timezone.utc))
    with FileLock(f"{path}.manual-review-refresh.lock", timeout=0), FileLock(
            f"{path}.continuity.lock", timeout=0), ExitStack() as stack:
        with closing(_connect(path, readonly=True)) as con:
            row = con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                raise ValueError("No hay continuidad")
            _scope(con, [{"continuity_id": row["id"]}], allow_stopped=True)
            competitors = sorted(json.loads(row["manifest"])["competitors"])
        for cid in competitors:
            stack.enter_context(FileLock(f"{path}.competitor-{cid}.lock", timeout=0))
        with closing(_connect(path, readonly=False)) as con:
            con.execute("BEGIN IMMEDIATE")
            continuity_id, rows = _manual_review_rows(con, path, now)
            selected = set(request_ids) if request_ids is not None else {item["id"] for item in rows}
            if not selected <= {item["id"] for item in rows}:
                raise ValueError("Hay IDs de solicitud inexistentes")
            changed = []
            for item in rows:
                if item["id"] not in selected or item["action"] not in {"requeue", "reconcile", "authorize"}:
                    continue
                before = dict(con.execute("SELECT * FROM manual_review_refreshes WHERE id=?",
                                          (item["id"],)).fetchone())
                if item["action"] in {"requeue", "authorize"}:
                    previous_grant = con.execute(
                        "SELECT * FROM manual_review_recovery_grants WHERE refresh_id=?", (item["id"],)).fetchone()
                    if previous_grant:
                        before["previous_grant"] = dict(previous_grant)
                    con.execute("""INSERT INTO manual_review_recovery_grants
                        (refresh_id,continuity_id,actor,note,build_commit,granted_at) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(refresh_id) DO UPDATE SET continuity_id=excluded.continuity_id,
                            actor=excluded.actor,note=excluded.note,build_commit=excluded.build_commit,
                            granted_at=excluded.granted_at""",
                                (item["id"], continuity_id, actor.strip(), note.strip(), build_commit,
                                 now.isoformat()))
                if item["action"] == "requeue":
                    status, detail = "pending", "Recuperación auditada de reserva; pendiente de lectura gobernada"
                    finished = before["finished_at"]
                elif item["action"] == "authorize":
                    status, detail, finished = before["status"], before["detail"], before["finished_at"]
                else:
                    status = "done"
                    detail = f"Lectura posterior reutilizada con fecha original {item['sampled_at']}"
                    finished = now.isoformat()
                if item["action"] != "authorize":
                    con.execute("""UPDATE manual_review_refreshes SET status=?,detail=?,retry_at=NULL,lease_until=NULL,
                        finished_at=?,updated_at=? WHERE id=?""",
                                (status, detail, finished, now.isoformat(), item["id"]))
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('manual_review',?,'recover_manual_review',?,?,?,?,?)""",
                            (str(item["id"]), json.dumps(before, ensure_ascii=False),
                             json.dumps({"status": status, "detail": detail}, ensure_ascii=False),
                             actor.strip(), json.dumps({"reason": note.strip(), "build_commit": build_commit,
                                                       "continuity_id": continuity_id}, ensure_ascii=False),
                             now.isoformat()))
                changed.append(item["id"])
            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                VALUES('price_continuity',?,'recover_manual_review_batch','',?,?,?,?)""",
                        (str(continuity_id), json.dumps({"counts": _manual_summary(rows), "changed": changed,
                                                        "selected": sorted(selected)}, ensure_ascii=False),
                         actor.strip(), json.dumps({"reason": note.strip(), "build_commit": build_commit},
                                                  ensure_ascii=False), now.isoformat()))
            con.commit()
    return {"counts": _manual_summary(rows), "requests": rows, "changed": changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Base SQLite existente; no se inicializa")
    parser.add_argument("--allowlist", help="Lista auditada con IDs esperados")
    parser.add_argument("--report", help="Informe histórico cuya huella consta en la lista")
    parser.add_argument("--manual-reviews", action="store_true", help="Inventario completo de lecturas manuales")
    parser.add_argument("--request-id", type=int, action="append", help="Limitar aplicación a solicitudes concretas")
    parser.add_argument("--apply", action="store_true", help="Aplicar únicamente los pares elegibles revalidados")
    parser.add_argument("--approved", action="store_true", help="Confirmación expresa de recuperación")
    parser.add_argument("--actor", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    if args.manual_reviews:
        if args.apply:
            result = apply_manual_reviews(args.database, actor=args.actor, note=args.note, build_commit=args.commit,
                                          approved=args.approved, request_ids=args.request_id)
        else:
            if args.request_id:
                parser.error("La vista previa siempre examina todas las solicitudes")
            result = preview_manual_reviews(args.database)
        print(json.dumps({"mode": "apply" if args.apply else "preview", **result}, ensure_ascii=False))
        return
    if not args.allowlist or not args.report or args.request_id:
        parser.error("--allowlist y --report son obligatorios fuera de --manual-reviews")
    if args.apply:
        rows = apply(args.database, args.allowlist, args.report, actor=args.actor, note=args.note,
                     build_commit=args.commit, approved=args.approved)
    else:
        rows = preview(args.database, args.allowlist, args.report)
    print(json.dumps({"mode": "apply" if args.apply else "preview", "eligible": sum(r["eligible"] for r in rows),
                      "excluded": sum(not r["eligible"] for r in rows), "pairs": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
