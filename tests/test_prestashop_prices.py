import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
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


def rule(sid=5, aid=0, rule_id=0, shop=0, currency=0, country=0, group=0, quantity=1,
         fixed="-1", discount="0.15", kind="percentage", included=0, start=OPEN, end=OPEN):
    return (sid, aid, rule_id, shop, 0, currency, country, group, quantity,
            Decimal(fixed), Decimal(discount), kind, included, start, end)


def calculate(rules, priority=PRIORITY, taxes=((0, "0", "0", Decimal(21), 1, 0),), *, combination=90, active=1):
    return ps._calculate((Decimal("100"), 0, 63, active, 1, 1), (Decimal("10"), 0, 1),
                         combination, taxes, rules, priority, CONTEXT, NOW, ZoneInfo("Europe/Madrid"))


def test_inactive_product_keeps_the_same_offer_and_tax():
    rules = [rule(aid=90, fixed="80")]
    assert calculate(rules, active=0) == calculate(rules, active=1)


@pytest.mark.parametrize("siblings,issues,reason", [
    ({}, {}, None),
    ({"91": "232.06"}, {}, "precios distintos"),
    ({}, {"91": "IVA no verificable"}, "No se pudo descartar"),
])
def test_price_reference_is_traceable_and_does_not_guess_variant_price(siblings, issues, reason):
    ean = "8056262600078"
    link = ps.PRICE_REFERENCES[ean]
    original, pid, aid, *_ = link
    raw = quote(product_id=pid, combination_id=aid, other_calibres=siblings, other_calibre_issues=issues)
    if reason:
        with pytest.raises(ps.QuoteError, match=reason):
            ps._annotate_reference(raw, ean, link)
        return
    ps._annotate_reference(raw, ean, link)
    stored, _, _ = ps.resolve([ean], [(ean, pid, aid, 0)], [(ean, json.dumps(raw))], CONTEXT, NOW)[ean]
    snapshot = json.loads(stored)
    assert snapshot["price_reference"]["kind"] == "marca/modelo/color"
    assert snapshot["price_reference"]["requested_ean"] == ean
    assert snapshot["price_reference"]["original_barcode"] == original
    assert snapshot["combination_id"] == aid
    assert snapshot["price"] == "232.05" and "in_stock" not in snapshot


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

    def mysql(eans, context):
        calls.append(set(eans))
        if fail[0]:
            raise OperationalError("private connection details")
        payload = quote(price=price[0], observed_at=current_time[0].isoformat(), active=False,
                        in_stock=len(calls) == 1)
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
    assert pd.isna(comparison()["own_price_eur"])
    assert comparison()["own_price_status"] == "stale"
    with store._connect() as con:
        current = con.execute("SELECT * FROM prestashop_prices WHERE ean=?", (EAN,)).fetchone()
        history = con.execute("SELECT payload FROM prestashop_price_history ORDER BY id").fetchall()
        assert [json.loads(row[0])["price"] for row in history] == ["232.05", "230.00"]
        assert current["synced_at"] == (current_time[0] - timedelta(minutes=1)).isoformat()
        assert json.loads(current["payload"])["price"] == "230.00"
        assert json.loads(current["payload"])["active"] is False
        assert json.loads(current["payload"])["in_stock"] is False
        assert all("in_stock" not in json.loads(row[0]) for row in history)
        assert "OperationalError" in current["issue"] and "private" not in current["issue"]
        assert con.execute("SELECT price FROM vytria_prices WHERE ean=?", (EAN,)).fetchone()[0] == 273
