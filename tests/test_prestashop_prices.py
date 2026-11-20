import json
from datetime import datetime, timedelta, timezone

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
        payload = quote(price=price[0], observed_at=current_time[0].isoformat())
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
        assert "OperationalError" in current["issue"] and "private" not in current["issue"]
        assert con.execute("SELECT price FROM vytria_prices WHERE ean=?", (EAN,)).fetchone()[0] == 273
