"""Local URL hypotheses from the 2026-11-04 documentary review, NOT captured HTTP or authorisation.

Examples keep their original locale/query. Neither a slug nor these indexed identity claims verifies a page.
Only the existing governed ficha reader and commercial classifier may do that in an approved later run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import csv
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from price_comparator import normalize_gtin
from price_references import compare_calibers, extract_caliber, key, model_key, normalize_caliber, tokens

REVIEW_DATE = "2026-11-04"
REVIEW_BASE = "92243628b4e925ba9d549b076580726c383a43ac"
EVIDENCE_SOURCE = "Diagonal_evidencia_Copilot/Evidencias_47.csv"
SUPPLIED_PATTERN_DATE = "2026-11-06"
MANUAL_COVERAGE_EVIDENCE = "User-supplied manual observations; not captured HTTP or verified identity"
AMEVISTA_MANUAL_EVIDENCE = {
    ("MIUMIU", "sol"): "manual:amevista-miu-miu-sunglasses",
    ("MIUMIU", "montura"): "manual:amevista-miu-miu-frames",
    ("BOTTEGAVENETA", "sol"): "manual:amevista-bottega-veneta-sunglasses",
}
AMEVISTA_RAYBAN_EVIDENCE = "manual:amevista-ray-ban-rb2140-rb4472-routes"
EDEL_MANUAL_EVIDENCE = "manual:edel-selected-variant-observations"
VISIO_FORMAT_EVIDENCE = "user:visio-public-fiche-format-examples"
MEASUREMENT_EVIDENCE_PATH = Path(__file__).resolve().parent / "docs" / "PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv"
MEASUREMENT_ASSOCIATIONS = {
    "gtin_exact_match",
    "ean_specific_manual",
    "ean_variant_corroborated",
    "ean_caliber_corroborated",
    "indexed_upc_selected_size",
    "model_color_only",
}
SUPPLIED_PATTERNS = {
    "domanci": (
        "https://domanci.com/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html",
    ),
}


@dataclass(frozen=True)
class Example:
    id: str
    shop: str
    brand: str
    model: str
    color: str
    category: str
    url: str
    gtins: tuple[str, ...] = ()
    caution: str = ""
    source: str = EVIDENCE_SOURCE
    review_date: str = REVIEW_DATE
    review_base: str = REVIEW_BASE
    route_attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AttributeResolution:
    product: dict[str, Any]
    evidence: dict[str, str] | None = None
    conflict: str = ""


@lru_cache(maxsize=1)
def _measurement_evidence() -> dict[str, dict[str, str]]:
    required = {"ean", "marca", "modelo", "color", "calibre", "puente",
                "fuente_medidas", "asociacion_medidas", "limitacion"}
    records: dict[str, dict[str, str]] = {}
    with MEASUREMENT_EVIDENCE_PATH.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("El CSV de medidas no cumple el contrato de columnas.")
        for line, row in enumerate(reader, 2):
            ean = normalize_gtin(row["ean"])
            if ean != row["ean"].strip() or ean in records:
                raise ValueError(f"EAN de medidas no canónico o duplicado en fila {line}: {row['ean']}")
            caliber = normalize_caliber(row["calibre"])
            bridge = row["puente"].strip()
            if not all(row[name].strip() for name in
                       ("marca", "modelo", "color", "fuente_medidas", "asociacion_medidas", "limitacion")):
                raise ValueError(f"Falta identidad o procedencia de medidas en fila {line}.")
            if not caliber or row["asociacion_medidas"].strip() not in MEASUREMENT_ASSOCIATIONS:
                raise ValueError(f"Calibre o asociación de medidas no reconocidos en fila {line}.")
            if bridge and (not re.fullmatch(r"\d{1,2}", bridge)
                           or (caliber.partition("-")[2] and caliber.partition("-")[2] != bridge)):
                raise ValueError(f"Puente no válido o contradictorio en fila {line}.")
            records[ean] = {name: value.strip() for name, value in row.items() if name and value is not None}
    return records


def documented_measurement_records() -> dict[str, dict[str, str]]:
    """Return a copy of the validated, EAN-scoped measurement evidence registry."""
    return {ean: dict(record) for ean, record in _measurement_evidence().items()}


def resolve_product_attributes(product: Mapping[str, Any]) -> AttributeResolution:
    """Overlay only validated dimensions documented for this exact EAN and catalogue identity."""
    resolved = dict(product)
    ean = normalize_gtin(resolved.get("ean"))
    evidence = _measurement_evidence().get(ean)
    if evidence is None:
        return AttributeResolution(resolved)
    if evidence["asociacion_medidas"] == "model_color_only":
        if resolved.get("measurement_source") == evidence["fuente_medidas"]:
            resolved.pop("caliber", None)
            resolved.pop("bridge", None)
        resolved["measurement_source"] = evidence["fuente_medidas"]
        resolved["measurement_association"] = evidence["asociacion_medidas"]
        resolved["measurement_limitation"] = evidence["limitacion"]
        return AttributeResolution(resolved, dict(evidence))

    identity_matches = (
        key(resolved.get("brand")) == key(evidence["marca"])
        and model_key(resolved.get("model")) == model_key(evidence["modelo"])
        and key(resolved.get("color")) == key(evidence["color"])
    )
    if not identity_matches:
        return AttributeResolution(
            resolved, dict(evidence),
            f"La evidencia de medidas para {ean} contradice marca, modelo o color del catálogo.",
        )

    existing_caliber = normalize_caliber(resolved.get("caliber"))
    evidence_caliber = normalize_caliber(evidence["calibre"])
    if resolved.get("caliber") and not existing_caliber:
        return AttributeResolution(resolved, dict(evidence), f"Calibre de catálogo no reconocido para {ean}.")
    if existing_caliber and compare_calibers(existing_caliber, evidence_caliber) == "different":
        return AttributeResolution(resolved, dict(evidence), f"Calibre de catálogo contradictorio para {ean}.")

    existing_bridge = str(resolved.get("bridge") or "").strip()
    evidence_bridge = evidence["puente"]
    if existing_bridge and not re.fullmatch(r"\d{1,2}", existing_bridge):
        return AttributeResolution(resolved, dict(evidence), f"Puente de catálogo no reconocido para {ean}.")
    if existing_bridge and evidence_bridge and existing_bridge != evidence_bridge:
        return AttributeResolution(resolved, dict(evidence), f"Puente de catálogo contradictorio para {ean}.")
    if resolved.get("measurement_source") and resolved["measurement_source"] != evidence["fuente_medidas"]:
        return AttributeResolution(resolved, dict(evidence), f"Procedencia de medidas contradictoria para {ean}.")
    if (resolved.get("measurement_association")
            and resolved["measurement_association"] != evidence["asociacion_medidas"]):
        return AttributeResolution(resolved, dict(evidence), f"Asociación de medidas contradictoria para {ean}.")

    if not existing_caliber or (existing_caliber.isdigit() and "-" in evidence_caliber):
        resolved["caliber"] = evidence_caliber
    if not existing_bridge and evidence_bridge:
        resolved["bridge"] = evidence_bridge
    resolved["measurement_source"] = evidence["fuente_medidas"]
    resolved["measurement_association"] = evidence["asociacion_medidas"]
    resolved["measurement_limitation"] = evidence["limitacion"]
    return AttributeResolution(resolved, dict(evidence))


# Transcribed documentary URLs, not fixtures purporting to be captured shop responses.
EXAMPLES = (
    Example("E001", "optima", "Prada", "PR 27ZS", "11P60C", "sol",
            "https://optica-optima.com/gafas-de-sol/prada-pr-27zs-11p60c.html"),
    Example("E002", "optima", "Tom Ford", "FT0711", "56E", "sol",
            "https://optica-optima.com/gafas-de-sol/tom-ford-ft0711-56e.html", ("889214029638",)),
    Example("E003", "optima", "Tom Ford", "FT5634-B", "001", "montura",
            "https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html"),
    Example("E004", "bassol", "Prada", "PR 27ZS", "19J09Z", "sol",
            "https://www.opticabassol.com/en-us/products/prada-pr-27zs-19j09z-54"),
    Example("E005", "bassol", "Prada", "PR 27ZS", "16K70A", "sol",
            "https://www.opticabassol.com/products/prada-pr-27zs-16k70a-54"),
    Example("E006", "bassol", "Miu Miu", "MU 01XV", "12W1O1", "montura",
            "https://www.opticabassol.com/en/products/miu-miu-mu-01xv-12w1o1-50"),
    Example("E007", "bassol", "Gucci", "GG1660S", "001", "sol",
            "https://www.opticabassol.com/products/gucci-gg1660s-001-54"),
    Example("E008", "blickers", "Prada", "PR 27ZS", "11L08Z", "sol",
            "https://www.blickers.com/es-es/gafas/prada-pr-27zs-c54/5313688741478400/"),
    Example("E009", "blickers", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.blickers.com/en-gb/glasses/prada-pr-27zs-c54/5649966192263168/"),
    Example("E010", "blickers", "Prada", "PR 27ZS", "19J09Z", "sol",
            "https://www.blickers.com/en-us/glasses/prada-pr-27zs-c54/6223251850264576/"),
    Example("E011", "visual", "Miu Miu", "MU 01XV", "VAU1O1", "montura",
            "https://visual-click.com/products/miu-miu-mu-01xv-vau1o1-50"),
    Example("E012", "visual", "Miu Miu", "MU 01XV", "1AB1O1", "montura",
            "https://visual-click.com/en/products/miu-miu-mu-01xv-1ab1o1-50"),
    Example("E013", "visual", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://visual-click.com/en-eu/products/prada-pr-27zs-16k08z-54"),
    Example("E014", "edel", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958",
            ("8056597875349",)),
    Example("E015", "edel", "Tom Ford", "FT0711", "01N", "sol",
            "https://www.edel-optics.es/FT0711-01N-de-Tom-Ford.html?eo_campaign=feed-ES&eo_medium=shop&eo_source=google-shopping&selectedSize=2037438",
            ("889214551740",)),
    Example("E016", "edel", "Tom Ford", "FT0711", "52F", "sol",
            "https://www.edel-optics.es/FT0711-52F-de-Tom-Ford.html", ("889214369024",)),
    Example("E017", "amevista", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.amevista.com/es/prada-0pr-27zs-16k08z"),
    Example("E018", "amevista", "Prada", "PR 27ZS", "19J09Z", "sol",
            "https://www.amevista.com/es/prada-pr-27zs-19j09z"),
    Example("E019", "amevista", "Tom Ford", "FT0711", "25E", "sol",
            "https://www.amevista.com/om/tom-ford-ft0711-25e"),
    Example("E020", "amevista", "Tiffany & Co.", "TF3104D", "6216S4", "sol",
            "https://www.amevista.com/es/tiffany-tf3104d-6216s4"),
    Example("E021", "amevista", "Tiffany & Co.", "TF3104D", "6216S4", "sol",
            "https://www.amevista.com/us/tiffany-tf3104d-6216s4"),
    Example("E022", "mia", "Prada", "PR 26ZS", "17K08Z", "sol",
            "https://miaburton.com/es/gafas-de-sol/prada/pr-26zs-17k08z", ("8056597875233",)),
    Example("E023", "mia", "Miu Miu", "MU 01XV", "VAU1O1", "montura",
            "https://miaburton.com/es/gafas-graduadas/miu-miu/regard-mu-01xv-vau1o1"),
    Example("E024", "mia", "Miu Miu", "MU 01XV", "1AB1O1", "montura",
            "https://miaburton.com/es/gafas-graduadas/miu-miu/regard-mu-01xv-1ab1o1",
            ("8056597981354",)),
    Example("E025", "mia", "Chloe", "CH0289S", "003", "sol",
            "https://miaburton.com/es/gafas-de-sol/chloe/ch0289s-003",
            ("889652543574", "8056376561067"), "GTIN publicados no equivalentes: requiere revisión"),
    Example("E026", "mia", "Bottega Veneta", "BV1274S", "002", "sol",
            "https://miaburton.com/en/sunglasses/bottega-veneta/bv1274s-002",
            ("889652456065", "8056376476330"), "GTIN publicados no equivalentes: requiere revisión"),
    Example("E027", "mia", "Saint Laurent", "SL 596 DUNE", "003", "sol",
            "https://miaburton.com/en/sunglasses/saint-laurent/dune-sl-596-003"),
    Example("E028", "opticalh", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://opticalh.com/en/products/prada-pr-27zs-16k08z-54-21-sunglasses-62083"),
    Example("E029", "opticalh", "Prada", "PR 27ZS", "11L08Z", "sol",
            "https://opticalh.com/fr/products/lunettes-de-soleil-prada-pr-27zs-11l08z-54-21-62081"),
    Example("E030", "opticalh", "Tom Ford", "FT1044", "01E", "sol",
            "https://opticalh.com/products/gafas-de-sol-tom-ford-bronson-ft1044-01e-60-12"),
    Example("E031", "opticalh", "Tom Ford", "FT1044", "01E", "sol",
            "https://opticalh.com/en/products/tom-ford-bronson-ft1044-01e-60-12-sunglasses-9",
            caution="Título y contenido indexados contradictorios: requiere revisión"),
    Example("E032", "domanci", "Chloe", "CH0289S", "003", "sol",
            "https://domanci.com/es/gafas-de-sol/chloe-ch0289s-003-58.html",
            caution="Candidata histórica sin reconfirmación; no demuestra ausencia del producto",
            route_attributes=(("caliber", "58"),)),
    Example("E033", "domanci", "Miu Miu", "MU 01XV", "VAU1O1", "montura",
            "https://domanci.com/es/monturas/miu-miu-mu-01xv-vau1o1-50.html",
            caution="Candidata histórica sin reconfirmación; no demuestra ausencia del producto",
            route_attributes=(("caliber", "50"),)),
    Example("E034", "universitaria", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.opticauniversitaria.es/gafas-de-sol/prada/prada-pr-27zs-16k08z/312245.html"),
    Example("E035", "universitaria", "Prada", "PR 65ZS", "ZVN09T", "sol",
            "https://www.opticauniversitaria.es/gafas-de-sol/prada/prada-pr-65zs-55-zvn09t/318857.html"),
    Example("E036", "universitaria", "Prada", "PR 65ZS", "", "sol",
            "https://www.opticauniversitaria.es/gafas-de-sol/prada/prada-pr-65zs/PRADA-65ZS.html"),
    Example("E037", "congafas", "Tom Ford", "FT0711", "01A", "sol",
            "https://www.congafasdesol.com/tom-ford-fausto-ft0711-01a.html"),
    Example("E038", "congafas", "Tom Ford", "FT0711", "75S", "sol",
            "https://www.congafasdesol.com/tom-ford-ft0711-fausto-75s.html"),
    Example("E039", "celine", "Prada", "PR 27ZS", "", "sol",
            "https://www.celine-opticien-lunetier.com/products/sunglasses-prada-symbole-pr-27zs"),
    Example("E040", "celine", "Prada", "PR 26ZS", "", "sol",
            "https://www.celine-opticien-lunetier.com/es/products/gafas-de-sol-prada-symbole-pr-26zs"),
    Example("E041", "visio", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/pr27zs-16k08z-54-21.html",
            ("8056597875349",)),
    Example("E042", "visio", "Cartier", "CT0550S", "001", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/cartier-core-range-ct0550s-001-56-18-oro.html"),
    Example("E043", "visio", "Ray Ban", "RB2140", "901", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901l-54-18.html",
            caution="Medidas no asociadas al UPC; dos RB2140 901 del catálogo requieren resolución"),
    Example("E044", "visio", "Ray Ban", "RB2140", "901/58", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901-58-50-22.html"),
    Example("E045", "otticait", "Prada", "PR 27ZS", "16K08Z", "sol",
            "https://www.otticait.com/en/prada-pr-27zs-16k08z"),
    Example("E046", "otticait", "Prada", "PR 27ZS", "16K70A", "sol",
            "https://www.otticait.com/prada-pr-27zs-16k70a", ("8056597899208",)),
    Example("E047", "otticait", "Tom Ford", "FT5634-B", "001", "montura",
            "https://www.otticait.com/tom-ford-ft5634-b-001"),
)

_SPEC_SOURCE = ("Especificacion_100_EAN_6_competidores.json; índice/documentación aportados "
                "el 2026-11-13; no es una respuesta HTTP actual")
_HISTORY_SOURCE = "Evidencia_historica_acotada.json; recibo histórico, no verificación actual"
_SPEC_DATE = "2026-11-13"
_SPEC_BASE = "a1fef77139b82403387267083f2f6e5fa872c094"
SUPPLEMENTAL_EXAMPLES = (
    Example("N001", "amevista", "Chloe", "CH0347S", "001", "sol",
            "https://www.amevista.com/es/chlo-ch0347s-001", ("889652630922",),
            source=f"{_SPEC_SOURCE}#N001 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("N002", "amevista", "Chloe", "CH0289S", "003", "sol",
            "https://www.amevista.com/es/chlo-ch0289s-003",
            caution="HREF de catálogo observado; ficha y EAN no contrastados.",
            source=f"{_SPEC_SOURCE}#N002 (public_catalog_href)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("N003", "amevista", "Ray Ban", "RB4441D", "667787", "sol",
            "https://www.amevista.com/es/ray-ban-rb4441d-667787",
            source=f"{_SPEC_SOURCE}#N003 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("N004", "optima", "Gucci", "GG1680S", "004", "sol",
            "https://optica-optima.com/gafas-de-sol/gucci-gg1680s-004.html",
            source=f"{_SPEC_SOURCE}#N004 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("N005", "optima", "Bottega Veneta", "BV1274S", "005", "sol",
            "https://optica-optima.com/gafas-de-sol/bottega-veneta-bv1274s-005.html", ("889652637075",),
            source=f"{_SPEC_SOURCE}#N005 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("N006", "visio", "Saint Laurent", "SL M160", "001", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
            "saint-laurent-monogram-sl-m160-001-53-19-negro.html", ("889652651774",),
            source=f"{_SPEC_SOURCE}#N006 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "53"), ("bridge", "19"))),
    Example("N007", "visio", "Saint Laurent", "SL M94", "001", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/slm94-001-53-20.html",
            ("889652359700",), source=f"{_SPEC_SOURCE}#N007 (public_indexed_product)",
            review_date=_SPEC_DATE, review_base=_SPEC_BASE,
            route_attributes=(("caliber", "53"), ("bridge", "20"))),
    Example("N008", "visio", "Miu Miu", "MU 04UV", "1AB1O1", "montura",
            "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/mu-04uv-1ab1o1-52-19.html",
            ("8056597669016",), source=f"{_SPEC_SOURCE}#N008 (public_indexed_product)",
            review_date=_SPEC_DATE, review_base=_SPEC_BASE,
            route_attributes=(("caliber", "52"), ("bridge", "19"))),
    Example("N009", "visio", "Miu Miu", "MU 01XV", "VAU1O1", "montura",
            "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/mu-01xv-vau-1o1-50-18.html",
            ("8056597981361",), source=f"{_SPEC_SOURCE}#N009 (public_indexed_product)",
            review_date=_SPEC_DATE, review_base=_SPEC_BASE,
            route_attributes=(("caliber", "50"), ("bridge", "18"))),
    Example("N010", "visio", "Miu Miu", "MU 01YV", "14L1O1", "montura",
            "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/"
            "miu-miu-mu-01yv-14l1o1-51-16-havana-honey.html", ("8056262600078",),
            source=f"{_SPEC_SOURCE}#N010 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "51"), ("bridge", "16"))),
    Example("N011", "visio", "Miu Miu", "MU 53WV", "ZVN1O1", "montura",
            "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/"
            "miu-miu-0mu-53wv-zvn-1o1-53-20-pale-gold.html", ("8056262410929",),
            source=f"{_SPEC_SOURCE}#N011 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "53"), ("bridge", "20"))),
    Example("N012", "visio", "Tiffany & Co.", "TF3104D", "6216S4", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
            "tiffany-co-tf3104d-6216s4-56-18-oro.html",
            source=f"{_SPEC_SOURCE}#N012 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "56"), ("bridge", "18"))),
    Example("N013", "edel", "Ray Ban", "RB4441D", "667787", "sol",
            "https://www.edel-optics.es/RB4441D-667787-de-Ray-Ban.html",
            source=f"{_SPEC_SOURCE}#N013 (public_indexed_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("U002", "edel", "Tom Ford", "FT1026", "05E", "sol",
            "https://www.edel-optics.es/FT1026-05E-de-Tom-Ford.html",
            source=f"{_SPEC_SOURCE}#U002 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("U003", "edel", "Miu Miu", "MU 53WV", "ZVN1O1", "montura",
            "https://www.edel-optics.es/MU-53WV-ZVN1O1-de-Miu-Miu.html",
            source=f"{_SPEC_SOURCE}#U003 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("U004", "domanci", "Tom Ford", "FT1026", "05E", "sol",
            "https://domanci.com/es/gafas-de-sol/tom-ford-bruce-ft1026-05e-61.html",
            caution="El 61 del enlace aportado no está asociado al EAN; verificar variante en catálogo.",
            source=f"{_SPEC_SOURCE}#U004 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "61"),)),
    Example("U005", "domanci", "Miu Miu", "MU 53WV", "ZVN1O1", "montura",
            "https://domanci.com/es/monturas/miu-miu-mu-53wv-zvn1o1-51.html",
            caution="El 51 del enlace no se asigna al EAN, cuya medida publicada por Visio es 53-20.",
            source=f"{_SPEC_SOURCE}#U005 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "51"),)),
    Example("U006", "visio", "Tom Ford", "FT1044", "01E", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/ft1044-s-01e-60-12.html",
            source=f"{_SPEC_SOURCE}#U006 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "60"), ("bridge", "12"))),
    Example("U007", "visio", "Miu Miu", "MU 52YS", "ZVN5D1", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/mu-52ys-zvn-5d1-54-21.html",
            source=f"{_SPEC_SOURCE}#U007 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "54"), ("bridge", "21"))),
    Example("U008", "visio", "Ray Ban", "RB2140", "901", "sol",
            "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901-50-22.html",
            caution="La talla del enlace no acredita asociación con ninguno de los EAN RB2140 901.",
            source=f"{_SPEC_SOURCE}#U008 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE, route_attributes=(("caliber", "50"), ("bridge", "22"))),
    Example("U009", "congafas", "Prada", "PR D01S", "0CD80L", "sol",
            "https://www.congafasdesol.com/prada-pr-d01s-0cd80l.html",
            source=f"{_SPEC_SOURCE}#U009 (user_supplied_product; outside the 100-EAN manifest)",
            review_date=_SPEC_DATE, review_base=_SPEC_BASE),
    Example("U010", "congafas", "Prada", "PR06YS", "09Q5S0", "sol",
            "https://www.congafasdesol.com/prada-pr06ys-09q5s0.html",
            source=f"{_SPEC_SOURCE}#U010 (user_supplied_product)", review_date=_SPEC_DATE,
            review_base=_SPEC_BASE),
    Example("report2-check:601", "amevista", "Tom Ford", "FT0711", "01A", "sol",
            "https://www.amevista.com/es/tom-ford-ft0711-01a-fausto", ("889214029614",),
            source=f"{_HISTORY_SOURCE}#report2-check:601", review_date="2026-11-12", review_base=""),
    Example("report2-check:607", "amevista", "Tom Ford", "FT1301", "52A", "sol",
            "https://www.amevista.com/es/tom-ford-ft1301-52a-joey-02", ("889214625625",),
            source=f"{_HISTORY_SOURCE}#report2-check:607", review_date="2026-11-12", review_base=""),
    Example("report2-check:608", "amevista", "Tom Ford", "FT1310", "52E", "sol",
            "https://www.amevista.com/es/tom-ford-ft1310-52e-chloe-02", ("889214625991",),
            source=f"{_HISTORY_SOURCE}#report2-check:608", review_date="2026-11-12", review_base=""),
    Example("report2-check:609", "amevista", "Tom Ford", "FT1044", "93E", "sol",
            "https://www.amevista.com/es/tom-ford-ft1044-93e-bronson", ("889214684097",),
            source=f"{_HISTORY_SOURCE}#report2-check:609", review_date="2026-11-12", review_base=""),
    Example("report2-check:614", "amevista", "Gucci", "GG1660S", "002", "sol",
            "https://www.amevista.com/es/gucci-gg1660s-002-gg-logo", ("889652494838",),
            source=f"{_HISTORY_SOURCE}#report2-check:614", review_date="2026-11-12", review_base=""),
    Example("report2-check:905", "optima", "Tom Ford", "FT1301", "52A", "sol",
            "https://optica-optima.com/gafas-de-sol/tom-ford-joey-02-ft1301-52a.html", ("889214625625",),
            source=f"{_HISTORY_SOURCE}#report2-check:905", review_date="2026-11-12", review_base=""),
    Example("report2-check:906", "optima", "Tom Ford", "FT1310", "52E", "sol",
            "https://optica-optima.com/gafas-de-sol/tom-ford-chloe-02-ft1310-52e.html", ("889214625991",),
            source=f"{_HISTORY_SOURCE}#report2-check:906", review_date="2026-11-12", review_base=""),
    Example("report2-check:907", "optima", "Tom Ford", "FT1044", "93E", "sol",
            "https://optica-optima.com/gafas-de-sol/tom-ford-bronson-ft1044-93e.html", ("889214684097",),
            source=f"{_HISTORY_SOURCE}#report2-check:907", review_date="2026-11-12", review_base=""),
)
ALL_EXAMPLES = EXAMPLES + SUPPLEMENTAL_EXAMPLES

DOMAINS = {urlparse(example.url).hostname.removeprefix("www."): example.shop for example in ALL_EXAMPLES}

CATALOG_DISCOVERY_PLANS = {
    "optima": {
        "entrypoints": {
            "sol": ("https://optica-optima.com/gafas-de-sol/",),
            "montura": ("https://optica-optima.com/gafas-graduadas/",),
        },
        "observed_links": ("https://optica-optima.com/gafas-de-sol/gucci/",),
        "evidence": ("O02", "N004", "N005"),
        "method": "Indexar href publicados de categoría/marca; resolver alias en el catálogo, no por slug.",
    },
    "amevista": {
        "entrypoints": {
            "sol": ("https://www.amevista.com/es/gafas-de-sol",),
            "montura": ("https://www.amevista.com/es/gafas-de-vista",),
        },
        "observed_links": ("https://www.amevista.com/es/gafas-de-sol/chloe",),
        "evidence": ("A02", "N002"),
        "method": "Usar catálogo español y href publicados; conservar alias exactos por referencia.",
    },
    "visio": {
        "entrypoints": {
            "sol": ("https://www.visiofactory.es/gafas-de-sol/todas-las-solares/",),
            "montura": ("https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/",),
        },
        "observed_links": (
            "https://www.visiofactory.es/gafas-de-sol/saint-laurent-hombre/",
            "https://www.visiofactory.es/gafas-graduadas/tom-ford/",
            "https://www.visiofactory.es/gafas-graduadas/miu-miu/",
        ),
        "evidence": ("V02", "N006", "N008", "N009", "N010", "N011", "N012"),
        "method": "Indexar href por marca/categoría; conservar handle, medidas y color tal como se publican.",
    },
    "edel": {
        "entrypoints": {"sol": ("https://www.edel-optics.es/",),
                        "montura": ("https://www.edel-optics.es/",)},
        "observed_links": ("https://www.edel-optics.es/Gafas-de-sol-Miu-Miu.html",),
        "evidence": ("E01", "E02", "N013"),
        "method": "Reutilizar índice; resolver href de marca y después Product/Offer/selectedSize.",
    },
    "domanci": {
        "entrypoints": {
            "sol": ("https://domanci.com/es/marcas", "https://domanci.com/es/marca/{brand}"),
            "montura": ("https://domanci.com/es/marcas", "https://domanci.com/es/marca/{brand}"),
        },
        "observed_links": (
            "https://domanci.com/es/marca/tom-ford",
            "https://domanci.com/es/marca/miu-miu",
        ),
        "evidence": ("D01", "D02", "U004", "U005"),
        "method": "Compartir el índice de href de marca; recuperar nombre, categoría y talla de la ficha fuente.",
    },
    "congafas": {
        "entrypoints": {"sol": (), "montura": ()},
        "observed_links": ("https://www.congafasdesol.com/sitemaps/es/sitemap.xml",),
        "evidence": ("C01", "C02"),
        "method": ("Reutilizar únicamente historial/índice local; no consultar mientras siga el bloqueo. "
                   "Sitemap solo por el flujo existente, autorizado y sin bloqueo."),
        "halted_after_403": True,
    },
}


def _catalog_discovery_plan(shop: str, category: str) -> dict[str, Any]:
    source = CATALOG_DISCOVERY_PLANS.get(shop)
    if source is None:
        return {}
    entrypoints = source["entrypoints"]
    urls = list(entrypoints.get(category, ())) if category else [
        url for paths in entrypoints.values() for url in paths
    ]
    return {
        "shop": shop,
        "category": category or "all",
        "shared": True,
        "entrypoints": list(dict.fromkeys(urls)),
        "observed_links": list(source.get("observed_links", ())),
        "method": source["method"],
        "evidence": list(source.get("evidence", ())),
        "requires_existing_authorization": True,
        "access_state": "not_assessed",
        "automatic_execution": False,
        "halted_after_403": bool(source.get("halted_after_403", False)),
    }


@dataclass
class UrlCandidate:
    url: str
    rule: str
    attributes: dict[str, str]
    evidence: list[str]
    origin: str = EVIDENCE_SOURCE
    state: str = "pending_verification"
    reason: str = ""
    review_date: str = REVIEW_DATE
    review_base: str = REVIEW_BASE

    @property
    def runnable(self) -> bool:
        return self.state == "pending_verification"

    def provenance(self) -> dict:
        return asdict(self)


@dataclass
class CandidatePlan:
    candidates: list[UrlCandidate] = field(default_factory=list)
    reason_code: str = ""
    reason: str = ""
    discovery_plan: dict[str, Any] = field(default_factory=dict)

    @property
    def runnable(self) -> list[UrlCandidate]:
        return [candidate for candidate in self.candidates if candidate.runnable]


def _category(value: str) -> str:
    normalized = key(value)
    if normalized in {"SOL", "GAFASDESOL", "ALLGAFASDESOL"}:
        return "sol"
    if normalized in {"MONTURA", "MONTURAS", "ALLMONTURAS", "GAFASGRADUADAS"}:
        return "montura"
    return ""


def _slug(value: str) -> str:
    return "-".join(tokens(value)).lower()


def _supplied_pattern_plan(product: dict, shop: str, category: str, attributes: dict) -> CandidatePlan:
    """User-supplied route hypotheses; page identity and access remain governed by the reader."""
    if shop != "domanci":
        return CandidatePlan(reason_code="needs_authorized_discovery",
                             reason="No hay un patrón de URL aportado para esta tienda.")
    used = dict(attributes)
    used["pattern_examples"] = " | ".join(SUPPLIED_PATTERNS[shop])
    used["evidence_note"] = "User-supplied route pattern; not captured HTTP or verified identity"
    model = model_key(product["model"])
    model_slug = _slug(product["model"])
    prefix = re.fullmatch(r"(PR|MU)([A-Z0-9]+)", model)
    if prefix:
        model_slug = f"{prefix[1].lower()}-{prefix[2].lower()}"
    elif re.fullmatch(r"FT\d{4}[A-Z]?", model):
        model_slug = model.lower()
    brand_slug, color = _slug(product["brand"]), key(product["color"]).lower()
    lens = normalize_caliber(product.get("caliber", "")).partition("-")[0]
    if not lens:
        return CandidatePlan(reason_code="missing_attribute",
                             reason="Falta calibre asociado al EAN para construir la URL Domanci; no se infiere")
    used["caliber"] = lens
    used["dimension_source"] = (
        product.get("measurement_source") or "Explicit caliber on the target product record"
    )
    section = "gafas-de-sol" if category == "sol" else "monturas"
    urls = [f"https://domanci.com/es/{section}/{brand_slug}-{model_slug}-{color}-{lens}.html"]
    evidence = ["user:domanci-route-2026-11-06", "E032", "E033"]
    return CandidatePlan([
        UrlCandidate(url, f"supplied_pattern:{shop}", used, evidence,
                     origin="User-provided URL patterns, 2026-11-06",
                     review_date=SUPPLIED_PATTERN_DATE, review_base="")
        for url in urls
    ])


def _visio_format_plan(product: dict, brand: str, model: str) -> CandidatePlan | None:
    """Build only the additional Visio formats evidenced for these model families."""
    family = (brand, model)
    if family not in {("TOMFORD", "FT1044"), ("MIUMIU", "MU52YS"), ("RAYBAN", "RB2140")}:
        return None

    raw_caliber = normalize_caliber(product.get("caliber", ""))
    lens, _, bridge = raw_caliber.partition("-")
    explicit_bridge = str(product.get("bridge") or "").strip()
    if explicit_bridge:
        if not re.fullmatch(r"\d{1,2}", explicit_bridge):
            return CandidatePlan(reason_code="missing_attribute", reason="Puente no reconocido para Visio")
        if bridge and bridge != explicit_bridge:
            return CandidatePlan(reason_code="missing_attribute",
                                 reason="Calibre y puente contradictorios para construir la URL Visio")
        bridge = explicit_bridge

    needs_bridge = family != ("RAYBAN", "RB2140") or "/" in str(product.get("color", ""))
    missing = (["calibre"] if not lens else []) + (["puente"] if needs_bridge and not bridge else [])
    if missing:
        return CandidatePlan(reason_code="missing_attribute",
                             reason="Falta " + " y ".join(missing) + " asociado al EAN para construir la URL Visio")

    color_value = str(product["color"]).strip().upper()
    if family == ("MIUMIU", "MU52YS"):
        if not re.fullmatch(r"ZVN[0-9A-Z]{3}", color_value):
            return CandidatePlan(reason_code="needs_authorized_discovery",
                                 reason="Visio solo documenta el separador ZVN-5D1 para Miu Miu MU52YS")
        color_slug = f"{color_value[:3]}-{color_value[3:]}".lower()
        model_slug = "mu-52ys"
        size = f"{lens}-{bridge}"
    elif family == ("TOMFORD", "FT1044"):
        color_slug = color_value.lower()
        model_slug = "ft1044-s"
        size = f"{lens}-{bridge}"
    else:
        if not re.fullmatch(r"[A-Z0-9]+(?:/[A-Z0-9]+)?", color_value):
            return CandidatePlan(reason_code="needs_authorized_discovery",
                                 reason="Formato de color Ray-Ban no reconocido para Visio")
        color_slug = color_value.lower().replace("/", "-")
        model_slug = "rb2140"
        size = f"{lens}-{bridge}" if "/" in color_value else lens

    attributes = {
        name: str(product[name]) for name in ("ean", "brand", "model", "color", "category")
    }
    attributes["ean"] = normalize_gtin(product["ean"])
    attributes["caliber"] = lens
    if bridge:
        attributes["bridge"] = bridge
    if product.get("measurement_source"):
        attributes["dimension_source"] = str(product["measurement_source"])
    if product.get("measurement_association"):
        attributes["dimension_association"] = str(product["measurement_association"])
    if product.get("measurement_limitation"):
        attributes["dimension_limitation"] = str(product["measurement_limitation"])
    attributes["variant_resolution"] = (
        "URL format only; ficha identity, EAN, selected variant, price and availability remain unverified"
    )
    url = ("https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
           f"{model_slug}-{color_slug}-{size}.html")
    candidate = UrlCandidate(
        url,
        "sample_pattern:visio",
        attributes,
        [VISIO_FORMAT_EVIDENCE],
        origin=MANUAL_COVERAGE_EVIDENCE,
        review_date="",
        review_base="",
    )
    return CandidatePlan([candidate])


def _candidate_plan(product: dict, domain: str) -> CandidatePlan:
    """Pure preview. Missing dimensions/opaque IDs are never enumerated or inferred from another colour."""
    host = urlparse(domain if "://" in domain else "https://" + domain).hostname or ""
    shop = DOMAINS.get(host.removeprefix("www.").lower())
    if not shop:
        return CandidatePlan(reason_code="needs_authorized_discovery", reason="Sin regla documental para el dominio")
    missing = [name for name in ("ean", "brand", "model", "color", "category") if not product.get(name)]
    if missing:
        return CandidatePlan(reason_code="missing_attribute", reason="Falta: " + ", ".join(missing))
    resolution = resolve_product_attributes(product)
    if resolution.conflict:
        return CandidatePlan(reason_code="measurement_conflict", reason=resolution.conflict)
    product = resolution.product
    ean = normalize_gtin(product["ean"])
    category = _category(product["category"])
    if not category:
        return CandidatePlan(reason_code="missing_attribute", reason="Falta categoría reconocida: sol o montura")
    brand, model, color = key(product["brand"]), model_key(product["model"]), key(product["color"])
    attributes = {name: str(product[name]) for name in ("ean", "brand", "model", "color", "category")}
    attributes["ean"] = ean
    for name in ("caliber", "bridge", "measurement_source", "measurement_association", "measurement_limitation"):
        if product.get(name):
            attributes[name] = str(product[name])
    if product.get("measurement_source"):
        attributes["dimension_source"] = str(product["measurement_source"])
    examples = [example for example in ALL_EXAMPLES if example.shop == shop]
    candidates: dict[str, UrlCandidate] = {}
    for example in examples:
        if shop == "amevista" and not urlparse(example.url).path.startswith("/es/"):
            continue
        if (key(example.brand), model_key(example.model), example.category) != (brand, model, category):
            continue
        if example.color and key(example.color) != color:
            continue
        reasons = [example.caution] if example.caution else []
        if reasons and "medidas no asociadas al upc" in example.caution.casefold():
            target_caliber = normalize_caliber(product.get("caliber"))
            route_caliber = extract_caliber(urlparse(example.url).path)
            has_ean_measurement = product.get("measurement_association") in (
                MEASUREMENT_ASSOCIATIONS - {"model_color_only"}
            )
            if (has_ean_measurement and target_caliber and route_caliber
                    and compare_calibers(target_caliber, route_caliber) == "equal"):
                reasons.remove(example.caution)
        if not example.color:
            reasons.append("Ficha padre sin color: resolver URL completa de variante, identidad, precio y stock")
        gtins = {normalize_gtin(value) for value in example.gtins}
        if gtins and gtins != {ean}:
            reasons.append("GTIN documentados contradictorios o distintos del objetivo: requiere revisión")
        used = dict(attributes)
        route_attributes = dict(example.route_attributes)
        if route_attributes:
            used["route_attributes"] = route_attributes
            used["route_attributes_note"] = (
                "Dimensiones del href, no atributos del EAN; no trasladar disponibilidad entre tallas."
            )
            route_measure = normalize_caliber("-".join(
                value for value in (route_attributes.get("caliber", ""), route_attributes.get("bridge", ""))
                if value
            ))
            target_measure = normalize_caliber(product.get("caliber"))
            if target_measure and "-" not in target_measure and product.get("bridge"):
                target_measure += "-" + str(product["bridge"]).strip()
            has_ean_measurement = product.get("measurement_association") in (
                MEASUREMENT_ASSOCIATIONS - {"model_color_only"}
            )
            route_lens, _, route_bridge = route_measure.partition("-")
            target_lens, _, target_bridge = target_measure.partition("-")
            route_matches_target = (
                route_lens == target_lens and (not route_bridge or not target_bridge
                                               or route_bridge == target_bridge)
            )
            if (shop == "domanci" and reasons and target_measure and route_matches_target
                    and has_ean_measurement and "candidata histórica" in reasons[0].casefold()):
                reasons.remove(reasons[0])
            if route_measure and not target_measure:
                reasons.append("La talla de la URL no está asociada al EAN; resolver la variante en la ficha.")
            elif route_measure and route_bridge and not target_bridge:
                reasons.append("El puente de la URL no está asociado al EAN; resolver la variante en la ficha.")
            elif route_measure and target_measure:
                route_lens, _, route_bridge = route_measure.partition("-")
                target_lens, _, target_bridge = target_measure.partition("-")
                if route_lens != target_lens or (route_bridge and target_bridge and route_bridge != target_bridge):
                    reasons.append(
                        f"La URL indica {route_measure} y el EAN tiene {target_measure}; "
                        "no asociar stock ni variante por el enlace."
                    )
        if example.gtins:
            used["documented_gtins"] = ", ".join(example.gtins)
        product_id = parse_qs(urlparse(example.url).query).get("selectedSize")
        if product_id:
            used["selectedSize_product_id"] = product_id[0]
        reason = "; ".join(dict.fromkeys(reasons))
        candidates[example.url] = UrlCandidate(
            example.url, "documented_url:" + example.id, used, [example.id],
            origin=example.source, state="probable" if reason else "pending_verification", reason=reason,
            review_date=example.review_date, review_base=example.review_base)

    if candidates:
        needs_review = any(not candidate.runnable for candidate in candidates.values())
        return CandidatePlan(list(candidates.values()),
                             "needs_authorized_discovery" if needs_review else "",
                             "; ".join(candidate.reason for candidate in candidates.values() if candidate.reason))

    if shop == "domanci" and normalize_caliber(product.get("caliber", "")):
        return _supplied_pattern_plan(product, shop, category, attributes)

    if shop == "visio" and category == "sol":
        format_plan = _visio_format_plan(product, brand, model)
        if format_plan is not None:
            return format_plan

    if shop in SUPPLIED_PATTERNS:
        return _supplied_pattern_plan(product, shop, category, attributes)

    # Keep generated URLs within the reviewed scopes and explicitly supplied patterns.
    scopes = {
        "optima": {("PRADA", "sol"), ("TOMFORD", "sol"), ("TOMFORD", "montura")},
        "bassol": {("PRADA", "sol"), ("MIUMIU", "montura"), ("GUCCI", "sol")},
        "amevista": {
            ("GUCCI", "sol"), ("PRADA", "sol"), ("TOMFORD", "sol"), ("TIFFANYCO", "sol"),
            ("MIUMIU", "sol"), ("MIUMIU", "montura"), ("BOTTEGAVENETA", "sol"),
            ("RAYBAN", "sol"),
        },
        "edel": {("PRADA", "sol"), ("TOMFORD", "sol"), ("MIUMIU", "sol"), ("MIUMIU", "montura"),
                 ("BOTTEGAVENETA", "sol"), ("SAINTLAURENT", "sol")},
        "visual": {("PRADA", "sol"), ("MIUMIU", "montura")},
        "otticait": {("PRADA", "sol"), ("TOMFORD", "montura")},
        "visio": {("PRADA", "sol")},
    }
    if ((brand, category) not in scopes.get(shop, set())
            or (shop == "visio" and model != "PR27ZS")
            or (shop == "amevista" and brand == "RAYBAN"
                and (model, color) not in {("RB2140", "901"), ("RB2140", "901S31"), ("RB4472", "13592")})
            or (shop == "edel" and brand == "SAINTLAURENT"
                and model not in {"SL596DUNE", "SL872ERIN", "SL908"})):
        if shop == "amevista" and brand == "MIUMIU":
            reason = "La regla Miu Miu de Amevista solo cubre las categorías sol y montura documentadas"
        elif shop == "amevista" and brand == "BOTTEGAVENETA":
            reason = "La regla Bottega Veneta de Amevista solo cubre gafas de sol"
        elif shop == "amevista" and brand == "RAYBAN":
            reason = "Amevista solo documenta Ray-Ban RB2140 y RB4472 con los formatos de color observados"
        elif shop == "edel" and brand == "SAINTLAURENT":
            reason = "Edel solo documenta Saint Laurent SL 596 DUNE, SL 872 ERIN y SL 908 en esta evidencia"
        elif shop == "amevista" and category != "sol":
            reason = "La evidencia Amevista respalda rutas marca-modelo-color solo para gafas de sol"
        elif shop == "amevista":
            reason = "Marca fuera de los patrones Amevista documentados; requiere URL/alias acreditado"
        elif shop == "congafas":
            reason = ("No se generan slugs genéricos para Prada ni otras marcas: reutilizar historial/índice local. "
                      "La ruta aportada cubre solo gafas de sol, no monturas. La investigación pública se detuvo "
                      "tras 403; sitemap solo mediante el flujo existente, autorizado y sin bloqueo.")
        elif shop == "visio" and brand == "PRADA" and category == "sol":
            reason = f"Visio solo documenta la subregla Prada PR27ZS en sol; {model} requiere una fuente autorizada"
        elif shop == "visio":
            reason = (f"La muestra Visio no documenta {brand} {category}; el slug no permite inferir "
                      "modelo, calibre ni puente")
        elif shop == "edel":
            reason = (f"Edel no tiene patrón de ruta documentado para {brand} {category}; no se inventa "
                      "un handle ni un Product Id selectedSize")
        else:
            reason = {
                "blickers": "Falta ID opaco por color y calibre; resolver mediante fuente autorizada",
                "mia": "Falta handle y nombre comercial acreditados (regard/dune no se eliminan)",
                "opticalh": "Falta handle/variante acreditados; no exigir ni inventar sufijo numérico",
                "universitaria": "Falta REF/handle de variante; puede ser alfanumérico",
                "celine": "Falta ID/URL completa de variante con color, precio y stock vinculados",
                "congafas": "Falta handle/nombre comercial por producto; el orden no es universal",
                "domanci": "Evidencia insuficiente; candidatas históricas pendientes, no producto inexistente",
            }.get(shop, f"Sin patrón documentado para {brand} {model} ({category}) en {shop}; "
                        "requiere descubrimiento autorizado")
        return CandidatePlan(list(candidates.values()), "needs_authorized_discovery", reason)

    used = dict(attributes)
    lens, _, bridge = normalize_caliber(product.get("caliber", "")).partition("-")
    refs = [
        example.id for example in EXAMPLES
        if example.shop == shop and key(example.brand) == brand and example.category == category
    ]
    if shop == "amevista":
        used["locale"] = "es (Spanish-market catalog; E021 documents a separate US URL)"
        if brand == "TIFFANYCO":
            used["brand_alias"] = "tiffany (E020, E021)"
        elif brand == "GUCCI":
            refs.append("user-provided candidate URL")
            used["evidence_note"] = "URL pattern only; not identity evidence"
        elif brand == "PRADA":
            used["model_alias_rule"] = "Observed PR model prefix; 0PR retained only on exact E017 URL"
        elif brand == "RAYBAN":
            if model == "RB2140":
                model_slug = "rb2140"
                used["model_alias_rule"] = "RB2140 retains the documented -wayfarer suffix"
            elif model == "RB4472" and color == "13592":
                used["color_alias_rule"] = "RB4472 catalog color 13592 uses documented route color 1359-2"
            else:
                return CandidatePlan(reason_code="needs_authorized_discovery",
                                     reason="Amevista Ray-Ban format is documented only for RB2140 and RB4472 1359/2")
            used["evidence_note"] = MANUAL_COVERAGE_EVIDENCE
            used["variant_resolution"] = (
                "On shared fiches, bind the GTIN and size from the same structured Product variant; "
                "never infer the mapping from list order. Normalize valid 12-digit UPCs."
            )
            used["price_rule"] = (
                "A common published model/color price may be reused across calibers; stock remains variant-specific."
            )
            refs.append(AMEVISTA_RAYBAN_EVIDENCE)
        if evidence := AMEVISTA_MANUAL_EVIDENCE.get((brand, category)):
            refs.append(evidence)
            used["evidence_note"] = MANUAL_COVERAGE_EVIDENCE
    elif shop == "edel":
        refs.append(EDEL_MANUAL_EVIDENCE)
        used["evidence_note"] = MANUAL_COVERAGE_EVIDENCE
        used["variant_resolution"] = (
            "Resolve the unique checked prd<id> control from the fiche; bind normalized UPC/EAN and Offer facts "
            "to that same Product variant. Do not hard-code IDs or treat indexed-only UPCs as current-page evidence."
        )
        if brand == "SAINTLAURENT":
            used["identity_evidence_note"] = (
                "Browser evidence records selected control and offer facts; UPC identity was recovered from "
                "indexed pages and is not asserted as visible in the current browser read."
            )
    if shop in {"bassol", "visual", "visio"}:
        absent = (["calibre"] if not lens else []) + (["puente"] if shop == "visio" and not bridge else [])
        if absent:
            return CandidatePlan(list(candidates.values()), "missing_attribute",
                                 "Falta " + " y ".join(absent) + "; no se infiere de otro color/EAN")
        used["caliber"] = lens
        if shop == "visio":
            used["bridge"] = bridge
    model_slug = _slug(product["model"])
    if brand == "TOMFORD" and re.fullmatch(r"FT\d{4}[A-Z]?", model):
        model_slug = model.lower()
    if brand in {"PRADA", "MIUMIU"}:
        match = re.fullmatch(r"(PR|MU)([A-Z0-9]+)", model)
        if match:
            model_slug = f"{match[1].lower()}-{match[2].lower()}"
    if shop in {"optima", "otticait"} and (brand, model, color) == ("TOMFORD", "FT5634B", "001"):
        model_slug = "ft5634-b"
        used["model_alias"] = "FT5634B -> FT5634-B (E003, E047)"
    brand_slug = "tiffany" if shop == "amevista" and brand == "TIFFANYCO" else _slug(product["brand"])
    color_slug = color.lower()
    if shop == "amevista" and brand == "RAYBAN" and model == "RB2140":
        color_slug += "-wayfarer"
    elif shop == "amevista" and brand == "RAYBAN" and model == "RB4472":
        color_slug = "1359-2"
    slug = f"{brand_slug}-{model_slug}-{color_slug}"
    if shop == "optima":
        path = f"/{'gafas-de-sol' if category == 'sol' else 'gafas-graduadas'}/{slug}.html"
    elif shop in {"bassol", "visual"}:
        path = f"/products/{slug}-{lens}"
    elif shop == "visio":
        path = f"/gafas-de-sol/todas-las-solares/{model.lower()}-{color.lower()}-{lens}-{bridge}.html"
    elif shop == "amevista":
        path = f"/es/{slug}"
    elif shop == "edel":
        edel_model = "-".join(str(product["model"]).strip().upper().split())
        edel_brand = "-".join(token.title() for token in tokens(product["brand"]))
        path = f"/{edel_model}-{color.upper()}-de-{edel_brand}.html"
    else:
        path = "/" + slug
    roots = {"optima": "https://optica-optima.com", "bassol": "https://www.opticabassol.com",
             "visual": "https://visual-click.com", "visio": "https://www.visiofactory.es",
             "amevista": "https://www.amevista.com", "otticait": "https://www.otticait.com",
             "edel": "https://www.edel-optics.es"}
    url = roots[shop] + path
    # An observed URL already supplies its precise locale/handle; do not generate locale alternatives.
    if not any(candidate.runnable for candidate in candidates.values()):
        if shop == "amevista" and brand == "GUCCI":
            origin, review_date, review_base = "user-provided candidate URL", REVIEW_DATE, REVIEW_BASE
        elif (shop == "amevista" and brand == "MIUMIU") \
                or (shop == "amevista" and brand == "BOTTEGAVENETA" and category == "sol") \
                or (shop == "amevista" and brand == "RAYBAN") \
                or shop == "edel":
            origin, review_date, review_base = MANUAL_COVERAGE_EVIDENCE, "", ""
        else:
            origin, review_date, review_base = EVIDENCE_SOURCE, REVIEW_DATE, REVIEW_BASE
        candidates.setdefault(
            url, UrlCandidate(url, f"sample_pattern:{shop}", used, sorted(set(refs)), origin=origin,
                              review_date=review_date, review_base=review_base)
        )
    return CandidatePlan(list(candidates.values()))


def candidate_plan(product: dict, domain: str) -> CandidatePlan:
    """Return candidate URLs alongside a non-executable shared discovery plan."""
    plan = _candidate_plan(product, domain)
    host = urlparse(domain if "://" in domain else "https://" + domain).hostname or ""
    shop = DOMAINS.get(host.removeprefix("www.").lower())
    plan.discovery_plan = _catalog_discovery_plan(shop or "", _category(product.get("category", "")))
    return plan
