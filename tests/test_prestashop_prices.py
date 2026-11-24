import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import pymysql
from pymysql import OperationalError

import prestashop_prices as ps
from price_comparator import PriceComparatorStore
from price_watch import PriceWatch
from vytria_price_sync import VytriaPriceSync

NOW = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)
EAN = "0889652637075"
CONTEXT = {"shop_id": 1, "group_id": 2, "country_id": 6, "currency_id": 1,
           "market": "ES", "currency": "EUR", "customer_id": 0, "quantity": 1,
           "tax_included": True, "shipping_included": False, "cart_discount": False}
OPEN = "0000-00-00 00:00:00"
PRIORITY = "id_group;id_currency;id_country;id_shop"
NATIVE_CONTEXT = {**CONTEXT, "group_id": 1}


@pytest.mark.parametrize(("mode", "ca"), [
    (None, None), ("PREFERRED", None), ("DISABLED", None), ("REQUIRED", "ca.pem"),
])
def test_mysql_rejects_missing_or_unsafe_tls_settings(monkeypatch, mode, ca):
    for key, value in [("PRESTASHOP_MYSQL_SSL_MODE", mode), ("PRESTASHOP_MYSQL_SSL_CA", ca)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(ps.QuoteError, match="Configurar TLS"):
        ps._mysql_tls_options()


@pytest.mark.parametrize("mode", ["REQUIRED", "VERIFY_IDENTITY"])
def test_mysql_configures_driver_for_mandatory_tls(monkeypatch, mode):
    monkeypatch.setenv("PRESTASHOP_MYSQL_SSL_MODE", mode)
    monkeypatch.delenv("PRESTASHOP_MYSQL_SSL_CA", raising=False)
    if mode == "VERIFY_IDENTITY":
        monkeypatch.setenv("PRESTASHOP_MYSQL_SSL_CA", "test-ca.pem")
        assert ps._mysql_tls_options() == {
            "ssl_ca": "test-ca.pem", "ssl_verify_cert": True, "ssl_verify_identity": True}
    else:
        con = pymysql.connect(defer_connect=True, **ps._mysql_tls_options())
        assert con._ssl_required and not con.ctx.check_hostname


@pytest.mark.parametrize(("cipher", "grant", "error"), [
    ("", "GRANT SELECT ON db.* TO reader", "no tiene TLS activo"),
    ("AES", "GRANT SELECT, INSERT ON db.* TO reader", "exclusivamente de lectura"),
    ("AES", "GRANT SELECT ON db.* TO reader WITH GRANT OPTION", "exclusivamente de lectura"),
    ("AES", "GRANT price_role TO reader", "exclusivamente de lectura"),
    ("AES", "GRANT SELECT ON db.* TO reader", None),
])
def test_mysql_validates_session_before_reading_prices(monkeypatch, cipher, grant, error):
    for key in ("HOST", "USER", "PASSWORD", "DATABASE"):
        monkeypatch.setenv(f"PRESTASHOP_MYSQL_{key}", "test")
    monkeypatch.setenv("PRESTASHOP_MYSQL_SSL_MODE", "REQUIRED")
    monkeypatch.delenv("PRESTASHOP_MYSQL_SSL_CA", raising=False)
    con = MagicMock()
    cur = con.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ("Ssl_cipher", cipher)
    cur.fetchall.return_value = [("GRANT USAGE ON *.* TO reader",), (grant,)]
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: con)
    read_batch = MagicMock(return_value=([], []))
    monkeypatch.setattr(ps, "read_batch", read_batch)
    if error:
        with pytest.raises(ps.QuoteError, match=error):
            ps._mysql_rows([EAN], CONTEXT)
        read_batch.assert_not_called()
    else:
        assert ps._mysql_rows([EAN], CONTEXT) == ([], [])
        cur.execute.assert_called_with("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        read_batch.assert_called_once_with(cur, [EAN], CONTEXT)
    con.close.assert_called_once()


def authorized_account(monkeypatch, **changes):
    config = {"host": "mysql.example.invalid", "user": "ps_user_vytria_lect",
              "password": "test", "database": "ps_vytria"}
    for key, value in config.items():
        monkeypatch.setenv(f"PRESTASHOP_MYSQL_{key.upper()}", value)
    monkeypatch.setenv("PRESTASHOP_MYSQL_ACCOUNT_POLICY", "authorized_broad")
    monkeypatch.setenv("PRESTASHOP_MYSQL_SSL_MODE", "REQUIRED")
    monkeypatch.delenv("PRESTASHOP_MYSQL_SSL_CA", raising=False)
    receipt = {"id": "approved-test", "actor": "reviewer", "at": NOW.isoformat(),
               "host": config["host"], "port": 3306, "user": config["user"],
               "database": config["database"], **changes}
    monkeypatch.setenv("PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION", json.dumps(receipt))
    return config, receipt


def test_authorized_broad_account_is_scoped_read_only_and_audited(monkeypatch):
    _, receipt = authorized_account(monkeypatch)
    con = MagicMock()
    cur = con.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [("Ssl_cipher", "AES"), ("ps_user_vytria_lect@origin", "ps_vytria"), (1,)]
    cur.fetchall.return_value = [
        ("GRANT USAGE ON *.* TO reader",),
        (r"GRANT SELECT, INSERT, UPDATE, EXECUTE ON `ps\\_vytria`.* TO reader",),
    ]
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: con)
    read_batch = MagicMock(return_value=([(EAN, 498, 90, 0)], [(EAN, json.dumps(quote()))]))
    monkeypatch.setattr(ps, "read_batch", read_batch)

    identities, rows = ps._mysql_rows([EAN], CONTEXT)
    stored, _, _ = ps.resolve([EAN], identities, rows, CONTEXT, NOW)[EAN]
    security = json.loads(stored)["connection_security"]
    assert security["account_policy"] == "authorized_broad"
    assert security["account_permissions"] == "broad"
    assert security["tls_required"] and security["tls_cipher"] == "AES"
    assert security["transaction_read_only"] is True
    assert security["certificate_verified"] is False
    assert security["server_identity_verified"] is False
    assert security["authorization"] == {key: receipt[key] for key in ("id", "actor", "at")}
    assert "host" not in security and "password" not in security
    statements = [call.args[0] for call in cur.execute.call_args_list]
    assert "SET SESSION TRANSACTION READ ONLY" in statements
    assert statements[-2:] == ["START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
                               "SELECT @@tx_read_only"]
    con.rollback.assert_called_once()
    con.close.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"host": "another.example.invalid"}, {"port": 3307}, {"database": "another"},
    {"user": "another"}, {"actor": ""}, {"at": "no-timezone"},
    {"at": "9999-01-01T00:00:00+00:00"},
])
def test_broad_account_authorization_cannot_move_to_another_connection(monkeypatch, changes):
    authorized_account(monkeypatch, **changes)
    connect = MagicMock()
    monkeypatch.setattr(pymysql, "connect", connect)
    with pytest.raises(ps.QuoteError, match="autorización válida y acotada"):
        ps._mysql_rows([EAN], CONTEXT)
    connect.assert_not_called()


@pytest.mark.parametrize(("grant", "identity"), [
    ("GRANT SELECT, INSERT ON other.* TO reader", ("ps_user_vytria_lect@origin", "ps_vytria")),
    ("GRANT SELECT, INSERT ON *.* TO reader", ("ps_user_vytria_lect@origin", "ps_vytria")),
    ("GRANT price_role TO reader", ("ps_user_vytria_lect@origin", "ps_vytria")),
    ("GRANT SELECT ON `ps_vytria`.* TO reader WITH GRANT OPTION",
     ("ps_user_vytria_lect@origin", "ps_vytria")),
    (r"GRANT SELECT, INSERT ON `ps\_vytria`.* TO reader", ("another@origin", "ps_vytria")),
    (r"GRANT SELECT, INSERT ON `ps\_vytria`.* TO reader", ("ps_user_vytria_lect@origin", "another")),
])
def test_broad_authorization_preserves_scope_and_actual_session_checks(monkeypatch, grant, identity):
    authorized_account(monkeypatch)
    con = MagicMock()
    cur = con.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [("Ssl_cipher", "AES"), identity]
    cur.fetchall.return_value = [("GRANT USAGE ON *.* TO reader",), (grant,)]
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: con)
    read_batch = MagicMock()
    monkeypatch.setattr(ps, "read_batch", read_batch)
    with pytest.raises(ps.QuoteError, match="ámbito autorizado|cuenta y base autorizadas"):
        ps._mysql_rows([EAN], CONTEXT)
    read_batch.assert_not_called()
    con.close.assert_called_once()


def test_missing_explicit_broad_authorization_and_out_of_scope_eans_fail_closed(monkeypatch):
    authorized_account(monkeypatch)
    monkeypatch.delenv("PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION")
    connect = MagicMock()
    monkeypatch.setattr(pymysql, "connect", connect)
    with pytest.raises(ps.QuoteError, match="autorización válida y acotada"):
        ps._mysql_rows([EAN], CONTEXT)
    authorized_account(monkeypatch)
    with pytest.raises(ps.QuoteError, match="manifiesto autorizado"):
        ps._mysql_rows(["0000000000017"], CONTEXT)
    connect.assert_not_called()


def test_broad_authorization_rejects_database_wildcard_grants():
    assert not ps._authorized_database_grant("GRANT SELECT, INSERT ON `ps_vytria`.* TO reader")


@pytest.mark.parametrize(("cipher", "read_only", "reason"), [
    ("", 1, "TLS activo"), ("AES", 0, "READ ONLY"),
])
def test_broad_authorization_does_not_bypass_tls_or_readonly(monkeypatch, cipher, read_only, reason):
    authorized_account(monkeypatch)
    con = MagicMock()
    cur = con.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [
        ("Ssl_cipher", cipher), ("ps_user_vytria_lect@origin", "ps_vytria"), (read_only,),
    ]
    cur.fetchall.return_value = [(r"GRANT SELECT, INSERT ON `ps\_vytria`.* TO reader",)]
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: con)
    read_batch = MagicMock()
    monkeypatch.setattr(ps, "read_batch", read_batch)
    with pytest.raises(ps.QuoteError, match=reason):
        ps._mysql_rows([EAN], CONTEXT)
    read_batch.assert_not_called()
    con.close.assert_called_once()


def rule(sid=5, aid=0, rule_id=0, shop=0, currency=0, country=0, group=0, quantity=1,
         fixed="-1", discount="0.15", kind="percentage", included=0, start=OPEN, end=OPEN):
    return (sid, aid, rule_id, shop, 0, currency, country, group, quantity,
            Decimal(fixed), Decimal(discount), kind, included, start, end)


def calculate(rules, priority=PRIORITY, taxes=((0, "0", "0", Decimal(21), 1, 0),), *, combination=90, active=1):
    return ps._calculate((Decimal("100"), 0, 63, active, 1, 1), (Decimal("10"), 0, 1),
                         combination, taxes, rules, priority, CONTEXT, NOW, ZoneInfo("Europe/Madrid"))


def native_confirmation(monkeypatch, **changes):
    authorized_account(monkeypatch)
    monkeypatch.setenv("PRESTASHOP_MYSQL_PREFIX", "ps_")
    monkeypatch.setenv("PRESTASHOP_PRICE_CODE_AUDITED", "0")
    monkeypatch.setenv("PRESTASHOP_PRICE_VALIDATION_MODE", "native_rules_confirmed")
    receipt = {"id": "native-test", "actor": "reviewer", "at": NOW.isoformat(),
               "account_authorization_id": "approved-test", "context": NATIVE_CONTEXT,
               "claim": "native_prestashop_catalog_rules", **changes}
    monkeypatch.setenv("PRESTASHOP_PRICE_NATIVE_CONFIRMATION", json.dumps(receipt))


def test_native_contract_is_explicit_not_a_php_audit_and_default_stays_blocked(monkeypatch):
    native_confirmation(monkeypatch)
    validation = ps._price_validation(NATIVE_CONTEXT)
    assert validation["mode"] == "native_rules_confirmed" and validation["php_inspected"] is False
    ps._validate_price_hooks([("actionpresentproduct", "theme")], validation)
    with pytest.raises(ps.QuoteError, match="modules/pricer/pricer.php"):
        ps._validate_price_hooks([("actionproductpricecalculation", "pricer")], validation)
    monkeypatch.delenv("PRESTASHOP_PRICE_VALIDATION_MODE")
    with pytest.raises(ps.QuoteError, match="Pendiente auditar"):
        ps._price_validation(CONTEXT)


@pytest.mark.parametrize("changes", [
    {"account_authorization_id": "another"}, {"claim": "php_audited"},
    {"context": {**NATIVE_CONTEXT, "group_id": 2}}, {"actor": ""}, {"at": "9999-01-01T00:00:00+00:00"},
])
def test_native_confirmation_cannot_change_scope_or_claim_php_inspection(monkeypatch, changes):
    native_confirmation(monkeypatch, **changes)
    with pytest.raises(ps.QuoteError, match="confirmación acotada"):
        ps._price_validation(NATIVE_CONTEXT)


def catalog_fixture():
    row = rule(sid=35, rule_id=35, shop=1, start="2026-11-01 00:00:00", end="2026-12-02 00:00:00")
    parents = {35: (1, 0, 0, 0, 1, Decimal(-1), Decimal(15), "percentage", 0, row[13], row[14])}
    return row, parents, [(35, 213, "category", "560")], {498: {560}}


def test_category_rule_is_materialized_once_and_specific_offer_takes_precedence():
    row, parents, conditions, categories = catalog_fixture()
    origin = ps._catalog_origin(498, row, parents, conditions, categories)
    assert origin["catalog_reduction"] == "15" and origin["materialized_reduction"] == "0.15"
    assert calculate([row])["price"] == "113.14"  # (100 + 10) * 1.21 * 0.85, not another 15%.
    specific = rule(sid=384459, aid=90, fixed="100", discount="0.10")
    result = calculate([row, specific])
    assert result["price"] == "108.90" and result["offer"]["reference"] == "384459"


@pytest.mark.parametrize("case", ["missing", "changed", "category", "unsupported"])
def test_native_catalog_lineage_discrepancies_are_not_silently_accepted(case):
    row, parents, conditions, categories = catalog_fixture()
    if case == "missing":
        parents = {}
    elif case == "changed":
        row = (*row[:10], Decimal("0.25"), *row[11:])
    elif case == "category":
        categories = {498: {561}}
    else:
        conditions = [(35, 213, "custom_discount", "560")]
    with pytest.raises(ps.QuoteError):
        ps._catalog_origin(498, row, parents, conditions, categories)


@pytest.mark.parametrize("value", [Decimal("90.5"), Decimal("NaN"), Decimal("Infinity"), -1, True, "90"])
def test_mysql_identity_normalization_rejects_fractional_or_invalid_identifiers(value):
    with pytest.raises(ps.QuoteError, match="Identificador MySQL"):
        ps._database_id(value)


@pytest.mark.parametrize("change", [None, "reference", "product"])
def test_current_literal_ean_preserves_approved_model_colour_without_transferring_stock(monkeypatch, change):
    native_confirmation(monkeypatch)
    ean = "8056262833902"
    original, pid, original_aid, brand, _, _, reference = ps.PRICE_REFERENCES[ean]
    current_aid = 21270
    current_pid = pid + 1 if change == "product" else pid
    cur = MagicMock()
    cur.fetchall.side_effect = [
        [(NOW.replace(tzinfo=None),)],
        [(original, pid, original_aid, 1), (ean, current_pid, Decimal(current_aid), 0)],
        [(pid, brand, "changed" if change == "reference" else reference)],
        [(1, "ES", "EUR", 2, 0, 0, 0)],
        list({"PS_VERSION_DB": "8.2.1", "PS_TAX": "1", "PS_UNIDENTIFIED_GROUP": "1",
              "PS_CURRENCY_DEFAULT": "1", "PS_PRICE_ROUND_MODE": "2", "PS_ROUND_TYPE": "2",
              "PS_TAX_ADDRESS_TYPE": "id_address_delivery", "PS_TIMEZONE": "Europe/Madrid",
              "PS_SPECIFIC_PRICE_FEATURE_ACTIVE": "1", "PS_SPECIFIC_PRICE_PRIORITIES": PRIORITY}.items()),
        [], [],
        [(pid, Decimal(100), 0, 63, 0, 1, 1)],
        [(pid, current_aid, 0, 0, 1)],
        [(63, 0, "0", "0", Decimal(21), 1, 0)],
        [], [],
        [(pid, original_aid, 100), (pid, current_aid, 0)],
    ]
    identities, rows = ps.read_batch(cur, [ean], NATIVE_CONTEXT)
    result = ps.resolve([ean], identities, rows, NATIVE_CONTEXT, NOW)[ean]
    if change:
        assert isinstance(result, str) and "referencia auditada cambió" in result
    else:
        price = json.loads(result[0])
        assert price["product_id"] == pid and price["combination_id"] == current_aid
        assert price["price"] == "121.00" and price["in_stock"] is False
        assert "price_reference" not in price
        association = price["price_association"]
        assert association["literal_ean_verified"] is True
        assert association["original_reference_combination_id"] == original_aid
        assert association["original_barcode"] == original


@pytest.mark.parametrize("contradictory_calibre", [False, True])
def test_native_batch_keeps_real_selection_trace_or_reports_calibre_conflict(monkeypatch, contradictory_calibre):
    native_confirmation(monkeypatch)
    monkeypatch.setenv("PRESTASHOP_MYSQL_PREFIX", "ps_")
    catalog, parents, conditions, categories = catalog_fixture()
    specific = rule(sid=384459, aid=90, fixed="100", discount="0.25")
    cur = MagicMock()
    current = [[]]

    def execute(sql, args=()):
        assert sql.lstrip().startswith("SELECT ") and ";" not in sql
        if "UTC_TIMESTAMP" in sql:
            rows = [(NOW.replace(tzinfo=None),)]
        elif "UNION ALL" in sql:
            rows = [(EAN, 498, Decimal(90), 0)]
        elif "price_display_method" in sql:
            rows = [(1, "ES", "EUR", 2, 0, 0, 0)]
        elif "SELECT name,value" in sql:
            rows = list({"PS_VERSION_DB": "8.2.1", "PS_TAX": "1", "PS_UNIDENTIFIED_GROUP": "1",
                         "PS_CURRENCY_DEFAULT": "1", "PS_PRICE_ROUND_MODE": "2", "PS_ROUND_TYPE": "2",
                         "PS_TAX_ADDRESS_TYPE": "id_address_delivery", "PS_TIMEZONE": "Europe/Madrid",
                         "PS_SPECIFIC_PRICE_FEATURE_ACTIVE": "1", "PS_SPECIFIC_PRICE_PRIORITIES": PRIORITY}.items())
        elif "LOWER(h.name)" in sql or "group_reduction" in sql or "SELECT id_product,priority" in sql:
            rows = []
        elif "SELECT id_product,price,ecotax" in sql:
            rows = [(498, Decimal(100), 0, 63, 0, 1, 1)]
        elif "SELECT id_product,id_product_attribute,price,ecotax" in sql:
            rows = [(498, 90, 0, 0, 1)]
            if contradictory_calibre:
                rows.append((498, 91, Decimal(10), 0, 1))
        elif "SELECT r.id_tax_rules_group" in sql:
            rows = [(63, 0, "0", "0", Decimal(21), 1, 0)]
        elif "SELECT id_product,id_specific_price" in sql:
            rows = [(498, *catalog), (498, *specific)]
        elif "SELECT id_specific_price_rule,id_shop" in sql:
            rows = [(rid, *parent) for rid, parent in parents.items()]
        elif "SELECT g.id_specific_price_rule" in sql:
            rows = conditions
        elif "SELECT id_product,id_category" in sql:
            rows = [(pid, category) for pid, values in categories.items() for category in values]
        elif "stock_available" in sql:
            rows = [(498, 90, 0)]
        else:
            pytest.fail(f"Unexpected price query: {sql}")
        current[0] = rows

    cur.execute.side_effect = execute
    cur.fetchall.side_effect = lambda: current[0]
    identities, raw = ps.read_batch(cur, [EAN], NATIVE_CONTEXT)
    result = ps.resolve([EAN], identities, raw, NATIVE_CONTEXT, NOW)[EAN]
    if contradictory_calibre:
        assert isinstance(result, str) and "contradictorios" in result
    else:
        price = json.loads(result[0])
        assert type(price["combination_id"]) is int and price["combination_id"] == 90
        assert price["price"] == "90.75" and price["active"] is False and price["in_stock"] is False
        assert price["price_validation"]["php_inspected"] is False
        assert price["discount_trace"]["selected_specific_price_id"] == 384459
        assert price["discount_trace"]["catalog_discount_reapplied"] is False
        assert {origin["kind"] for origin in price["discount_trace"]["eligible_sources"]} == {
            "specific_offer", "materialized_catalog_rule",
        }


def test_inactive_product_keeps_the_same_offer_and_tax():
    rules = [rule(aid=90, fixed="80")]
    assert calculate(rules, active=0) == calculate(rules, active=1)


@pytest.mark.parametrize("siblings,issues", [
    ({}, {}),
    ({"91": "232.06"}, {}),
    ({}, {"91": "IVA no verificable"}),
])
def test_price_reference_does_not_require_a_known_calibre(siblings, issues):
    ean = "8056262600078"
    link = ps.PRICE_REFERENCES[ean]
    original, pid, aid, *_ = link
    raw = quote(product_id=pid, combination_id=aid, other_calibres=siblings, other_calibre_issues=issues)
    ps._annotate_reference(raw, ean, link)
    stored, _, _ = ps.resolve([ean], [(ean, pid, aid, 0)], [(ean, json.dumps(raw))], CONTEXT, NOW)[ean]
    snapshot = json.loads(stored)
    assert snapshot["price_reference"]["kind"] == "marca/modelo/color"
    assert snapshot["price_reference"]["requested_ean"] == ean
    assert snapshot["price_reference"]["original_barcode"] == original
    assert snapshot["combination_id"] == aid
    assert snapshot["price"] == "232.05" and "in_stock" not in snapshot
    assert snapshot["other_calibres"] == siblings
    assert snapshot.get("other_calibre_issues", {}) == issues


@pytest.mark.parametrize(("rules", "final", "winner"), [
    ([], "133.10", None),
    ([rule()], "113.14", "5"),
    ([rule(kind="amount", discount="10")], "121.00", "5"),
    ([rule(kind="amount", discount="10", included=1)], "123.10", "5"),
    ([rule(fixed="80", discount="0")], "108.90", "5"),
    ([rule(sid=6, shop=1, rule_id=35), rule(aid=90, fixed="80")], "82.28", "5"),
    ([rule(sid=6, shop=1), rule(group=2)], "113.14", "5"),
    ([rule(quantity=2)], "133.10", None),
    ([rule(sid=6, start="2026-11-20 11:00:00"), rule(end="2026-11-20 09:59:59")],
     "133.10", None),
])
def test_mysql_calculator_order_rules_and_taxes(rules, final, winner):
    quote = calculate(rules)
    assert quote["price"] == final
    assert (quote["offer"]["reference"] if quote["offer"] else None) == winner


def test_mysql_calculator_bounds_and_fails_closed():
    next_start = rule(start="2026-11-20 10:30:00")
    assert calculate([next_start])["valid_until"] == (NOW + timedelta(minutes=30)).isoformat()
    with pytest.raises(ps.QuoteError, match="Empate"):
        calculate([rule(), rule(sid=6)])
    with pytest.raises(ps.QuoteError, match="territorial"):
        calculate([], taxes=((1, "0", "0", Decimal(21), 1, 0),))
    with pytest.raises(ps.QuoteError, match="Reducción"):
        calculate([rule(kind="custom")])


def test_sibling_issue_does_not_invalidate_selected_combination():
    def price_for(aid):
        if aid == 91:
            raise ps.QuoteError("Ecotasa o combinación no verificable")
        return calculate([rule(aid=aid, fixed="80" if aid == 92 else "100")], combination=aid)

    payload = ps._with_other_calibres(price_for, 90, [90, 91, 92])
    assert payload["price"] == "102.85"
    assert payload["other_calibres"] == {"92": "82.28"}
    assert payload["other_calibre_issues"] == {"91": "Ecotasa o combinación no verificable"}
    with pytest.raises(ps.QuoteError, match="Ecotasa"):
        ps._with_other_calibres(price_for, 91, [90, 91, 92])


def quote(**changes):
    return {**CONTEXT, "product_id": 498, "combination_id": 90, "price": "232.05",
            "observed_at": NOW.isoformat(), "valid_until": (NOW + timedelta(hours=12)).isoformat(),
            "fiscal_context": "Fixture ES: tax rules group 7", "engine": "fixture",
            "offer": None, "other_calibres": {}, **changes}


def offer(**conditions):
    return {"reference": "fixture-rule", "starts_at": NOW.isoformat(), "ends_at": None,
            "conditions": {"min_quantity": 1, "priority": ["shop", "country", "currency", "group"], **conditions}}


@pytest.mark.parametrize(("price", "rule"), [
    ("273.00", None),
    ("232.05", offer(reduction_type="percentage", reduction="0.15")),
    ("251.49", offer(reduction_type="amount", reduction="21.51", reduction_tax_included=True)),
    ("225.62", offer(reduction_type="specific_price", specific_price="186.46281")),
])
def test_native_amount_and_winner_are_preserved_without_recalculation(price, rule):
    raw = quote(price=price, offer=rule, other_calibres={"91": "240.00"})
    stored, _, _ = ps.resolve([EAN], [(EAN[1:], 498, 90, 0)], [(EAN, json.dumps(raw))], CONTEXT, NOW)[EAN]
    decoded = json.loads(stored)
    assert decoded["price"] == price
    assert decoded["offer"] == rule
    assert decoded["combination_id"] == 90
    assert decoded["other_calibres"] == {"91": "240.00"}


@pytest.mark.parametrize(("changes", "reason"), [
    ({"offer": offer(min_quantity=2)}, "una unidad"),
    ({"offer": {**offer(), "starts_at": (NOW + timedelta(seconds=1)).isoformat()}}, "vigente"),
    ({"offer": {**offer(), "ends_at": NOW.isoformat()}}, "caducado"),
    ({"valid_until": NOW.isoformat()}, "caducado"),
    ({"group_id": 3}, "group_id"),
    ({"tax_included": False}, "tax_included"),
    ({"cart_discount": True}, "cart_discount"),
    ({"combination_id": 91}, "combinación"),
])
def test_reject_inapplicable_native_quotes(changes, reason):
    result = ps.resolve([EAN], [(EAN, 498, 90, 0)], [(EAN, json.dumps(quote(**changes)))], CONTEXT, NOW)
    assert isinstance(result[EAN], str) and reason in result[EAN]


def test_ambiguous_ean_and_duplicate_source_rows_are_not_guessed():
    row = (EAN, json.dumps(quote()))
    native = [(EAN, 498, 90, 0), (EAN, 498, 0, 1)]
    assert not isinstance(ps.resolve([EAN], native, [row], CONTEXT, NOW)[EAN], str)
    for identities in (native + [(EAN[1:], 498, 91, 0)], native + [(EAN, 499, 0, 0)],
                       [(EAN, 498, 0, 1)]):
        assert "ambiguo" in ps.resolve([EAN], identities, [row], CONTEXT, NOW)[EAN]
    assert "Varios" in ps.resolve([EAN], native, [row, row], CONTEXT, NOW)[EAN]


def test_watched_sync_is_scoped_deduplicates_and_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESTASHOP_PRICE_ENABLED", "1")
    monkeypatch.setattr(ps, "_context", lambda: CONTEXT)
    scope = ps.manifest_eans()
    extra = "0000000000017"
    assert extra not in scope
    store = PriceComparatorStore(tmp_path / "prices.sqlite")
    store.import_products(pd.DataFrame([
        {"EAN": ean, "MARCA": "Pruebas", "NOMBRE": ean, "MODELO": "A", "COLOR": "B", "CATEGORIA": "Sol"}
        for ean in sorted(scope | {extra})
    ]))
    PriceWatch(store).add(sorted(scope | {extra}), actor="test")
    with store._connect() as con, con:
        con.execute("""INSERT INTO vytria_prices(ean,market,price,currency,price_includes_tax,source,pricelist_id,
                    validation_status,comparable,fingerprint,first_seen_at,changed_at,synced_at)
                    VALUES(?,'ES',273,'EUR',1,'Odoo tarifa 59',59,'validated',1,'x',?,?,?)""",
                    (EAN, *(NOW.isoformat(),) * 3))

    calls = []
    current_time = [NOW]
    fail = [False]
    price = ["232.05"]
    security = {"account_policy": "authorized_broad", "account_permissions": "broad",
                "tls_required": True, "tls_cipher": "AES", "certificate_verified": False,
                "server_identity_verified": False,
                "authorization": {"id": "approved-test", "actor": "reviewer", "at": NOW.isoformat()}}

    def mysql(eans, context):
        calls.append(set(eans))
        if fail[0]:
            raise OperationalError("private connection details")
        payload = quote(price=price[0], observed_at=current_time[0].isoformat(), active=False,
                        in_stock=len(calls) == 1, connection_security=security)
        return [(EAN, 498, 90, 0)], [(EAN, json.dumps(payload))]

    monkeypatch.setattr(ps, "_mysql_rows", mysql)
    sync = VytriaPriceSync(store, clock=lambda: current_time[0])
    monkeypatch.setattr(sync, "_fetch_with_retries", lambda eans: [])
    monkeypatch.setattr(sync, "_apply", lambda *args: None)  # Existing Odoo reference stays unchanged.

    def comparison():
        return store.dashboard(now=current_time[0])[0].set_index("ean").loc[EAN]

    for amount in ("232.05", "232.05", "230.00"):
        price[0] = amount
        result = sync.run(watched_only=True)
        assert result.prestashop == {"monitored": 100, "issues": 99}
        assert comparison()["own_price_eur"] == float(amount)
        current_time[0] += timedelta(minutes=1)
    fail[0] = True
    result = sync.run(watched_only=True)
    assert result.prestashop == {"monitored": 100, "issues": 100}
    assert calls == [set(scope)] * 4
    assert comparison()["native_price_eur"] == 230
    assert pd.isna(comparison()["own_price_eur"])
    assert comparison()["own_price_status"] == "stale"
    with store._connect() as con:
        current = con.execute("SELECT * FROM prestashop_prices WHERE ean=?", (EAN,)).fetchone()
        history = con.execute("SELECT payload FROM prestashop_price_history ORDER BY id").fetchall()
        assert [json.loads(row[0])["price"] for row in history] == ["232.05", "230.00"]
        assert current["synced_at"] == (current_time[0] - timedelta(minutes=1)).isoformat()
        assert current["observed_at"] == (current_time[0] - timedelta(minutes=1)).isoformat()
        assert json.loads(current["payload"])["price"] == "230.00"
        assert json.loads(current["payload"])["active"] is False
        assert json.loads(current["payload"])["in_stock"] is False
        assert all("in_stock" not in json.loads(row[0]) for row in history)
        assert json.loads(current["payload"])["connection_security"] == security
        assert all(json.loads(row[0])["connection_security"] == security for row in history)
        assert "OperationalError" in current["issue"] and "private" not in current["issue"]
        assert con.execute("SELECT price FROM vytria_prices WHERE ean=?", (EAN,)).fetchone()[0] == 273


def test_prada_offer_is_applied_once_and_keeps_odoo_as_reference(tmp_path, monkeypatch):
    ean = "8056597435581"
    assert ean in ps.manifest_eans()
    monkeypatch.setenv("PRESTASHOP_PRICE_ENABLED", "1")
    monkeypatch.setattr(ps, "_context", lambda: CONTEXT)
    store = PriceComparatorStore(tmp_path / "prada.sqlite")
    store.import_products(pd.DataFrame([{
        "EAN": ean, "MARCA": "Prada", "NOMBRE": "PR 17WS 2AU8C1", "MODELO": "PR 17WS",
        "COLOR": "2AU8C1", "CATEGORIA": "Sol",
    }]))
    PriceWatch(store).add([ean], actor="test")
    with store._connect() as con, con:
        con.execute("""INSERT INTO vytria_prices(ean,market,price,currency,price_includes_tax,source,pricelist_id,
                    validation_status,comparable,fingerprint,first_seen_at,changed_at,synced_at)
                    VALUES(?,'ES',273,'EUR',1,'Odoo tarifa 59',59,'validated',1,'prada',?,?,?)""",
                    (ean, *(NOW.isoformat(),) * 3))

    calculated = ps._calculate(
        (Decimal("225.6198"), 0, 63, 1, 1, 1), (Decimal("0"), 0, 1), 9367,
        [(0, "0", "0", Decimal("21"), 1, 0)],
        [rule(sid=427094, aid=9367, fixed="225.6198", discount="0.15")],
        PRIORITY, CONTEXT, NOW, ZoneInfo("Europe/Madrid"),
    )
    assert calculated["price"] == "232.05"
    payload = quote(product_id=498, combination_id=9367, price=calculated["price"],
                    offer=calculated["offer"], observed_at=NOW.isoformat(),
                    valid_until=calculated["valid_until"])
    monkeypatch.setattr(ps, "_mysql_rows", lambda eans, context: (
        [(ean, 498, 9367, 0)], [(ean, json.dumps(payload))]))
    sync = VytriaPriceSync(store, clock=lambda: NOW)
    monkeypatch.setattr(sync, "_fetch_with_retries", lambda eans: [])
    monkeypatch.setattr(sync, "_apply", lambda *args: None)

    result = sync.run(watched_only=True)
    row = store.dashboard(now=NOW)[0].set_index("ean").loc[ean]

    assert result.prestashop == {"monitored": 1, "issues": 0}
    assert row["odoo_price_eur"] == 273
    assert row["native_price_eur"] == row["own_price_eur"] == 232.05
    assert row["native_price_source"] == ps.SOURCE
    assert row["native_offer"]["reference"] == "427094"
    assert row["native_price_checked_at"] == NOW.isoformat()
    with store._connect() as con:
        assert con.execute("SELECT price FROM vytria_prices WHERE ean=?", (ean,)).fetchone()[0] == 273
