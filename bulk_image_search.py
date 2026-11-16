"""Explicit, read-only bulk image and Odoo lookup. No repository or provider is initialised."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from image_repository import ImageRecord, image_fingerprint
from provider_images import ean_key, ean_valid
from provider_odoo_orders import _read_only, load_links
from repository_catalog_readonly import (
    CatalogUnavailable, DEFAULT_REQUIRED_VIEWS, MAX_FILE_BYTES, code_variants,
    coverage, fetch_records, open_catalog, unknown_coverage,
)
from repository_storage import check_image_root

MAX_INPUTS = 500
MAX_ROWS = 5000
PORTALS = {"kering", "luxottica", "marcolin", "safilo", "nuorder"}


def group_inputs(values):
    values = [str(v).strip() for v in values if str(v).strip()]
    if len(values) > MAX_INPUTS:
        raise ValueError(f"El límite es {MAX_INPUTS} entradas.")
    groups, invalid = {}, []
    for code in values:
        if not ean_valid(code):
            invalid.append(code)
            continue
        groups.setdefault(ean_key(code), []).append(code)
    return {"total": len(values), "groups": groups, "invalid": invalid,
            "duplicates": sum(len(g) - 1 for g in groups.values())}


def _image_root(root):
    root = Path(root).resolve()
    check_image_root(root)
    if not root.is_dir() or not os.access(root, os.R_OK):
        raise OSError("raiz_imagenes_no_disponible")
    return root


def read_catalog(codes, catalog, root):
    """Verify originals, not just catalog metadata; errors do not imply absence."""
    try:
        root = _image_root(root)
        connection, _ = open_catalog(Path(catalog) if catalog else None)
        try:
            found = fetch_records(connection, codes)
        finally:
            connection.close()
        result = {}
        for code in codes:
            records = found.get(ean_key(code), [])
            try:
                report = coverage(records, DEFAULT_REQUIRED_VIEWS, root, True)
                report["views"] = [v for v in report["views"] if v in DEFAULT_REQUIRED_VIEWS]
                report["missing_views"] = [v for v in DEFAULT_REQUIRED_VIEWS if v not in report["views"]]
                report["label"] = ("completas" if not report["missing_views"] else
                                   "parciales" if report["views"] else
                                   "sin_vistas_validas" if records else "sin_registros")
            except (OSError, RuntimeError):
                report = unknown_coverage()
            result[ean_key(code)] = {"coverage": report, "records": records}
        return result, None
    except (CatalogUnavailable, OSError, ValueError, RuntimeError) as error:
        reason = error.code if isinstance(error, CatalogUnavailable) else "almacen_no_disponible"
        return {ean_key(c): {"coverage": unknown_coverage(), "records": []} for c in codes}, reason


PRODUCT_QUERY = text("""
    SELECT pp.id AS product_id, pp.barcode, pp.default_code, pt.id AS template_id,
           pt.name AS product, dpb.name AS brand
    FROM product_product pp JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN diagonal_product_brand dpb ON dpb.id = pt.brand_id
    WHERE pp.barcode IN :codes ORDER BY pp.id LIMIT :limit
""").bindparams(bindparam("codes", expanding=True))

SUPPLIER_QUERY = text("""
    SELECT DISTINCT si.product_tmpl_id AS template_id, p.id AS partner_id, p.name AS partner
    FROM product_supplierinfo si JOIN res_partner p ON p.id = si.partner_id
    WHERE si.product_tmpl_id IN :templates ORDER BY si.product_tmpl_id, p.id LIMIT :limit
""").bindparams(bindparam("templates", expanding=True))

PURCHASE_QUERY = text("""
    SELECT DISTINCT pol.product_id, p.id AS partner_id, p.name AS partner
    FROM purchase_order_line pol JOIN purchase_order po ON po.id = pol.order_id
    JOIN res_partner p ON p.id = po.partner_id
    WHERE pol.product_id IN :products AND po.state IN ('purchase', 'done')
      AND COALESCE(pol.display_type, '') = ''
    ORDER BY pol.product_id, p.id LIMIT :limit
""").bindparams(bindparam("products", expanding=True))


def read_odoo(engine, codes):
    forms = list(dict.fromkeys(form for code in codes for form in code_variants(code)))
    if not forms:
        return {}, {}, {}
    with engine.connect() as connection:
        products = _read_only(connection, PRODUCT_QUERY, {"codes": forms, "limit": MAX_ROWS + 1})
        if len(products) > MAX_ROWS:
            raise ValueError("demasiadas_variantes")
        templates = list({r["template_id"] for r in products})
        ids = list({r["product_id"] for r in products})
        suppliers = _read_only(connection, SUPPLIER_QUERY, {"templates": templates, "limit": MAX_ROWS + 1}) if ids else []
        purchases = _read_only(connection, PURCHASE_QUERY, {"products": ids, "limit": MAX_ROWS + 1}) if ids else []
    if len(suppliers) > MAX_ROWS or len(purchases) > MAX_ROWS:
        raise ValueError("demasiadas_relaciones")
    by_code, by_template, by_product = {}, {}, {}
    for row in products:
        by_code.setdefault(ean_key(row["barcode"]), []).append(dict(row))
    for row in suppliers:
        by_template.setdefault(row["template_id"], []).append(dict(row))
    for row in purchases:
        by_product.setdefault(row["product_id"], []).append(dict(row))
    return by_code, by_template, by_product


def route(products, suppliers, purchases, links, explicit_routes=()):
    """Never infer variant scope or current validity from a template-level supplier."""
    if not products:
        return {"status": "Sin correspondencia", "portal": "", "supplier": "",
                "reason": "EAN/UPC sin variante en Odoo.", "evidence": []}
    if len(products) != 1:
        return {"status": "Revisión", "portal": "", "supplier": "",
                "reason": "Varias variantes comparten esta identidad.", "evidence": []}
    product = products[0]
    evidence = []
    template = suppliers.get(product["template_id"], [])
    history = purchases.get(product["product_id"], [])
    partners = {r["partner_id"]: r["partner"] for r in [*template, *history]}
    if template:
        evidence.append("Relación proveedor–plantilla en Odoo; variante y vigencia no verificadas.")
    if history:
        evidence.append("Compras confirmadas históricas de la variante; no acreditan proveedor vigente.")
    explicit = [r for r in explicit_routes if r.get("confirmed") and r.get("product_id") == product["product_id"]
                and r.get("partner_id") and r.get("portal") in PORTALS and r.get("evidence")]
    possible = {(provider, int(partner))
                for provider, link in links.items() if provider in PORTALS and link.get("confirmed")
                for partner in link.get("partner_ids", []) if int(partner) in partners}
    explicit_set = {(r["portal"], r["partner_id"]) for r in explicit}
    explicit_partners = {r["partner_id"] for r in explicit}
    all_partners = set(partners) | explicit_partners
    if len(all_partners) > 1 or len(possible | explicit_set) > 1:
        return {"status": "Revisión", "portal": "", "supplier": ", ".join(sorted(set(partners.values()))),
                "reason": "Proveedores o portales múltiples o contradictorios.", "evidence": evidence}
    if len(explicit_set) == 1:
        portal, partner_id = next(iter(explicit_set))
        proof = next(r for r in explicit if (r["portal"], r["partner_id"]) == (portal, partner_id))
        return {"status": "Ruta confirmada", "portal": portal, "supplier": partners.get(partner_id, str(partner_id)),
                "reason": "Asociación explícita confirmada de variante, proveedor y portal.",
                "evidence": [*evidence, str(proof["evidence"])]}
    if not template and history:
        return {"status": "Revisión", "portal": "", "supplier": ", ".join(sorted(set(partners.values()))),
                "reason": "Las compras históricas por sí solas no acreditan proveedor vigente.", "evidence": evidence}
    if len(possible) == 1:
        portal, partner_id = next(iter(possible))
        brands = links[portal].get("brands", [])
        if brands and str(product.get("brand") or "").strip().casefold() not in {
            str(brand).strip().casefold() for brand in brands
        }:
            return {"status": "Revisión", "portal": "", "supplier": partners[partner_id],
                    "reason": "La marca no coincide con el ámbito confirmado del vínculo.", "evidence": evidence}
        evidence.append("Asociación proveedor–portal confirmada; no confirma esta variante.")
        return {"status": "Portal candidato", "portal": portal, "supplier": partners[partner_id],
                "reason": "Pendiente de validar variante, vigencia y ámbito de marca.", "evidence": evidence}
    return {"status": "Sin correspondencia", "portal": "", "supplier": ", ".join(sorted(set(partners.values()))),
            "reason": "Sin asociación proveedor–portal confirmada para esta relación.", "evidence": evidence}


def analyze(values, *, catalog, root, engine_factory, links_root, explicit_routes=()):
    parsed = group_inputs(values)
    codes = [g[0] for g in parsed["groups"].values()]
    if not codes:
        return {**parsed, "rows": [], "catalog_error": None, "odoo_error": None, "links_error": None}
    catalog_data, catalog_error = read_catalog(codes, catalog, root)
    try:
        products, suppliers, purchases = read_odoo(engine_factory(), codes)
        odoo_error = None
    except (SQLAlchemyError, OSError, ValueError, RuntimeError, TypeError) as error:
        products, suppliers, purchases = {}, {}, {}
        odoo_error = type(error).__name__
    try:
        links = load_links(links_root)
        if not isinstance(links, dict) or any(
            not isinstance(link, dict) or not isinstance(link.get("partner_ids", []), list)
            or not isinstance(link.get("brands", []), list)
            or any(not isinstance(partner, int) or isinstance(partner, bool) for partner in link.get("partner_ids", []))
            for link in links.values()
        ):
            raise ValueError("formato_invalido")
    except (OSError, ValueError, json.JSONDecodeError):
        links, links_error = {}, "asociaciones_no_disponibles"
    else:
        links_error = None
    rows = []
    for key, originals in parsed["groups"].items():
        matches = products.get(key, [])
        if odoo_error:
            proposal = {"status": "Odoo no disponible", "portal": "", "supplier": "",
                        "reason": "No se pudo consultar Odoo; revisar antes de adquirir.", "evidence": []}
        else:
            proposal = route(matches, suppliers, purchases, links, explicit_routes)
            if links_error and matches:
                proposal = {**proposal, "status": "Revisión", "portal": "",
                            "reason": "Asociaciones proveedor–portal no disponibles; requiere revisión."}
        rows.append({"code": originals[0], "equivalents": list(dict.fromkeys(originals)),
                     "product": matches[0] if len(matches) == 1 else None,
                     **catalog_data[key], "proposal": proposal})
    return {**parsed, "rows": rows, "catalog_error": catalog_error, "odoo_error": odoo_error,
            "links_error": links_error}


def photo_records(rows, root):
    root = _image_root(root)
    result = []
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or "staging" in relative.parts:
            continue
        path = (root / relative).resolve()
        if root not in path.parents:
            continue
        result.append(ImageRecord(id=row["record_id"], ean=row["ean"], checksum=row["checksum"],
                                  pixels=row["pixels"], path=path, provider=row["provider"], origin=row["origin"],
                                  view=row["view"], market=row["market"], name=row["name"], date=row["date"],
                                  metadata={}))
    return result


def verified_photo(record):
    if record.path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("archivo_demasiado_grande")
    content = record.path.read_bytes()
    if hashlib.sha256(content).hexdigest() != record.checksum or image_fingerprint(content) != record.pixels:
        raise ValueError("archivo_modificado")
    return content
