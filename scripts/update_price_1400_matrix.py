"""Regenerate the exact 100-EAN x 14-competitor offline matrix."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from competitor_requirements import COMPETITORS, requirement_for

MANIFEST = ROOT / "price_trial_manifest.json"
SIX_MATRIX = ROOT / "docs" / "PRICE_COVERAGE_100_EAN_6_STORES.csv"
OUT = ROOT / "docs" / "Precios" / "100_EAN" / "MATRIZ_100_EAN_14_COMPETIDORES.csv"
SUMMARY = ROOT / "docs" / "Precios" / "100_EAN" / "MATRIZ_100_EAN_14_RESUMEN.json"
HISTORICAL_PACKAGE = ROOT / "docs" / "Precios" / "100_EAN" / "Paquete_revision_7_casos_20261117.zip"

COMPETITOR_IDS = {
    "Óptica Óptima": "optica-optima-com",
    "Óptica Bassol": "opticabassol-com",
    "Blickers": "blickers-com",
    "Visual-Click": "visual-click-com",
    "Edel-Optics": "edel-optics-es",
    "Amevista": "amevista-com",
    "Mia Burton": "mia-burton-com",
    "Optical H": "opticalh-com",
    "Domanci": "domanci-com",
    "Óptica Universitaria": "optica-universitaria-es",
    "ConGafasDeSol": "congafasdesol-com",
    "Céline Opticien Lunetier": "celine-opticien-lunetier-com",
    "Visio Factory": "visiofactory-es",
    "Otticait Eyewear": "otticait-eyewear-com",
}

FIELDS = (
    "ean", "competitor_id", "competitor", "source_url", "evidence_date",
    "known_state", "concrete_rejection", "applicable_correction", "proposed_action",
    "requires_new_http", "candidate", "identity_verified", "saved_read",
    "comparable_offer_vigente", "evidence_references", "request_id",
    "receipt_ids", "pending_limits",
)


def _rows():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    eans = manifest["eans"]
    if len(eans) != 100 or len(set(eans)) != 100:
        raise ValueError("El manifiesto debe contener 100 EAN únicos")
    if not all(ean.isascii() and ean.isdigit() and len(ean) == 13 for ean in eans):
        raise ValueError("Todos los EAN deben tener 13 dígitos y conservar ceros iniciales")

    historical = {}
    if SIX_MATRIX.exists():
        with SIX_MATRIX.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream, delimiter=";"):
                ean = row["ean"]
                name = row["competidor"]
                historical[(ean, name)] = row

    package_entries = {}
    if HISTORICAL_PACKAGE.exists():
        import zipfile
        with zipfile.ZipFile(HISTORICAL_PACKAGE) as archive:
            source = json.loads(archive.read("revision-7-casos-original.json"))
        for case in source["cases"]:
            package_entries[(case["ean"], case["competitor_id"])] = case

    pairs = []
    for ean in eans:
        for name, site in COMPETITORS:
            cid = COMPETITOR_IDS[name]
            restrictions, required = requirement_for(name)
            historical_row = None
            for candidate_name in ("Óptica Óptima", "Óptica Bassol", "Blickers", "Visual-Click",
                                   "Edel-Optics", "Amevista", "Domanci", "ConGafasDeSol",
                                   "Visio Factory"):
                if candidate_name == name:
                    historical_row = historical.get((ean, name))
                    break
            case = package_entries.get((ean, cid))
            source_url = case["url"] if case else (historical_row or {}).get("url_historica") \
                or (historical_row or {}).get("url_candidata") or ""
            evidence_date = "2026-11-17T22:45:34Z" if case else \
                ((historical_row or {}).get("fecha_evidencia") or "2026-10-30")
            if case:
                sample = case.get("sample") or {}
                page = sample.get("page") or {}
                identity = bool(page.get("ean_published") == ean and not page.get("ambiguous"))
                if not identity:
                    identity = any(
                        fact.get("facts", {}).get("ean_published") == ean
                        and not fact.get("facts", {}).get("ambiguous")
                        for fact in case.get("discovery_facts", [])
                    )
                saved_read = bool(case.get("sample") or case.get("previous_sample") or case.get("discovery_facts"))
                if cid == "opticalh-com" and identity:
                    state = "Lectura guardada no comparable"
                    rejection = "Mercado comercial vacío en la muestra; configuración ES/IVA incluida posterior"
                    correction = "Reevaluación local auditada; no renueva fecha ni crea oferta"
                    action = "reevaluación local"
                else:
                    state = "Evidencia histórica / rechazo"
                    rejection = case.get("detail") or "Motivo disponible en el paquete histórico"
                    correction = "Resolver identidad o captura gobernada antes de una lectura"
                    action = "revisión"
                comparable = "no"
                receipts = json.dumps(case.get("receipts") or [], ensure_ascii=False)
                request_id = str(case.get("id", ""))
            else:
                state = "No verificada"
                identity = False
                saved_read = False
                comparable = "no"
                rejection = restrictions
                correction = required
                action = "ninguna" if cid not in {"opticalh-com"} else "reevaluación local si existe muestra"
                receipts = "[]"
                request_id = ""

            pairs.append({
                "ean": ean, "competitor_id": cid, "competitor": name,
                "source_url": source_url,
                "evidence_date": evidence_date,
                "known_state": state,
                "concrete_rejection": rejection,
                "applicable_correction": correction,
                "proposed_action": action,
                "requires_new_http": "sí" if action in {"captura", "recuperación"} else "no",
                "candidate": "sí" if source_url else "no",
                "identity_verified": "sí" if identity else "no",
                "saved_read": "sí" if saved_read else "no",
                "comparable_offer_vigente": comparable,
                "evidence_references": "Paquete_revision_7_casos_20261117.zip" if case
                                      else "cobertura_competidores.json / 2026-10-30",
                "request_id": request_id,
                "receipt_ids": receipts,
                "pending_limits": restrictions + " " + required,
            })
    return manifest, pairs


def main():
    manifest, pairs = _rows()
    unique = {(row["ean"], row["competitor_id"]) for row in pairs}
    if len(pairs) != 1400 or len(unique) != 1400:
        raise ValueError("La matriz debe contener exactamente 1.400 pares únicos")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(pairs)
    summary = {
        "snapshot_exported_at": "2026-11-17T22:45:34Z",
        "historical_only": True,
        "eans": len(manifest["eans"]),
        "competitors": len(COMPETITORS),
        "pairs": len(pairs),
        "unique_pairs": len(unique),
        "technical_coverage": sum(row["candidate"] == "sí" for row in pairs),
        "identity_verified": sum(row["identity_verified"] == "sí" for row in pairs),
        "saved_reads": sum(row["saved_read"] == "sí" for row in pairs),
        "comparable_current": sum(row["comparable_offer_vigente"] == "sí" for row in pairs),
        "matrix": str(OUT.relative_to(ROOT)).replace("\\", "/"),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
