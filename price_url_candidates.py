"""Local URL hypotheses from the 2026-11-04 documentary review, NOT captured HTTP or authorisation.

Examples keep their original locale/query. Neither a slug nor these indexed identity claims verifies a page.
Only the existing governed ficha reader and commercial classifier may do that in an approved later run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from urllib.parse import parse_qs, urlparse

from price_comparator import normalize_gtin
from price_references import key, model_key, normalize_caliber, tokens

REVIEW_DATE = "2026-11-04"
REVIEW_BASE = "92243628b4e925ba9d549b076580726c383a43ac"
EVIDENCE_SOURCE = "Diagonal_evidencia_Copilot/Evidencias_47.csv"
SUPPLIED_PATTERN_DATE = "2026-11-06"
SUPPLIED_PATTERNS = {
    "domanci": (
        "https://domanci.com/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html",
    ),
    "congafas": (
        "https://www.congafasdesol.com/prada-pr-d01s-0cd80l.html",
        "https://www.congafasdesol.com/prada-pr06ys-09q5s0.html",
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
            caution="Candidata histórica sin reconfirmación; no demuestra ausencia del producto"),
    Example("E033", "domanci", "Miu Miu", "MU 01XV", "VAU1O1", "montura",
            "https://domanci.com/es/monturas/miu-miu-mu-01xv-vau1o1-50.html",
            caution="Candidata histórica sin reconfirmación; no demuestra ausencia del producto"),
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

DOMAINS = {urlparse(example.url).hostname.removeprefix("www."): example.shop for example in EXAMPLES}


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
    if shop == "congafas" and category != "sol":
        return CandidatePlan(reason_code="needs_authorized_discovery",
                             reason="El patrón aportado de ConGafasDeSol corresponde a gafas de sol")
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
    if shop == "domanci":
        lens = normalize_caliber(product.get("caliber", "")).partition("-")[0]
        if not lens:
            return CandidatePlan(reason_code="missing_attribute",
                                 reason="Falta calibre asociado al EAN para construir la URL Domanci; no se infiere")
        used["caliber"] = lens
        used["dimension_source"] = "Explicit caliber on the target product record"
        section = "gafas-de-sol" if category == "sol" else "monturas"
        urls = [f"https://domanci.com/es/{section}/{brand_slug}-{model_slug}-{color}-{lens}.html"]
        evidence = ["user:domanci-route-2026-11-06", "E032", "E033"]
    else:
        # Two supplied Prada examples demonstrate both PR-06YS and PR06YS spelling.
        # Try no more than these two deterministic spellings, never a list of colour/size guesses.
        spellings = [model_slug]
        if key(product["brand"]) == "PRADA" and prefix and prefix[1] == "PR":
            compact = model.lower()
            spellings = [compact, model_slug] if prefix[2][0].isdigit() else [model_slug, compact]
        urls = [f"https://www.congafasdesol.com/{brand_slug}-{spelling}-{color}.html"
                for spelling in dict.fromkeys(spellings)]
        evidence = ["user:congafas-route-2026-11-06"]
    return CandidatePlan([
        UrlCandidate(url, f"supplied_pattern:{shop}", used, evidence,
                     origin="User-provided URL patterns, 2026-11-06",
                     review_date=SUPPLIED_PATTERN_DATE, review_base="")
        for url in urls
    ])


def candidate_plan(product: dict, domain: str) -> CandidatePlan:
    """Pure preview. Missing dimensions/opaque IDs are never enumerated or inferred from another colour."""
    host = urlparse(domain if "://" in domain else "https://" + domain).hostname or ""
    shop = DOMAINS.get(host.removeprefix("www.").lower())
    if not shop:
        return CandidatePlan(reason_code="needs_authorized_discovery", reason="Sin regla documental para el dominio")
    missing = [name for name in ("ean", "brand", "model", "color", "category") if not product.get(name)]
    if missing:
        return CandidatePlan(reason_code="missing_attribute", reason="Falta: " + ", ".join(missing))
    ean = normalize_gtin(product["ean"])
    category = _category(product["category"])
    if not category:
        return CandidatePlan(reason_code="missing_attribute", reason="Falta categoría reconocida: sol o montura")
    brand, model, color = key(product["brand"]), model_key(product["model"]), key(product["color"])
    attributes = {name: str(product[name]) for name in ("ean", "brand", "model", "color", "category")}
    attributes["ean"] = ean
    # An explicitly sized target can use the supplied Domanci route without treating
    # an old historical URL (which did not associate its size to this EAN) as verified.
    if shop == "domanci" and normalize_caliber(product.get("caliber", "")):
        return _supplied_pattern_plan(product, shop, category, attributes)
    examples = [example for example in EXAMPLES if example.shop == shop]
    candidates: dict[str, UrlCandidate] = {}
    for example in examples:
        if shop == "amevista" and not urlparse(example.url).path.startswith("/es/"):
            continue
        if (key(example.brand), model_key(example.model), example.category) != (brand, model, category):
            continue
        if example.color and key(example.color) != color:
            continue
        reason = example.caution
        if not example.color:
            reason = "Ficha padre sin color: resolver URL completa de variante, identidad, precio y stock"
        gtins = {normalize_gtin(value) for value in example.gtins}
        if gtins and gtins != {ean}:
            reason = "GTIN documentados contradictorios o distintos del objetivo: requiere revisión"
        used = dict(attributes)
        if example.gtins:
            used["documented_gtins"] = ", ".join(example.gtins)
        product_id = parse_qs(urlparse(example.url).query).get("selectedSize")
        if product_id:
            used["selectedSize_product_id"] = product_id[0]
        candidates[example.url] = UrlCandidate(
            example.url, "documented_url:" + example.id, used, [example.id],
            state="probable" if reason else "pending_verification", reason=reason)

    if candidates:
        needs_review = any(not candidate.runnable for candidate in candidates.values())
        return CandidatePlan(list(candidates.values()),
                             "needs_authorized_discovery" if needs_review else "",
                             "; ".join(candidate.reason for candidate in candidates.values() if candidate.reason))

    if shop in SUPPLIED_PATTERNS:
        return _supplied_pattern_plan(product, shop, category, attributes)

    # Keep generated URLs within the reviewed scopes and explicitly supplied patterns.
    scopes = {
        "optima": {("PRADA", "sol"), ("TOMFORD", "sol"), ("TOMFORD", "montura")},
        "bassol": {("PRADA", "sol"), ("MIUMIU", "montura"), ("GUCCI", "sol")},
        "amevista": {
            ("GUCCI", "sol"), ("PRADA", "sol"), ("TOMFORD", "sol"), ("TIFFANYCO", "sol"),
        },
        "visual": {("PRADA", "sol"), ("MIUMIU", "montura")},
        "otticait": {("PRADA", "sol"), ("TOMFORD", "montura")},
        "visio": {("PRADA", "sol")},
    }
    if (brand, category) not in scopes.get(shop, set()) or (shop == "visio" and model != "PR27ZS"):
        if shop == "amevista" and category != "sol":
            reason = "La evidencia Amevista respalda rutas marca-modelo-color solo para gafas de sol"
        elif shop == "amevista":
            reason = "Marca fuera de los patrones Amevista documentados; requiere URL/alias acreditado"
        else:
            reason = {
                "blickers": "Falta ID opaco por color y calibre; resolver mediante fuente autorizada",
                "edel": "Falta Product Id de selectedSize; no es calibre ni EAN",
                "mia": "Falta handle y nombre comercial acreditados (regard/dune no se eliminan)",
                "opticalh": "Falta handle/variante acreditados; no exigir ni inventar sufijo numérico",
                "universitaria": "Falta REF/handle de variante; puede ser alfanumérico",
                "celine": "Falta ID/URL completa de variante con color, precio y stock vinculados",
                "congafas": "Falta handle/nombre comercial por producto; el orden no es universal",
                "domanci": "Evidencia insuficiente; candidatas históricas pendientes, no producto inexistente",
            }.get(shop, "Marca/categoría/modelo fuera del alcance demostrado; requiere descubrimiento autorizado")
        return CandidatePlan(list(candidates.values()), "needs_authorized_discovery", reason)

    used = dict(attributes)
    lens, _, bridge = normalize_caliber(product.get("caliber", "")).partition("-")
    refs = [example.id for example in examples if key(example.brand) == brand and example.category == category]
    if shop == "amevista":
        used["locale"] = "es (Spanish-market catalog; E021 documents a separate US URL)"
        if brand == "TIFFANYCO":
            used["brand_alias"] = "tiffany (E020, E021)"
        elif brand == "GUCCI":
            refs.append("user-provided candidate URL")
            used["evidence_note"] = "URL pattern only; not identity evidence"
        elif brand == "PRADA":
            used["model_alias_rule"] = "Observed PR model prefix; 0PR retained only on exact E017 URL"
    # Only this GTIN has dimensions explicitly associated in the package. Do not enrich the product record.
    if shop in {"bassol", "visual", "visio"} and ean == "8056597875349" \
            and (brand, model, color) == ("PRADA", "PR27ZS", "16K08Z") and not lens:
        lens, bridge = "54", "21"
        refs.append("E041")
        used["dimension_source"] = "E041: medidas asociadas al GTIN en muestra indexada"
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
    slug = f"{brand_slug}-{model_slug}-{color.lower()}"
    if shop == "optima":
        path = f"/{'gafas-de-sol' if category == 'sol' else 'gafas-graduadas'}/{slug}.html"
    elif shop in {"bassol", "visual"}:
        path = f"/products/{slug}-{lens}"
    elif shop == "visio":
        path = f"/gafas-de-sol/todas-las-solares/{model.lower()}-{color.lower()}-{lens}-{bridge}.html"
    elif shop == "amevista":
        path = f"/es/{slug}"
    else:
        path = "/" + slug
    roots = {"optima": "https://optica-optima.com", "bassol": "https://www.opticabassol.com",
             "visual": "https://visual-click.com", "visio": "https://www.visiofactory.es",
             "amevista": "https://www.amevista.com", "otticait": "https://www.otticait.com"}
    url = roots[shop] + path
    # An observed URL already supplies its precise locale/handle; do not generate locale alternatives.
    if not any(candidate.runnable for candidate in candidates.values()):
        origin = "user-provided candidate URL" if shop == "amevista" and brand == "GUCCI" \
            else EVIDENCE_SOURCE
        candidates.setdefault(
            url, UrlCandidate(url, f"sample_pattern:{shop}", used, sorted(set(refs)), origin=origin)
        )
    return CandidatePlan(list(candidates.values()))
