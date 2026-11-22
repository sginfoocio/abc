"""Audited, explicit recovery of interrupted continuity pairs; never performs HTTP."""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
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
MANUAL_ERROR_RECOVERY_RULES = "approved-read-errors-1"
MANUAL_CORRECTED_ERROR_RECOVERY_VERSION = "approved-read-errors-2"
_AMEVISTA_ERROR_RULES = (
    (re.compile(r"Distinto modelo: catálogo FT1044, ficha BRONSON, FT1044", re.IGNORECASE),
     "tomford-bronson-ft1044"),
    (re.compile(r"Distinto modelo: catálogo FT1301, ficha FT1301, JOEY02", re.IGNORECASE),
     "tomford-joey02-ft1301"),
    (re.compile(r"Distinto marca: catálogo TIFFANYCO, ficha TIFFANY", re.IGNORECASE),
     "tiffany-brand-alias"),
    (re.compile(r"Distinto modelo: catálogo (GG1660S|GG1680S), ficha \1, LOGOTIPOGG", re.IGNORECASE),
     "gucci-logotipo-gg"),
)


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
            j.previous_sample AS monitor_previous_sample,
            w.active AS watch_active,c.active AS competitor_active,
            c.domain AS competitor_domain,
            p.state AS continuity_pair_state,
            EXISTS(SELECT 1 FROM manual_review_identity i WHERE i.refresh_id=r.id) AS identity_captured,
            EXISTS(SELECT 1 FROM manual_review_recovery_grants g
                   WHERE g.refresh_id=r.id AND g.continuity_id=?)
            OR EXISTS(SELECT 1 FROM manual_review_recovery_events e
                   WHERE e.refresh_id=r.id AND e.continuity_id=?) AS recovery_granted
        FROM manual_review_refreshes r
        LEFT JOIN monitor_jobs j ON j.id=r.monitor_job_id
        LEFT JOIN watch_products w ON w.ean=r.ean
        LEFT JOIN competitors c ON c.id=r.competitor_id
        LEFT JOIN price_continuity_pairs p ON p.continuity_id=? AND p.competitor_id=r.competitor_id AND p.ean=r.ean
        ORDER BY r.requested_at,r.id""", (continuity_id, continuity_id, continuity_id)).fetchall()
    result = []
    for record in rows:
        task = dict(record)
        cid, ean = task["competitor_id"], task["ean"]
        category, action, reason = "excluded", "", ""
        related_request_id = None
        error_rule = ""
        identity = None
        identity_ok = False
        evidence = _manual_review_evidence(con, task)
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
                from price_monitor import _review_identity

                identity = _review_identity(con, task)
                identity_ok = _review_identity_valid(con, task)
                sample_matches = _review_sample_matches(task, con=con, now=now)
                ambiguous_sample = _review_sample_ambiguous(task)
            except (KeyError, TypeError, ValueError) as exc:
                reason = f"Identidad o muestra ilegible: {type(exc).__name__}"
                identity_ok = sample_matches = ambiguous_sample = False
            if not reason and not identity_ok:
                reason = "La identidad aceptada cambió o no puede acreditarse"
            if not reason and task["status"] == "error":
                identity_payload = json.dumps(
                    identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                identity_digest = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
                applied_recovery = None
                if evidence["approval_audits"] == 1 and evidence["approval_ref"]:
                    applied_recovery = con.execute("""SELECT outcome FROM manual_review_recovery_events
                        WHERE refresh_id=? AND rules_version=? AND approval_ref=? AND identity_digest=?
                        ORDER BY id DESC LIMIT 1""",
                                                   (task["id"], MANUAL_CORRECTED_ERROR_RECOVERY_VERSION,
                                                    evidence["approval_ref"], identity_digest)).fetchone()
                if applied_recovery:
                    category = "already_resolved"
                    reason = ("La recuperación versionada ya se aplicó "
                              f"(resultado: {applied_recovery['outcome']}); no se repite automáticamente")
            if not reason and task["status"] == "error":
                error_rule = _corrected_error_rule(task, identity)
                if error_rule:
                    artifact_evaluations = _reevaluate_saved_artifacts(con, task, now, error_rule)
                    evidence["artifact_evaluations"] = artifact_evaluations
                    contradiction = next((item for item in artifact_evaluations
                                          if item["result"] == "contradiction"), None)
                    if contradiction:
                        reason = ("La evidencia guardada contradice la identidad aprobada: "
                                  + contradiction["reason"])
                    duplicate = con.execute("""SELECT id FROM manual_review_refreshes
                        WHERE id<>? AND competitor_id=? AND ean=? AND url=?
                          AND status IN ('pending','deferred','running')
                        ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,id
                        LIMIT 1""", (task["id"], cid, ean, task["url"])).fetchone()
                    if reason:
                        pass
                    elif duplicate:
                        category, action = "recoverable", "already_queued"
                        related_request_id = duplicate["id"]
                        reason = f"Solicitud equivalente {related_request_id} ya está en cola"
                    elif evidence["approval_audits"] != 1:
                        reason = "La aprobación vigente no tiene una auditoría única y completa"
                    elif evidence["unresolved_reservation"]:
                        reason = "Existe una reserva HTTP sin recibo final; requiere reconciliación"
                    else:
                        recovery = _manual_recovery_identity(
                            task, identity, evidence, error_rule)
                        evidence.update(recovery["evidence"])
                        already_applied = con.execute("""SELECT id FROM manual_review_recovery_events
                            WHERE refresh_id=? AND recovery_key=?""",
                                                       (task["id"], recovery["recovery_key"])).fetchone()
                        if already_applied:
                            category = "already_resolved"
                            reason = "La misma recuperación versionada ya se aplicó; no se repite automáticamente"
                        elif task["monitor_state"] in {"pending", "waiting"}:
                            category, action = "recoverable", "already_queued"
                            reason = "El monitor ya tiene una lectura pendiente; se conserva sin duplicarla"
                        elif task["monitor_state"] == "running":
                            if task["monitor_lease_until"] and _time(task["monitor_lease_until"]) > now:
                                category, action = "recoverable", "already_queued"
                                reason = f"Lectura del monitor en curso hasta {task['monitor_lease_until']}"
                            else:
                                reason = "Lectura del monitor interrumpida; reconciliar antes de reprogramar"
                        elif int(task["monitor_attempts"] or 0) >= MAX_ATTEMPTS:
                            reason = "Máximo de intentos del monitor alcanzado"
                        elif task["monitor_not_before"] and _time(task["monitor_not_before"]) > now:
                            reason = f"Reintento del monitor aplazado hasta {task['monitor_not_before']}"
                        elif task["continuity_pair_state"] not in {"pending", "review"}:
                            reason = f"Restricción del par: {task['continuity_pair_state']}"
                        else:
                            category, action = "recoverable", "recover_corrected_error"
                            offline_exact = any(item["result"] == "exact_identity"
                                                for item in artifact_evaluations)
                            evidence_note = ("Artefacto guardado reevaluado como identidad exacta; "
                                             if offline_exact else
                                             "Sin artefacto reevaluable suficiente; ")
                            reason = (f"Rechazo histórico cubierto por {error_rule}; {evidence_note}"
                                      "requiere lectura gobernada actual")
                elif task["monitor_state"] in {"pending", "waiting", "running"}:
                    if (task["monitor_state"] != "running"
                            or (task["monitor_lease_until"] and _time(task["monitor_lease_until"]) > now)):
                        category, action = "recoverable", "already_queued"
                        reason = "El monitor tiene una lectura pendiente o en curso; no se duplica"
                    else:
                        reason = "Lectura del monitor interrumpida; reconciliar antes de reprogramar"
                else:
                    reason = "Error histórico no cubierto por una regla de recuperación verificable"
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
            elif not reason and task["status"] not in {"pending", "deferred", "error"}:
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
            elif (not reason and task["monitor_state"] in {"review", "failed", "gone", "inactive"}
                  and action != "recover_corrected_error"):
                reason = f"El monitor requiere revisión: {task['monitor_state']}"
            elif not reason and int(task["monitor_attempts"] or 0) >= MAX_ATTEMPTS:
                reason = "Máximo de intentos del monitor alcanzado"
            elif not reason and task["monitor_state"] == "blocked" and not task["monitor_not_before"]:
                reason = task["monitor_detail"] or "Bloqueo de acceso sin fecha"
            if not reason and action != "recover_corrected_error":
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
                if action != "recover_corrected_error":
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
                       "http_requests": task["http_requests"],
                       "sampled_at": task["monitor_sampled_at"], "detail": task["detail"],
                       "monitor_detail": task["monitor_detail"], "monitor_state": task["monitor_state"],
                       "approval_id": evidence["approval_id"], "approval_audits": evidence["approval_audits"],
                       "identity_valid": identity_ok,
                       "recovery_rule": error_rule,
                       "recovery_key": evidence.get("recovery_key"),
                       "evidence": evidence, "related_request_id": related_request_id,
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
    if "contrad" in reason:
        return "5_revision_contradiccion_real"
    if any(word in reason for word in ("muestra", "captura", "evidencia", "históric", "anterior",
                                       "identidad", "aprobación")):
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


def _corrected_error_rule(task, identity):
    if (task["status"] != "error" or task["monitor_state"] != "review" or not identity
            or not task["detail"] or
            (task["monitor_detail"] and task["monitor_detail"] != task["detail"])):
        return ""
    from price_references import _BRAND_ALIASES, _COMMERCIAL_NAMES, key, model_key

    detail = task["detail"].strip()
    domain = (identity.get("domain") or "").lower().removeprefix("www.")
    brand = _BRAND_ALIASES.get(key(identity.get("brand")), key(identity.get("brand")))
    model = model_key(identity.get("model"))
    if (domain == "opticalh.com" and detail ==
            "Distinto modelo: catálogo MUA06S, ficha GAFASDESOLMIUMIUA06S"
            and identity.get("ean") == "8056262407691" and brand == "MIUMIU"
            and model == "MUA06S" and key(identity.get("color")) == "14L09Z"):
        return "miu-a06s-merged-descriptor"
    if domain == "edel-optics.es" and detail == "La variante extraída no corresponde a la URL aceptada":
        return "edel-selected-variant-url"
    if domain != "amevista.com":
        return ""
    for pattern, rule in _AMEVISTA_ERROR_RULES:
        if not pattern.fullmatch(detail):
            continue
        if rule == "tomford-bronson-ft1044":
            valid = (brand == "TOMFORD" and model == "FT1044"
                     and "BRONSON" in _COMMERCIAL_NAMES.get((brand, model), set()))
        elif rule == "tomford-joey02-ft1301":
            valid = (brand == "TOMFORD" and model == "FT1301"
                     and "JOEY02" in _COMMERCIAL_NAMES.get((brand, model), set()))
        elif rule == "tiffany-brand-alias":
            valid = (brand == "TIFFANYCO" and model == "TF3104D"
                     and key(identity.get("color")) == "6216S4"
                     and identity.get("ean") == "8056262010730")
        else:
            gucci_evidence = {
                ("0889652494821", "GG1660S", "001"),
                ("0889652498768", "GG1680S", "002"),
            }
            valid = (brand == "GUCCI" and
                     (identity.get("ean"), model, key(identity.get("color"))) in gucci_evidence)
        if valid:
            return rule
    return ""


def _reevaluate_saved_artifacts(con, task, now, recovery_rule):
    """Re-run current identity rules on retained HTML without creating an observation."""
    from price_monitor import _accepted_edel_resolution_reason
    from price_references import EXACT, classify
    from price_web import comparability_gap
    from price_web_extract import extract_product, resolve_market, select_product_variant

    product = con.execute("SELECT * FROM products WHERE ean=?", (task["ean"],)).fetchone()
    config_row = con.execute("""SELECT w.*,c.domain,c.market,c.currency
        FROM competitor_web_config w JOIN competitors c ON c.id=w.competitor_id
        WHERE w.competitor_id=?""", (task["competitor_id"],)).fetchone()
    if not product or not config_row:
        return []
    product, config = dict(product), dict(config_row)
    if product["ean"] != task["ean"]:
        return []

    artifacts = []
    captures = con.execute("""SELECT id,current,at FROM access_audit
        WHERE scope='competitor' AND scope_id=? AND action='capture_continuity_html'
          AND json_extract(current,'$.ean')=?
          AND json_extract(current,'$.requested_url')=?
        ORDER BY id DESC LIMIT 10""", (task["competitor_id"], task["ean"], task["url"])).fetchall()
    for row in captures:
        try:
            data = json.loads(row["current"])
        except (TypeError, json.JSONDecodeError):
            continue
        if data.get("competitor_id") != task["competitor_id"]:
            continue
        artifacts.append(("continuity_html", row["id"], data, data.get("html_base64")))

    diagnostic_rows = con.execute("""SELECT id,result,artifact,expires_at,created_at
        FROM price_diagnostic_requests
        WHERE competitor_id=? AND ean=? AND url=? AND status='captured'
          AND artifact IS NOT NULL AND expires_at>?
        ORDER BY id DESC LIMIT 10""",
                                  (task["competitor_id"], task["ean"], task["url"], now.isoformat())).fetchall()
    for row in diagnostic_rows:
        try:
            metadata = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        artifacts.append(("sanitized_jsonld", row["id"], metadata, row["artifact"]))

    results = []
    for kind, artifact_id, metadata, encoded in artifacts:
        requested_url = metadata.get("requested_url")
        final_url = metadata.get("final_url")
        captured_at = metadata.get("captured_at") or metadata.get("at")
        item = {
            "source": kind,
            "artifact_id": artifact_id,
            "captured_at": captured_at,
            "result": "insufficient_evidence",
            "reason": "",
        }
        if requested_url != task["url"] or final_url != task["url"]:
            item["reason"] = "La URL solicitada o final no coincide exactamente con la aprobada"
            results.append(item)
            continue
        try:
            if not captured_at or _time(captured_at) > now:
                raise ValueError("fecha ausente o futura")
            if _time(captured_at) < _time(task["requested_at"]):
                item["reason"] = "El artefacto es anterior a la aprobación vigente"
                results.append(item)
                continue
            payload = (base64.b64decode(encoded, validate=True) if kind == "continuity_html"
                       else bytes(encoded))
            expected_digest = (metadata.get("sha256") if kind == "continuity_html"
                               else metadata.get("artifact_sha256"))
            if not expected_digest or hashlib.sha256(payload).hexdigest() != expected_digest:
                raise ValueError("la huella SHA-256 no coincide")
        except (TypeError, ValueError) as exc:
            item["reason"] = f"Artefacto no verificable: {exc}"
            results.append(item)
            continue

        page = extract_product(
            payload.decode("utf-8", errors="replace"), final_url,
            price_selector=config["price_selector"],
            availability_selector=config["availability_selector"],
            tax_policy=config["tax_policy"],
            url_trace={"requested_url": requested_url, "final_url": final_url,
                       "redirects": metadata.get("redirects", []), "candidate_url": requested_url},
        )
        if page is None:
            item["reason"] = "El extractor actual no encuentra datos de producto"
            results.append(item)
            continue
        page = select_product_variant(page, product)
        match = classify(product, page.candidate(), allow_reference_match=True)
        edel_reason = ""
        if ((config.get("domain") or "").lower().removeprefix("www.") == "edel-optics.es"
                and page.url != task["url"]):
            edel_reason = _accepted_edel_resolution_reason(page, task["url"], product)
        conflicts = [reason for reason in match.reasons
                     if re.search(r"(?:distinto|contradice|ean publicado distinto)", reason, re.IGNORECASE)
                     and not _known_recovery_alias_reason(reason, recovery_rule)]
        specific_conflict = bool(conflicts)
        exact = (match.status == EXACT and not page.ambiguous and not page.offer_binding_unresolved
                 and not edel_reason and page.url_trace.get("candidate_url") == task["url"]
                 and page.url_trace.get("final_url") == task["url"])
        gap = comparability_gap(page, config)
        market, _, _ = resolve_market(page, config)
        item.update({
            "result": ("contradiction" if specific_conflict else
                       "exact_identity" if exact else "ambiguous_or_incomplete"),
            "identity_status": match.status,
            "identity_reasons": list(match.reasons),
            "selected_url": page.url,
            "offer_binding_source": page.offer_binding_source,
            "offer_variant_url": page.url_trace.get("offer_variant_url", ""),
            "edel_resolution_reason": edel_reason,
            "commercial_gap": gap,
            "market": market,
            "currency": page.currency or config["currency"],
            "includes_tax": page.includes_tax,
            "offer_comparable": False,
            "validity": "No establecida: el artefacto no es una observación vigente del monitor",
            "has_price": page.has_price,
            "availability": page.availability,
            "stock_conflict": page.stock_conflict,
            "reason": (conflicts[0] if specific_conflict else edel_reason),
        })
        if not item["reason"] and not exact:
            item["reason"] = ("La relación Product/Offer o la selección de variante es ambigua"
                              if page.ambiguous or page.offer_binding_unresolved
                              else "; ".join(match.reasons) or "No se pudo confirmar la identidad exacta")
        results.append(item)
    return results


def _known_recovery_alias_reason(reason, recovery_rule):
    allowed = {
        "tomford-bronson-ft1044": r"Distinto modelo: catálogo FT1044, ficha BRONSON(?:\s+FT1044)?",
        "tomford-joey02-ft1301": r"Distinto modelo: catálogo FT1301, ficha JOEY02(?:\s+FT1301)?",
        "tiffany-brand-alias": r"Distinto marca: catálogo TIFFANYCO, ficha TIFFANY",
        "gucci-logotipo-gg": r"Distinto modelo: catálogo GG(?:1660S|1680S), ficha LOGOTIPOGG",
        "miu-a06s-merged-descriptor":
            r"Distinto modelo: catálogo MUA06S, ficha GAFASDESOLMIUMIUA06S",
    }
    pattern = allowed.get(recovery_rule)
    return bool(pattern and re.fullmatch(pattern, reason.strip(), re.IGNORECASE))


def _manual_review_evidence(con, task):
    purpose = f"manual_review:{task['id']}"
    receipts = con.execute("""SELECT id,reserved_at,result FROM access_requests
        WHERE purpose=? ORDER BY id""", (purpose,)).fetchall()
    sample_value = task.get("monitor_sample")
    sample_kind = "none"
    sample_digest = ""
    raw_html = False
    if sample_value:
        try:
            sample = json.loads(sample_value)
        except (TypeError, json.JSONDecodeError):
            sample = None
        if isinstance(sample, dict) and isinstance(sample.get("page"), dict):
            sample_kind = "structured_page"
            sample_digest = hashlib.sha256(json.dumps(
                sample, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            raw_html = any(isinstance(sample.get(key), str) and sample[key]
                           for key in ("html", "raw_html", "response_html"))
    recorded_samples = con.execute("""SELECT COUNT(*) FROM manual_review_recorded_sample_events
        WHERE refresh_id=?""", (task["id"],)).fetchone()[0]
    audit_scope = f"{task['competitor_id']}:{task['ean']}:{task['url']}"
    approvals = con.execute("""SELECT id FROM access_audit WHERE scope='product_review'
        AND action='candidate_confirmed' AND at=? AND scope_id=?
        AND actor<>'' AND note<>'' AND current='verified' ORDER BY id""",
                            (task.get("requested_at"), audit_scope)).fetchall()
    approval_source = "access_audit" if len(approvals) == 1 else ""
    approval_ref = f"access_audit:{approvals[0]['id']}" if len(approvals) == 1 else ""
    if not approvals:
        from price_monitor import _review_identity, _review_identity_valid

        identity = _review_identity(con, task)
        if identity and _review_identity_valid(con, task):
            event = con.execute("""SELECT id FROM locate_events
                WHERE id=? AND job_id=? AND method IN ('review','review+price') AND url=?""",
                                (identity["review_event_id"], task["locate_job_id"], task["url"])).fetchall()
            if len(event) == 1:
                approvals = event
                approval_source = "locate_event"
                approval_ref = f"locate_event:{event[0]['id']}"
    return {
        "approval_id": approvals[0]["id"] if len(approvals) == 1 else None,
        "approval_audits": len(approvals),
        "approval_source": approval_source if len(approvals) == 1 else "",
        "approval_ref": approval_ref if len(approvals) == 1 else "",
        "sample_kind": sample_kind,
        "sample_digest": sample_digest,
        "raw_html_available": raw_html,
        "recorded_sample_count": recorded_samples,
        "receipt_count": len(receipts),
        "receipt_ids": [row["id"] for row in receipts],
        "receipt_summaries": [
            {"id": row["id"], "reserved_at": row["reserved_at"], "result": row["result"]}
            for row in receipts
        ],
        "completed_receipt_count": sum(row["result"] not in (None, "", "reserved") for row in receipts),
        "unresolved_reservation": any(row["result"] in (None, "", "reserved") for row in receipts),
    }


def _manual_recovery_identity(task, identity, evidence, recovery_rule):
    identity_payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    evidence_payload = json.dumps(
        evidence.get("artifact_evaluations", []), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    identity_digest = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
    evidence_digest = hashlib.sha256(evidence_payload.encode("utf-8")).hexdigest()
    payload = {
        "request_id": task["id"],
        "approval_ref": evidence["approval_ref"],
        "rules_version": MANUAL_CORRECTED_ERROR_RECOVERY_VERSION,
        "recovery_rule": recovery_rule,
        "identity_digest": identity_digest,
        "evidence_digest": evidence_digest,
    }
    recovery_key = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "recovery_key": recovery_key,
        "evidence": {
            "identity_digest": identity_digest,
            "evidence_digest": evidence_digest,
            "recovery_version": MANUAL_CORRECTED_ERROR_RECOVERY_VERSION,
            "recovery_key": recovery_key,
        },
    }


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


def preview_manual_reviews(database, *, now=None, request_ids=None, approval_ids=None,
                           competitor_ids=None, ean_query="", reason_query=""):
    path = Path(database).resolve()
    now = _time(now or datetime.now(timezone.utc))
    for selected in (request_ids, approval_ids):
        if selected is not None and any(not isinstance(value, int) or value < 1 for value in selected):
            raise ValueError("Los IDs seleccionados deben ser enteros positivos")
    with closing(_connect(path, readonly=True)) as con:
        con.execute("BEGIN")
        continuity_id, rows = _manual_review_rows(con, path, now)
        approvals = [item for item in _approval_inventory(con, continuity_id, now) if item["request_id"] is None]
        con.rollback()
    if request_ids is not None:
        selected = set(request_ids)
        rows = [row for row in rows if row["id"] in selected]
        if len(rows) != len(selected):
            raise ValueError("Hay IDs de solicitud inexistentes")
    if approval_ids is not None:
        selected = set(approval_ids)
        approvals = [row for row in approvals if row["approval_id"] in selected]
        if len(approvals) != len(selected):
            raise ValueError("Hay IDs de aprobación inexistentes o sustituidos")
    if competitor_ids:
        allowed = set(competitor_ids)
        rows = [row for row in rows if row["competitor_id"] in allowed]
        approvals = [row for row in approvals if row["competitor_id"] in allowed]
    if ean_query.strip():
        query = ean_query.strip().casefold()
        rows = [row for row in rows if query in row["ean"].casefold()]
        approvals = [row for row in approvals if query in row["ean"].casefold()]
    if reason_query.strip():
        query = reason_query.strip().casefold()
        rows = [row for row in rows if query in " ".join([
            *(str(row.get(field) or "") for field in ("reason", "detail", "monitor_detail")),
            *(str(item.get("reason") or "") for item in
              row["evidence"].get("artifact_evaluations", [])),
        ]).casefold()]
        approvals = [row for row in approvals if query in str(row.get("reason") or "").casefold()]
    action_counts = {
        "reuse": sum(row["action"] == "local_reevaluation" for row in rows),
        "requeue": sum(row["action"] in {"requeue", "recover_corrected_error"} for row in rows),
        "already_queued": sum(row["action"] == "already_queued" for row in rows),
        "excluded": sum(row["category"] == "excluded" for row in rows),
    }
    return {"counts": _manual_summary(rows), "classifications": _classification_summary(rows), "requests": rows,
            "action_counts": action_counts, "approvals_without_request": approvals}


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
            linked = []
            for item in rows:
                if item["id"] not in selected:
                    continue
                if item["action"] == "already_queued" and item["related_request_id"] is not None:
                    prior_links = con.execute("""SELECT current FROM access_audit
                        WHERE scope='manual_review' AND scope_id=?
                          AND action='link_recovered_manual_review'""", (str(item["id"]),)).fetchall()
                    linked_before = False
                    for link in prior_links:
                        try:
                            linked_before = linked_before or json.loads(link["current"]).get(
                                "related_request_id") == item["related_request_id"]
                        except (TypeError, json.JSONDecodeError):
                            continue
                    if not linked_before:
                        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                            VALUES('manual_review',?,'link_recovered_manual_review',?,?,?,?,?)""",
                                    (str(item["id"]), json.dumps({"status": item["status"],
                                                                 "detail": item["detail"]},
                                                                ensure_ascii=False),
                                     json.dumps({"related_request_id": item["related_request_id"],
                                                 "url": item["url"]}, ensure_ascii=False),
                                     actor.strip(), json.dumps({"reason": note.strip(),
                                                               "build_commit": build_commit,
                                                               "rules_version": MANUAL_ERROR_RECOVERY_RULES},
                                                              ensure_ascii=False), now.isoformat()))
                        linked.append({"request_id": item["id"],
                                       "related_request_id": item["related_request_id"]})
                    continue
                if item["action"] not in {
                        "requeue", "reconcile", "local_reevaluation", "authorize",
                        "recover_corrected_error"}:
                    continue
                before = dict(con.execute("SELECT * FROM manual_review_refreshes WHERE id=?",
                                          (item["id"],)).fetchone())
                if item["action"] in {"requeue", "authorize", "recover_corrected_error"}:
                    previous_grant = con.execute(
                        "SELECT * FROM manual_review_recovery_grants WHERE refresh_id=?", (item["id"],)).fetchone()
                    if previous_grant:
                        before["previous_grant"] = dict(previous_grant)
                    if item["action"] != "recover_corrected_error" or not previous_grant:
                        con.execute("""INSERT INTO manual_review_recovery_grants
                            (refresh_id,continuity_id,actor,note,build_commit,granted_at) VALUES(?,?,?,?,?,?)
                            ON CONFLICT(refresh_id) DO UPDATE SET continuity_id=excluded.continuity_id,
                                actor=excluded.actor,note=excluded.note,build_commit=excluded.build_commit,
                                granted_at=excluded.granted_at""",
                                    (item["id"], continuity_id, actor.strip(), note.strip(), build_commit,
                                     now.isoformat()))
                if item["action"] == "recover_corrected_error":
                    recovery_evidence = item["evidence"]
                    if not item["recovery_key"] or not recovery_evidence.get("approval_ref"):
                        raise ValueError("La recuperación carece de identidad versionada o aprobación vinculada")
                    con.execute("""INSERT INTO manual_review_recovery_events(
                        refresh_id,continuity_id,recovery_key,rules_version,recovery_rule,approval_ref,
                        identity_digest,evidence_digest,historical_error,outcome,actor,note,build_commit,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                                (item["id"], continuity_id, item["recovery_key"],
                                 MANUAL_CORRECTED_ERROR_RECOVERY_VERSION, item["recovery_rule"],
                                 recovery_evidence["approval_ref"], recovery_evidence["identity_digest"],
                                 recovery_evidence["evidence_digest"], item["detail"], actor.strip(),
                                 note.strip(), build_commit, now.isoformat()))
                if item["action"] == "requeue":
                    status, detail = "pending", "Recuperación auditada de reserva; pendiente de lectura gobernada"
                    finished = before["finished_at"]
                elif item["action"] == "recover_corrected_error":
                    status = "pending"
                    detail = ("Rechazo técnico reelegible por reglas actuales; pendiente de lectura gobernada. "
                              f"Regla: {item['recovery_rule']}")
                    finished = before["finished_at"]
                    monitor_update = con.execute("""UPDATE monitor_jobs SET state='pending',detail=?,updated_at=?
                        WHERE id=? AND state='review' AND (not_before IS NULL OR not_before<=?)""",
                                                 (detail[:1000], now.isoformat(), before["monitor_job_id"],
                                                  now.isoformat()))
                    if monitor_update.rowcount != 1:
                        raise ValueError("El trabajo de monitor cambió o conserva un aplazamiento vigente")
                    con.execute("""INSERT INTO monitor_events(job_id,at,state,detail)
                        VALUES(?,?,'pending',?)""",
                                (before["monitor_job_id"], now.isoformat(), detail[:1000]))
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
                                                       "continuity_id": continuity_id,
                                                       "rules_version": MANUAL_ERROR_RECOVERY_RULES,
                                                       "recovery_version": item.get("evidence", {}).get(
                                                           "recovery_version"),
                                                       "recovery_rule": item.get("recovery_rule"),
                                                       "recovery_key": item.get("recovery_key"),
                                                       "historical_error": item.get("detail"),
                                                       "approval_id": item.get("approval_id"),
                                                       "approval_ref": item.get("evidence", {}).get("approval_ref"),
                                                       "evidence": item.get("evidence")}, ensure_ascii=False),
                             now.isoformat()))
                changed.append(item["id"])
            if changed or linked or any(result["action"] == "created" for result in approval_results):
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'recover_manual_review_batch','',?,?,?,?)""",
                            (str(continuity_id), json.dumps({"counts": _manual_summary(rows), "changed": changed,
                                                            "linked": linked, "selected": sorted(selected),
                                                            "approval_results": approval_results}, ensure_ascii=False),
                             actor.strip(), json.dumps({"reason": note.strip(), "build_commit": build_commit,
                                                       "rules_version": MANUAL_ERROR_RECOVERY_RULES},
                                                      ensure_ascii=False), now.isoformat()))
            con.commit()
    return {"counts": _manual_summary(rows), "classifications": _classification_summary(rows),
            "requests": rows, "changed": changed, "linked": linked, "approval_results": approval_results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Base SQLite existente; no se inicializa")
    parser.add_argument("--allowlist", help="Lista auditada con IDs esperados")
    parser.add_argument("--report", help="Informe histórico cuya huella consta en la lista")
    parser.add_argument("--manual-reviews", action="store_true", help="Inventario completo de lecturas manuales")
    parser.add_argument("--request-id", type=int, action="append", help="Limitar aplicación a solicitudes concretas")
    parser.add_argument("--approval-id", type=int, action="append", help="Recuperar aprobación por ID de auditoría")
    parser.add_argument("--competitor", action="append", help="Filtrar por ID de competidor")
    parser.add_argument("--ean", default="", help="Filtrar por EAN/UPC (texto)")
    parser.add_argument("--reason", default="", help="Filtrar por motivo")
    parser.add_argument("--apply", action="store_true", help="Aplicar únicamente los pares elegibles revalidados")
    parser.add_argument("--approved", action="store_true", help="Confirmación expresa de recuperación")
    parser.add_argument("--actor", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    if args.manual_reviews:
        if args.apply:
            if (args.competitor or args.ean or args.reason) and not (args.request_id or args.approval_id):
                parser.error("Al aplicar filtros, indique también --request-id o --approval-id")
            result = apply_manual_reviews(args.database, actor=args.actor, note=args.note, build_commit=args.commit,
                                          approved=args.approved, request_ids=args.request_id,
                                          approval_ids=args.approval_id)
        else:
            result = preview_manual_reviews(args.database, request_ids=args.request_id,
                                            approval_ids=args.approval_id, competitor_ids=args.competitor,
                                            ean_query=args.ean, reason_query=args.reason)
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
