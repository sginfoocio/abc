"""Read-only acquisition and aggregation for the Cloud purchase-order panel."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from time import monotonic
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken

from image_repository import VIEW_ALIASES, repository_root
from kering_images import ConfigStore, NON_TECHNICAL_REASONS, STATES, data_root as kering_root
from provider_images import ean_key, ean_valid
from provider_images_ui import IMAGE_PROVIDER_KEYS, data_root
from provider_odoo_orders import load_links, read_purchase_page
from repository_catalog_readonly import (
    CatalogUnavailable, DEFAULT_REQUIRED_VIEWS, fetch_records, open_catalog, verified_original,
)
from repository_storage import check_image_root, state_root

PROVIDERS = {key: ("Sáfilo" if key == "safilo" else label) for label, key in IMAGE_PROVIDER_KEYS.items()}
VIEWS = (*DEFAULT_REQUIRED_VIEWS, "detalle", "lifestyle", "unknown")
COMPLETE, PARTIAL, NONE, UNKNOWN = "Completa", "Parcial", "Sin imágenes válidas", "No determinada"
MAX_RECORDS = 60
MAX_VERIFICATIONS = 800
VERIFY_SECONDS = 12
MAX_HISTORY = 30
MADRID = ZoneInfo("Europe/Madrid")


def associations(root: Path, kering: Path):
    results, notices = [], []
    try:
        links = load_links(root)
        if not isinstance(links, dict):
            raise ValueError
    except (OSError, ValueError):
        links = {}
        notices.append("No se pudieron leer las asociaciones de Odoo.")
    for provider, label in PROVIDERS.items():
        try:
            link = links.get(provider) or {}
            if not isinstance(link, dict):
                raise ValueError
            if link.get("confirmed") is True:
                ids = {int(p) for p in link.get("partner_ids", ())}
                if not ids or min(ids) <= 0 or len(ids) > 100:
                    raise ValueError
                results.extend((provider, p, True) for p in sorted(ids))
            elif provider == "kering":
                config = ConfigStore(kering).load()
                partner_id = int(config.get("supplier_id") or 0)
                if partner_id > 0:
                    results.append((provider, partner_id, config.get("include_commercial_contacts") is True))
        except (OSError, ValueError, TypeError, InvalidToken):
            notices.append(f"{label}: asociación local no legible o inválida.")
    return results, notices


def open_existing(path):
    """Existing SQLite database only, without schema initialisation or recovery."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def local_date(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(MADRID).date()


def history_date(value):
    return datetime.fromtimestamp(value, tz=MADRID).strftime("%Y-%m-%d %H:%M")


def append_kering_history(orders, root):
    by_id = {o["id"]: o for o in orders if o["provider"] == "kering"}
    path = root / "history.sqlite3"
    if not by_id or not path.is_file():
        return
    with closing(open_existing(path)) as db:
        ids = ",".join("?" for _ in by_id)
        rows = db.execute(f"""
            SELECT * FROM (
                SELECT a.order_id, a.number, a.supplier, a.snapshot, a.status, a.error, a.results, r.started,
                       ROW_NUMBER() OVER (PARTITION BY a.order_id ORDER BY r.started DESC, a.attempt DESC) AS rn
                FROM attempts a JOIN runs r ON r.id = a.run_id WHERE a.order_id IN ({ids})
            ) WHERE rn <= ? ORDER BY started DESC
        """, [*by_id, MAX_HISTORY + 1])
        for row in rows:
            order = by_id[row["order_id"]]
            saved_order = json.loads(row["snapshot"] or "{}")
            if order["name"] != row["number"] or order["partner_id"] != saved_order.get(
                "supplier_id", row["supplier"]
            ):
                continue
            if len(order["history"]) >= MAX_HISTORY:
                order["history_limited"] = True
                continue
            results = json.loads(row["results"] or "{}")
            codes = sorted(e for e in results if ean_valid(e))
            failed_codes = [e for e in codes if results[e].get("reason", "") not in NON_TECHNICAL_REASONS]
            error = bool(row["error"] or failed_codes or row["status"] in ("Error", "Interrumpido"))
            order["history"].append({
                "date": history_date(row["started"]), "status": row["status"] if row["status"] in STATES else "Otro",
                "source": "Kering", "eans": codes, "error": error,
                "detail": ("Error en referencias: " + ", ".join(failed_codes) if failed_codes else
                           "Líneas sin EAN" if row["error"] == "lineas_sin_ean" else
                           "Incidencia registrada por Kering" if error else ""),
            })


def append_assisted_history(orders, root):
    path = root / "provider_requests.sqlite"
    grouped = defaultdict(list)
    for order in orders:
        if order["provider"] in ("marcolin", "safilo", "nuorder") and not order["order_identity_conflict"]:
            grouped[order["provider"], order["name"], order["supplier"], order["date"]].append(order)
    if not grouped or not path.is_file():
        return
    with closing(open_existing(path)) as db:
        for identity, candidates in grouped.items():
            if len(candidates) != 1:
                continue
            order = candidates[0]
            lines = {(ean_key(line["ean"]), line["sku"]) for line in order["lines"]
                     if ean_valid(line["ean"]) and line["sku"] and not line["barcode_conflict"]}
            requests = defaultdict(set)
            for item in db.execute("""
                SELECT r.id, i.ean, i.sku
                FROM request_odoo_items i JOIN requests r ON i.request_id = r.id
                WHERE r.provider = ? AND i.order_name = ? AND i.supplier = ?
                    AND i.order_date = ? AND i.status = 'confirmed'
                ORDER BY r.id DESC LIMIT 1001
            """, (*identity[:3], order["date_order"][:10])):
                if ean_valid(item["ean"]) and (ean_key(item["ean"]), item["sku"]) in lines:
                    requests[item["id"]].add(ean_key(item["ean"]))
            attempts = []
            for rid, codes in list(requests.items())[:MAX_HISTORY + 1]:
                for event in db.execute("""
                    SELECT at, to_state FROM events WHERE request_id = ? ORDER BY id DESC LIMIT ?
                """, (rid, MAX_HISTORY + 1)):
                    state = event["to_state"]
                    error = state in ("failed", "needs_review")
                    attempts.append((event["at"], {
                        "date": history_date(event["at"]), "status": state,
                        "source": f"{PROVIDERS[order['provider']]} · solicitud {rid}", "eans": sorted(codes),
                        "error": error, "detail": "Incidencia en la solicitud vinculada" if error else "",
                    }))
                for review in db.execute("""
                    SELECT DISTINCT ean FROM review_items
                    WHERE request_id = ? AND status = 'open' AND ean IS NOT NULL LIMIT 1000
                """, (rid,)):
                    if ean_valid(review["ean"]) and ean_key(review["ean"]) in codes:
                        order["review_codes"].add(ean_key(review["ean"]))
            attempts.sort(key=lambda item: item[0], reverse=True)
            order["history_limited"] = len(attempts) > MAX_HISTORY or len(requests) > MAX_HISTORY
            order["history"] = [attempt for _, attempt in attempts[:MAX_HISTORY]]


def image_evidence(orders, catalog, root):
    codes = {line["ean"] for o in orders for line in o["lines"]
             if ean_valid(line["ean"]) and not line["barcode_conflict"]}
    if not codes:
        return {}, None
    try:
        check_image_root(root, allow_read_only=True)
        if not root.is_dir():
            raise OSError
        db, _ = open_catalog(catalog)
        with closing(db):
            records = fetch_records(db, sorted(codes), max_per_literal=MAX_RECORDS)
    except (CatalogUnavailable, OSError, ValueError, RuntimeError):
        return {}, "Catálogo o archivos no disponibles; no se puede determinar la cobertura."

    evidence, verified = {}, {}
    deadline = monotonic() + VERIFY_SECONDS
    for key in dict.fromkeys(ean_key(line["ean"]) for o in orders for line in o["lines"] if line["ean"] in codes):
        photos, issues, incomplete = {}, set(), False
        candidates = records.get(key, [])
        if len(candidates) > MAX_RECORDS:
            incomplete = True
            issues.add("Demasiados registros para comprobar")
        for record in candidates[:MAX_RECORDS]:
            asset = record["path"], record["checksum"], record["pixels"]
            if asset not in verified:
                if len(verified) >= MAX_VERIFICATIONS or monotonic() >= deadline:
                    incomplete = True
                    issues.add("Verificación pendiente por límite de consulta")
                    break
                try:
                    _, pixels, issue = verified_original(record, root)
                except (OSError, RuntimeError):
                    pixels, issue = None, "archivo_no_accesible"
                verified[asset] = pixels, issue
            pixels, issue = verified[asset]
            if issue:
                issues.add(issue)
                incomplete |= issue == "archivo_no_accesible"
                continue
            view = VIEW_ALIASES.get(record["view"], record["view"])
            view = view if view in VIEWS else "unknown"
            photo = photos.setdefault(pixels, {"views": set(), "record": record})
            photo["views"].add(view)
        counts = Counter()
        for photo in photos.values():
            known = photo["views"] - {"unknown"}
            if len(known) > 1:
                issues.add("Clasificaciones incompatibles para la misma foto")
            view = next(iter(known)) if len(known) == 1 else "unknown"
            if view == "unknown":
                issues.add("Foto sin vista acreditada")
            photo["view"] = view
            counts[view] += 1
        missing = [v for v in DEFAULT_REQUIRED_VIEWS if not counts[v]]
        status = (UNKNOWN if incomplete and not photos else COMPLETE if not missing and not incomplete
                  else PARTIAL if photos else NONE)
        evidence[key] = {"status": status, "counts": counts, "missing": missing, "issues": sorted(issues),
                         "photos": list(photos.values()), "incomplete": incomplete}
    return evidence, None


def aggregate(orders, evidence, catalog_notice):
    all_codes, with_photos, review = set(), set(), set()
    metrics = Counter()
    for order in orders:
        per_order = {}
        for line in order["lines"]:
            code = line["ean"]
            key = ean_key(code) if ean_valid(code) else None
            issue = ("Sin EAN/UPC" if not code else "EAN/UPC no válido" if not key else
                     "Código compartido por variantes Odoo distintas" if line["barcode_conflict"] else "")
            report = evidence.get(key) if not issue else None
            line["image"] = report
            line["issues"] = [issue] if issue else list(report["issues"]) if report else [catalog_notice]
            if key in order["review_codes"]:
                line["issues"].append("Revisión abierta en solicitud vinculada")
            if key:
                all_codes.add(key)
                per_order[key] = report["status"] if report else UNKNOWN
                if report and report["photos"]:
                    with_photos.add(key)
                if any(line["issues"]):
                    review.add(key)
        counts = Counter(per_order.values())
        partial_acquisition = len(order["lines"]) != order["line_count"]
        if (counts[UNKNOWN] or not counts or partial_acquisition
                or any(not ean_valid(line["ean"]) for line in order["lines"])):
            coverage = PARTIAL if counts[COMPLETE] or counts[PARTIAL] else UNKNOWN
        elif counts[COMPLETE] == len(per_order):
            coverage = COMPLETE
        elif counts[NONE] == len(per_order):
            coverage = NONE
        else:
            coverage = PARTIAL
        order.update(coverage=coverage, counts=counts, codes=set(per_order),
                     has_errors=any(h["error"] for h in order["history"]))
        metrics[coverage] += 1
        metrics["errors"] += order["has_errors"]
        metrics["lines"] += len(order["lines"])
    return {**metrics, "codes": len(all_codes), "photos": len(with_photos), "review": len(review)}


def acquire(engine_factory, links, providers, start, end, *, before=None, request_root=None, history_root=None):
    if not any(provider in providers for provider, _, _ in links):
        raise ValueError("No hay una asociación confirmada para los proveedores seleccionados.")
    orders, cursor = read_purchase_page(engine_factory(), links, providers, start, end, before=before)
    for order in orders:
        order.update(date=local_date(order["date_order"]).isoformat(), history=[],
                     review_codes=set(), history_limited=False)
    notices = []
    for reader, root, label in (
        (append_kering_history, history_root or kering_root(), "Kering"),
        (append_assisted_history, request_root or data_root(), "solicitudes asistidas"),
    ):
        try:
            reader(orders, root)
        except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError, OverflowError):
            notices.append(f"No se pudo leer íntegramente el historial de {label}.")
    root = repository_root()
    try:
        catalog = state_root(root) / ".catalog.sqlite3"
    except (OSError, ValueError):
        catalog = None
    evidence, catalog_notice = image_evidence(orders, catalog, root)
    totals = aggregate(orders, evidence, catalog_notice)
    if catalog_notice:
        notices.append(catalog_notice)
    return {"orders": orders, "next": cursor, "totals": totals, "notices": notices, "root": root, "links": tuple(links),
            "providers": providers, "start": start, "end": end,
            "queried": datetime.now(MADRID).strftime("%Y-%m-%d %H:%M")}
