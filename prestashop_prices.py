"""Reproduce the audited PrestaShop 8.2.1 pricing path from a MySQL snapshot."""

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from sqlite3 import Connection
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
SOURCE = "PrestaShop · MySQL"
ENGINE = "PrestaShop 8.2.1 / ABC SQL v3"
INCOMPLETE = "Lectura en curso o interrumpida"
# EAN solicitado -> (código original, producto, combinación, marca, modelo, color, referencia).
PRICE_REFERENCES = {
    "0889214095985": ("889214095978", 949, 11091, "Tom Ford", "FT0751N", "01A", "FT0751N01A"),
    "8056262600078": ("8056262600085", 7917, 8481, "Miu Miu", "MU 01YV", "14L1O1", "MU01YV14L1O1"),
    "8056262833902": ("8056262833919", 9404, 0, "Ray-Ban", "RB4472", "13592", "RB447213592"),
    "8056597669016": ("8056262667484", 9090, 13347, "Miu Miu", "MU 04UV", "1AB1O1", "MU04UV1AB1O1"),
    "8056597669023": ("8056262667491", 1615, 13343, "Miu Miu", "MU 04UV", "VAU1O1", "MU04UVVAU1O1"),
}


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


def _identifier(variable: str, suffix: str) -> str:
    value = os.getenv(variable)
    if value is None or not re.fullmatch(r"[A-Za-z0-9_]*", value) or not (value + suffix):
        raise QuoteError(f"Falta configurar un identificador MySQL válido: {variable}")
    return f"`{value}{suffix}`"


def _mysql_tls_options() -> dict:
    mode = os.getenv("PRESTASHOP_MYSQL_SSL_MODE", "VERIFY_IDENTITY")
    ca = os.getenv("PRESTASHOP_MYSQL_SSL_CA")
    if mode == "VERIFY_IDENTITY" and ca:
        return {"ssl_ca": ca, "ssl_verify_cert": True, "ssl_verify_identity": True}
    if mode == "REQUIRED" and not ca:
        # A nonempty SSL options dict makes PyMySQL require TLS before authentication.
        return {"ssl": {"check_hostname": False, "verify_mode": False}}
    raise QuoteError("Configurar TLS de MySQL: VERIFY_IDENTITY con CA o REQUIRED explícito sin CA")


def _account_authorization(config: dict, port: int) -> dict | None:
    policy = os.getenv("PRESTASHOP_MYSQL_ACCOUNT_POLICY", "select_only")
    if policy == "select_only":
        return None
    if policy != "authorized_broad":
        raise QuoteError("Política de cuenta MySQL no reconocida")
    try:
        receipt = json.loads(os.environ["PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION"])
        required = {"id", "actor", "at", "host", "port", "user", "database"}
        if (type(receipt) is not dict or set(receipt) != required
                or any(not isinstance(receipt[key], str) or not receipt[key].strip()
                       or len(receipt[key]) > 200 for key in required - {"port"})
                or type(receipt["port"]) is not int or receipt["port"] != port
                or any(receipt[key] != config[key] for key in ("host", "user", "database"))
                or receipt["user"] != "ps_user_vytria_lect"
                or receipt["database"] != "ps_vytria"
                or _time(receipt["at"]) > datetime.now(timezone.utc)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise QuoteError("Falta autorización válida y acotada para la cuenta MySQL de permisos amplios") from None
    return {key: receipt[key] for key in ("id", "actor", "at")}


def _authorized_database_grant(grant: str) -> bool:
    match = re.match(r"^GRANT (.+?) ON (.+?) TO ", grant)
    if not match or "WITH GRANT OPTION" in grant:
        return False
    privileges, scope = match.groups()
    if privileges == "USAGE" and scope == "*.*":
        return True
    scope = scope.replace("\\\\", "\\")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "REFERENCES", "INDEX",
               "ALTER", "CREATE TEMPORARY TABLES", "LOCK TABLES", "EXECUTE", "CREATE VIEW",
               "SHOW VIEW", "CREATE ROUTINE", "ALTER ROUTINE", "EVENT", "TRIGGER"}
    return scope == r"`ps\_vytria`.*" and set(privileges.split(", ")) <= allowed


def _connection_security(cur, config: dict, cipher: str, authorization: dict | None) -> dict:
    cur.execute("SHOW GRANTS")
    grants = [grant for (grant,) in cur.fetchall()]
    restricted = all(re.match(r"^GRANT (USAGE|SELECT) ON ", grant)
                     and "WITH GRANT OPTION" not in grant for grant in grants)
    if not grants or (not restricted and authorization is None):
        raise QuoteError("La cuenta de PrestaShop no tiene permisos exclusivamente de lectura")
    if authorization is not None:
        if not all(_authorized_database_grant(grant) for grant in grants):
            raise QuoteError("Permisos MySQL fuera del ámbito autorizado")
        cur.execute("SELECT CURRENT_USER(), DATABASE()")
        identity = cur.fetchone()
        if (not identity or len(identity) != 2 or not isinstance(identity[0], str)
                or identity[0].rpartition("@")[0] != config["user"]
                or identity[1] != config["database"]):
            raise QuoteError("La sesión MySQL no corresponde a la cuenta y base autorizadas")
    security = {
        "account_policy": "authorized_broad" if authorization is not None else "select_only",
        "account_permissions": "select_only" if restricted else "broad",
        "tls_required": True, "tls_cipher": cipher,
        "certificate_verified": os.getenv("PRESTASHOP_MYSQL_SSL_MODE", "VERIFY_IDENTITY") == "VERIFY_IDENTITY",
        "server_identity_verified": os.getenv("PRESTASHOP_MYSQL_SSL_MODE", "VERIFY_IDENTITY") == "VERIFY_IDENTITY",
    }
    if authorization is not None:
        security["authorization"] = authorization
    return security


def _price_validation(context: dict, *, audit: bool = False) -> dict:
    if audit:
        return {"mode": "audit_only", "php_inspected": False}
    mode = os.getenv("PRESTASHOP_PRICE_VALIDATION_MODE", "php_review")
    if mode == "php_review":
        if os.getenv("PRESTASHOP_PRICE_CODE_AUDITED") != "1":
            raise QuoteError("Pendiente auditar overrides PHP y módulos de presentación de precios")
        return {"mode": mode, "php_inspected": True}
    if mode != "native_rules_confirmed":
        raise QuoteError("Modo de validación de precios no soportado")
    expected = {"shop_id": 1, "group_id": 1, "country_id": 6, "currency_id": 1, "market": "ES",
                "currency": "EUR", "customer_id": 0, "quantity": 1, "tax_included": True,
                "shipping_included": False, "cart_discount": False}
    try:
        config = {key: os.environ[f"PRESTASHOP_MYSQL_{key.upper()}"]
                  for key in ("host", "user", "database")}
        account = _account_authorization(config, int(os.getenv("PRESTASHOP_MYSQL_PORT", "3306")))
        receipt = json.loads(os.environ["PRESTASHOP_PRICE_NATIVE_CONFIRMATION"])
        if (account is None or type(receipt) is not dict
                or set(receipt) != {"id", "actor", "at", "account_authorization_id", "context", "claim"}
                or any(not isinstance(receipt[key], str) or not receipt[key].strip()
                       or len(receipt[key]) > 200 for key in ("id", "actor", "at", "account_authorization_id"))
                or receipt["account_authorization_id"] != account["id"]
                or receipt["claim"] != "native_prestashop_catalog_rules"
                or type(receipt["context"]) is not dict or receipt["context"] != expected
                or context != expected
                or any(type(receipt["context"][key]) is not type(value) for key, value in expected.items())
                or _time(receipt["at"]) > datetime.now(timezone.utc)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise QuoteError("Falta confirmación acotada del responsable para el cálculo nativo") from None
    return {"mode": mode, "php_inspected": False,
            "basis": "responsible_confirmation_and_native_database_checks",
            "authorization": {key: receipt[key] for key in ("id", "actor", "at")},
            "account_authorization_id": account["id"]}


def _catalog_origin(product_id: int, row: tuple, parents: dict, conditions: list, categories: dict) -> dict:
    sid, _, rule_id, *_ = row
    if not rule_id:
        return {"specific_price_id": sid, "catalog_rule_id": 0, "kind": "specific_offer"}
    parent = parents.get(rule_id)
    if parent is None:
        raise QuoteError(f"Precio específico {sid} sin regla nativa de origen {rule_id}")
    # Catalog percentages are stored as 15; materialized specific prices store 0.15.
    reduction = Decimal(parent[6]) / 100 if parent[7] == "percentage" else Decimal(parent[6])
    expected = (parent[0], parent[1], parent[2], parent[3], parent[4], parent[5],
                reduction, parent[7], parent[8], parent[9], parent[10])
    actual = (row[3], row[5], row[6], row[7], row[8], row[9], row[10], row[11],
              row[12], row[13], row[14])
    if actual != expected:
        raise QuoteError(f"Precio específico {sid} discrepa de su regla nativa {rule_id}")
    groups = defaultdict(set)
    for rid, group, kind, value in conditions:
        if rid != rule_id:
            continue
        if kind != "category" or not isinstance(value, str) or not value.isdecimal() or int(value) < 1:
            raise QuoteError(f"Condición no soportada en regla nativa {rule_id}: {kind}")
        groups[group].add(int(value))
    if groups and not any(group <= categories.get(product_id, set()) for group in groups.values()):
        raise QuoteError(f"Producto {product_id} fuera de las categorías de la regla nativa {rule_id}")
    return {"specific_price_id": sid, "catalog_rule_id": rule_id, "kind": "materialized_catalog_rule",
            "condition_groups": {str(group): sorted(values) for group, values in groups.items()},
            "catalog_reduction": str(parent[6]), "materialized_reduction": str(row[10])}


def _validate_price_hooks(hooks: list, validation: dict) -> None:
    for hook, module in hooks:
        if validation["mode"] == "native_rules_confirmed":
            if hook in {"actionproductpricecalculation", "actiontaxmanager", "actiongetproductpropertiesafter"}:
                raise QuoteError(f"Cálculo no soportado: {module}/{hook}; revisar modules/{module}/{module}.php")
        elif (hook, module) not in {("actionpresentproduct", "altautofiller"),
                                    ("displayproductpriceblock", "revolutpayment")}:
            raise QuoteError("Hay un módulo de cálculo o presentación de precios no auditado")


def _database_id(value: int | Decimal) -> int:
    if (type(value) not in (int, Decimal)
            or isinstance(value, Decimal) and (not value.is_finite() or value != value.to_integral_value())
            or not 0 <= value <= 0xFFFFFFFF):
        raise QuoteError("Identificador MySQL no entero o fuera de rango")
    return int(value)


def _mysql_rows(eans: list[str], context: dict) -> tuple[list[tuple], list[tuple]]:
    import pymysql

    try:
        config = {name: os.environ[f"PRESTASHOP_MYSQL_{key}"] for name, key in (
            ("host", "HOST"), ("user", "USER"), ("password", "PASSWORD"),
            ("database", "DATABASE"))}
        port = int(os.getenv("PRESTASHOP_MYSQL_PORT", "3306"))
    except (KeyError, ValueError):
        raise QuoteError("Falta configurar la conexión MySQL de PrestaShop") from None
    authorization = _account_authorization(config, port)
    if not eans or len(eans) != len(set(eans)) or not set(eans) <= manifest_eans():
        raise QuoteError("La lectura MySQL debe limitarse al manifiesto autorizado de 100 EAN")
    with closing(pymysql.connect(**config, port=port, **_mysql_tls_options(),
                                 connect_timeout=5, read_timeout=15, write_timeout=15,
                                 charset="utf8mb4")) as con, con.cursor() as cur:
        cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        cipher = cur.fetchone()
        if not cipher or not cipher[1]:
            raise QuoteError("La sesión MySQL de PrestaShop no tiene TLS activo")
        security = _connection_security(cur, config, cipher[1], authorization)
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        try:
            if authorization is not None:
                cur.execute("SELECT @@tx_read_only")
                read_only = cur.fetchone()
                if not read_only or read_only[0] != 1:
                    raise QuoteError("La sesión MySQL autorizada no está en modo READ ONLY")
                security["transaction_read_only"] = True
            identities, quotes = read_batch(cur, eans, context)
            return identities, [(ean, json.dumps({**json.loads(raw), "connection_security": security})
                                 if isinstance(raw, str) else raw) for ean, raw in quotes]
        finally:
            con.rollback()


def read_batch(cur, eans: list[str], context: dict, *, audit: bool = False) -> tuple[list[tuple], list[tuple]]:
    shop, country, currency, group = (context[k] for k in ("shop_id", "country_id", "currency_id", "group_id"))

    def select(sql, args=()):
        if (not sql.lstrip().upper().startswith("SELECT ")
                or re.search(r";|--|/\*|#|\b(?:INTO|LOAD_FILE|SLEEP|BENCHMARK)\b", sql, re.I)):
            raise QuoteError("El lector de precios solo admite sus consultas SELECT parametrizadas")
        sql = re.sub(r"\{([a-z_]+)\}", lambda m: _identifier("PRESTASHOP_MYSQL_PREFIX", m[1]), sql)
        cur.execute(sql, args)
        return list(cur.fetchall())

    (reference,), = select("SELECT UTC_TIMESTAMP(6)")
    now = datetime.fromisoformat(str(reference)).replace(tzinfo=timezone.utc)
    references = {ean: PRICE_REFERENCES[ean] for ean in eans if ean in PRICE_REFERENCES}
    forms = sorted({form for ean in eans for form in ((ean, ean[1:]) if ean.startswith("0") else (ean,))}
                   | {ref[0] for ref in references.values()})
    identity_marks = ",".join(["%s"] * len(forms))
    identities = select("""SELECT p.ean13, p.id_product, 0,
                    EXISTS (SELECT 1 FROM {product_attribute} a
                        JOIN {product_attribute_shop} ats
                        ON ats.id_product_attribute=a.id_product_attribute AND ats.id_shop=ps.id_shop
                        WHERE a.id_product=p.id_product)
                FROM {product} p JOIN {product_shop} ps
                ON ps.id_product=p.id_product AND ps.id_shop=%s
                WHERE p.ean13 IN (MARKS)
                UNION ALL
                SELECT a.ean13,a.id_product,a.id_product_attribute,0
                FROM {product_attribute} a JOIN {product_attribute_shop} ats
                ON ats.id_product_attribute=a.id_product_attribute AND ats.id_shop=%s
                JOIN {product_shop} ps ON ps.id_product=a.id_product AND ps.id_shop=ats.id_shop
                WHERE a.ean13 IN (MARKS)""".replace("MARKS", identity_marks), (shop, *forms, shop, *forms))
    # MySQL can promote the literal-zero/unsigned-ID UNION column to DECIMAL.
    identities = [(ean, _database_id(pid), _database_id(aid), unresolved)
                  for ean, pid, aid, unresolved in identities]
    reference_links = {}
    literal_reference_links = {}
    reference_issues = {}
    if references:
        refs = sorted({row[6] for row in references.values()})
        reference_rows = select("""SELECT p.id_product,m.name,p.reference FROM {product} p
            JOIN {manufacturer} m ON m.id_manufacturer=p.id_manufacturer
            JOIN {product_shop} ps ON ps.id_product=p.id_product AND ps.id_shop=%s
            WHERE p.reference IN (MARKS)""".replace("MARKS", ",".join(["%s"] * len(refs))), (shop, *refs))
        for ean, ref in references.items():
            original, pid, aid, brand, _, _, reference = ref
            matching_rows = {row for row in identities if row[0].zfill(13) == original.zfill(13)}
            expected_rows = {(original, pid, aid, 0), (original, pid, 0, bool(aid))}
            live = {row[0] for row in reference_rows if row[1:] == (brand, reference)}
            literal = {row for row in identities if row[0].zfill(13) == ean}
            if literal:
                if live == {pid} and {row[1] for row in literal} == {pid}:
                    literal_reference_links[ean] = ref
                else:
                    reference_issues[ean] = QuoteError(
                        "La ficha de referencia auditada cambió, no pertenece a la tienda o es ambigua")
            elif live == {pid} and (original, pid, aid, 0) in matching_rows and matching_rows <= expected_rows:
                reference_links[ean] = ref
                identities.append((ean, pid, aid, 0))
            else:
                reference_issues[ean] = QuoteError(
                    "La ficha de referencia auditada cambió, no pertenece a la tienda o es ambigua")
    requested = set(eans)
    identities = [row for row in identities if row[0].zfill(13) in requested
                  and row[0].zfill(13) not in reference_issues]
    products = sorted({row[1] for row in identities})
    if not products:
        return identities, list(reference_issues.items())
    marks = ",".join(["%s"] * len(products))
    settings = {}
    market = select("""SELECT s.id_shop_group,co.iso_code,cu.iso_code,cu.`precision`,g.price_display_method,
        g.reduction,sg.share_stock FROM {shop} s JOIN {shop_group} sg ON sg.id_shop_group=s.id_shop_group
        JOIN {country} co ON co.id_country=%s AND co.active=1
        JOIN {currency} cu ON cu.id_currency=%s AND cu.active=1 AND cu.deleted=0
        JOIN {group} g ON g.id_group=%s WHERE s.id_shop=%s AND s.active=1""", (country, currency, group, shop))
    if len(market) != 1 or tuple(market[0][1:5]) != ("ES", "EUR", 2, 0) or Decimal(market[0][5]) != 0:
        raise QuoteError("Contexto de tienda, IVA visible, grupo o moneda no soportado")
    shop_group = market[0][0]
    for key, value in select("""SELECT name,value FROM {configuration}
        WHERE (id_shop_group IS NULL OR id_shop_group=%s) AND (id_shop IS NULL OR id_shop=%s)
        AND name IN ('PS_VERSION_DB','PS_TAX','PS_UNIDENTIFIED_GROUP','PS_CURRENCY_DEFAULT',
          'PS_PRICE_ROUND_MODE','PS_ROUND_TYPE','PS_TAX_ADDRESS_TYPE','PS_TIMEZONE',
          'PS_SPECIFIC_PRICE_FEATURE_ACTIVE','PS_SPECIFIC_PRICE_PRIORITIES')
        ORDER BY id_shop_group IS NOT NULL,id_shop IS NOT NULL,id_configuration""", (shop_group, shop)):
        settings[key] = value
    expected = {"PS_VERSION_DB": "8.2.1", "PS_TAX": "1", "PS_UNIDENTIFIED_GROUP": str(group),
                "PS_CURRENCY_DEFAULT": str(currency), "PS_PRICE_ROUND_MODE": "2",
                "PS_ROUND_TYPE": "2", "PS_TAX_ADDRESS_TYPE": "id_address_delivery"}
    if any(settings.get(key) != value for key, value in expected.items()):
        raise QuoteError("Configuración fiscal, precisión o versión de PrestaShop no soportada")
    hooks = select("""SELECT DISTINCT LOWER(h.name),m.name FROM {hook_module} hm
        JOIN {hook} h ON h.id_hook=hm.id_hook JOIN {module} m ON m.id_module=hm.id_module
        WHERE hm.id_shop=%s AND m.active=1 AND LOWER(h.name) IN
        ('actionproductpricecalculation','actiontaxmanager','actionpresentproduct',
         'actiongetproductpropertiesafter','displayproductpriceblock')""", (shop,))
    validation = _price_validation(context, audit=audit)
    _validate_price_hooks(hooks, validation)
    if validation["mode"] == "native_rules_confirmed":
        validation["presentation_hooks"] = [{"hook": hook, "module": module} for hook, module in hooks]
    if select("SELECT 1 FROM {group_reduction} WHERE id_group=%s LIMIT 1", (group,)):
        raise QuoteError("Grupo público con descuentos por categoría no soportados")
    bases = {r[0]: r[1:] for r in select("""SELECT id_product,price,ecotax,id_tax_rules_group,active,show_price,
               minimal_quantity FROM {product_shop} WHERE id_shop=%s AND id_product IN (MARKS)"""
                                             .replace("MARKS", marks), (shop, *products))}
    variants = defaultdict(dict)
    for pid, aid, impact, ecotax, minimum in select("""SELECT id_product,id_product_attribute,price,ecotax,
        minimal_quantity FROM {product_attribute_shop} WHERE id_shop=%s AND id_product IN (MARKS)"""
                                                     .replace("MARKS", marks), (shop, *products)):
        variants[pid][aid] = (impact, ecotax, minimum)
    taxes = defaultdict(list)
    for row in select("""SELECT r.id_tax_rules_group,r.id_state,r.zipcode_from,r.zipcode_to,t.rate,
          t.active,t.deleted FROM {tax_rule} r JOIN {tax} t ON t.id_tax=r.id_tax
          WHERE r.id_country=%s AND r.id_tax_rules_group IN (SELECT id_tax_rules_group FROM {product_shop}
             WHERE id_shop=%s AND id_product IN (MARKS))""".replace("MARKS", marks), (country, shop, *products)):
        taxes[row[0]].append(row[1:])
    priorities = dict(select("""SELECT id_product,priority FROM {specific_price_priority}
        WHERE id_product IN (MARKS) ORDER BY id_specific_price_priority""".replace("MARKS", marks), products))
    rules = defaultdict(list)
    if settings.get("PS_SPECIFIC_PRICE_FEATURE_ACTIVE") == "1":
        for row in select("""SELECT id_product,id_specific_price,id_product_attribute,id_specific_price_rule,
            id_shop,id_shop_group,id_currency,id_country,id_group,from_quantity,price,reduction,reduction_type,
            reduction_tax,CAST(`from` AS CHAR),CAST(`to` AS CHAR)
            FROM {specific_price} WHERE id_product IN (0,MARKS) AND id_shop IN (0,%s)
            AND id_shop_group IN (0,%s) AND id_currency IN (0,%s)
            AND id_country IN (0,%s) AND id_group IN (0,%s) AND id_cart=0 AND id_customer=0 AND from_quantity<=1"""
                          .replace("MARKS", marks), (*products, shop, shop_group, currency, country, group)):
            rules[row[0]].append(row[1:])
    parents, conditions, categories = {}, [], defaultdict(set)
    if validation["mode"] == "native_rules_confirmed":
        rule_ids = sorted({row[2] for rows in rules.values() for row in rows if row[2]})
        if rule_ids:
            rule_marks = ",".join(["%s"] * len(rule_ids))
            parents = {row[0]: row[1:] for row in select("""SELECT id_specific_price_rule,id_shop,id_currency,
                id_country,id_group,from_quantity,price,reduction,reduction_type,reduction_tax,
                CAST(`from` AS CHAR),CAST(`to` AS CHAR) FROM {specific_price_rule}
                WHERE id_specific_price_rule IN (MARKS)""".replace("MARKS", rule_marks), rule_ids)}
            conditions = select("""SELECT g.id_specific_price_rule,g.id_specific_price_rule_condition_group,
                c.type,c.value FROM {specific_price_rule_condition_group} g
                LEFT JOIN {specific_price_rule_condition} c USING(id_specific_price_rule_condition_group)
                WHERE g.id_specific_price_rule IN (MARKS)""".replace("MARKS", rule_marks), rule_ids)
            for pid, category in select("""SELECT id_product,id_category FROM {category_product}
                WHERE id_product IN (MARKS)""".replace("MARKS", marks), products):
                categories[pid].add(category)
    stock = defaultdict(list)
    stock_scope = (0, shop_group) if market[0][6] else (shop, 0)
    for pid, aid, quantity in select("""SELECT id_product,id_product_attribute,quantity
        FROM {stock_available} WHERE id_shop=%s AND id_shop_group=%s AND id_product IN (MARKS)"""
                                     .replace("MARKS", marks), (*stock_scope, *products)):
        stock[pid, aid].append(quantity)
    try:
        tz = ZoneInfo(settings.get("PS_TIMEZONE") or "")
    except (ValueError, ZoneInfoNotFoundError):
        raise QuoteError("Zona horaria de PrestaShop no verificable") from None
    quotes = list(reference_issues.items())
    seen = set()
    for raw_ean, pid, aid, unresolved in identities:
        ean = raw_ean.zfill(13)
        if unresolved or (ean, pid, aid) in seen:
            continue
        seen.add((ean, pid, aid))
        try:
            def calculate(combination):
                return _calculate(bases[pid], variants[pid].get(combination), combination,
                                  taxes[bases[pid][2]], rules[0] + rules[pid],
                                  priorities.get(pid) or settings["PS_SPECIFIC_PRICE_PRIORITIES"],
                                  context, now, tz)

            payload = _with_other_calibres(calculate, aid, variants[pid])
            if validation["mode"] == "native_rules_confirmed":
                if any(price != payload["price"] for price in payload["other_calibres"].values()):
                    raise QuoteError("Importes contradictorios entre combinaciones: verificar modelo/color/calibre")
                eligible = set(payload["eligible_specific_price_ids"])
                payload["discount_trace"] = {
                    "authority": "specific_price_materialized_rows",
                    "selected_specific_price_id": int(payload["offer"]["reference"]) if payload["offer"] else None,
                    "eligible_sources": [_catalog_origin(pid, row, parents, conditions, categories)
                                         for row in rules[0] + rules[pid] if row[0] in eligible],
                    "catalog_discount_reapplied": False,
                }
            payload["price_validation"] = validation
            if ean in reference_links:
                _annotate_reference(payload, ean, reference_links[ean])
            elif ean in literal_reference_links:
                _annotate_reference(payload, ean, literal_reference_links[ean], literal=True)
            quantities = stock[pid, aid]
            payload.update(context, product_id=pid, combination_id=aid, engine=ENGINE,
                           observed_at=now.isoformat())
            if ean not in reference_links:
                payload["active"] = bool(bases[pid][3])
                payload["in_stock"] = quantities[0] > 0 if len(quantities) == 1 else None
            quotes.append((ean, json.dumps(payload)))
        except QuoteError as exc:
            quotes.append((ean, exc))
    return identities, quotes


def _annotate_reference(payload: dict, ean: str, link: tuple, *, literal: bool = False) -> None:
    original, pid, aid, brand, model, colour, reference = link
    association = {
        "kind": "marca/modelo/color", "requested_ean": ean, "original_barcode": original,
        "brand": brand, "model": model, "colour": colour, "reference": reference,
        "evidence": "Auditoría PR #145: catálogo vigilado, ps_manufacturer, ps_product.reference, "
                    "ps_product_lang.name y características Modelo/Color",
        "reason": "Mismo modelo y color; el calibre no modifica el precio. El código coincide solo con la ficha de referencia. "
                  "No acredita variante ni disponibilidad del EAN solicitado.",
    }
    if literal:
        association.update(
            original_reference_product_id=pid, original_reference_combination_id=aid,
            literal_ean_verified=True,
            reason="Asociación de precio conservada: mismo producto, marca y referencia completa auditados. "
                   "La identidad actual procede de la coincidencia literal UPC-12/EAN-13, no del código anterior. "
                   "La disponibilidad se consulta sólo para la combinación actual.",
        )
    payload["price_association" if literal else "price_reference"] = association


def _with_other_calibres(calculate: Callable[[int], dict], combination: int, siblings: Iterable[int]) -> dict:
    payload = calculate(combination)
    prices, issues = {}, {}
    for sibling in siblings:
        if sibling == combination:
            continue
        try:
            quote = calculate(sibling)
        except QuoteError as exc:
            issues[str(sibling)] = str(exc)
            continue
        prices[str(sibling)] = quote["price"]
        payload["valid_until"] = min(payload["valid_until"], quote["valid_until"])
    return {**payload, "other_calibres": prices, "other_calibre_issues": issues}


def _calculate(base, variant, combination, taxes, rules, priority, context, now, tz):
    base_price, ecotax, tax_group, _active, show_price, minimum = base
    impact, variant_eco, variant_minimum = variant or (0, 0, 1)
    if not show_price:
        raise QuoteError("PrestaShop no muestra el precio de este producto")
    if max(minimum, variant_minimum) > 1:
        raise QuoteError("La cantidad mínima del producto supera una unidad")
    if ecotax or variant_eco or (combination and variant is None):
        raise QuoteError("Ecotasa o combinación no verificable")
    if tax_group and (len(taxes) != 1 or tuple(taxes[0][:3]) != (0, "0", "0")
                      or tuple(taxes[0][4:]) != (1, 0)):
        raise QuoteError("Regla fiscal ES territorial, múltiple o no verificable")
    rate = Decimal(taxes[0][3]) if tax_group else Decimal(0)
    factor = 1 + rate / 100
    fields = priority.split(";")
    mapping = {"id_shop": ("shop_id", 3), "id_currency": ("currency_id", 5),
               "id_country": ("country_id", 6), "id_group": ("group_id", 7)}
    if len(fields) != len(mapping) or set(fields) != set(mapping):
        raise QuoteError("Prioridad de precios específicos no soportada")
    contenders = []
    until = now + timedelta(hours=24)
    local_minute = now.astimezone(tz).replace(second=0, microsecond=0, tzinfo=None)

    def bound(raw):
        if raw == "0000-00-00 00:00:00":
            return None
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            raise QuoteError("Fecha de vigencia de oferta no verificable") from None

    for row in rules:
        _, aid, rule, _, _, _, _, _, quantity, _, _, _, _, start, end = row
        if aid not in (0, combination) or quantity > context["quantity"]:
            continue
        begin, finish = bound(start), bound(end)
        if any(value and value.replace(tzinfo=tz, fold=0).utcoffset() !=
               value.replace(tzinfo=tz, fold=1).utcoffset() for value in (begin, finish)):
            raise QuoteError("Vigencia en un cambio de hora ambiguo")
        if begin and begin > local_minute:
            first = (begin + timedelta(seconds=59)).replace(second=0, microsecond=0)
            until = min(until, first.replace(tzinfo=tz).astimezone(timezone.utc))
            continue
        if finish and finish < local_minute:
            continue
        if finish:
            next_minute = finish.replace(second=0, microsecond=0) + timedelta(minutes=1)
            until = min(until, next_minute.replace(tzinfo=tz).astimezone(timezone.utc))
        score = sum(2 ** rank for rank, field in enumerate(reversed(fields), 1)
                    if row[mapping[field][1]] == context[mapping[field][0]])
        contenders.append(((aid, quantity, -rule, score, end, start), row))
    contenders.sort(key=lambda item: item[0], reverse=True)
    if len(contenders) > 1 and contenders[0][0] == contenders[1][0]:
        raise QuoteError("Empate entre precios específicos sin orden inequívoco")
    selected = contenders[0][1] if contenders else None
    net = Decimal(base_price) + Decimal(impact)
    offer = None
    gross = net * factor
    if selected:
        sid, aid, rule, shop, shop_group, currency, country, group, qty, fixed, reduction, kind, incl, start, end = selected
        if fixed >= 0:
            net = Decimal(fixed) + (0 if aid else Decimal(impact))
            gross = net * factor
        discount = Decimal(reduction)
        if kind == "percentage" and 0 <= discount <= 1:
            gross -= gross * discount
        elif kind == "amount" and discount >= 0 and incl in (0, 1):
            gross -= discount if incl else discount * factor
        else:
            raise QuoteError("Reducción de PrestaShop no soportada")
        offer = {"reference": str(sid), "starts_at": bound(start).replace(tzinfo=tz).isoformat()
                 if bound(start) else None, "ends_at": None,
                 "conditions": {"rule_id": rule, "shop_id": shop, "shop_group_id": shop_group,
                                "currency_id": currency, "country_id": country, "group_id": group,
                                "combination_id": aid, "min_quantity": max(1, qty),
                                "fixed_price_ex_tax": str(fixed), "reduction": str(reduction),
                                "type": kind, "reduction_tax_included": bool(incl),
                                "priority": fields, "local_ends_at": end}}
    return {"price": str(max(Decimal(0), gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "base_price_ex_tax": str(base_price), "combination_impact_ex_tax": str(impact),
            "taxable_base": str(net), "vat_rate": str(rate), "rounding": "half-up/2",
            "offer": offer, "valid_until": until.isoformat(),
            "eligible_specific_price_ids": [row[0] for _, row in contenders],
            "fiscal_context": f"ES peninsular · grupo fiscal {tax_group} · IVA {rate}%"}


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
    if payload.get("other_calibre_issues"):
        snapshot["other_calibre_issues"] = payload["other_calibre_issues"]
    if payload.get("price_reference"):
        snapshot["price_reference"] = payload["price_reference"]
    if payload.get("connection_security"):
        snapshot["connection_security"] = payload["connection_security"]
    for key in ("price_validation", "discount_trace", "price_association"):
        if payload.get(key):
            snapshot[key] = payload[key]
    for key in ("active", "in_stock"):
        if type(payload.get(key)) is bool:
            snapshot[key] = payload[key]
    for key in ("base_price_ex_tax", "combination_impact_ex_tax", "taxable_base", "vat_rate", "rounding"):
        if key in payload:
            snapshot[key] = payload[key]
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False), observed.isoformat(), valid_until.isoformat()


def resolve(eans: list[str], identities: Iterable[tuple], rows: Iterable[tuple], context: dict,
            now: datetime) -> dict[str, tuple[str, str, str] | str]:
    candidates: dict[str, set[tuple]] = defaultdict(set)
    quotes: dict[str, list[str | QuoteError]] = defaultdict(list)
    for ean, product, combination, unresolved in identities:
        if isinstance(ean, str) and re.fullmatch(r"[0-9]{12,13}", ean):
            candidates[ean.zfill(13)].add((product, combination, unresolved))
    for ean, raw in rows:
        quotes[ean].append(raw)
    results: dict[str, tuple[str, str, str] | str] = {}
    for ean in eans:
        matches = candidates[ean]
        # This installation also stores the same barcode on many parent rows.
        # An exact, unique combination under that same product is more specific.
        if len({match[0] for match in matches}) == 1:
            combinations = {match for match in matches if match[1]}
            if combinations:
                matches = combinations
        if not matches:
            results[ean] = (str(quotes[ean][0]) if len(quotes[ean]) == 1 and isinstance(quotes[ean][0], QuoteError)
                            else "EAN no localizado en la tienda de PrestaShop")
        elif len(matches) != 1 or next(iter(matches))[2]:
            results[ean] = "EAN ambiguo o sin combinación inequívoca en PrestaShop"
        elif len(quotes[ean]) != 1:
            results[ean] = ("Varios resultados nativos para el EAN y contexto" if quotes[ean]
                            else "Sin resultado del motor nativo para el EAN")
        else:
            product, combination, _ = next(iter(matches))
            try:
                raw = quotes[ean][0]
                if isinstance(raw, QuoteError):
                    raise raw
                results[ean] = _decode(raw, (product, combination), context, now)
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
                SELECT ean,?,?,json_remove(?,'$.active','$.in_stock') FROM prestashop_prices WHERE ean=?
                AND json_remove(payload,'$.active','$.in_stock') IS NOT json_remove(?,'$.active','$.in_stock')""",
                        (synced_at, observed, payload, ean, payload))
            con.execute("""UPDATE prestashop_prices SET payload=?,observed_at=?,valid_until=?,
                synced_at=?,issue='' WHERE ean=?""", (payload, observed, until, synced_at, ean))
    return {"monitored": len(eans), "issues": sum(isinstance(value, str) for value in results.values())}
