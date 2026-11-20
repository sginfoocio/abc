"""Audited, explicit recovery of interrupted continuity pairs; never performs HTTP."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
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
                sample_matches = _review_sample_matches(task, con=con, now=now)
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
                if policy_reason == "La muestra posterior a la solicitud ha caducado":
                    sample_matches = False
                    if task["status"] == "done":
                        category, action, reason = (
                            "recoverable", "already_queued",
                            "Lectura caducada; la vigilancia renovará el precio con la aprobación vigente")
                elif not policy_reason:
                    if task["status"] == "done":
                        category, reason = "already_resolved", "Lectura vigente y compatible de la misma ficha"
                    else:
                        category, action, reason = (
                            "recoverable", "local_reevaluation",
                            "Lectura guardada vigente; reevaluar localmente sin HTTP")
                elif sample_matches:
                    reason = policy_reason
            elif not reason and task["status"] == "done":
                reason = "Solicitud terminada sin muestra posterior verificable de la misma ficha"
            elif not reason and task["status"] == "running":
                if task["lease_until"] and _time(task["lease_until"]) > now:
                    category, action = "recoverable", "already_queued"
                    reason = f"Lectura en curso hasta {task['lease_until']}"
                else:
                    since = (_time(task["started_at"]) - timedelta(seconds=1)
                             if task["started_at"] else now - timedelta(minutes=5))
                    uncertain = con.execute("""SELECT 1 FROM access_requests
                        WHERE competitor_id=? AND (ean=? OR ean='' OR ean IS NULL)
                          AND reserved_at>=? LIMIT 1""",
                                             (cid, ean, since.isoformat())).fetchone()
                    if uncertain:
                        reason = "Interrupción con petición o reserva sin muestra recuperable"
                    else:
                        category, action = "recoverable", "already_queued"
                        reason = "La misma solicitud en cola se reanudará al vencer la interrupción"
            elif not reason and task["status"] not in {"pending", "deferred"}:
                reason = f"Estado de solicitud no recuperable: {task['status']}"
            elif (not reason and task["status"] == "deferred"
                  and "bloqueo de continuidad ocupado" in str(task["detail"] or "").casefold()):
                category, action = "recoverable", "already_queued"
                reason = f"Aplazado por bloqueo; próximo intento {task['retry_at'] or 'pendiente'}"
            elif (not reason and task["status"] == "deferred"
                  and task["detail"] != "Par reservado al piloto de 72 h o ventana finalizada"
                  and "bloqueo de continuidad ocupado" not in str(task["detail"] or "").casefold()):
                reason = f"Aplazamiento ajeno al defecto: {task['detail']}"
            elif not reason and task["continuity_pair_state"] == "running":
                category, action, reason = (
                    "recoverable", "already_queued", "Lectura de continuidad en curso; se reutilizará")
            elif not reason and task["continuity_pair_state"] not in {"pending", "review"}:
                reason = f"Restricción del par: {task['continuity_pair_state']}"
            elif not reason and task["monitor_state"] == "running":
                category, action, reason = (
                    "recoverable", "already_queued", "Lectura del monitor en curso; no se duplica")
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
                       "sampled_at": task["monitor_sampled_at"], "detail": task["detail"],
                       "classification": _classify_manual_row({
                           "category": category, "action": action, "reason": reason})})
    return continuity_id, result


def _manual_summary(rows):
    return {name: sum(row["category"] == name for row in rows)
            for name in ("recoverable", "already_resolved", "excluded")}


def _classify_manual_row(row):
    reason = (row.get("reason") or "").casefold()
    category = row.get("category")
    action = row.get("action")
    if category == "already_resolved":
        return "1_ya_resuelta"
    if action in {"reconcile", "local_reevaluation"}:
        return "2_reevaluable_local"
    if category == "recoverable":
        return "3_recuperable_lectura_gobernada"
    if any(word in reason for word in ("contrad", "identidad", "cambio")):
        return "5_revision_contradiccion_real"
    if any(word in reason for word in ("muestra", "captura", "evidencia", "históric", "anterior")):
        return "4_necesita_captura_evidencia"
    return "6_excluida_control_vigencia"


def _classification_summary(rows):
    names = (
        "1_ya_resuelta",
        "2_reevaluable_local",
        "3_recuperable_lectura_gobernada",
        "4_necesita_captura_evidencia",
        "5_revision_contradiccion_real",
        "6_excluida_control_vigencia",
    )
    result = {name: 0 for name in names}
    for row in rows:
        result[_classify_manual_row(row)] += 1
    return result


def _approval_inventory(con, continuity_id, now):
    from price_comparator import normalize_gtin
    from price_locator import MAX_ATTEMPTS
    from price_monitor import _inventory_datetime, _review_sample_policy_reason, _review_saved_sample_reusable
    from price_references import key, model_key
    from price_web import WebSearchError, validate_competitor_url

    continuity = con.execute("SELECT status,started_at FROM price_continuity WHERE id=?",
                             (continuity_id,)).fetchone()
    rows = con.execute("""SELECT u.*,p.state AS pair_state,w.active AS watch_active,c.active AS competitor_active,
            c.domain,prod.brand,prod.model,prod.color,prod.updated_at AS product_updated_at,
            l.id AS locate_job_id,l.state AS locate_state,l.url AS locate_url,
            j.id AS monitor_job_id,j.state AS monitor_state,j.url AS monitor_url,
            j.sampled_at AS monitor_sampled_at,j.valid_until AS monitor_valid_until,
            j.last_ok_at AS monitor_last_ok_at,j.sample AS monitor_sample,
            j.detail AS monitor_detail,j.not_before AS monitor_not_before,
            j.lease_until AS monitor_lease_until,j.updated_at AS monitor_updated_at,
            j.attempts AS monitor_attempts,j.eligible AS monitor_eligible,
            COALESCE(g.interval_hours,s.interval_hours,24) AS interval_hours
        FROM competitor_product_urls u
        JOIN price_continuity_pairs p ON p.continuity_id=? AND p.competitor_id=u.competitor_id
            AND p.ean=u.ean
        JOIN competitors c ON c.id=u.competitor_id
        JOIN products prod ON prod.ean=u.ean
        LEFT JOIN watch_products w ON w.ean=u.ean
        LEFT JOIN watch_groups g ON g.name=w.group_name
        LEFT JOIN competitor_schedule s ON s.competitor_id=u.competitor_id
        LEFT JOIN locate_jobs l ON l.competitor_id=u.competitor_id AND l.ean=u.ean
        LEFT JOIN monitor_jobs j ON j.competitor_id=u.competitor_id AND j.ean=u.ean
        WHERE u.status='verified' AND u.source='user'
        ORDER BY u.competitor_id,u.ean,u.url""", (continuity_id,)).fetchall()
    result = []
    for row in rows:
        task = dict(row)
        cid, ean, url = task["competitor_id"], task["ean"], task["url"]
        audits = con.execute("""SELECT * FROM access_audit WHERE scope='product_review'
            AND action='candidate_confirmed' AND at=? AND scope_id=? ORDER BY id""",
                             (task["verified_at"], f"{cid}:{ean}:{url}")).fetchall()
        existing = con.execute("""SELECT id,status FROM manual_review_refreshes
            WHERE competitor_id=? AND ean=? AND url=? AND requested_at=? ORDER BY id""",
                               (cid, ean, url, task["verified_at"])).fetchall()
        active = con.execute("""SELECT id FROM manual_review_refreshes WHERE competitor_id=? AND ean=?
            AND status IN ('pending','deferred','running') ORDER BY id""", (cid, ean)).fetchall()
        approval = dict(audits[0]) if len(audits) == 1 else None
        reason = ""
        if not approval:
            reason = "La auditoría de aprobación no es única o no está disponible"
        elif not approval["actor"].strip() or not approval["note"].strip() or approval["current"] != "verified":
            reason = "La auditoría de aprobación está incompleta"
        elif continuity["status"] != "active" or not continuity["started_at"] or _time(continuity["started_at"]) > now:
            reason = "Continuidad no activa o aún no iniciada"
        elif not task["watch_active"] or not task["competitor_active"]:
            reason = "Producto o competidor inactivo"
        elif task["pair_state"] not in {"pending", "review", "running"}:
            reason = f"Restricción del par: {task['pair_state']}"
        elif (task["locate_state"] != "done" or task["locate_url"] != url
              or task["note"] != f"Revisión de {approval['actor']}: {approval['note']}"):
            reason = "La aprobación o URL vigente fue sustituida"
        elif not all(str(task[field] or "").strip() for field in ("brand", "model", "color")):
            reason = "Falta marca, modelo o color para acreditar la identidad"
        elif task["match_status"] != "exact_match":
            reason = "La coincidencia de la ficha aprobada cambió"
        elif len(existing) > 1:
            reason = "Hay varias solicitudes para la misma aprobación"
        elif active and not existing:
            reason = "Existe otra solicitud activa para el par; reconciliar antes de crear otra"
        elif task["monitor_url"] is not None and task["monitor_url"] != url:
            reason = "La URL del monitor no coincide con la ficha aprobada"
        elif task["monitor_state"] in {"review", "failed", "gone", "inactive"}:
            reason = f"El monitor requiere resolución: {task['monitor_state']} · {task['monitor_detail']}"
        elif task["monitor_state"] == "blocked" and not task["monitor_not_before"]:
            reason = task["monitor_detail"] or "El monitor tiene un bloqueo vigente"
        else:
            try:
                events = con.execute("""SELECT id,at,url,detail,method FROM locate_events
                    WHERE job_id=? AND method IN ('review','review+price') ORDER BY id DESC""",
                                     (task["locate_job_id"],)).fetchall()
                event = events[0] if events else None
                if _inventory_datetime(task["verified_at"]) > now:
                    reason = "La aprobación tiene fecha futura"
                elif task["monitor_sampled_at"] and _inventory_datetime(task["monitor_sampled_at"]) > now:
                    reason = "La fecha de la muestra es futura"
                elif ((not existing and
                     _inventory_datetime(task["product_updated_at"]) >= _inventory_datetime(task["verified_at"]))
                        or not event or _inventory_datetime(event["at"]) !=
                        _inventory_datetime(task["verified_at"]).replace(microsecond=0) or event["url"] != url
                        or event["detail"] != f"{approval['actor']}: {approval['note']}"[:1000]
                        or sum(_inventory_datetime(e["at"]) ==
                               _inventory_datetime(task["verified_at"]).replace(microsecond=0)
                               and e["url"] == url for e in events) != 1):
                    reason = "La identidad histórica o el evento de aprobación no se pueden acreditar"
                elif ((task["model_code"] and model_key(task["model_code"]) != model_key(task["model"]))
                      or (task["color_code"] and key(task["color_code"]) != key(task["color"]))
                      or (task["ean_published"] and normalize_gtin(task["ean_published"]) != ean)):
                    reason = "Contradicción entre la ficha aprobada y la identidad del producto"
                elif validate_competitor_url(url, task["domain"]) != url:
                    reason = "La URL aprobada no es canónica para el competidor"
                elif task["monitor_state"] == "running" and (
                        not task["monitor_lease_until"]
                        or _time(task["monitor_lease_until"]) <= now):
                    since = (_inventory_datetime(task["monitor_updated_at"]) - timedelta(seconds=1)).isoformat()
                    if con.execute("""SELECT 1 FROM access_requests
                        WHERE competitor_id=? AND (ean=? OR ean='' OR ean IS NULL)
                          AND reserved_at>=? LIMIT 1""", (cid, ean, since)).fetchone():
                        reason = "Lectura del monitor interrumpida: reserva o resultado requiere reconciliación"
            except (TypeError, ValueError, WebSearchError) as exc:
                reason = f"Identidad o URL ilegible: {exc}"
        action = "excluded" if reason else "already_queued" if existing else "create"
        if not reason and not existing and _review_saved_sample_reusable(con, task, now):
            try:
                policy_reason = _review_sample_policy_reason(con, task, now, task["interval_hours"])
            except (KeyError, TypeError, ValueError) as exc:
                policy_reason = f"Muestra comercial ilegible: {type(exc).__name__}"
            if not policy_reason:
                action = "reuse_saved_sample"
            elif policy_reason != "La muestra posterior a la solicitud ha caducado":
                action, reason = "excluded", policy_reason
        if action == "create" and int(task["monitor_attempts"] or 0) >= MAX_ATTEMPTS:
            action, reason = "excluded", "Máximo de intentos del monitor alcanzado"
        if not reason:
            reason = ("Solicitud existente; se conserva" if existing else
                      "Lectura vigente compatible; se reevaluará sin HTTP" if action == "reuse_saved_sample" else
                      "Crear una solicitud prioritaria para la aprobación vigente")
        result.append({
            "ean": ean, "competitor_id": cid, "url": url, "verified_at": task["verified_at"],
            "approval_note": task["note"], "approval_id": approval["id"] if approval else None,
            "approval_audits": len(audits), "active_requests": len(active),
            "request_id": existing[0]["id"] if len(existing) == 1 else None,
            "monitor_url": task["monitor_url"], "monitor_state": task["monitor_state"],
            "sampled_at": task["monitor_sampled_at"], "valid_until": task["monitor_valid_until"],
            "monitor_detail": task["monitor_detail"], "monitor_not_before": task["monitor_not_before"],
            "monitor_lease_until": task["monitor_lease_until"], "action": action, "reason": reason,
        })
    return result


def preview_manual_reviews(database, *, now=None):
    path = Path(database).resolve()
    now = _time(now or datetime.now(timezone.utc))
    with closing(_connect(path, readonly=True)) as con:
        con.execute("BEGIN")
        continuity_id, rows = _manual_review_rows(con, path, now)
        approvals = [item for item in _approval_inventory(con, continuity_id, now) if item["request_id"] is None]
        con.rollback()
    return {"counts": _manual_summary(rows), "classifications": _classification_summary(rows), "requests": rows,
            "approvals_without_request": approvals}


def apply_manual_reviews(database, *, actor, note, build_commit, approved=False, request_ids=None, approval_ids=None,
                         now=None):
    if not approved or not actor.strip() or not note.strip():
        raise ValueError("Confirmación explícita, responsable y motivo obligatorios")
    if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
        raise ValueError("Commit válido de 40 caracteres requerido")
    if request_ids is not None and (not request_ids and not approval_ids or
                                    any(not isinstance(i, int) or i < 1 for i in request_ids)):
        raise ValueError("Los IDs seleccionados deben ser enteros positivos")
    if approval_ids is not None and (not approval_ids or any(not isinstance(i, int) or i < 1 for i in approval_ids)):
        raise ValueError("Los IDs de aprobación deben ser enteros positivos")
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
            inventory = _approval_inventory(con, continuity_id, now) if approval_ids else []
            by_approval = {item["approval_id"]: item for item in inventory if item["approval_id"] is not None}
            by_request = {item["id"]: item for item in rows}
            approval_results = []
            for approval_id in sorted(set(approval_ids or [])):
                item = by_approval.get(approval_id)
                if not item:
                    approval_results.append({"approval_id": approval_id, "action": "excluded",
                                             "reason": "La aprobación fue sustituida o no está vigente",
                                             "request_id": None})
                    continue
                result = {"approval_id": approval_id, "request_id": item["request_id"],
                          "action": item["action"], "reason": item["reason"]}
                if item["action"] == "already_queued":
                    request = by_request[item["request_id"]]
                    result["action"] = request["action"] or request["category"]
                    result["reason"] = request["reason"]
                if item["action"] in {"create", "reuse_saved_sample"}:
                    from price_monitor import _review_identity, _review_identity_valid

                    approval = con.execute("SELECT * FROM access_audit WHERE id=?", (approval_id,)).fetchone()
                    monitor = con.execute("SELECT id FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                                          (item["competitor_id"], item["ean"])).fetchone()
                    if not monitor:
                        monitor_id = con.execute("""INSERT INTO monitor_jobs(ean,competitor_id,url,state,updated_at)
                            VALUES(?,?,?,'pending',?)""",
                                                 (item["ean"], item["competitor_id"], item["url"],
                                                  now.isoformat())).lastrowid
                    else:
                        monitor_id = monitor["id"]
                    locate = con.execute("SELECT id FROM locate_jobs WHERE competitor_id=? AND ean=?",
                                         (item["competitor_id"], item["ean"])).fetchone()
                    refresh_id = con.execute("""INSERT INTO manual_review_refreshes
                        (locate_job_id,monitor_job_id,ean,competitor_id,url,actor,reason,status,detail,
                         requested_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)""",
                                             (locate["id"], monitor_id, item["ean"], item["competitor_id"],
                                              item["url"], approval["actor"], approval["note"],
                                              "Recuperación auditada de aprobación existente; pendiente de lectura",
                                              item["verified_at"], now.isoformat())).lastrowid
                    identity = _review_identity(con, {"id": refresh_id})
                    con.execute("INSERT INTO manual_review_identity(refresh_id,snapshot) VALUES(?,?)",
                                (refresh_id, json.dumps(identity, ensure_ascii=False)))
                    task = dict(con.execute("SELECT * FROM manual_review_refreshes WHERE id=?",
                                            (refresh_id,)).fetchone())
                    if not _review_identity_valid(con, task):
                        raise ValueError("La identidad de la aprobación cambió durante la recuperación")
                    con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                        VALUES('manual_review',?,'recover_approved_identity',?,?,?, ?,?)""",
                                (str(refresh_id), json.dumps(item, ensure_ascii=False),
                                 json.dumps({"approval_id": approval_id, "request_id": refresh_id},
                                            ensure_ascii=False), actor.strip(),
                                 json.dumps({"reason": note.strip(), "build_commit": build_commit,
                                             "continuity_id": continuity_id}, ensure_ascii=False), now.isoformat()))
                    result.update(action="created", request_id=refresh_id)
                approval_results.append(result)
            selected = (set(request_ids) if request_ids is not None else
                        set() if approval_ids else {item["id"] for item in rows})
            selected.update(item["request_id"] for item in approval_results
                            if item["action"] in {"already_queued", "requeue", "reconcile",
                                                  "local_reevaluation", "authorize"}
                            and item["request_id"] is not None)
            if not selected <= {item["id"] for item in rows}:
                raise ValueError("Hay IDs de solicitud inexistentes")
            changed = []
            for item in rows:
                if item["id"] not in selected or item["action"] not in {
                        "requeue", "reconcile", "local_reevaluation", "authorize"}:
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
                                                        "selected": sorted(selected),
                                                        "approval_results": approval_results}, ensure_ascii=False),
                         actor.strip(), json.dumps({"reason": note.strip(), "build_commit": build_commit},
                                                  ensure_ascii=False), now.isoformat()))
            con.commit()
    return {"counts": _manual_summary(rows), "classifications": _classification_summary(rows),
            "requests": rows, "changed": changed, "approval_results": approval_results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Base SQLite existente; no se inicializa")
    parser.add_argument("--allowlist", help="Lista auditada con IDs esperados")
    parser.add_argument("--report", help="Informe histórico cuya huella consta en la lista")
    parser.add_argument("--manual-reviews", action="store_true", help="Inventario completo de lecturas manuales")
    parser.add_argument("--request-id", type=int, action="append", help="Limitar aplicación a solicitudes concretas")
    parser.add_argument("--approval-id", type=int, action="append", help="Recuperar aprobación por ID de auditoría")
    parser.add_argument("--apply", action="store_true", help="Aplicar únicamente los pares elegibles revalidados")
    parser.add_argument("--approved", action="store_true", help="Confirmación expresa de recuperación")
    parser.add_argument("--actor", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    if args.manual_reviews:
        if args.apply:
            result = apply_manual_reviews(args.database, actor=args.actor, note=args.note, build_commit=args.commit,
                                          approved=args.approved, request_ids=args.request_id,
                                          approval_ids=args.approval_id)
        else:
            if args.request_id or args.approval_id:
                parser.error("La vista previa siempre examina todas las solicitudes")
            result = preview_manual_reviews(args.database)
        print(json.dumps({"mode": "apply" if args.apply else "preview", **result}, ensure_ascii=False))
        return
    if not args.allowlist or not args.report or args.request_id or args.approval_id:
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
