"""Rebuild local route metadata in the fixed 100-EAN x six-store coverage matrix."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from price_url_candidates import (  # noqa: E402
    MANUAL_COVERAGE_EVIDENCE,
    VISIO_FORMAT_EVIDENCE,
    candidate_plan,
)
from price_comparator import normalize_gtin  # noqa: E402
from price_references import model_key  # noqa: E402

MATRIX = ROOT / "docs" / "PRICE_COVERAGE_100_EAN_6_STORES.csv"
EAN_SUMMARY = ROOT / "docs" / "PRICE_COVERAGE_EAN_SUMMARY_100.csv"
MEASUREMENT_EVIDENCE = ROOT / "docs" / "PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv"
DOMAINS = {
    "optica-optima-com": "optica-optima.com",
    "amevista-com": "amevista.com",
    "visiofactory-es": "visiofactory.es",
    "edel-optics-es": "edel-optics.es",
    "domanci-com": "domanci.com",
    "congafasdesol-com": "congafasdesol.com",
}
EXPECTED_STORES = {
    "Óptica Óptima",
    "Amevista",
    "Visio Factory",
    "Edel-Optics",
    "Domanci",
    "ConGafasDeSol",
}
VISIO_FAMILIES = {("TOMFORD", "FT1044"), ("MIUMIU", "MU52YS"), ("RAYBAN", "RB2140")}
NEW_FIELDS = (
    "calibre",
    "puente",
    "fuente_medidas",
    "asociacion_medidas",
    "atributos_disponibles",
    "metodo_descubrimiento",
    "fuente_evidencia_patron",
    "alcance_patron",
    "ruta_o_metodo_disponible",
    "candidata_en_plan_local",
    "urls_en_plan_local",
    "consulta_gobernada_elegible_localmente",
    "url_final_observada_esta_revision",
    "elegibilidad_local",
    "motivo_exclusion_o_pendiente",
    "datos_pendientes",
)
DEPRECATED_FIELDS = {
    "candidata_generada_esta_revision",
    "urls_candidatas_generadas_esta_revision",
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _route_metadata(row: dict[str, str]) -> dict[str, str]:
    product = {
        "ean": row["ean"],
        "brand": row["marca"],
        "model": row["modelo"],
        "color": row["color"],
        "category": row["categoria"],
        "caliber": row.get("calibre", ""),
        "bridge": row.get("puente", ""),
        "measurement_source": row.get("fuente_medidas", ""),
    }
    plan = candidate_plan(product, DOMAINS[row["competidor_id"]])
    candidates = plan.candidates
    runnable = plan.runnable
    candidate_urls = list(dict.fromkeys(candidate.url for candidate in candidates))
    rules = list(dict.fromkeys(candidate.rule for candidate in candidates))
    evidence = list(dict.fromkeys(item for candidate in candidates for item in candidate.evidence))
    reason = "; ".join(dict.fromkeys(candidate.reason for candidate in candidates if candidate.reason))
    eligibility = ""
    pattern_state = ""
    method_available = bool(candidates or row["url_historica"] or row["url_candidata"])
    pending = ""

    shop = row["competidor_id"]
    brand = "".join(character for character in row["marca"].upper() if character.isalnum())
    model = model_key(row["modelo"])
    category = "sol" if "sol" in row["categoria"].casefold() else "montura"

    if candidates:
        method = " | ".join(rules)
        if not evidence:
            evidence = [row["evidencia_regla"]] if row["evidencia_regla"] else []
        if runnable:
            eligibility = "candidate_for_governed_ficha_check"
            reason = "Candidata local; no es autorización, ficha verificada ni oferta comparable."
            pending = "Identidad/EAN/variante, precio, mercado, IVA, disponibilidad, acceso y vigencia."
        else:
            eligibility = "review_required"
            pending = "Resolver la contradicción o cautela documentada antes de cualquier consulta."
        pattern_state = (
            "partial" if any(candidate.origin == MANUAL_COVERAGE_EVIDENCE for candidate in candidates)
            or any(candidate.rule.startswith("documented_url:") for candidate in candidates)
            else "confirmed_within_scope"
        )
    elif shop == "domanci-com" and plan.reason_code == "missing_attribute":
        method = "supplied_pattern:domanci"
        evidence = ["user:domanci-route-2026-11-06", "E032", "E033"]
        pattern_state = "partial"
        eligibility = "missing_attributes"
        reason = plan.reason
        pending = "Calibre vinculado a este EAN; no consta en Productos_100.csv."
        method_available = True
    elif (shop == "visiofactory-es" and category == "sol"
          and (brand, model) in VISIO_FAMILIES
          and plan.reason_code == "missing_attribute"):
        method = "sample_pattern:visio"
        evidence = [VISIO_FORMAT_EVIDENCE]
        pattern_state = "partial"
        eligibility = "missing_attributes"
        reason = plan.reason
        pending = "Calibre" + (" y puente" if brand != "RAYBAN" else " (y puente para colores slash)")
        pending += "; no constan en Productos_100.csv."
        method_available = True
    elif row["url_historica"] or row["url_candidata"]:
        method = "historical_or_matrix_candidate_url"
        evidence = [row["evidencia_regla"]] if row["evidencia_regla"] else []
        pattern_state = "partial"
        eligibility = "historical_or_candidate_requires_governed_check"
        reason = row["motivo_pendiente"] or "Enlace conservado; no acredita estado actual."
        pending = "Resolver identidad, variante, acceso y vigencia con el flujo autorizado."
    else:
        method = "authorized_discovery_required"
        evidence = [row["evidencia_regla"]] if row["evidencia_regla"] else []
        pattern_state = "not_verifiable"
        eligibility = "no_local_candidate"
        reason = plan.reason or row["motivo_pendiente"] or "No hay ruta local documentada; no implica inexistencia."
        pending = "URL/handle/alias o fuente autorizada que vincule la ficha con el producto."

    if not reason:
        reason = "Método local disponible; la candidata aún requiere comprobación gobernada."
    attributes = {
        "ean": row["ean"],
        "marca": row["marca"],
        "modelo": row["modelo"],
        "color": row["color"],
        "categoria": row["categoria"],
        "calibre": row.get("calibre", "") or "not_provided_in_matrix_source",
        "puente": row.get("puente", "") or "not_provided_in_matrix_source",
    }
    return {
        "fuente_medidas": row.get("fuente_medidas", ""),
        "asociacion_medidas": row.get("asociacion_medidas", ""),
        "atributos_disponibles": _json(attributes),
        "metodo_descubrimiento": method,
        "fuente_evidencia_patron": "; ".join(evidence),
        "alcance_patron": pattern_state,
        "ruta_o_metodo_disponible": str(method_available).lower(),
        "candidata_en_plan_local": str(bool(candidates)).lower(),
        "urls_en_plan_local": _json(candidate_urls),
        "consulta_gobernada_elegible_localmente": str(bool(runnable)).lower(),
        "url_final_observada_esta_revision": row.get("url_final_observada_esta_revision", ""),
        "elegibilidad_local": eligibility,
        "motivo_exclusion_o_pendiente": reason,
        "datos_pendientes": pending,
    }


def main() -> None:
    with MATRIX.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        rows = list(reader)
        original_fields = reader.fieldnames or []
    pairs = {(row["ean"], row["competidor_id"]) for row in rows}
    eans = {row["ean"] for row in rows}
    stores = {row["competidor"] for row in rows}
    if (len(rows) != 600 or len(eans) != 100 or len(pairs) != 600
            or stores != EXPECTED_STORES
            or any(sum(row["competidor"] == store for row in rows) != 100 for store in stores)):
        raise ValueError("La matriz debe conservar exactamente 100 EAN x seis tiendas sin pares duplicados.")

    with MEASUREMENT_EVIDENCE.open(encoding="utf-8-sig", newline="") as stream:
        measurement_rows = list(csv.DictReader(stream, delimiter=";"))
    measurements = {}
    products_by_ean = {}
    for row in rows:
        products_by_ean.setdefault(row["ean"], set()).add(
            (row["marca"], row["modelo"], row["color"], row["categoria"])
        )
    for evidence in measurement_rows:
        ean = normalize_gtin(evidence["ean"])
        if ean != evidence["ean"] or ean in measurements:
            raise ValueError(f"EAN de evidencia de medidas no canónico o duplicado: {evidence['ean']}")
        if ean not in products_by_ean:
            raise ValueError(f"EAN de evidencia de medidas fuera del manifiesto: {ean}")
        expected = (evidence["marca"], evidence["modelo"], evidence["color"])
        actual = {(brand, model, color) for brand, model, color, _ in products_by_ean[ean]}
        if actual != {expected}:
            raise ValueError(f"Los atributos de evidencia de medidas no corresponden al EAN {ean}.")
        if not evidence["fuente_medidas"] or not evidence["asociacion_medidas"]:
            raise ValueError(f"Falta procedencia de calibre/puente para el EAN {ean}.")
        measurements[ean] = evidence
    for row in rows:
        evidence = measurements.get(row["ean"])
        if evidence:
            for field in ("calibre", "puente"):
                source_value = evidence[field]
                if row.get(field) and row[field] != source_value:
                    raise ValueError(f"Medida existente contradictoria para {row['ean']}: {field}")
                row[field] = source_value
            row["fuente_medidas"] = evidence["fuente_medidas"]
            row["asociacion_medidas"] = evidence["asociacion_medidas"]
        else:
            row.setdefault("fuente_medidas", "")
            row.setdefault("asociacion_medidas", "")

    fields = [field for field in original_fields if field not in DEPRECATED_FIELDS]
    fields += [field for field in NEW_FIELDS if field not in fields]
    for row in rows:
        for field in DEPRECATED_FIELDS:
            row.pop(field, None)
        row.update(_route_metadata(row))
    with MATRIX.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    summary_fields = (
        "ean",
        "marca",
        "modelo",
        "color",
        "categoria",
        "calibre",
        "puente",
        "tiendas_con_ruta_o_candidato_antes",
        "tiendas_con_ruta_o_metodo_despues",
        "tiendas_con_candidata_en_plan_local",
        "tiendas_elegibles_para_consulta_gobernada",
        "competidores_con_ruta_antes",
        "competidores_con_ruta_o_metodo_despues",
        "competidores_con_candidata_en_plan_local",
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["ean"], []).append(row)
    summary = []
    for ean, product_rows in grouped.items():
        attributes = {
            tuple(row[key] for key in ("marca", "modelo", "color", "categoria"))
            for row in product_rows
        }
        if len(product_rows) != 6 or len(attributes) != 1:
            raise ValueError(f"Atributos inconsistentes para EAN {ean}.")
        before = [row for row in product_rows if row["url_historica"] or row["url_candidata"]]
        after = [row for row in product_rows if row["ruta_o_metodo_disponible"] == "true"]
        planned = [row for row in product_rows if row["candidata_en_plan_local"] == "true"]
        eligible = [
            row for row in product_rows
            if row["consulta_gobernada_elegible_localmente"] == "true"
        ]
        first = product_rows[0]
        summary.append({
            "ean": ean,
            "marca": first["marca"],
            "modelo": first["modelo"],
            "color": first["color"],
            "categoria": first["categoria"],
            "calibre": first.get("calibre", ""),
            "puente": first.get("puente", ""),
            "tiendas_con_ruta_o_candidato_antes": str(len(before)),
            "tiendas_con_ruta_o_metodo_despues": str(len(after)),
            "tiendas_con_candidata_en_plan_local": str(len(planned)),
            "tiendas_elegibles_para_consulta_gobernada": str(len(eligible)),
            "competidores_con_ruta_antes": _json([row["competidor"] for row in before]),
            "competidores_con_ruta_o_metodo_despues": _json([row["competidor"] for row in after]),
            "competidores_con_candidata_en_plan_local": _json([row["competidor"] for row in planned]),
        })
    if len(summary) != 100:
        raise ValueError("El resumen debe conservar los mismos 100 EAN.")
    with EAN_SUMMARY.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)

    print(f"Actualizadas {len(rows)} pares y {len(summary)} EAN; no se realizaron consultas HTTP.")


if __name__ == "__main__":
    main()
