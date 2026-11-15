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
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    return parsed.astimezone(timezone.utc)


def load_allowlist(path, report_path):
    """Validate the supplied historical snapshot, not the current database."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    content = Path(report_path).read_bytes()
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


def _scope(con, entries):
    ids = {e["continuity_id"] for e in entries}
    if len(ids) != 1:
        raise ValueError("La lista debe pertenecer a una única continuidad")
    continuity_id = next(iter(ids))
    row = con.execute("SELECT * FROM price_continuity WHERE id=?", (continuity_id,)).fetchone()
    if not row or row["status"] not in {"active", "paused"}:
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
                       OR ((competitor_id='' OR competitor_id IS NULL) AND scope!='competitor'))
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
            if changed:
                previous = [r["previous"] for r in changed]
                current = [{"id": r["pair_id"], "state": "pending", "next_check_at": r["previous"]["next_check_at"]}
                           for r in changed]
                metadata = {"reason": note.strip(), "build_commit": build_commit,
                            "source_report_sha256": allowlist["source_report_sha256"],
                            "excluded": [{k: r[k] for k in ("pair_id", "competitor_id", "ean", "reason")}
                                         for r in rows if not r["eligible"]]}
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'recover_interrupted_pairs',?,?,?,?,?)""",
                            (str(continuity_id), json.dumps(previous, ensure_ascii=False),
                             json.dumps(current, ensure_ascii=False), actor.strip(),
                             json.dumps(metadata, ensure_ascii=False), now))
            con.commit()
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Base SQLite existente; no se inicializa")
    parser.add_argument("--allowlist", required=True, help="Lista auditada con IDs esperados")
    parser.add_argument("--report", required=True, help="Informe histórico cuya huella consta en la lista")
    parser.add_argument("--apply", action="store_true", help="Aplicar únicamente los pares elegibles revalidados")
    parser.add_argument("--approved", action="store_true", help="Confirmación expresa de recuperación")
    parser.add_argument("--actor", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    if args.apply:
        rows = apply(args.database, args.allowlist, args.report, actor=args.actor, note=args.note,
                     build_commit=args.commit, approved=args.approved)
    else:
        rows = preview(args.database, args.allowlist, args.report)
    print(json.dumps({"mode": "apply" if args.apply else "preview", "eligible": sum(r["eligible"] for r in rows),
                      "excluded": sum(not r["eligible"] for r in rows), "pairs": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
