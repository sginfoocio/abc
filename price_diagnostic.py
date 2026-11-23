"""Sanitised, bounded evidence for supervised price diagnostics."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from price_web_extract import _Collector, _LD_JSON, _from_json_ld, _is_type, _nodes, extract_product, parse_html

DEFAULT_BODY_LIMIT = 2 * 1024 * 1024
DEFAULT_QUOTA = 100 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 7
CAPTURE_VERSION = "mia-explicit-main-entity-v1"
_SAFE_KEYS = {
    "@context", "@graph", "@id", "@type", "name", "brand", "model", "color", "sku", "mpn",
    "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "url", "offers", "price",
    "priceCurrency", "availability", "itemOffered", "hasVariant", "isVariantOf", "productGroupID",
    "variesBy", "size", "mainEntity", "additionalProperty", "propertyID", "value", "priceSpecification",
    "priceValidUntil", "lowPrice", "highPrice", "offerCount", "priceType", "valueAddedTaxIncluded",
    "eligibleRegion", "shippingDetails", "shippingDestination", "addressCountry", "deliveryTime",
    "handlingTime", "transitTime", "minValue", "maxValue", "unitCode",
}
_RESTRICTION_KEYS = {"eligibleQuantity", "eligibleCustomerType", "validForMemberTier", "eligibleTransactionVolume"}
_COMMERCIAL_KEYS = {"offers", "eligibleRegion", "shippingDetails", "shippingDestination", "addressCountry",
                    "deliveryTime", "handlingTime", "transitTime", "priceSpecification", "additionalProperty"}
_URL_KEYS = {"@id", "url", "@context", "availability", "priceType"}
_ALLOWED_TYPES = {"Product", "ProductGroup", "WebPage", "Offer", "AggregateOffer", "Brand",
                  "PropertyValue", "QuantitativeValue", "PriceSpecification", "UnitPriceSpecification",
                  "OfferShippingDetails", "DefinedRegion", "ShippingDeliveryTime", "Country"}
_SECRET = re.compile(r"(?i)(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|bearer\s+\S+|"
                     r"(?:password|passwd|secret|token|session|cookie|csrf|authorization|api[_-]?key)\s*[:=])")
_URL_SECRET = re.compile(r"(?i)password|passwd|secret|token|session|cookie|csrf|authorization|api[_-]?key")
_UNSCOPED_EAN = re.compile(
    r"(?:\bEAN(?:/GTIN)?\b|\bGTIN(?:-?13)?\b|\bUPC\b|c[oó]digo\s+de\s+barras)\s*:?\s*"
    r"(?:<[^>]{0,200}>\s*){0,4}:?\s*(\d{12,13})(?!\d)"
    r"|\bdata-ean=[\"'](\d{12,13})[\"']", re.I)
_MIA_BARCODE = re.compile(
    r"\bBARCODE\b\s*:?\s*(?:<[^>]{0,200}>\s*){0,4}:?\s*(\d{12,13})(?!\d)", re.I)
_EDEL_MICRODATA_PROPERTIES = {
    "name", "brand", "model", "color", "sku", "gtin12", "gtin13", "url", "offers", "price",
    "priceCurrency", "availability", "valueAddedTaxIncluded", "priceSpecification", "priceType",
    "shippingDetails", "shippingDestination", "addressCountry", "deliveryTime", "handlingTime",
    "transitTime", "minValue", "maxValue", "unitCode",
}


class EvidenceError(ValueError):
    pass


def require_private_database(database):
    if os.name != "posix":
        raise EvidenceError("No se pueden verificar los permisos privados del almacenamiento")
    path = Path(database).resolve(strict=True)
    info, parent = path.stat(), path.parent.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or parent.st_mode & 0o022
            or info.st_uid not in {os.geteuid(), 0}):
        raise EvidenceError("El almacenamiento no acredita permisos privados")
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists():
            saved = companion.stat()
            if not stat.S_ISREG(saved.st_mode) or saved.st_mode & 0o077 or saved.st_uid not in {os.geteuid(), 0}:
                raise EvidenceError("El almacenamiento no acredita permisos privados")


def limits():
    def positive(name, default):
        value = int(os.environ.get(name, str(default)))
        if value <= 0:
            raise ValueError(f"{name} debe ser positivo")
        return value

    return (positive("PRICE_DIAGNOSTIC_BODY_BYTES", DEFAULT_BODY_LIMIT),
            positive("PRICE_DIAGNOSTIC_QUOTA_BYTES", DEFAULT_QUOTA),
            positive("PRICE_DIAGNOSTIC_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def has_capacity(database, used, body_limit, quota):
    return used + body_limit <= quota and shutil.disk_usage(Path(database).parent).free >= 2 * body_limit


def safe_url(url, base=""):
    value = urljoin(base, str(url))
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise EvidenceError("URL de evidencia no conservable")
    if (_SECRET.search(unquote(parsed.path)) or _SECRET.search(unquote(parsed.fragment))
            or _URL_SECRET.search(unquote(parsed.path)) or _URL_SECRET.search(unquote(parsed.fragment))):
        raise EvidenceError("Contenido sensible en URL")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key in {"variant", "selectedSize"} and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value)
             and not _URL_SECRET.search(value)]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _clean(value, base, key="", depth=0):
    if depth > 30:
        raise EvidenceError("Estructura JSON-LD demasiado profunda")
    if isinstance(value, dict):
        types = value.get("@type")
        if key == "mainEntity":
            types = types if isinstance(types, list) else [types] if types else []
            if any(str(t).rsplit("/", 1)[-1] != "Product" for t in types):
                raise EvidenceError("Entidad principal no conservable")
            return {k: _clean(v, base, k, depth + 1) for k, v in value.items() if k in {"@id", "@type"}}
        if types:
            types = types if isinstance(types, list) else [types]
            if not all(str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] in _ALLOWED_TYPES for t in types):
                if key in _COMMERCIAL_KEYS:
                    raise EvidenceError(f"Tipo comercial no conservable: {key}")
                return None
            if any(str(t).rsplit("/", 1)[-1] == "WebPage" for t in types):
                if len(types) != 1 or any(k in value for k in _RESTRICTION_KEYS):
                    raise EvidenceError("Página principal no conservable")
                return {k: _clean(v, base, k, depth + 1) for k, v in value.items()
                        if k in {"@type", "url", "mainEntity"}}
        return {k: (v is not None if k in _RESTRICTION_KEYS else _clean(v, base, k, depth + 1))
                for k, v in value.items() if k in _SAFE_KEYS or (k in _RESTRICTION_KEYS and v is not None)}
    if isinstance(value, list):
        return [v for item in value if (v := _clean(item, base, key, depth + 1)) is not None]
    if isinstance(value, str):
        if len(value) > 1024:
            raise EvidenceError("Contenido sensible en campos de diagnóstico")
        if key in {"@id", "url"} or (key in _URL_KEYS and value.startswith(("https://", "http://", "/", "#"))):
            return safe_url(value, base)
        if _SECRET.search(value):
            raise EvidenceError("Contenido sensible en campos de diagnóstico")
        if "<" in value or ">" in value:
            raise EvidenceError("Contenido no conservable en campos de diagnóstico")
        return value
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise EvidenceError("Valor JSON-LD no conservable")


def _has_product_or_offer(value):
    if isinstance(value, dict):
        types = value.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if any(str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] in {"Product", "ProductGroup", "Offer"}
               for t in types):
            return True
        return any(_has_product_or_offer(item) for item in value.values())
    return isinstance(value, list) and any(_has_product_or_offer(item) for item in value)


def _sanitise_edel_microdata(text, url):
    if (urlsplit(url).hostname or "").lower().removeprefix("www.") != "edel-optics.es":
        raise EvidenceError("Sin estructura Product/Offer conservable")
    original = extract_product(text, url)
    if not original or original.method != "edel-microdata" or original.ambiguous:
        raise EvidenceError("Microdatos Edel sin relación inequívoca de producto y oferta")

    parsed = parse_html(text)
    output = ["<!doctype html>"]
    stack = []
    void = {"input", "link", "meta"}
    allowed_tags = {"html", "head", "body", "title", "h1", "aside", "input", "link", "meta"}
    class_pattern = re.compile(r"^(?:eoProductPrice|eoViewsListviewItemClassic|"
                               r"eoDetailListTileSize\d+|eoProductsPriceBySize-\d+)$")
    for element in parsed.elements:
        while stack and stack[-1][0] != element["parent"]:
            output.append(f"</{stack.pop()[1]}>")
        source_tag = element["tag"]
        tag = source_tag if source_tag in allowed_tags else "div"
        attrs = element["attrs"]
        kept = {}
        classes = [name for name in attrs.get("class", "").split() if class_pattern.fullmatch(name)]
        if classes:
            kept["class"] = " ".join(classes)
        if "itemscope" in attrs:
            kept["itemscope"] = ""
        if attrs.get("itemtype"):
            itemtype = urlsplit(attrs["itemtype"])
            if (itemtype.scheme not in {"http", "https"} or itemtype.netloc != "schema.org"
                    or not re.fullmatch(r"/[A-Za-z][A-Za-z0-9]*", itemtype.path)
                    or itemtype.query or itemtype.fragment):
                raise EvidenceError("Tipo de microdatos no conservable")
            kept["itemtype"] = attrs["itemtype"]
        prop = attrs.get("itemprop", "")
        scope = next((parsed.elements[index] for index, _ in reversed(stack)
                      if "itemscope" in parsed.elements[index]["attrs"]), None)
        scope_type = (scope["attrs"].get("itemtype", "").rsplit("/", 1)[-1] if scope else "")
        own_type = attrs.get("itemtype", "").rsplit("/", 1)[-1]
        if prop and (set(prop.split()) & _RESTRICTION_KEYS) and (scope_type in _ALLOWED_TYPES
                                                                 or own_type in _ALLOWED_TYPES):
            raise EvidenceError("Restricción de microdatos no conservable")
        if prop and set(prop.split()) <= _EDEL_MICRODATA_PROPERTIES \
                and (scope_type in _ALLOWED_TYPES or own_type in _ALLOWED_TYPES):
            kept["itemprop"] = prop
            for name in ("content", "href", "value"):
                if name in attrs:
                    kept[name] = (_clean(attrs[name], url, "url") if name == "href"
                                  else _clean(attrs[name], url, prop.split()[0]))
        if source_tag == "input" and attrs.get("type", "").lower() == "radio" \
                and re.fullmatch(r"prd\d+", attrs.get("id", ""), re.I):
            kept.update({"type": "radio", "id": attrs["id"]})
            if "checked" in attrs:
                kept["checked"] = ""
        if source_tag == "html" and re.fullmatch(r"[A-Za-z]{2}(?:-[A-Za-z]{2})?", attrs.get("lang", "")):
            kept["lang"] = attrs["lang"]
        output.append("<" + tag + "".join(f' {k}="{escape(v, quote=True)}"' for k, v in kept.items()) + ">")
        if tag not in void:
            stack.append((element["index"], tag))
            if source_tag == "title":
                output.append(escape(_clean(element["text"].strip(), url, "name")))
            elif any(re.fullmatch(r"eoDetailListTileSize\d+", name) for name in classes):
                eans = re.findall(r"\bEAN\b\s*:?\s*(\d{12,13})(?!\d)", element["text"], re.I)
                output.append(escape(" ".join("EAN " + ean for ean in eans)))
            elif "itemprop" in kept and not any(name in attrs for name in ("content", "href", "value")) \
                    and "itemscope" not in attrs:
                output.append(escape(_clean(element["text"].strip(), url, kept["itemprop"].split()[0])))
    while stack:
        output.append(f"</{stack.pop()[1]}>")
    artifact = "".join(output).encode("utf-8")
    replay = extract_product(artifact.decode("utf-8"), url)
    if not replay or asdict(original) != asdict(replay):
        raise EvidenceError("Los microdatos sanitizados no conservan la selección original")
    return parsed, artifact


def sanitise(fetched, max_bytes):
    body = fetched.content
    if len(body) > max_bytes:
        raise EvidenceError("Respuesta demasiado grande")
    if not re.search(rb"(?i)<(?:!doctype\s+html|html|head|body)\b", body[:4096]):
        raise EvidenceError("Formato no HTML")
    collector = _Collector()
    collector.feed(fetched.text)
    records = []
    mia_selection = False
    for block in _LD_JSON.findall(fetched.text):
        try:
            data = json.loads(block.strip(), strict=False)
        except (json.JSONDecodeError, RecursionError):
            continue
        if (urlsplit(fetched.url).hostname or "").lower().removeprefix("www.") == "miaburton.com":
            mia_selection |= any(_is_type(node, "WebPage") and node.get("mainEntity") is not None
                                 for node in _nodes(data))
        records.append(_clean(data, fetched.url))
    serialised = json.dumps(records, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    if not _has_product_or_offer(records):
        collector, artifact = _sanitise_edel_microdata(fetched.text, fetched.url)
        artifact_kind, sanitization_version = "sanitizado_parcial_microdatos", 4
    else:
        artifact = ("<!doctype html><html><head><script type=\"application/ld+json\">"
                    + serialised + "</script></head><body></body></html>").encode("utf-8")
        artifact_kind, sanitization_version = "sanitizado_parcial_jsonld", 3
    if mia_selection:
        original = _from_json_ld(fetched.text, fetched.url)
        replay = _from_json_ld(artifact.decode("utf-8"), fetched.url)
        fields = ("ambiguous", "offer_binding_unresolved", "variant_id", "variant_scoped", "title", "brand",
                  "model", "group_reference", "identifiers", "color",
                  "ean_published", "ean_published_raw", "price", "currency", "availability", "stock_conflict",
                  "includes_tax", "tax_conflict", "market", "market_conflict", "offer_binding_source")
        if not original or not replay or any(getattr(original, field) != getattr(replay, field) for field in fields):
            raise EvidenceError("La sanitización no conserva la selección principal y su oferta")
    canonical = [safe_url(urljoin(fetched.url, href)) for href in collector.canonicals]
    unscoped_eans = sorted({match.group(1) or match.group(2) for match in _UNSCOPED_EAN.finditer(fetched.text)})
    if len(unscoped_eans) > 32:
        raise EvidenceError("Demasiados identificadores EAN sin vincular")
    unscoped_barcodes = (sorted(set(_MIA_BARCODE.findall(fetched.text)))
                         if (urlsplit(fetched.url).hostname or "").lower().removeprefix("www.")
                         == "miaburton.com" else [])
    if len(unscoped_barcodes) > 32:
        raise EvidenceError("Demasiados identificadores BARCODE sin vincular")
    if len(artifact) > max_bytes:
        raise EvidenceError("Artefacto sanitizado demasiado grande")
    redirects = [{"url": safe_url(r["url"]), "status": r["status"],
                  "location": safe_url(r["location"])} for r in fetched.redirects]
    metadata = {
        "artifact_kind": artifact_kind, "capture_version": CAPTURE_VERSION,
        "requested_url": safe_url(fetched.requested_url),
        "sanitization_version": sanitization_version, "unscoped_eans": unscoped_eans,
        "unscoped_barcodes": unscoped_barcodes,
        "final_url": safe_url(fetched.url), "canonical_urls": list(dict.fromkeys(canonical)),
        "redirects": redirects, "http_status": fetched.status, "received_bytes": len(body),
        "received_sha256": hashlib.sha256(body).hexdigest(),
        "artifact_bytes": len(artifact), "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
    }
    return metadata, artifact


def expire(con, now):
    con.execute("PRAGMA secure_delete=ON")
    rows = con.execute("""SELECT id,competitor_id FROM price_diagnostic_requests
        WHERE artifact IS NOT NULL AND expires_at<=?""", (now.isoformat(),)).fetchall()
    for row in rows:
        con.execute("UPDATE price_diagnostic_requests SET artifact=NULL,status='expired' WHERE id=?", (row["id"],))
        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
            VALUES('competitor',?,'expire_diagnostic_artifact',?,'expired','worker','',?)""",
                    (row["competitor_id"], str(row["id"]), now.isoformat()))


def expiry(now, retention_days):
    return (now + timedelta(days=retention_days)).isoformat()
