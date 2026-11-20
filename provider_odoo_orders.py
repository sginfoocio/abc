"""Optional, read-only EAN selection from real Odoo purchase orders for supplier image requests (#59).

Cloud never needs this module: EANs can always be typed or imported. Odoo only shows where an
EAN came from; it is never evidence that a downloaded image belongs to that EAN.
Nothing here writes to Odoo or contacts a portal.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from kering_images import AccessNotConfirmed, utc_interval
from provider_images import PROVIDERS, ean_key, ean_valid
from repository_catalog_readonly import catalog_coverage, unknown_coverage

LINKS_FILE = "provider_odoo_links.json"
SELECTION_LOG = "provider_odoo_selections.jsonl"
CONFIRMED_STATES = ("purchase", "done")  # confirmed / locked; draft, sent and cancel are excluded
DEFAULT_MAX_LINES = 2000
HARD_MAX_LINES = 10000
STATEMENT_TIMEOUT_MS = 20000

ELIGIBLE, DUPLICATE, REVIEW, COMPLETE = "Elegible", "Duplicado", "Requiere revisión", "Ya completa"
IMAGES_COMPLETE, IMAGES_PARTIAL = "Completas", "Parciales"
IMAGES_NONE = "Sin registros en el catálogo"
IMAGES_NO_VIEWS = "Sin vistas válidas"
IMAGES_UNKNOWN = "Cobertura desconocida"
IMAGE_LABELS = {"completas": IMAGES_COMPLETE, "parciales": IMAGES_PARTIAL, "sin_registros": IMAGES_NONE,
                "sin_vistas_validas": IMAGES_NO_VIEWS, "desconocida": IMAGES_UNKNOWN}
BASIS_TEXT = {"solo_metadatos_del_catalogo": "metadatos del catálogo (archivos no verificados)",
              "archivos_verificados": "archivos verificados", "catalogo_no_consultado": "catálogo no consultado"}


class OdooUnavailable(Exception):
    """Odoo failed or timed out; the rest of Cloud keeps working."""
    code = "odoo_no_disponible"


class AssociationNotConfigured(AccessNotConfirmed):
    pass


@dataclass(frozen=True)
class OrderLine:
    ean: str
    reference: str
    product: str
    supplier: str
    order: str
    order_id: int
    date: str


# --- explicit Odoo supplier <-> portal association ----------------------------------------

def load_links(root: Path) -> dict:
    path = Path(root) / LINKS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def confirmed_partner_ids(root: Path, provider: str) -> tuple[int, ...]:
    link = load_links(root).get(provider) or {}
    if not link.get("confirmed") or not link.get("partner_ids"):
        raise AssociationNotConfigured("asociacion_proveedor_portal_no_configurada")
    return tuple(int(p) for p in link["partner_ids"])


def save_link(root: Path, provider: str, partner_ids, *, reviewer: str, brands=(), note: str = "") -> dict:
    """Persist a reviewed association. Never called implicitly; needs an explicit reviewer."""
    if provider not in PROVIDERS:
        raise ValueError("proveedor_desconocido")
    ids = sorted({int(p) for p in partner_ids})
    if not ids:
        raise ValueError("sin_proveedores_odoo")
    if not str(reviewer).strip():
        raise ValueError("revisor_obligatorio")
    links = load_links(root)
    links[provider] = {"partner_ids": ids, "brands": [str(b).strip() for b in brands if str(b).strip()],
                       "confirmed": True, "reviewer": str(reviewer).strip(), "note": note,
                       "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = Path(root) / LINKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(links, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return links[provider]


def _read_only(connection, statement, parameters):
    transaction = connection.begin()
    try:
        if connection.engine.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        return connection.execute(statement, parameters).mappings().all()
    finally:
        transaction.rollback()


def propose_partners(engine, name_fragment: str, limit: int = 25) -> list[dict]:
    """Candidate Odoo suppliers by name. A proposal only: the operator must confirm it (save_link)."""
    fragment = (name_fragment or "").strip().lower()
    if len(fragment) < 3:
        raise ValueError("fragmento_demasiado_corto")
    query = text("""
        SELECT p.id, p.name, COALESCE(p.commercial_partner_id, p.id) AS commercial_id,
               (SELECT COUNT(*) FROM purchase_order po WHERE po.partner_id = p.id AND po.state IN ('purchase','done')) AS orders
        FROM res_partner p WHERE p.supplier_rank > 0 AND LOWER(p.name) LIKE :n ORDER BY p.id LIMIT :limit""")
    try:
        with engine.connect() as connection:
            rows = _read_only(connection, query, {"n": f"%{fragment}%", "limit": min(int(limit), 100)})
    except SQLAlchemyError as error:
        raise OdooUnavailable(str(error.__class__.__name__)) from error
    return [dict(r) for r in rows]


# --- purchase-order lines ----------------------------------------------------------------

def read_order_lines(engine, partner_ids, start: date, end: date, *, max_lines: int = DEFAULT_MAX_LINES,
                     include_commercial_contacts: bool = True) -> tuple[list[OrderLine], bool]:
    """Read-only, bounded read of lines of confirmed/done orders. Returns (lines, truncated)."""
    max_lines = max(1, min(int(max_lines), HARD_MAX_LINES))
    lower, upper = utc_interval(start, end)
    supplier_filter = "po.partner_id IN :partners"
    if include_commercial_contacts:
        supplier_filter = """po.partner_id IN (
            SELECT contact.id FROM res_partner contact
            JOIN res_partner selected ON selected.id IN :partners
            WHERE COALESCE(contact.commercial_partner_id, contact.id)
                = COALESCE(selected.commercial_partner_id, selected.id))"""
    query = text(f"""
        SELECT po.id AS order_id, po.name AS order_name, po.date_order, partner.name AS supplier,
               pp.barcode, pp.default_code, pol.name AS line_name
        FROM purchase_order po
        JOIN res_partner partner ON partner.id = po.partner_id
        JOIN purchase_order_line pol ON pol.order_id = po.id
          AND COALESCE(pol.display_type, '') = '' AND pol.product_id IS NOT NULL
        JOIN product_product pp ON pp.id = pol.product_id
        WHERE {supplier_filter} AND po.state IN :states
          AND po.date_order >= :lower AND po.date_order < :upper
        ORDER BY po.date_order DESC, po.id DESC, pol.id
        LIMIT :limit""").bindparams(bindparam("partners", expanding=True), bindparam("states", expanding=True))
    parameters = {"partners": [int(p) for p in partner_ids], "states": list(CONFIRMED_STATES),
                  "lower": lower, "upper": upper, "limit": max_lines + 1}
    try:
        with engine.connect() as connection:
            rows = _read_only(connection, query, parameters)
    except SQLAlchemyError as error:
        raise OdooUnavailable(str(error.__class__.__name__)) from error
    truncated = len(rows) > max_lines
    lines = [OrderLine((r["barcode"] or "").strip(), (r["default_code"] or "").strip(), r["line_name"] or "",
                       r["supplier"] or "", r["order_name"] or "", int(r["order_id"]), str(r["date_order"])[:10])
             for r in rows[:max_lines]]
    return lines, truncated


ORDER_PAGE_SIZE = 50
ORDER_LINE_BUDGET = 2000
MAX_ORDER_DAYS = 366


def read_purchase_page(engine, associations, providers, start, end, *, before=None, order_id=None):
    """Read one bounded page of purchase orders in a single read-only transaction.

    Associations are (provider key, partner ID, include commercial contacts). Resolve
    all configured providers before filtering so a conflicting mapping is never hidden.
    """
    if not 0 <= (end - start).days < MAX_ORDER_DAYS:
        raise ValueError(f"Seleccione un intervalo de hasta {MAX_ORDER_DAYS} días.")
    if not associations or not providers:
        return [], None
    lower, upper = utc_interval(start, end)
    parameters = {"providers": list(providers), "lower": lower, "upper": upper,
                  "take": ORDER_PAGE_SIZE + 1, "line_limit": ORDER_LINE_BUDGET + 1}
    values = []
    for i, (provider, partner, family) in enumerate(associations):
        parameters.update({f"provider{i}": provider, f"partner{i}": partner, f"family{i}": family})
        values.append(f"(CAST(:provider{i} AS TEXT), CAST(:partner{i} AS INTEGER), :family{i})")
    cursor_filter = ""
    line_filter = ""
    order_filter = ""
    if order_id is not None:
        parameters["order_id"] = order_id
        order_filter = "AND po.id = :order_id"
    if before is not None:
        parameters.update(before_date=before[0], before_id=before[1])
        cursor_filter = "AND (po.date_order, po.id) < (:before_date, :before_id)"
        if len(before) == 3:
            cursor_filter = "AND (po.date_order, po.id) <= (:before_date, :before_id)"
            line_filter = "WHERE id <> :before_id OR line_id > :before_line"
            parameters["before_line"] = before[2]
    postgres = engine.dialect.name == "postgresql"
    metadata = """
        COALESCE(to_jsonb(pt)->>'modelo', '') AS model,
        COALESCE(to_jsonb(pt)->>'color_proveedor', '') AS color,
        COALESCE((SELECT CASE WHEN COUNT(DISTINCT value) = 1 THEN MIN(value) END
          FROM (
            SELECT COALESCE(to_jsonb(pav.name)->>'es_ES', to_jsonb(pav.name)->>'en_US',
                            to_jsonb(pav.name)#>>'{}') AS value
            FROM product_variant_combination pvc
            JOIN product_template_attribute_value ptav ON ptav.id = pvc.product_template_attribute_value_id
            JOIN product_attribute_value pav ON pav.id = ptav.product_attribute_value_id
            JOIN product_attribute pa ON pa.id = pav.attribute_id
            WHERE pvc.product_product_id = pp.id
              AND LOWER(COALESCE(to_jsonb(pa.name)->>'es_ES', to_jsonb(pa.name)->>'en_US',
                                 to_jsonb(pa.name)#>>'{}')) = 'calibre'
          ) AS sizes), '') AS caliber
    """ if postgres else "'' AS model, '' AS color, '' AS caliber"
    template = "LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id" if postgres else ""
    statement = text(f"""
        WITH associations(provider, partner_id, family) AS (VALUES {', '.join(values)}),
        matched AS (
            SELECT DISTINCT contact.id, a.provider
            FROM associations a
            JOIN res_partner selected ON selected.id = a.partner_id
            JOIN res_partner contact ON contact.id = selected.id OR
              (a.family AND COALESCE(contact.commercial_partner_id, contact.id) =
                            COALESCE(selected.commercial_partner_id, selected.id))
        ), partners AS (
            SELECT id, MIN(provider) AS provider, COUNT(*) AS provider_count
            FROM matched GROUP BY id
            HAVING SUM(CASE WHEN provider IN :providers THEN 1 ELSE 0 END) > 0
        ), orders AS (
            SELECT po.id, po.name, po.date_order, po.state, po.partner_id,
                   p.provider, p.provider_count, contact.name AS supplier
            FROM purchase_order po JOIN partners p ON p.id = po.partner_id
            JOIN res_partner contact ON contact.id = po.partner_id
            WHERE po.date_order >= :lower AND po.date_order < :upper {cursor_filter} {order_filter}
            ORDER BY po.date_order DESC, po.id DESC LIMIT :take
        ), lines AS (
            SELECT orders.*, pol.id AS line_id, pol.product_id, pol.name AS product,
                   pol.product_qty AS quantity,
                   COUNT(pol.id) OVER (PARTITION BY orders.id) AS line_count,
                   ROW_NUMBER() OVER (PARTITION BY orders.id ORDER BY pol.id) AS line_number
            FROM orders LEFT JOIN purchase_order_line pol ON pol.order_id = orders.id
              AND COALESCE(pol.display_type, '') = '' AND pol.product_id IS NOT NULL
        ), bounded_lines AS (
            SELECT * FROM lines {line_filter}
            ORDER BY date_order DESC, id DESC, line_id LIMIT :line_limit
        )
        SELECT b.*, pp.barcode, pp.default_code AS sku, {metadata},
               EXISTS (SELECT 1 FROM purchase_order duplicate WHERE duplicate.id <> b.id
                   AND duplicate.name = b.name AND duplicate.partner_id = b.partner_id
                   AND SUBSTR(CAST(duplicate.date_order AS TEXT), 1, 10) =
                       SUBSTR(CAST(b.date_order AS TEXT), 1, 10)) AS order_identity_conflict,
               EXISTS (SELECT 1 FROM product_product other WHERE other.id <> pp.id
                   AND other.barcode IN (
                       pp.barcode,
                       CASE WHEN LENGTH(LTRIM(pp.barcode, '0')) <= 8 THEN
                           SUBSTR('00000000' || LTRIM(pp.barcode, '0'), LENGTH(LTRIM(pp.barcode, '0')) + 1) END,
                       CASE WHEN LENGTH(LTRIM(pp.barcode, '0')) <= 12 THEN
                           SUBSTR('000000000000' || LTRIM(pp.barcode, '0'), LENGTH(LTRIM(pp.barcode, '0')) + 1) END,
                       CASE WHEN LENGTH(LTRIM(pp.barcode, '0')) <= 13 THEN
                           SUBSTR('0000000000000' || LTRIM(pp.barcode, '0'), LENGTH(LTRIM(pp.barcode, '0')) + 1) END,
                       SUBSTR('00000000000000' || LTRIM(pp.barcode, '0'), LENGTH(LTRIM(pp.barcode, '0')) + 1)
                   )) AS barcode_conflict
        FROM bounded_lines b LEFT JOIN product_product pp ON pp.id = b.product_id
        {template}
        ORDER BY b.date_order DESC, b.id DESC, b.line_id
    """).bindparams(bindparam("providers", expanding=True))
    try:
        with engine.connect() as connection:
            rows = _read_only(connection, statement, parameters)
    except SQLAlchemyError:
        raise OdooUnavailable("odoo_no_disponible") from None
    orders = {}
    for row in rows[:ORDER_LINE_BUDGET]:
        order = orders.setdefault(row["id"], {
            "id": row["id"], "name": row["name"], "date_order": str(row["date_order"]),
            "cursor": (row["date_order"], row["id"]), "state": row["state"],
            "partner_id": row["partner_id"], "supplier": row["supplier"],
            "order_identity_conflict": bool(row["order_identity_conflict"]),
            "provider": row["provider"] if row["provider_count"] == 1 else "",
            "line_count": row["line_count"], "lines": [],
        })
        order["last_line_number"] = row["line_number"]
        if row["line_id"] is not None:
            order["lines"].append({
                "id": row["line_id"], "product_id": row["product_id"], "product": row["product"],
                "ean": (row["barcode"] or "").strip(), "sku": row["sku"] or "",
                "quantity": str(row["quantity"]) if row["quantity"] is not None else "",
                "model": row["model"], "color": row["color"], "caliber": row["caliber"],
                "barcode_conflict": bool(row["barcode_conflict"]),
            })
    page = list(orders.values())[:ORDER_PAGE_SIZE]
    has_more = len(orders) > len(page) or len(rows) > ORDER_LINE_BUDGET
    if len(page) > 1 and page[-1]["last_line_number"] < page[-1]["line_count"]:
        page.pop()  # Fetch the complete order first on the next page.
    if not has_more or not page:
        return page, None
    last = page[-1]
    cursor = last["cursor"]
    if last["last_line_number"] < last["line_count"]:
        cursor = (*cursor, last["lines"][-1]["id"])
    return page, cursor


def read_confirmed_skus(engine, partner_ids, eans) -> list[dict]:
    """Read exact SKU evidence for an explicit export operation, without writing to Odoo."""
    requested = [str(e).strip() for e in dict.fromkeys(eans) if str(e).strip()]
    if not requested:
        return []
    forms = {ean_key(e) for e in requested if e.isdigit()}
    if not forms:
        return [{"ean": e, "status": REVIEW, "reason": "EAN no numérico"} for e in requested]
    query = text("""
        SELECT pp.barcode, pp.default_code, pol.name AS line_name, partner.name AS supplier,
               po.name AS order_name, po.date_order
        FROM purchase_order po
        JOIN res_partner partner ON partner.id = po.partner_id
        JOIN purchase_order_line pol ON pol.order_id = po.id
          AND COALESCE(pol.display_type, '') = '' AND pol.product_id IS NOT NULL
        JOIN product_product pp ON pp.id = pol.product_id
        WHERE po.partner_id IN (
            SELECT contact.id FROM res_partner contact
            JOIN res_partner selected ON selected.id IN :partners
            WHERE COALESCE(contact.commercial_partner_id, contact.id)
                = COALESCE(selected.commercial_partner_id, selected.id))
          AND po.state IN :states AND pp.barcode IS NOT NULL
    """).bindparams(bindparam("partners", expanding=True), bindparam("states", expanding=True))
    try:
        with engine.connect() as connection:
            rows = _read_only(connection, query, {"partners": [int(p) for p in partner_ids],
                                                   "states": list(CONFIRMED_STATES)})
    except SQLAlchemyError as error:
        raise OdooUnavailable(str(error.__class__.__name__)) from error
    matches: dict[str, list[dict]] = {key: [] for key in forms}
    for row in rows:
        key = ean_key(str(row["barcode"]).strip())
        if key in matches:
            matches[key].append({
                "ean": str(row["barcode"]).strip(), "sku": (row["default_code"] or "").strip(),
                "product": row["line_name"] or "", "supplier": row["supplier"] or "",
                "order": row["order_name"] or "", "date": str(row["date_order"])[:10],
            })
    result = []
    for ean in requested:
        candidates = matches.get(ean_key(ean), [])
        skus = {candidate["sku"] for candidate in candidates if candidate["sku"]}
        if len(skus) == 1 and len(candidates) >= 1:
            item = next(candidate for candidate in candidates if candidate["sku"])
            result.append({**item, "ean": ean, "status": "confirmed", "reason": ""})
        elif not candidates:
            result.append({"ean": ean, "status": "pending", "reason": "sin correspondencia confirmada en Odoo"})
        elif not skus:
            result.append({"ean": ean, "status": "pending", "reason": "Odoo no aporta SKU completo"})
        else:
            result.append({"ean": ean, "status": REVIEW, "reason": "referencias Odoo ambiguas"})
    return result


def _images_state(cov: dict) -> str:
    return IMAGE_LABELS.get(cov["label"], IMAGES_UNKNOWN)


def build_preview(lines, coverage, live_keys=frozenset(), *, max_refs: int = 200) -> dict:
    """One row per distinct code (most recent order first) plus review rows for lines without a usable EAN.

    ``coverage`` maps each code to a read-only catalog verdict (``repository_catalog_readonly``); an
    unknown verdict is shown as such and is never treated as missing images. A UPC-12 and its EAN-13
    form with a leading zero are the same product here.
    """
    rows, seen, summary = [], set(), {"sin_ean": 0, "ean_invalido": 0, "duplicados": 0}
    for line in lines:
        base = {"EAN": line.ean, "Referencia": line.reference, "Producto": line.product,
                "Proveedor": line.supplier, "Pedido": line.order, "Fecha": line.date}
        key = ean_key(line.ean) if line.ean.isdigit() else line.ean
        if not line.ean:
            summary["sin_ean"] += 1
            rows.append({**base, "Imágenes": "", "Base": "", "Estado": REVIEW, "Motivo": "sin EAN en Odoo"})
        elif not ean_valid(line.ean):
            summary["ean_invalido"] += 1
            rows.append({**base, "Imágenes": "", "Base": "", "Estado": REVIEW, "Motivo": "EAN inválido"})
        elif key in seen:
            summary["duplicados"] += 1
            rows.append({**base, "Imágenes": "", "Base": "", "Estado": DUPLICATE,
                         "Motivo": "EAN repetido en otra línea (o su forma UPC/EAN-13)"})
        else:
            seen.add(key)
            cov = coverage.get(line.ean) or unknown_coverage()
            images = _images_state(cov)
            base = {**base, "Imágenes": images, "Base": BASIS_TEXT.get(cov["basis"], "")}
            if key in live_keys:
                rows.append({**base, "Estado": DUPLICATE, "Motivo": "solicitud en curso"})
            else:
                rows.append({**base, "Estado": COMPLETE if cov["label"] == "completas" else ELIGIBLE, "Motivo": ""})
    eligible = [r for r in rows if r["Estado"] == ELIGIBLE]
    return {"rows": rows[:max_refs] if max_refs else rows, "summary": summary, "eligible": len(eligible),
            "shown_truncated": len(rows) > max_refs}


def pick_pilot(rows, size: int = 8) -> list[str]:
    """Proposal for a small real batch: eligible, no valid images first, then partial, recent orders first.

    Rows whose coverage is unknown are never proposed as 'without images'. Partner membership is
    already guaranteed by the confirmed association used to read the orders.
    """
    size = max(1, min(int(size), 10))
    rank = {IMAGES_NONE: 0, IMAGES_NO_VIEWS: 0, IMAGES_PARTIAL: 1}
    eligible = [r for r in rows if r["Estado"] == ELIGIBLE and r["Imágenes"] in rank]
    eligible.sort(key=lambda r: (rank[r["Imágenes"]], -int(r["Fecha"].replace("-", "") or 0)))
    return [r["EAN"] for r in eligible[:size]]


def demand_preview(engine, connector, root: Path, start: date, end: date, *, max_lines=DEFAULT_MAX_LINES,
                   max_refs: int = 200, catalog=None, image_root=None, verify_files: bool = False, env=None) -> dict:
    """Odoo -> preview rows for the chosen portal. Raises AssociationNotConfigured / OdooUnavailable.

    Image coverage comes from the existing catalog opened read-only; the repository is never built.
    """
    service = connector.service
    partners = confirmed_partner_ids(root, service.settings.provider)
    lines, truncated = read_order_lines(engine, partners, start, end, max_lines=max_lines)
    live = service.store.live_keys(service.settings.provider)
    codes = [line.ean for line in lines if line.ean and ean_valid(line.ean)]
    environment = os.environ if env is None else env
    report = catalog_coverage(
        codes, catalog=catalog, required_views=service.settings.required_views,
        image_root=image_root or (environment.get("IMAGE_REPOSITORY_ROOT") or None) if verify_files else None,
        verify_files=verify_files, env=environment)
    preview = build_preview(lines, report["coverage"], live, max_refs=max_refs)
    return {**preview, "lines_read": len(lines), "truncated": truncated, "partners": list(partners),
            "catalog": report["catalog"]}


def create_requests(connector, root: Path, rows, selected_eans, *, approved_by: str) -> dict:
    """Plan the operator-approved EANs through the common planner. Never contacts a portal.

    Only EANs that are Eligible in the shown preview can be planned, whatever was ticked.
    """
    if not str(approved_by).strip():
        raise ValueError("aprobacion_obligatoria")
    by_ean = {r["EAN"]: r for r in rows if r["Estado"] == ELIGIBLE}
    chosen = [e for e in dict.fromkeys(selected_eans) if e in by_ean]
    rejected = [e for e in dict.fromkeys(selected_eans) if e not in by_ean]
    result = connector.plan_eans(chosen, limit=len(chosen)) if chosen else {
        "created": [], "already_live": [], "invalid": [], "selected": 0}
    evidence = [{**by_ean[e], "ean": e, "sku": by_ean[e].get("Referencia", ""),
                 "status": "confirmed" if by_ean[e].get("Referencia") else "pending",
                 "reason": "" if by_ean[e].get("Referencia") else "Odoo no aporta SKU completo"}
                for e in chosen]
    assigned = {}
    for request_id in result["created"]:
        request_eans = connector.service.store.get(request_id)["eans"]
        assigned[request_id] = [item for item in evidence if ean_key(item["ean"]) in
                                {ean_key(row["ean"]) for row in request_eans}]
        connector.service.store.save_odoo_items(request_id, assigned[request_id])
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provider": connector.service.settings.provider,
             "approved_by": str(approved_by).strip(), "source": "odoo_purchase_orders",
             "eans": [{"ean": e, "order": by_ean[e]["Pedido"], "reference": by_ean[e]["Referencia"]} for e in chosen]}
    if chosen:
        path = Path(root) / SELECTION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {**result, "not_eligible": rejected}
