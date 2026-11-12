"""Extraction of product facts from a public product page (no network access here).

Priority: JSON-LD ``Product``/``Offer`` > page metadata (OpenGraph/itemprop) > configured CSS selector.
Nothing is invented: a field the page does not publish stays empty, and the VAT treatment stays
unknown unless the page states it explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from price_comparator import normalize_gtin
from price_references import (caliber_from_code, compare_calibers, extract_caliber, extract_lens,
                              normalize_caliber, parse_reference)

_LD_JSON = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_EAN_LABELLED = re.compile(r"(?:\bEAN(?:/GTIN)?\b|\bGTIN(?:-?13)?\b|c[oó]digo\s+de\s+barras)\s*:?\s*"
                           r"(?:<[^>]{0,200}>\s*){0,4}:?\s*(\d{13})(?!\d)", re.I)
_EAN_DATA_ATTR = re.compile(r"\bdata-ean=[\"'](\d{13})[\"']", re.I)
_PRESTASHOP_CURRENCY = re.compile(r"var prestashop\s*=\s*\{\"currency\":\{[^{}]*?\"iso_code\":\"([A-Z]{3})\"")
_AVAILABLE = {"instock", "limitedavailability"}
_UNAVAILABLE = {"outofstock", "soldout", "discontinued"}
_VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "embed", "wbr"}
_EDEL_SIZE_CLASS = re.compile(r"(?:^|\s)eoDetailListTileSize(\d+)(?:\s|$)")


@dataclass
class ProductPage:
    url: str
    method: str = ""
    structured_type: str = ""
    title: str = ""
    brand: str = ""
    model: str = ""
    color: str = ""
    caliber: str = ""
    ean_published: str = ""
    identifiers: list[str] = field(default_factory=list)
    price: float | None = None
    previous_price: float | None = None
    currency: str = ""
    availability: str = "unknown"
    includes_tax: bool | None = None
    configured_tax_policy: str = "unknown"
    tax_policy_evidence: str = ""
    tax_conflict: bool = False
    vat_rate: float | None = None
    market: str = ""
    market_source: str = ""
    market_conflict: bool = False
    ambiguous: bool = False
    source_url: str = ""
    variant_urls: list[str] = field(default_factory=list)
    structured_variants: list[dict[str, Any]] = field(default_factory=list)
    variant_id: str = ""
    variant_scoped: bool = False
    stock_evidence: list[str] = field(default_factory=list)
    delivery_evidence: list[str] = field(default_factory=list)
    stock_conflict: bool = False
    notes: list[str] = field(default_factory=list)
    url_trace: dict[str, Any] = field(default_factory=dict)

    @property
    def has_price(self) -> bool:
        return self.price is not None and bool(self.currency)

    def candidate(self) -> dict[str, Any]:
        """The listing as seen by ``price_references.classify``."""
        return {"brand": self.brand, "model": self.model, "color": self.color, "caliber": self.caliber,
                "ean_published": self.ean_published, "title": self.title,
                "identifiers": self.identifiers, "ambiguous": self.ambiguous}


def parse_price(value: Any) -> float | None:
    """'239', '239.00', '239,00', '1.234,56', '1,234.56' and '239,00 €' -> float; anything else -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head}.{tail}" if len(tail) in {1, 2} else text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        result = float(text)
    except ValueError:
        return None
    return result if result >= 0 else None


def availability_of(value: Any) -> str:
    token = re.sub(r"[^a-z]", "", str(value or "").rsplit("/", 1)[-1].lower())
    if token in _AVAILABLE:
        return "available"
    if token in _UNAVAILABLE:
        return "unavailable"
    return "unknown"


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return True if text in {"true", "1", "yes", "sí", "si"} else False if text in {"false", "0", "no"} else None


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def _nodes(data: Any) -> Iterable[dict]:
    for item in _as_list(data):
        if isinstance(item, dict):
            yield item
            yield from _nodes(item.get("@graph"))


def _is_type(node: dict, name: str) -> bool:
    return any(str(t).rsplit("/", 1)[-1] == name for t in _as_list(node.get("@type")))


def _name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    return str(value).strip() if value is not None else ""


def _gtins(node: dict) -> list[str]:
    found: list[str] = []
    for key in ("gtin13", "gtin12", "gtin14", "gtin8", "gtin", "ean"):
        for raw in _as_list(node.get(key)):
            try:
                found.append(normalize_gtin(raw))
            except ValueError:
                continue
    return list(dict.fromkeys(found))


def _url_key(url: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(url)
    return (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"),
            tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))))


def _product_urls(node: dict, url: str) -> list[str]:
    declared = [node.get("url")]
    for offer in _as_list(node.get("offers")):
        if isinstance(offer, dict):
            declared.append(offer.get("url"))
            declared.extend(o.get("url") for o in _as_list(offer.get("offers")) if isinstance(o, dict))
    return list(dict.fromkeys(urljoin(url, value) for value in declared if isinstance(value, str) and value))


def _matches_product_url(node: dict, url: str) -> bool:
    declared = node.get("url")
    if isinstance(declared, str):
        identity_url = urljoin(url, declared)
        if any(key in {"variant", "selectedSize"} for key, _ in parse_qsl(urlparse(identity_url).query)) \
                and _url_key(identity_url) != _url_key(url):
            return False
    return any(_url_key(value) == _url_key(url) for value in _product_urls(node, url))


def _price_restrictions(node: dict) -> list[str]:
    fields = ("eligibleQuantity", "eligibleCustomerType", "validForMemberTier", "eligibleTransactionVolume")
    restrictions = [field for field in fields if node.get(field) is not None]
    if re.search(r"cup[oó]n|coupon|promo(?:tional)? code", _name(node.get("name")), re.I):
        restrictions.append("nombre de oferta promocional")
    return restrictions


def _conditional_price(node: dict) -> bool:
    return bool(_price_restrictions(node))


def _offer_facts(node: dict, url: str) -> dict[str, Any]:
    """price/currency/availability/VAT from the offers of one Product node; several different prices = ambiguous."""
    offers: list[tuple[dict, tuple[str, ...]]] = []

    def collect(offer: dict, inherited: tuple[str, ...] = ()) -> None:
        inner = [item for item in _as_list(offer.get("offers")) if isinstance(item, dict)]
        owner = "AggregateOffer" if _is_type(offer, "AggregateOffer") else "Offer"
        restrictions = (*inherited, *(f"{owner}.{field}" for field in _price_restrictions(offer)))
        if inner:
            for item in inner:
                collect(item, restrictions)
        else:
            offers.append((offer, restrictions))

    for offer in _as_list(node.get("offers")):
        if isinstance(offer, dict):
            collect(offer)
    facts: dict[str, Any] = {
        "ambiguous": False, "variant_conflict": False, "notes": [], "stock_evidence": [], "delivery_evidence": []
    }
    variant_offers = [(o, restrictions) for o, restrictions in offers if isinstance(o.get("url"), str)
                      and urlparse(urljoin(url, o["url"])).path == urlparse(url).path
                      and urlparse(urljoin(url, o["url"])).query]
    if variant_offers and (len(variant_offers) > 1 or any(
            key in {"variant", "selectedSize"}
            for o, _ in variant_offers for key, _ in parse_qsl(urlparse(o["url"]).query))):
        selected_offers = [(o, restrictions) for o, restrictions in variant_offers
                           if _url_key(urljoin(url, o["url"])) == _url_key(url)]
        if not selected_offers:
            facts["ambiguous"] = True
            facts["variant_conflict"] = True
            facts["notes"].append("Ninguna oferta corresponde a la URL de variante solicitada")
            offers = []
        else:
            variant_offer_ids = {id(offer) for offer, _ in variant_offers}
            shared_offers = [(offer, restrictions) for offer, restrictions in offers
                             if id(offer) not in variant_offer_ids]
            offers = [*shared_offers, *selected_offers]
    markets: set[str] = set()
    seen = []
    for offer, inherited_restrictions in offers:
        if inherited_restrictions:
            price = parse_price(offer.get("price"))
            currency = str(offer.get("priceCurrency") or "").upper()
            quoted_price = f" ({price:g} {currency})" if price is not None and currency else ""
            facts["notes"].append(
                f"Oferta condicionada excluida ({'; '.join(inherited_restrictions)}){quoted_price}: "
                "no se usa como precio de venta")
            continue
        raw_stock = str(offer.get("availability") or "")
        if raw_stock:
            facts["stock_evidence"].append(raw_stock)
        for region in _as_list(offer.get("eligibleRegion")):
            country = _name(region.get("addressCountry")) if isinstance(region, dict) else _name(region)
            if re.fullmatch(r"[A-Za-z]{2}", country):
                markets.add(country.upper())
        for shipping in _as_list(offer.get("shippingDetails")):
            if isinstance(shipping, dict):
                if shipping.get("deliveryTime"):
                    facts["delivery_evidence"].append(json.dumps(shipping["deliveryTime"], ensure_ascii=False))
                for destination in _as_list(shipping.get("shippingDestination")):
                    country = _name(destination.get("addressCountry")) if isinstance(destination, dict) else ""
                    if re.fullmatch(r"[A-Za-z]{2}", country):
                        markets.add(country.upper())
        price, previous, tax = None, None, _truthy(offer.get("valueAddedTaxIncluded"))
        for item in _as_list(offer.get("priceSpecification")):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("priceType") or "").rsplit("/", 1)[-1].lower()
            value = parse_price(item.get("price"))
            if kind in {"listprice", "strikethroughprice", "srp", "msrp"}:
                previous = previous or value
            elif _conditional_price(item):
                facts["notes"].append(
                    f"Precio condicionado excluido ({'; '.join(_price_restrictions(item))}): "
                    "no se usa como precio de venta")
                continue
            elif kind in {"", "saleprice"} and value is not None and price is None:
                price = value
            elif value is not None and kind not in {"", "saleprice"}:
                facts["notes"].append("Tipo de precio no verificado como venta: " + kind)
            if kind in {"", "saleprice"} and item.get("valueAddedTaxIncluded") is not None:
                declared_tax = _truthy(item.get("valueAddedTaxIncluded"))
                if tax is not None and declared_tax is not None and declared_tax != tax:
                    facts["tax_conflict"] = facts["ambiguous"] = True
                    facts["notes"].append("Declaraciones de IVA contradictorias entre Offer y precio de venta")
                elif tax is None:
                    tax = declared_tax
        if offer.get("price") is not None:
            price = parse_price(offer.get("price"))
        if price is None and _is_type(offer, "AggregateOffer"):
            facts["notes"].append("AggregateOffer sin precio único: no se usa lowPrice")
        seen.append((price, str(offer.get("priceCurrency") or "").upper(), availability_of(offer.get("availability")),
                     tax, previous))
    distinct = {(item[0], item[1], item[3]) for item in seen if item[0] is not None}
    if len(distinct) > 1:
        facts["ambiguous"] = True
        facts["notes"].append("La ficha publica varias ofertas distintas")
    chosen_price = next((item for item in seen if item[0] is not None), None)
    chosen_stock = next((item for item in seen if item[2] != "unknown"), None)
    chosen = chosen_price or (seen[0] if seen else None)
    if chosen:
        facts.update(price=chosen[0], currency=chosen[1],
                     availability=chosen_stock[2] if chosen_stock else chosen[2],
                     includes_tax=chosen[3], previous_price=chosen[4])
    stocks = {item[2] for item in seen if item[2] != "unknown"}
    ordered = any(availability_of(value) == "unknown" and re.search(
        r"backorder|preorder|preventa|pedido\s+anticipado", value, re.I)
                  for value in facts["stock_evidence"])
    facts["stock_conflict"] = len(stocks) > 1 or ("available" in stocks and ordered)
    if facts["stock_conflict"]:
        facts["availability"] = "unknown"
        facts["notes"].append("Stock publicado contradictorio entre ofertas: revisión obligatoria")
    if len(markets) == 1:
        facts["market"] = next(iter(markets))
    elif markets:
        facts["notes"].append("Varios mercados comerciales publicados: requieren revisión")
        facts["market_conflict"] = True
    return facts


def _color_code(value: str) -> str:
    """A colour *code* has digits (16K08Z). Descriptive names ('black', 'Nero') say nothing about the variant."""
    return value if re.search(r"\d", value) else ""


def _from_json_ld(html: str, url: str) -> ProductPage | None:
    records: list[dict[str, Any]] = []
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block.strip(), strict=False)  # shops often leave raw line breaks inside strings
        except json.JSONDecodeError:
            continue
        for node in _nodes(data):
            if _is_type(node, "Product"):
                records.append({"product": node, "shared_offer": None})
            elif _is_type(node, "ProductGroup"):
                common_offer = _common_group_offer(node)
                inherited = {key: node[key] for key in ("name", "brand", "model", "color", "mpn", "sku", "url")
                             if node.get(key) is not None}
                for variant in _as_list(node.get("hasVariant")):
                    if isinstance(variant, dict):
                        records.append({"product": {**inherited, **variant}, "shared_offer": common_offer})
    if not records:
        return None
    products = [record["product"] for record in records]
    page = ProductPage(url=url, method="json-ld", structured_type="schema.org/Product")
    page.variant_urls = list(dict.fromkeys(value for p in products for value in _product_urls(p, url)))
    variants = [value for key, value in parse_qsl(urlparse(url).query)
                if key in {"variant", "selectedSize"}]
    if len(set(variants)) > 1:
        page.ambiguous = True
        page.variant_scoped = True
        page.notes.append("La URL contiene varios identificadores de variante incompatibles")
        return page
    page.variant_id = variants[0] if len(set(variants)) == 1 else ""
    page.variant_scoped = len(products) > 1 or bool(variants) or any(
        key in {"variant", "selectedSize"}
        for value in page.variant_urls for key, _ in parse_qsl(urlparse(value).query))
    matching = [record for record in records if _matches_product_url(record["product"], url)]
    matching = list({json.dumps(record, sort_keys=True): record for record in matching}.values())
    if len(matching) == 1:
        record = matching[0]
        node = _with_shared_group_offer(record["product"], record["shared_offer"])
    elif not matching and len(products) == 1 and not page.variant_scoped:
        node = products[0]
    else:
        page.ambiguous = True
        page.variant_scoped = True
        if len(matching) > 1:
            page.structured_variants = matching
            page.notes.append("Variantes estructuradas pendientes de vincular al EAN objetivo")
        else:
            page.notes.append("La URL no identifica una única variante: no se toma identidad, precio ni stock")
        return page
    page.title = _name(node.get("name"))
    page.brand = _name(node.get("brand"))
    page.model = _name(node.get("model"))
    page.color = _color_code(_name(node.get("color")))
    gtins = _gtins(node)
    page.ean_published = gtins[0] if gtins else ""
    if len(gtins) > 1:
        page.ambiguous = True
        page.notes.append("EAN publicados contradictorios: " + ", ".join(gtins))
    page.identifiers = [str(node[k]) for k in ("mpn", "sku") if node.get(k)]
    page.caliber = (extract_caliber(_name(node.get("size"))) or extract_caliber(page.title)
                    or extract_caliber(urlparse(url).path.rsplit("/", 1)[-1]))
    facts = _offer_facts(node, url)
    page.ambiguous |= facts["ambiguous"]
    if facts["variant_conflict"]:
        page.ean_published = ""
    page.notes += facts["notes"]
    page.price, page.currency = facts.get("price"), facts.get("currency", "")
    page.previous_price = facts.get("previous_price")
    page.availability = facts.get("availability", "unknown")
    page.includes_tax = facts.get("includes_tax")
    page.tax_conflict = facts.get("tax_conflict", False)
    page.market = facts.get("market", "")
    page.market_source = "ficha:JSON-LD eligibleRegion/shippingDestination" if page.market else ""
    page.market_conflict = facts.get("market_conflict", False)
    page.stock_evidence = facts["stock_evidence"]
    page.delivery_evidence = facts["delivery_evidence"]
    page.stock_conflict = facts.get("stock_conflict", False)
    page.notes.extend("Plazo publicado: " + value for value in page.delivery_evidence)
    if page.variant_scoped:
        page.notes.append("Variante según URL: " + url + "; identificadores: " + ", ".join(page.identifiers))
        if any(key == "selectedSize" for key, _ in parse_qsl(urlparse(url).query)):
            page.notes.append("selectedSize se conserva como Product Id de Edel-Optics, no como calibre ni EAN")
    return page


def _common_group_offer(group: dict) -> dict[str, Any] | None:
    offers = [offer for offer in _as_list(group.get("offers")) if isinstance(offer, dict)]
    if len(offers) != 1 or _is_type(offers[0], "AggregateOffer") or _as_list(offers[0].get("offers")):
        return None
    offer = offers[0]
    if _price_restrictions(offer) or parse_price(offer.get("price")) is None \
            or not str(offer.get("priceCurrency") or "").strip():
        return None
    shared = {"@type": "Offer", "price": offer["price"], "priceCurrency": offer["priceCurrency"]}
    if offer.get("valueAddedTaxIncluded") is not None:
        shared["valueAddedTaxIncluded"] = offer["valueAddedTaxIncluded"]
    if offer.get("priceSpecification") is not None:
        shared["priceSpecification"] = offer["priceSpecification"]
    return shared


def _with_shared_group_offer(product: dict, shared_offer: dict | None) -> dict:
    result = dict(product)
    if shared_offer:
        result["offers"] = [shared_offer, *_as_list(result.get("offers"))]
    return result


def select_product_variant(page: ProductPage | None, product: dict[str, Any]) -> ProductPage | None:
    """Bind a shared ProductGroup page to one structured variant by normalized target EAN."""
    if page is None:
        return page
    selected = page
    selected_from_group = bool(page.structured_variants)
    if page.structured_variants:
        try:
            target_ean = normalize_gtin(product.get("ean"))
        except ValueError:
            return replace(page, ambiguous=True, notes=[*page.notes, "EAN objetivo no válido para resolver variante"])
        matches = [record for record in page.structured_variants
                   if target_ean in _gtins(record["product"])]
        if len(matches) != 1:
            reason = ("El EAN objetivo no aparece en una variante estructurada"
                      if not matches else "El EAN objetivo aparece en varias variantes estructuradas")
            return replace(page, ambiguous=True, notes=[*page.notes, reason])
        record = matches[0]
        selected_node = _with_shared_group_offer(record["product"], record["shared_offer"])
        html = ('<script type="application/ld+json">' + json.dumps(
            {"@context": "https://schema.org", "@graph": [selected_node]}, ensure_ascii=False
        ) + "</script>")
        selected = _from_json_ld(html, page.url)
        if selected is None:
            return replace(page, ambiguous=True, notes=[*page.notes, "Variante estructurada no interpretable"])
        selected.structured_variants = page.structured_variants
        selected.variant_scoped = True
        selected.url_trace = page.url_trace
    _apply_tax_policy(selected, page.configured_tax_policy)
    target_caliber = normalize_caliber(product.get("caliber"))
    published_caliber = normalize_caliber(selected.caliber)
    if selected_from_group and target_caliber and published_caliber \
            and compare_calibers(target_caliber, published_caliber) == "different":
        selected.ambiguous = True
        selected.ean_published = ""
        selected.price = None
        selected.currency = ""
        selected.availability = "unknown"
        selected.notes.append("El calibre de la variante estructurada contradice el producto objetivo")
    elif selected_from_group:
        selected.notes.append("Variante ProductGroup vinculada por EAN normalizado, no por orden de listas")
    return selected


class _EdelSelectedRadios(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, values) -> None:
        attrs = dict(values)
        if tag.lower() != "input" or str(attrs.get("type") or "").lower() != "radio":
            return
        match = re.fullmatch(r"prd(\d+)", str(attrs.get("id") or ""), re.I)
        if match and "checked" in attrs:
            self.ids.append(match[1])


def _edel_variant_facts(html: str, selected_id: str) -> tuple[set[str], str, bool]:
    parsed = parse_html(html)
    ids: set[str] = set()
    eans: set[str] = set()
    for element in parsed.elements:
        classes = element["attrs"].get("class", "")
        match = _EDEL_SIZE_CLASS.search(classes)
        if not match:
            continue
        variant_id = match[1]
        ids.add(variant_id)
        if variant_id != selected_id:
            continue
        text = element["text"]
        for raw in re.findall(r"\bEAN\b\s*:?\s*(\d{12,13})(?!\d)", text, re.I):
            try:
                eans.add(normalize_gtin(raw))
            except ValueError:
                continue
    return ids, next(iter(eans)) if len(eans) == 1 else "", len(eans) > 1


def _edel_selection(html: str, url: str) -> tuple[str, str, bool, str]:
    if (urlparse(url).hostname or "").lower().removeprefix("www.") != "edel-optics.es":
        return url, "", False, ""
    query = parse_qsl(urlparse(url).query, keep_blank_values=True)
    requested = [value for key, value in query if key == "selectedSize"]
    if len(set(requested)) > 1 or any(not value for value in requested):
        return url, "", False, "selectedSize ausente o contradictorio"
    radios = _EdelSelectedRadios()
    radios.feed(html.replace("--!>", "-->"))
    radios.close()
    if len(radios.ids) > 1:
        return url, "", False, "La ficha publica más de una opción prd seleccionada"
    radio_id = radios.ids[0] if radios.ids else ""
    if requested and radio_id and requested[0] != radio_id:
        return url, requested[0], False, "selectedSize y el radio prd seleccionado se contradicen"
    selected_id = requested[0] if requested else radio_id
    if not selected_id:
        return url, "", False, ""
    if requested:
        return url, selected_id, False, ""
    query.append(("selectedSize", selected_id))
    selected_url = urlunparse(urlparse(url)._replace(query=urlencode(query)))
    return selected_url, selected_id, True, ""


def _ambiguous_edel_page(url: str, variant_id: str, reason: str) -> ProductPage:
    return ProductPage(url=url, method="edel-variant-selection", structured_type="Edel-Optics variant",
                       variant_id=variant_id, variant_scoped=True, ambiguous=True, notes=[reason])


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.lang = ""
        self.elements: list[dict[str, Any]] = []
        self.links: list[tuple[str, str]] = []
        self.canonicals: list[str] = []
        self._stack: list[dict[str, Any]] = []
        self._in_title = False
        self._link: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = {k: v or "" for k, v in attrs}
        if tag == "html" and attrs.get("lang"):
            self.lang = attrs["lang"]
        if tag == "meta":
            name = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if name and "content" in attrs:
                self.meta.setdefault(name.lower(), attrs["content"])
        elif tag == "title":
            self._in_title = True
        elif tag == "link" and "canonical" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            self.canonicals.append(attrs["href"])
        if tag == "a" and attrs.get("href"):
            self._link = [attrs["href"], ""]
        element = {"tag": tag, "attrs": attrs, "text": "",
                   "parent": self._stack[-1]["index"] if self._stack else None,
                   "index": len(self.elements)}
        self.elements.append(element)
        if tag in _VOID:
            if attrs.get("itemprop") and "content" in attrs:
                self.meta.setdefault(attrs["itemprop"].lower(), attrs["content"])
            elif attrs.get("itemprop") and attrs.get("href"):
                self.meta.setdefault(attrs["itemprop"].lower(), attrs["href"])
            return
        self._stack.append(element)
        if attrs.get("itemprop") and "content" in attrs:
            self.meta.setdefault(attrs["itemprop"].lower(), attrs["content"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._link:
            self.links.append((self._link[0], self._link[1].strip()))
            self._link = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._link is not None:
            self._link[1] += data
        for element in self._stack[-6:]:
            element["text"] += data


_SELECTOR = re.compile(r"^(?P<tag>[a-z0-9]+)?(?P<id>#[\w-]+)?(?P<classes>(?:\.[\w-]+)*)(?P<attr>\[[^\]]+\])?$", re.I)


def css_text(parsed: _Collector, selector: str) -> str:
    """First text matching one compound selector: ``tag#id.class[attr=value]`` (no combinators)."""
    match = _SELECTOR.match(selector.strip()) if selector.strip() else None
    if not match:
        return ""
    classes = [c for c in match["classes"].split(".") if c]
    attr_name = attr_value = None
    if match["attr"]:
        attr_name, _, attr_value = match["attr"][1:-1].partition("=")
        attr_value = attr_value.strip("\"'")
    for element in parsed.elements:
        attrs = element["attrs"]
        if match["tag"] and element["tag"] != match["tag"].lower():
            continue
        if match["id"] and attrs.get("id") != match["id"][1:]:
            continue
        if any(c not in attrs.get("class", "").split() for c in classes):
            continue
        if attr_name and (attr_name not in attrs or (attr_value and attrs[attr_name] != attr_value)):
            continue
        return (attrs.get("content") or " ".join(element["text"].split()))
    return ""


def parse_html(html: str) -> _Collector:
    parsed = _Collector()
    parsed.feed(html.replace("--!>", "-->"))  # HTML5 closes a comment with --!>; the stdlib parser would swallow the page
    parsed.close()
    return parsed


def _edel_microdata_page(parsed: _Collector, url: str, selected_id: str,
                         ids: set[str], selected_ean: str) -> ProductPage | None:
    """Only the main price widget, with EAN and Offer tied to the selected variant."""
    if not selected_ean:
        return None

    def ancestors(element: dict) -> list[dict]:
        parents = []
        index = element["parent"]
        while index is not None:
            parent = parsed.elements[index]
            parents.append(parent)
            index = parent["parent"]
        return parents

    def product_scope(element: dict) -> int | None:
        return next((parent["index"] for parent in ancestors(element)
                     if parent["attrs"].get("itemtype", "").rsplit("/", 1)[-1]
                     in {"Product", "ProductGroup"}), None)

    def related(element: dict) -> bool:
        return any(parent["tag"] == "aside"
                   or "eoViewsListviewItemClassic" in parent["attrs"].get("class", "").split()
                   for parent in [element, *ancestors(element)])

    def properties(root: dict) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        for element in parsed.elements[root["index"] + 1:]:
            parents = ancestors(element)
            scope = next((parent for parent in parents if "itemscope" in parent["attrs"]), None)
            if scope is not root:
                continue
            attrs = element["attrs"]
            value = ({"@type": attrs.get("itemtype", "").rsplit("/", 1)[-1], **properties(element)}
                     if "itemscope" in attrs else
                     attrs.get("content") or attrs.get("href") or attrs.get("value") or element["text"].strip())
            for name in attrs.get("itemprop", "").split():
                if name in {"name", "brand", "model", "color", "sku", "gtin12", "gtin13", "url",
                            "price", "priceCurrency", "availability", "valueAddedTaxIncluded",
                            "priceSpecification", "priceType",
                            "shippingDetails", "shippingDestination", "addressCountry", "deliveryTime",
                            "handlingTime", "transitTime", "minValue", "maxValue", "unitCode"} and value:
                    old = facts.get(name)
                    if old is not None and old != value:
                        values = _as_list(old)
                        facts[name] = values if value in values else [*values, value]
                    else:
                        facts[name] = value
        return facts

    widgets = [element for element in parsed.elements
               if element["attrs"].get("itemtype", "").rsplit("/", 1)[-1] == "Offer"
               and any("eoProductPrice" in parent["attrs"].get("class", "").split()
                       for parent in ancestors(element))
               and not related(element)]
    if not widgets:
        return None
    offers = []
    selected_widgets = []
    for widget in widgets:
        scoped_ids = {match[1] for parent in [widget, *ancestors(widget)]
                      if (match := re.search(
                          r"(?:^|\s)eo(?:DetailListTileSize|ProductsPriceBySize-)(\d+)(?:\s|$)",
                          parent["attrs"].get("class", "")))}
        if scoped_ids and selected_id not in scoped_ids:
            continue
        if len(scoped_ids) > 1:
            return _ambiguous_edel_page(url, selected_id, "La oferta pertenece a varios Product Id")
        offer = properties(widget)
        if any(isinstance(value, list) for value in offer.values()):
            return _ambiguous_edel_page(url, selected_id, "Microdatos de oferta contradictorios")
        if offer.get("sku") and str(offer["sku"]) != selected_id:
            return _ambiguous_edel_page(url, selected_id, "El Product Id de la oferta contradice selectedSize")
        if any(name in offer for name in ("gtin12", "gtin13")) and _gtins(offer) != [selected_ean]:
            return _ambiguous_edel_page(url, selected_id, "El EAN de la oferta contradice la variante seleccionada")
        offer_url = offer.get("url")
        if offer_url:
            if not isinstance(offer_url, str) or _url_key(urljoin(url, offer_url)) != _url_key(url):
                return _ambiguous_edel_page(url, selected_id, "La URL de la oferta contradice selectedSize")
        elif ids != {selected_id} and scoped_ids != {selected_id}:
            return _ambiguous_edel_page(
                url, selected_id, "EAN vinculado, pero la oferta/stock no se vincula al Product Id seleccionado")
        offers.append(offer)
        selected_widgets.append(widget)
    if not offers:
        return _ambiguous_edel_page(url, selected_id, "La oferta pertenece a otro Product Id")
    if any(offer != offers[0] for offer in offers[1:]):
        return _ambiguous_edel_page(url, selected_id, "Ofertas del Product Id seleccionado contradictorias")
    widget, offer = selected_widgets[0], offers[0]
    root_index = product_scope(widget)
    if any(product_scope(other) != root_index for other in selected_widgets):
        return _ambiguous_edel_page(url, selected_id, "Ofertas de distintos Product para la variante seleccionada")
    tiles = [element for element in parsed.elements
             if (match := _EDEL_SIZE_CLASS.search(element["attrs"].get("class", "")))
             and match[1] == selected_id and product_scope(element) == root_index and not related(element)]
    if not any(selected_ean in _gtins({"gtin": re.findall(
            r"\bEAN\b\s*:?\s*(\d{12,13})(?!\d)", tile["text"], re.I)}) for tile in tiles):
        return None
    node = properties(parsed.elements[root_index]) if root_index is not None else {}
    additional_names = []
    if isinstance(node.get("name"), list):
        node["name"], *additional_names = node["name"]
    if any(isinstance(value, list) for value in node.values()):
        return _ambiguous_edel_page(url, selected_id, "Microdatos de producto contradictorios")
    if node.get("sku") and str(node["sku"]) != selected_id:
        return _ambiguous_edel_page(url, selected_id, "El Product Id del producto contradice selectedSize")
    if node.get("url"):
        published_url = urlparse(urljoin(url, node["url"]))
        requested = urlparse(url)
        published_query = parse_qsl(published_url.query, keep_blank_values=True)
        expected_query = parse_qsl(requested.query, keep_blank_values=True)
        if published_url.scheme not in {"http", "https"} or published_url.netloc != requested.netloc \
                or published_url.path != requested.path \
                or (published_query and sorted(published_query) != sorted(expected_query)):
            return _ambiguous_edel_page(url, selected_id, "La URL del producto contradice selectedSize")
        node["url"] = url
    node.setdefault("name", parsed.meta.get("og:title") or " ".join(parsed.title.split()))
    node.setdefault("url", url)
    if any(name in node for name in ("gtin12", "gtin13")):
        if len(_gtins(node)) != 1:
            return _ambiguous_edel_page(url, selected_id, "EAN de producto no válido o contradictorio")
    else:
        node["gtin13"] = selected_ean
    node.update({"@type": "Product", "offers": {"@type": "Offer", **offer}})
    page = _from_json_ld('<script type="application/ld+json">' + json.dumps(node) + "</script>", url)
    if page is not None:
        page.method = "edel-microdata"
        page.identifiers.extend(additional_names)
        page.notes.append("Microdatos del precio principal vinculados al EAN y Product Id seleccionado")
    return page


class _EdelSearchCards(HTMLParser):
    """Extract facts only from each product-card container in an Edel-Optics result page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._card: dict[str, Any] | None = None
        self._stack: list[tuple[str, bool]] = []
        self._captures: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, values) -> None:
        attrs = {key: value or "" for key, value in values}
        classes = set(attrs.get("class", "").split())
        root = tag == "div" and "eoViewsListviewItemClassic" in classes
        if tag == "br" and self._captures:
            for capture in self._captures:
                capture["parts"].append(" ")
        if root and self._card is None:
            self._card = {"price": "", "currency": "", "url": "", "title": "", "link_title": "", "brand": "",
                          "ean_published": "", "availability": "unknown"}
        if self._card is None:
            return
        prop = attrs.get("itemprop", "").lower()
        if prop in {"price", "pricecurrency", "availability", "gtin13", "gtin", "model", "color"}:
            value = attrs.get("content") or attrs.get("href") or attrs.get("value") or ""
            if value:
                field = {"pricecurrency": "currency", "gtin13": "ean_published",
                         "gtin": "ean_published"}.get(prop, prop)
                self._card[field] = value
        if prop == "url" and attrs.get("href"):
            self._card["url"] = attrs["href"]
        if tag == "a" and attrs.get("href") and not self._card["url"]:
            self._card["url"] = attrs["href"]
        if tag == "a" and attrs.get("title") and not self._card["link_title"]:
            self._card["link_title"] = attrs["title"]
        capture = ""
        if prop == "name":
            capture = "title"
        elif "eoBrandListviewItemTxt" in classes:
            capture = "brand"
        if capture:
            self._captures.append({"tag": tag, "field": capture, "parts": []})
        if tag not in _VOID:
            self._stack.append((tag, root))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._captures) - 1, -1, -1):
            if self._captures[index]["tag"] == tag:
                capture = self._captures.pop(index)
                text = " ".join("".join(capture["parts"]).split())
                if text and self._card is not None:
                    old = self._card.get(capture["field"], "")
                    self._card[capture["field"]] = " ".join(filter(None, (old, text)))
                break
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                closed = self._stack[index:]
                del self._stack[index:]
                if any(is_root for _, is_root in closed) and self._card is not None:
                    self.cards.append(self._card)
                    self._card = None
                    self._captures.clear()
                break


def extract_edel_search_cards(html: str, search_url: str, product: dict, *,
                              tax_policy: str = "unknown") -> list[ProductPage]:
    """Parse listing-card facts independently; never treat the result page's aggregate metadata as a product."""
    parser = _EdelSearchCards()
    parser.feed(html)
    parser.close()
    pages: list[ProductPage] = []
    for card in parser.cards:
        title = (card["title"] or card["link_title"]).strip()
        brand = card["brand"].strip()
        reference = parse_reference(title, brands=[str(product.get("brand") or "")],
                                    models=[str(product.get("model") or "")])
        model = card.get("model") or reference.model
        color = _color_code(card.get("color", "")) or _color_code(reference.color)
        if not title:
            title = " ".join(part for part in (brand, model, color) if part)
        try:
            ean = normalize_gtin(card["ean_published"]) if card["ean_published"] else ""
        except ValueError:
            ean = ""
        page = ProductPage(
            url=card["url"], source_url=search_url, method="edel-optics-search-card",
            structured_type="Edel-Optics result card", title=title, brand=brand, model=model, color=color,
            caliber=reference.caliber, ean_published=ean, price=parse_price(card["price"]),
            currency=card["currency"].upper(), availability=availability_of(card["availability"]),
            identifiers=[f"{model} {color}".strip()] if model and color else [],
            notes=["Datos extraídos de esta tarjeta; el HTML no publica disponibilidad"
                   if card["availability"] == "unknown" else "Datos extraídos de esta tarjeta"])
        if card.get("availability"):
            page.notes = ["Datos extraídos de esta tarjeta"]
        _apply_tax_policy(page, tax_policy)
        if not page.ean_published:
            page.notes.append("La tarjeta no publica EAN; identidad basada en referencia")
        pages.append(page)
    return pages


def product_page_rejection(html: str, url: str) -> str:
    """Positive listing evidence, never inferred from a missing product match or a redirect."""
    parsed_url = urlparse(url)
    if (parsed_url.hostname or "").lower().removeprefix("www.") == "congafasdesol.com" \
            and not parsed_url.path.strip("/"):
        return "no_product: portada de ConGafasDeSol, no ficha de producto"
    if (parsed_url.hostname or "").lower().removeprefix("www.") == "optica-optima.com" \
            and parsed_url.path.rstrip("/") in {"/gafas-de-sol", "/gafas-graduadas"}:
        return "no_product: ruta de categoría de Óptica Óptima, no ficha de producto"
    nodes = []
    for block in _LD_JSON.findall(html):
        try:
            nodes.extend(_nodes(json.loads(block.strip(), strict=False)))
        except json.JSONDecodeError:
            continue
    for node in nodes:
        if _is_type(node, "CollectionPage") and (
                not node.get("url") or _url_key(urljoin(url, str(node["url"]))) == _url_key(url)):
            return "no_product: CollectionPage, no ficha de producto"
    if any(_is_type(node, "ItemList") for node in nodes) and not any(
            _is_type(node, "Product") and _matches_product_url(node, url) for node in nodes):
        return "no_product: ItemList sin un Product vinculado a la URL completa"
    return ""


def extract_product(html: str, url: str, *, price_selector: str = "", availability_selector: str = "",
                    tax_policy: str = "unknown", url_trace: dict[str, Any] | None = None) -> ProductPage | None:
    """Return what the page publishes, or None when it carries no product information at all."""
    requested_url = url
    parsed = parse_html(html)
    if product_page_rejection(html, requested_url):
        return None
    if any("eoViewsListviewItemClassic" in element["attrs"].get("class", "").split()
           for element in parsed.elements):
        return None
    url, selected_size, radio_selected, selection_error = _edel_selection(html, requested_url)
    if selection_error:
        page = _ambiguous_edel_page(requested_url, selected_size, selection_error)
        page.url_trace = {**(url_trace or {}), "final_url": requested_url}
        _apply_tax_policy(page, tax_policy)
        return page
    edel_ids, selected_ean, ean_conflict = _edel_variant_facts(html, selected_size) if selected_size else (set(), "", False)
    if edel_ids and selected_size not in edel_ids:
        page = _ambiguous_edel_page(requested_url, selected_size,
                                    "selectedSize no coincide con los datos de variante de la ficha")
        page.url_trace = {**(url_trace or {}), "final_url": requested_url}
        _apply_tax_policy(page, tax_policy)
        return page
    page = _from_json_ld(html, url)
    if page is None and selected_size:
        page = _edel_microdata_page(parsed, url, selected_size, edel_ids, selected_ean)
    if page is None and selected_size:
        page = _ambiguous_edel_page(
            url, selected_size,
            "EAN vinculado, pero faltan datos de oferta/stock vinculados al Product Id seleccionado"
            if selected_ean else "No hay datos de variante vinculados al Product Id seleccionado")
    meta = parsed.meta
    if page is None:
        title = meta.get("og:title") or " ".join(parsed.title.split())
        amount = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
        if not (title or amount):
            return None
        page = ProductPage(url=url, method="metadatos", structured_type="OpenGraph/itemprop", title=title.strip())
        page.price = parse_price(amount)
        page.currency = (meta.get("product:price:currency") or meta.get("og:price:currency")
                         or meta.get("pricecurrency") or "").upper()
        page.availability = availability_of(meta.get("product:availability") or meta.get("og:availability")
                                            or meta.get("availability"))
        page.brand = meta.get("product:brand") or meta.get("brand", "")
        for key in ("gtin13", "gtin", "product:ean"):
            if meta.get(key):
                try:
                    page.ean_published = normalize_gtin(meta[key])
                except ValueError:
                    page.notes.append("EAN publicado no válido")
                break
        page.caliber = extract_caliber(page.title) or extract_caliber(urlparse(url).path.rsplit("/", 1)[-1])
    if selected_size and page is not None:
        page.variant_id = selected_size
        page.variant_scoped = True
        selected_identity_conflict = bool(selected_ean and page.ean_published
                                          and selected_ean != page.ean_published)
        if selected_ean and not page.ean_published and not page.ambiguous:
            page.ean_published = selected_ean
        elif selected_identity_conflict:
            page.ambiguous = True
            page.notes.append("El EAN del producto contradice el EAN vinculado al selectedSize")
        if ean_conflict:
            page.ambiguous = True
            page.notes.append("El selectedSize tiene varios EAN publicados: revisión obligatoria")
        if selected_identity_conflict or ean_conflict:
            page.ean_published = ""
            page.price, page.currency, page.availability = None, "", "unknown"
        if selected_ean:
            page.notes.append(f"EAN vinculado a selectedSize={selected_size} en datos de variante")
        if radio_selected:
            page.notes.append(f"Product Id {selected_size} resuelto desde el único radio prd marcado")
    elif (urlparse(requested_url).hostname or "").lower().removeprefix("www.") == "edel-optics.es":
        page.ambiguous = True
        page.variant_scoped = True
        page.availability = "unknown"
        page.notes.append("Edel-Optics sin Product Id de variante seleccionada: requiere revisión")
    if price_selector and page.price is None and not page.variant_scoped:
        text = css_text(parsed, price_selector)
        conditioned = re.search(r"\bPVP\b|recomendado|hist[oó]rico|cup[oó]n|coupon|promo(?:tional)? code", text, re.I)
        selected = None if conditioned else parse_price(text)
        if selected is not None:
            page.price, page.method = selected, "selector"
            page.notes.append("Precio tomado del selector configurado")
        elif conditioned:
            page.notes.append("Selector con precio recomendado, histórico o condicionado: no se usa como venta")
    if availability_selector and not page.variant_scoped:
        _selector_stock(page, css_text(parsed, availability_selector))
    if not page.variant_scoped:
        _infer_tax_from_pretax(page, meta)
        _enrich(page, html, url)
    _apply_tax_policy(page, tax_policy)
    explicit = (meta.get("product:market") or "").upper() if not page.variant_scoped else ""
    if re.fullmatch(r"[A-Z]{2}", explicit):
        if page.market and page.market != explicit:
            page.market_conflict = True
        else:
            page.market, page.market_source = explicit, "ficha:product:market"
    page.url_trace = {**(url_trace or {}), "final_url": requested_url,
                      "selected_variant_url": url if selected_size else "",
                      "canonical_urls": list(dict.fromkeys(urljoin(requested_url, href)
                                                           for href in parsed.canonicals))}
    return page


def _apply_tax_policy(page: ProductPage, tax_policy: str) -> None:
    from competitor_requirements import business_tax_evidence, business_tax_policy_for

    page.configured_tax_policy = tax_policy
    policy = business_tax_policy_for(page.url)
    if policy:
        page.tax_policy_evidence = business_tax_evidence(policy)
        if page.tax_policy_evidence not in page.notes:
            page.notes.append(page.tax_policy_evidence)
        if page.includes_tax is False:
            page.tax_conflict = page.ambiguous = True
            note = "La oferta declara IVA excluido y contradice la política de negocio: revisión obligatoria"
            if note not in page.notes:
                page.notes.append(note)
        elif page.includes_tax is None:
            page.includes_tax = True
        return
    if page.includes_tax is None and tax_policy in {"included", "excluded"}:
        page.includes_tax = tax_policy == "included"
        page.notes.append(f"IVA según la política verificada del competidor ({tax_policy})")


def _selector_stock(page: ProductPage, text: str) -> None:
    if not text:
        return
    page.stock_evidence.append(text)
    page.notes.append("Stock publicado: " + text)
    unavailable = bool(re.search(r"\bagotado\b|\bsin stock\b|\bno disponible\b|\bout of stock\b", text, re.I))
    available = bool(re.search(r"\ben stock\b|\bin stock\b", text, re.I))
    ordered = bool(re.search(
        r"\bbajo pedido\b|\bbackorder\b|\bpreorder\b|\bpre-order\b|\bpreventa\b|\bpedido\s+anticipado\b",
        text, re.I))
    reviewed_delay = bool(re.search(r"\b(?:entrega|env[ií]o|delivery)\s+en\s+40\s*d[ií]as?\b", text, re.I))
    if re.search(r"\bentrega\b|\benv[ií]o\b|\bdelivery\b|\bshipping\b", text, re.I):
        page.delivery_evidence.append(text)
        page.notes.append("Plazo publicado: " + text)
    state = "unavailable" if unavailable else "available" if available and not ordered else "unknown"
    page.stock_conflict |= (available and (unavailable or ordered or reviewed_delay)) \
        or (page.availability in {"available", "unavailable"} and state in {"available", "unavailable"}
            and page.availability != state) or (page.availability == "available" and ordered)
    if page.stock_conflict:
        page.availability = "unknown"
        page.notes.append("Stock y plazo/señales contradictorios: revisión obligatoria")
    elif state != "unknown" and page.availability == "unknown":
        page.availability = state


def resolve_market(page: ProductPage, config: dict) -> tuple[str, str, bool]:
    """Commercial country evidence, never locale/domain. Keep contradictions out of comparable offers."""
    configured = config.get("sales_market", "") if config.get("market_verified") \
        and config.get("market_evidence_url") and config.get("market_note") else ""
    conflict = page.market_conflict or bool(page.market and configured and page.market != configured)
    if conflict:
        return page.market or configured, "Mercado contradictorio entre ficha y configuración; revisión obligatoria", True
    if page.market:
        return page.market, f"Mercado {page.market} según {page.market_source or 'ficha explícita'}", False
    if configured:
        return configured, (f"Mercado {configured} según configuración verificada; "
                            f"{config['market_evidence_url']}; {config['market_note']}; "
                            f"{config.get('market_verified_at') or ''}"), False
    return "", "Mercado sin evidencia comercial verificada", False


def _enrich(page: ProductPage, html: str, url: str) -> None:
    """Facts the page publishes outside JSON-LD: EAN in the visible specification, calibre in a manufacturer code,
    currency in the shop's own configuration. Each is used only when the structured data left the field empty."""
    if not page.ean_published:
        found: list[str] = []
        for raw in [*_EAN_LABELLED.findall(html), *_EAN_DATA_ATTR.findall(html)]:
            try:
                found.append(normalize_gtin(raw))
            except ValueError:
                continue
        distinct = list(dict.fromkeys(found))
        if len(distinct) == 1:
            page.ean_published = distinct[0]
            page.notes.append("EAN leído de la especificación visible de la ficha")
        elif len(distinct) > 1:
            page.notes.append("La ficha muestra varios EAN (variantes): no se toma ninguno")
    if "-" not in page.caliber:
        from_code = next((c for c in map(caliber_from_code, page.identifiers) if c), "")
        lens = extract_lens(page.title) or extract_lens(urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1])
        page.caliber = from_code or page.caliber or lens
    if page.price is not None and not page.currency and (found := _PRESTASHOP_CURRENCY.search(html)):
        page.currency = found.group(1)
        page.notes.append("Moneda según la configuración de la tienda publicada en la ficha")


_VAT_FACTORS = {1.21: 21.0, 1.10: 10.0, 1.04: 4.0}


def _infer_tax_from_pretax(page: ProductPage, meta: dict[str, str]) -> None:
    """The page publishes both the shown price and the pre-tax price: a ratio equal to a Spanish VAT rate proves
    the shown price includes VAT. Without that evidence the VAT treatment stays as it was (unknown)."""
    pretax = parse_price(meta.get("product:pretax_price:amount"))
    if page.price and pretax and page.includes_tax is None:
        ratio = page.price / pretax
        for factor, rate in _VAT_FACTORS.items():
            if abs(ratio - factor) / factor < 0.003:
                page.includes_tax, page.vat_rate = True, rate
                page.notes.append(f"IVA incluido: precio {page.price:g} = precio sin IVA publicado {pretax:g} × {factor}")
                return
