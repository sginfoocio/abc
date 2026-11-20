"""Generate an offline, documentary 38×14 coverage matrix; never performs shop requests."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

SOURCE_MATRIX = Path("docs/Precios/100_EAN/MATRIZ_100_EAN_14_COMPETIDORES.csv")
OUTPUT_MATRIX = Path("docs/Precios/busqueda_38_20261119/MATRIZ_38_EAN_14_COMPETIDORES.csv")
NO_ROUTE_SHOPS = {"CELINEOPTICIENLUNETIER": "celine"}
BLOCKER = (
    "Consulta no ejecutada: el entorno no tiene acceso verificable a continuidad, trabajador, "
    "estado/cola y recibos compartidos. Las rutas aportadas son candidatas documentales, no lecturas vigentes."
)
FIELDS = (
    "ean", "competitor_id", "competitor", "documented_routes_count", "documented_routes",
    "route_evidence_date", "current_read_status", "current_read_at", "current_identity",
    "current_price", "current_currency", "current_market", "current_tax_included",
    "current_availability", "current_comparable", "current_receipt_ids", "reason",
    "historical_source_url", "historical_evidence_date", "historical_known_state",
    "historical_identity_verified", "historical_saved_read", "historical_comparable_offer",
    "historical_receipt_ids",
)


def _runtime_dependencies():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from price_comparator import normalize_gtin
    from price_references import key
    from price_url_candidates import DOMAINS, ROUTE_EXAMPLES, ROUTE_EVIDENCE_DATE, ROUTE_EVIDENCE_SOURCE

    return normalize_gtin, key, DOMAINS, ROUTE_EXAMPLES, ROUTE_EVIDENCE_DATE, ROUTE_EVIDENCE_SOURCE


def _shop_labels(package: dict, domains: dict, key_function) -> dict[str, str]:
    labels: dict[str, str] = {}
    for product in package["productos"]:
        entries = [(product["competidor_principal"], product["url_principal"])]
        entries.extend((row["competidor"], row["url"]) for row in product.get("referencias_adicionales", []))
        for label, url in entries:
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            shop = domains.get(host)
            if not shop:
                raise ValueError(f"Ruta fuera del registro de competidores: {url}")
            normalized = key_function(label)
            if normalized in labels and labels[normalized] != shop:
                raise ValueError(f"Etiqueta de competidor ambigua: {label}")
            labels[normalized] = shop
    return labels


def build_rows(root: Path = ROOT) -> list[dict[str, str]]:
    normalize_gtin, key, domains, route_examples, evidence_date, evidence_source = _runtime_dependencies()
    source_path = root / evidence_source
    package = json.loads(source_path.read_text(encoding="utf-8"))
    products = package.get("productos")
    if package.get("fecha_revision") != evidence_date or not isinstance(products, list) or len(products) != 38:
        raise ValueError("El paquete de rutas debe contener los 38 productos revisados el 2026-11-19.")

    eans = [normalize_gtin(product.get("ean")) for product in products]
    if len(set(eans)) != 38:
        raise ValueError("El paquete debe conservar 38 EAN únicos.")
    labels = _shop_labels(package, domains, key)
    if len(route_examples) != 130:
        raise ValueError("El registro de rutas documentales debe contener 130 referencias.")

    routes_by_pair: dict[tuple[str, str], list[dict]] = {}
    for example in route_examples:
        metadata = dict(example.documentary_attributes)
        route = {
            "source_evidence_id": example.id,
            "source_file": example.source.split("#", 1)[0],
            "source_date": example.review_date,
            "role": metadata["route_role"],
            "url": example.url,
            "evidence_level": metadata["evidence_level"],
            "language": metadata["route_language"],
            "market_hint": metadata["route_market_hint"],
            "documentary_note": metadata["documentary_note"],
            "numeric_references": [
                value for value in metadata["documented_numeric_references"].split(" | ") if value
            ],
        }
        routes_by_pair.setdefault((example.source_ean, example.shop), []).append(route)

    with (root / SOURCE_MATRIX).open(encoding="utf-8-sig", newline="") as stream:
        history = list(csv.DictReader(stream, delimiter=";"))
    history_pairs = {(row["ean"], row["competitor_id"]) for row in history}
    if len(history) != 1400 or len(history_pairs) != 1400:
        raise ValueError("La matriz de origen debe contener exactamente 1.400 pares únicos.")
    history_eans = {row["ean"] for row in history}
    competitor_ids = {row["competitor_id"] for row in history}
    if len(history_eans) != 100 or len(competitor_ids) != 14 or not set(eans) <= history_eans:
        raise ValueError("Los 38 EAN deben pertenecer a la selección histórica de 100×14.")

    rows = []
    target_eans = set(eans)
    for prior in history:
        ean = prior["ean"]
        if ean not in target_eans:
            continue
        shop = labels.get(key(prior["competitor"])) or NO_ROUTE_SHOPS.get(key(prior["competitor"]))
        if not shop:
            raise ValueError(f"No se puede mapear el competidor {prior['competitor']} al paquete.")
        routes = routes_by_pair.get((ean, shop), [])
        reasons = [BLOCKER]
        if not routes:
            reasons.append("El paquete no contiene una ruta para este par; eso no demuestra inexistencia comercial.")
        if any(route["market_hint"] == "non_ES" for route in routes):
            reasons.append("Las rutas con locale explícito de otro mercado se conservan, pero no acreditan el mercado español.")
        if prior["saved_read"].casefold() in {"sí", "si"}:
            reasons.append("Hay lectura histórica; no se reutilizó ni se rejuveneció.")
        rows.append({
            "ean": ean,
            "competitor_id": prior["competitor_id"],
            "competitor": prior["competitor"],
            "documented_routes_count": str(len(routes)),
            "documented_routes": json.dumps(routes, ensure_ascii=False, separators=(",", ":")),
            "route_evidence_date": evidence_date,
            "current_read_status": "no_ejecutada",
            "current_read_at": "",
            "current_identity": "no_verificada",
            "current_price": "",
            "current_currency": "",
            "current_market": "",
            "current_tax_included": "",
            "current_availability": "",
            "current_comparable": "no_verificada",
            "current_receipt_ids": "",
            "reason": " ".join(reasons),
            "historical_source_url": prior["source_url"],
            "historical_evidence_date": prior["evidence_date"],
            "historical_known_state": prior["known_state"],
            "historical_identity_verified": prior["identity_verified"],
            "historical_saved_read": prior["saved_read"],
            "historical_comparable_offer": prior["comparable_offer_vigente"],
            "historical_receipt_ids": prior["receipt_ids"],
        })
    if len(rows) != 532 or len({(row["ean"], row["competitor_id"]) for row in rows}) != 532:
        raise ValueError("La matriz resultante debe tener exactamente 532 pares únicos.")
    return rows


def write_matrix(root: Path = ROOT) -> Path:
    destination = root / OUTPUT_MATRIX
    rows = build_rows(root)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(write_matrix(args.root))
