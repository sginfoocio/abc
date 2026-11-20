"""Read native PrestaShop quotes; never calculate a retail price from discount rows."""

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from sqlite3 import Connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS prestashop_prices (
    ean TEXT PRIMARY KEY REFERENCES products(ean),
    payload TEXT,
    observed_at TEXT,
    valid_until TEXT,
    synced_at TEXT,
    attempted_at TEXT NOT NULL,
    issue TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prestashop_price_history (
    id INTEGER PRIMARY KEY,
    ean TEXT NOT NULL REFERENCES products(ean),
    synced_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prestashop_price_history_ean ON prestashop_price_history(ean,id);
"""
SOURCE = "PrestaShop · motor nativo"
INCOMPLETE = "Lectura en curso o interrumpida"


class QuoteError(ValueError):
    """Sanitized, operator-visible error."""


def enabled() -> bool:
    return os.getenv("PRESTASHOP_PRICE_ENABLED") == "1"


@lru_cache(maxsize=1)
def manifest_eans() -> frozenset[str]:
    path = Path(__file__).with_name("price_trial_manifest.json")
    eans = json.loads(path.read_text(encoding="utf-8"))["eans"]
    if len(eans) != 100 or len(set(eans)) != 100:
        raise ValueError("El alcance de PrestaShop debe ser el manifiesto existente de 100 EAN")
    return frozenset(eans)


def watched_scope(con: Connection) -> set[str]:
    return manifest_eans().intersection(
        row[0] for row in con.execute("SELECT ean FROM watch_products WHERE active=1"))


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Se requiere fecha con zona horaria")
    return result.astimezone(timezone.utc)


def _money(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}(\.[0-9]{1,2})?", value):
        raise QuoteError("Importe final en EUR inválido")
    number = Decimal(value)
    if number <= 0:
        raise QuoteError("Importe final en EUR inválido")
    return format(number, ".2f")


def _context() -> dict:
    names = ("shop_id", "group_id", "country_id", "currency_id")
    try:
        values = {name: int(os.environ[f"PRESTASHOP_PRICE_{name.upper()}"]) for name in names}
        if min(values.values()) < 1:
            raise ValueError
    except (KeyError, ValueError):
        raise QuoteError("Falta configurar tienda, grupo público, país o moneda de PrestaShop") from None
    return {**values, "market": "ES", "currency": "EUR", "customer_id": 0, "quantity": 1,
            "tax_included": True, "shipping_included": False, "cart_discount": False}


def _identifier(variable: str, suffix: str = "") -> str:
    value = os.getenv(variable)
    if value is None or not re.fullmatch(r"[A-Za-z0-9_]*", value) or not (value + suffix):
        raise QuoteError(f"Falta configurar un identificador MySQL válido: {variable}")
    return f"`{value}{suffix}`"


def _mysql_rows(eans: list[str], context: dict) -> tuple[list[tuple], list[tuple]]:
    import pymysql

    tables = {name: _identifier("PRESTASHOP_MYSQL_PREFIX", name) for name in (
        "product", "product_shop", "product_attribute", "product_attribute_shop")}
    quotes = _identifier("PRESTASHOP_MYSQL_NATIVE_QUOTES")
    try:
        config = {name: os.environ[f"PRESTASHOP_MYSQL_{key}"] for name, key in (
            ("host", "HOST"), ("user", "USER"), ("password", "PASSWORD"),
            ("database", "DATABASE"), ("ssl_ca", "SSL_CA"))}
        port = int(os.getenv("PRESTASHOP_MYSQL_PORT", "3306"))
    except (KeyError, ValueError):
        raise QuoteError("Falta configurar la conexión MySQL de solo lectura y su CA") from None
    shop = context["shop_id"]
    forms = [form for ean in eans for form in ((ean, ean[1:]) if ean.startswith("0") else (ean,))]
    identity_marks = ",".join(["%s"] * len(forms))
    marks = ",".join(["%s"] * len(eans))
    with closing(pymysql.connect(**config, port=port, ssl_verify_cert=True, ssl_verify_identity=True,
                                 connect_timeout=5, read_timeout=15, write_timeout=15,
                                 charset="utf8mb4")) as con, con.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT")
        cur.execute(f"""SELECT p.ean13, p.id_product, 0,
                    EXISTS (SELECT 1 FROM {tables['product_attribute']} a
                        JOIN {tables['product_attribute_shop']} ats
                        ON ats.id_product_attribute=a.id_product_attribute AND ats.id_shop=ps.id_shop
                        WHERE a.id_product=p.id_product)
                FROM {tables['product']} p JOIN {tables['product_shop']} ps
                ON ps.id_product=p.id_product AND ps.id_shop=%s
                WHERE p.ean13 IN ({identity_marks})
                UNION ALL
                SELECT a.ean13,a.id_product,a.id_product_attribute,0
                FROM {tables['product_attribute']} a JOIN {tables['product_attribute_shop']} ats
                ON ats.id_product_attribute=a.id_product_attribute AND ats.id_shop=%s
                JOIN {tables['product_shop']} ps ON ps.id_product=a.id_product AND ps.id_shop=ats.id_shop
                WHERE a.ean13 IN ({identity_marks})""", (shop, *forms, shop, *forms))
        identities = list(cur.fetchall())
        cur.execute(f"""SELECT ean, payload FROM {quotes}
            WHERE shop_id=%s AND country_id=%s AND currency_id=%s AND group_id=%s
            AND ean IN ({marks})""", (
            shop, context["country_id"], context["currency_id"], context["group_id"], *eans))
        return identities, list(cur.fetchall())


def _decode(raw: str, identity: tuple[int, int], context: dict, now: datetime) -> tuple[str, str, str]:
    payload = json.loads(raw)
    if type(payload) is not dict:
        raise QuoteError("Formato de resultado nativo inválido")
    for key, value in context.items():
        if type(payload.get(key)) is not type(value) or payload[key] != value:
            raise QuoteError(f"Contexto público de PrestaShop no confirmado: {key}")
    product, combination = identity
    if any(type(payload.get(key)) is not int for key in ("product_id", "combination_id")) or (
        payload["product_id"], payload["combination_id"]
    ) != identity:
        raise QuoteError("El resultado nativo no coincide con el producto y la combinación del EAN")
    observed = _time(payload["observed_at"])
    valid_until = min(_time(payload["valid_until"]), observed + timedelta(hours=24))
    if not observed <= now < valid_until:
        raise QuoteError("El resultado nativo está caducado o tiene fecha futura")
    if not isinstance(payload.get("fiscal_context"), str) or not payload["fiscal_context"].strip():
        raise QuoteError("Falta el contexto fiscal de la lectura")
    if not isinstance(payload.get("engine"), str) or not payload["engine"].strip():
        raise QuoteError("Falta identificar el motor nativo de la lectura")
    offer = payload["offer"]
    if offer is not None:
        if (type(offer) is not dict or not isinstance(offer.get("reference"), str)
                or not offer["reference"].strip() or type(offer.get("conditions")) is not dict):
            raise QuoteError("Faltan referencia o condiciones de la oferta aplicada")
        minimum = offer["conditions"].get("min_quantity")
        if type(minimum) is not int or not 1 <= minimum <= context["quantity"]:
            raise QuoteError("La oferta aplicada no es válida para una unidad")
        if offer["starts_at"] is not None and _time(offer["starts_at"]) > observed:
            raise QuoteError("La oferta aplicada aún no estaba vigente")
        if offer["ends_at"] is not None:
            valid_until = min(valid_until, _time(offer["ends_at"]))
            if now >= valid_until:
                raise QuoteError("La oferta aplicada ha caducado")
    price = _money(payload["price"])
    sizes = payload["other_calibres"]
    if type(sizes) is not dict or any(not re.fullmatch(r"[1-9][0-9]*", key) or int(key) == combination
                                      for key in sizes):
        raise QuoteError("Vinculación de otros calibres inválida")
    other_calibres = {key: _money(value) for key, value in sizes.items()}
    snapshot = {**context, "product_id": product, "combination_id": combination,
                "price": price, "offer": offer, "other_calibres": other_calibres,
                "engine": payload["engine"], "fiscal_context": payload["fiscal_context"], "source": SOURCE}
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False), observed.isoformat(), valid_until.isoformat()


def resolve(eans: list[str], identities: Iterable[tuple], rows: Iterable[tuple], context: dict,
            now: datetime) -> dict[str, tuple[str, str, str] | str]:
    candidates: dict[str, set[tuple]] = defaultdict(set)
    quotes: dict[str, list[str]] = defaultdict(list)
    for ean, product, combination, unresolved in identities:
        if isinstance(ean, str) and re.fullmatch(r"[0-9]{12,13}", ean):
            candidates[ean.zfill(13)].add((product, combination, unresolved))
    for ean, raw in rows:
        quotes[ean].append(raw)
    results: dict[str, tuple[str, str, str] | str] = {}
    for ean in eans:
        matches = candidates[ean]
        if not matches:
            results[ean] = "EAN no localizado en la tienda de PrestaShop"
        elif len(matches) != 1 or next(iter(matches))[2]:
            results[ean] = "EAN ambiguo o sin combinación inequívoca en PrestaShop"
        elif len(quotes[ean]) != 1:
            results[ean] = ("Varios resultados nativos para el EAN y contexto" if quotes[ean]
                            else "Sin resultado del motor nativo para el EAN")
        else:
            product, combination, _ = next(iter(matches))
            try:
                results[ean] = _decode(quotes[ean][0], (product, combination), context, now)
            except QuoteError as exc:
                results[ean] = str(exc)
            except (TypeError, ValueError, KeyError, OverflowError):
                results[ean] = "Formato o vigencia del resultado nativo inválidos"
    return results


def usable(row: dict, now: datetime) -> bool:
    try:
        return bool(row["payload"] and not row["issue"] and
                    _time(row["observed_at"]) <= now < _time(row["valid_until"]))
    except (TypeError, ValueError):
        return False


def due(con: Connection, scope: set[str], now: datetime, interval: timedelta) -> bool:
    rows = {r["ean"]: dict(r) for r in con.execute("SELECT * FROM prestashop_prices")}
    return any(ean not in rows or (
        now - _time(rows[ean]["attempted_at"]) >= timedelta(minutes=30)
        and (not usable(rows[ean], now) or now - _time(rows[ean]["synced_at"]) >= interval)
    ) for ean in scope)


def refresh(connect: Callable, selected: list[str], clock: Callable[[], datetime]) -> dict[str, int]:
    from pymysql import MySQLError

    started = clock().isoformat()
    with connect() as con, con:
        eans = sorted(watched_scope(con).intersection(selected))
        if not eans:
            return {"monitored": 0, "issues": 0}
        con.executemany("""INSERT INTO prestashop_prices(ean,attempted_at,issue) VALUES(?,?,?)
            ON CONFLICT(ean) DO UPDATE SET attempted_at=excluded.attempted_at,issue=excluded.issue""",
                        [(ean, started, INCOMPLETE) for ean in eans])
    try:
        context = _context()
        identities, rows = _mysql_rows(eans, context)
        results = resolve(eans, identities, rows, context, clock())
    except (QuoteError, MySQLError, OSError) as exc:
        # Never pass a remote driver's message into logs or SQLite: it can include host/user details.
        issue = str(exc) if isinstance(exc, QuoteError) else (
            f"No se pudo leer MySQL de PrestaShop ({type(exc).__name__})")
        results = dict.fromkeys(eans, issue)
    with connect() as con, con:
        synced_at = clock().isoformat()
        for ean, result in results.items():
            if isinstance(result, str):
                con.execute("UPDATE prestashop_prices SET issue=? WHERE ean=?", (result, ean))
                continue
            payload, observed, until = result
            con.execute("""INSERT INTO prestashop_price_history(ean,synced_at,observed_at,payload)
                SELECT ean,?,?,? FROM prestashop_prices WHERE ean=? AND payload IS NOT ?""",
                        (synced_at, observed, payload, ean, payload))
            con.execute("""UPDATE prestashop_prices SET payload=?,observed_at=?,valid_until=?,
                synced_at=?,issue='' WHERE ean=?""", (payload, observed, until, synced_at, ean))
    return {"monitored": len(eans), "issues": sum(isinstance(value, str) for value in results.values())}
