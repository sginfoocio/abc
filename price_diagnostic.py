"""Sanitised, bounded evidence for supervised price diagnostics."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from price_web_extract import _Collector, _LD_JSON

DEFAULT_BODY_LIMIT = 2 * 1024 * 1024
DEFAULT_QUOTA = 100 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 7
_SAFE_KEYS = {
    "@context", "@graph", "@id", "@type", "name", "brand", "model", "color", "sku", "mpn",
    "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "url", "offers", "price",
    "priceCurrency", "availability", "itemOffered", "hasVariant", "isVariantOf", "productGroupID",
    "variesBy", "size", "additionalProperty", "propertyID", "value", "priceSpecification",
    "priceValidUntil", "lowPrice", "highPrice", "offerCount",
}
_URL_KEYS = {"@id", "url", "@context", "availability"}
_ALLOWED_TYPES = {"Product", "ProductGroup", "Offer", "AggregateOffer", "Brand",
                  "PropertyValue", "QuantitativeValue", "PriceSpecification", "UnitPriceSpecification"}
_SECRET = re.compile(r"(?i)(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|bearer\s+\S+|"
                     r"(?:password|passwd|secret|token|session|cookie|csrf|authorization|api[_-]?key)\s*[:=])")
_URL_SECRET = re.compile(r"(?i)password|passwd|secret|token|session|cookie|csrf|authorization|api[_-]?key")


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
        if types:
            types = types if isinstance(types, list) else [types]
            if not all(str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] in _ALLOWED_TYPES for t in types):
                return None
        return {k: _clean(v, base, k, depth + 1) for k, v in value.items() if k in _SAFE_KEYS}
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


def sanitise(fetched, max_bytes):
    body = fetched.content
    if len(body) > max_bytes:
        raise EvidenceError("Respuesta demasiado grande")
    if not re.search(rb"(?i)<(?:!doctype\s+html|html|head|body)\b", body[:4096]):
        raise EvidenceError("Formato no HTML")
    collector = _Collector()
    collector.feed(fetched.text)
    records = []
    for block in _LD_JSON.findall(fetched.text):
        try:
            data = json.loads(block.strip(), strict=False)
        except (json.JSONDecodeError, RecursionError):
            continue
        records.append(_clean(data, fetched.url))
    serialised = json.dumps(records, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    if not _has_product_or_offer(records):
        raise EvidenceError("Sin estructura Product/Offer conservable")
    canonical = [safe_url(urljoin(fetched.url, href)) for href in collector.canonicals]
    artifact = ("<!doctype html><html><head><script type=\"application/ld+json\">"
                + serialised + "</script></head><body></body></html>").encode("utf-8")
    if len(artifact) > max_bytes:
        raise EvidenceError("Artefacto sanitizado demasiado grande")
    redirects = [{"url": safe_url(r["url"]), "status": r["status"],
                  "location": safe_url(r["location"])} for r in fetched.redirects]
    metadata = {
        "artifact_kind": "sanitizado_parcial_jsonld", "requested_url": safe_url(fetched.requested_url),
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
