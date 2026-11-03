"""Automatic confirmation rule for discovered competitor product pages (fichas).

Pure decision code: no network and no writes. The commercial identity is brand + model + colour code. Calibre,
price and stock never decide identity. A published EAN is optional, but a contradictory one always needs review.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping

from price_references import EXACT, classify, key, model_key
from price_web import WebSearchError, _same_site, validate_competitor_url
from price_web_extract import ProductPage

AUTO_RULE = "identidad_comercial_v1"
AUTO_RULE_TEXT = ("Marca, modelo y código de color publicados e idénticos al catálogo; el calibre, el precio y el "
                  "stock no intervienen; el EAN publicado es opcional y nunca puede contradecir al catálogo")
AUTO_SOURCE = "auto_confirmed"
AUTO_ORIGIN = "automatic"
MANUAL_ORIGIN = "manual"
# Candidates built from a search-results card: the product page itself was never requested.
SEARCH_CARD_METHODS = frozenset({"web:edel-optics-search-card"})
_PAGE_FIELDS = {item.name for item in fields(ProductPage)}


@dataclass
class AutoDecision:
    confirm: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)


def method_authorized(config: Mapping[str, Any], method: str) -> str:
    """Return an empty string when ``method`` is currently authorized for discovery, else the reason."""
    if not config.get("terms_reviewed") or not str(config.get("terms_note") or "").strip() \
            or config.get("access_status") != "robots_allowed":
        return "Sin autorización comercial y robots.txt verificado"
    mode = config.get("search_mode") or "auto"
    if mode == "known":
        return "Competidor configurado solo para URLs conocidas"
    allowed = set(str(config.get("locate_strategy") or "").split(","))
    if method == "sitemap":
        return "" if "sitemap" in allowed else "Localización por sitemap no autorizada"
    if not config.get("search_verified") or "site_search" not in allowed:
        return "Búsqueda GET no verificada o no autorizada"
    permitted = {mode} if mode != "auto" else {
        config.get("search_preferred") or "", *str(config.get("search_alternatives") or "").split(",")}
    return "" if method and method in permitted else f"Método «{method or 'desconocido'}» no autorizado"


def page_from_evidence(evidence: Mapping[str, Any]) -> ProductPage | None:
    data = evidence.get("page") if isinstance(evidence, Mapping) else None
    if not isinstance(data, Mapping) or not data.get("url"):
        return None
    try:
        return ProductPage(**{name: value for name, value in data.items() if name in _PAGE_FIELDS})
    except TypeError:
        return None


def evaluate(product: Mapping[str, Any], config: Mapping[str, Any], *, method: str, evidence: Mapping[str, Any],
             fetched_ok: bool, previously_discarded: bool, previously_rejected: bool) -> AutoDecision:
    """Decide whether a stored discovery candidate can be confirmed without a manual review.

    Every check is evaluated so the audit record explains all missing conditions, not only the first one.
    """
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    authorization = method_authorized(config, method)
    checks["metodo_autorizado"] = not authorization
    if authorization:
        reasons.append(authorization)

    url = str(evidence.get("url") or "")
    page = page_from_evidence(evidence)
    domain = str(config.get("domain") or "")
    try:
        validate_competitor_url(url, domain)
        in_domain = bool(domain) and _same_site(url, domain) and (page is None or _same_site(page.url, domain))
    except WebSearchError:
        in_domain = False
    checks["dominio_competidor"] = in_domain
    if not in_domain:
        reasons.append("La URL no pertenece al dominio del competidor")

    page_method = str(evidence.get("method") or "")
    visited = page is not None and page_method not in SEARCH_CARD_METHODS and not page.source_url
    checks["ficha_consultada"] = visited and fetched_ok
    if page is None:
        reasons.append("No hay evidencia analizada de la ficha")
    elif not visited:
        reasons.append("La ficha no se consultó directamente (solo una tarjeta de resultados de búsqueda)")
    elif not fetched_ok:
        reasons.append("No consta una respuesta HTTP 2xx de la ficha en la ejecución registrada")

    catalogue = {"brand": key(product.get("brand")), "model": model_key(product.get("model")),
                 "color": key(product.get("color"))}
    normalized: dict[str, Any] = {}
    match_basis = ""
    identity_ok = False
    ean_ok = False
    unambiguous = False
    if page is not None:
        match = classify(product, page.candidate())
        normalized, match_basis = match.normalized, match.basis
        listing = normalized.get("listing", {})
        published_ean = str(listing.get("ean") or "")
        ean_ok = not published_ean or published_ean == str(product.get("ean") or "")
        unambiguous = not page.ambiguous and evidence.get("state") == "exact"
        field_names = (("brand", "brands", "Marca"), ("model", "models", "Modelo"),
                       ("color", "colors", "Código de color"))
        identity_ok = match.status == EXACT
        for name, listing_key, label in field_names:
            expected, found = catalogue[name], set(listing.get(listing_key) or [])
            if not expected:
                identity_ok = False
                reasons.append(f"{label} sin valor en el catálogo")
            elif not found:
                identity_ok = False
                reasons.append(f"{label} no publicado en la ficha" if name != "color"
                               else "La ficha no publica el código de color")
            elif found != {expected}:
                identity_ok = False
                reasons.append(f"{label} distinto: catálogo {expected}, ficha {', '.join(sorted(found))}")
        if not ean_ok:
            reasons.append(f"EAN publicado contradictorio: ficha {published_ean}, catálogo {product.get('ean')}")
        if not unambiguous:
            reasons.append("Coincidencia ambigua o no exacta: requiere revisión manual")
        if match.status != EXACT:
            reasons.extend(reason for reason in match.reasons if reason not in reasons)
    checks["marca_modelo_color_exactos"] = identity_ok
    checks["ean_no_contradictorio"] = ean_ok
    checks["sin_ambiguedad"] = unambiguous

    checks["url_no_descartada"] = not previously_discarded
    if previously_discarded:
        reasons.append("La URL fue descartada anteriormente")
    checks["url_no_rechazada"] = not previously_rejected
    if previously_rejected:
        reasons.append("La URL fue rechazada anteriormente por identidad")

    confirm = all(checks.values())
    audit = {
        "rule": AUTO_RULE, "rule_text": AUTO_RULE_TEXT, "method": method, "page_method": page_method, "url": url,
        "checks": checks, "reasons": reasons, "match_basis": match_basis, "catalogue": catalogue,
        "listing": normalized.get("listing", {}),
        # Informational only: never part of the identity decision.
        "informational": {"caliber": page.caliber if page else "", "price": page.price if page else None,
                          "availability": page.availability if page else ""},
    }
    return AutoDecision(confirm, reasons, checks, audit)
