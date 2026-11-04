"""Daily watched-price circuit, entirely simulated (no external services)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from filelock import FileLock
import pandas as pd
import pytest

from price_comparator import PriceComparatorStore, vytria_price_states
from price_monitor import PriceMonitor, SCHEDULE_ENV
from tests.test_price_locator import Crash, Site, _env, _gtin, _watch
from tests.test_vytria_price_sync import FlakyReader, OdooFixture
from tests.web_fixtures import page
from vytria_price_sync import SqlOdooPriceReader, VytriaPriceSync

NAMES = ("Visio Factory", "Edel-Optics", "Amevista", "Óptica Óptima", "Domanci", "Óptica Bassol")


def _daily_env(tmp_path, count=2):
    store, access, locator, clock, _ = _env(tmp_path, Site())
    clock.now = datetime.now(timezone.utc)
    all_competitors = store.competitors().set_index("name")
    ids = [all_competitors.loc[name, "id"] for name in NAMES]
    shops = {cid: Site(base=f"https://shop-{i}.example") for i, cid in enumerate(ids)}
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=0")
        for cid, shop in shops.items():
            con.execute("UPDATE competitors SET active=1,url=?,domain=?,market='ES',currency='EUR' WHERE id=?",
                        (shop.base + "/", shop.base.removeprefix("https://"), cid))
    locator.search.ensure_config()
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_web_config SET tax_policy='included',pause_seconds=0,
            sales_market='ES',market_verified=1""")
    for cid, shop in shops.items():
        for method in ("ficha", "catalog"):
            access.set_method(cid, method, "authorized", actor="test", note="Simulación, no autorización real")
        access.set_budget("competitor", cid, daily_limit=1000, per_minute_limit=600, pause_seconds=0, actor="test")
        locator.save_source(cid, "catalog", shop.base + "/marca/{brand}", actor="test")

    events = []

    def transport(url, headers, timeout):
        events.append("competitor")
        for shop in shops.values():
            if url.startswith(shop.base + "/"):
                return shop(url, headers, timeout)
        raise AssertionError(f"Unsimulated URL: {url}")

    locator._transport = transport
    odoo = OdooFixture(tmp_path / "odoo.sqlite3")
    rows = []
    for i in range(count):
        ean, model, color = _gtin(f"842{i + 1:09d}"), f"AL{1000 + i // 5}", f"{i % 5 + 1:03d}"
        rows.append((ean, "Alpha", model, color, "50-18"))
        for shop in shops.values():
            shop.product("Alpha", model, color, caliber="58-20")
        odoo.product(ean, 200, 140, maps=(odoo.map_rule("Vytria (30%)", 30),))
    _watch(store, rows)

    class Reader(SqlOdooPriceReader):
        def fetch(self, eans):
            events.append(("vytria", list(eans)))
            return super().fetch(eans)

    reader = Reader(odoo.engine_factory)
    sync = VytriaPriceSync(store, reader, clock=clock, sleep=lambda _: None)
    monitor = PriceMonitor(store, transport=transport, clock=clock,
                           sleep=lambda s: clock.advance(seconds=s), vytria_sync=sync)
    for cid in ids:
        monitor.configure(cid, enabled=True, max_jobs=100, actor="test")
    return store, access, locator, monitor, sync, clock, ids, shops, odoo, rows, events


def _counts(store):
    with store._connect() as con:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                ("offers", "monitor_jobs", "vytria_prices", "vytria_price_history", "price_alerts",
                 "vytria_price_alerts", "access_requests")}


def test_daily_50_by_six_freshness_order_history_recovery_and_deduplication(tmp_path, monkeypatch):
    store, _, locator, monitor, sync, clock, ids, shops, odoo, rows, events = _daily_env(tmp_path, 50)
    eans = [r[0] for r in rows]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM locate_jobs").fetchone()[0] == 300
    assert locator.run(ids, max_jobs=300).states == {"done": 300}
    discovery_calls = sum(shop.count("/marca/") for shop in shops.values())
    assert monitor.tick()["runs"] == []  # server switch is off
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    assert monitor.tick()["runs"] == []  # Vytria switch is also required
    sync.set_auto_enabled(True)
    events.clear()
    first = monitor.tick()
    assert first["vytria"]["monitored"] == 50
    assert len(first["runs"]) == 6
    assert events[0] == ("vytria", [r[0] for r in rows])
    assert monitor.jobs()["state"].eq("done").sum() == 300
    assert monitor.inventory()["own_price_eur"].eq(140).all()
    assert monitor.inventory()["own_price_source"].eq("Odoo tarifa 59").all()
    summary, _ = store.dashboard(now=clock())
    assert summary.loc[summary["ean"].isin(eans), "own_price_eur"].eq(140).all()
    before = _counts(store)
    events.clear()
    restored_sync = VytriaPriceSync(PriceComparatorStore(store.database_path), sync.reader,
                                    clock=clock, sleep=lambda _: None)
    monitor.vytria = restored_sync
    assert monitor.tick()["vytria"] is None
    assert not events and _counts(store) == before

    clock.advance(hours=25)
    assert not restored_sync.prices()["usable"].any()
    assert store.dashboard(now=clock())[0]["own_price_eur"].isna().all()
    odoo.execute("UPDATE product_template SET list_price=180")
    odoo.execute("UPDATE prestashop_pricelist_item_pricelist SET price=126")
    for shop in shops.values():
        for ean, brand, model, color, _ in rows:
            url = next(url for url in shop.pages if f"{model.lower()}-{color}-" in url)
            shop.pages[url] = page(name=f"{brand} {model} {color} 58-20", price=100, brand=brand)
    original = monitor._commit_sample
    crashed = False

    def crash(job, *args):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise Crash()
        return original(job, *args)

    monkeypatch.setattr(monitor, "_commit_sample", crash)
    with pytest.raises(Crash):
        monitor.tick()
    prices = restored_sync.prices()
    assert prices.loc[prices["ean"].isin(eans), "price"].eq(126).all()
    reads = sum(isinstance(e, tuple) and e[0] == "vytria" for e in events)
    clock.advance(minutes=11)
    result = monitor.tick()
    assert result["vytria"] is None
    assert sum(isinstance(e, tuple) and e[0] == "vytria" for e in events) == reads
    assert monitor.jobs()["state"].eq("done").sum() == 300
    assert sum(shop.count("/marca/") for shop in shops.values()) == discovery_calls
    assert len(restored_sync.price_history(rows[0][0])) == 2
    alerts = monitor.alerts(limit=2000)
    assert len(alerts.query("competitor == 'Vytria'")) == 50
    assert not alerts.duplicated("event_key").any()
    stable = _counts(store)
    events.clear()
    monitor.tick()
    assert not events and _counts(store) == stable
    with store._connect() as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        for table in ("price_pilot", "pilot_runs", "pilot_jobs", "pilot_budgets"):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_sync_only_watched_manual_missing_inconsistent_and_error_keep_valid_price(tmp_path):
    store, _, _, monitor, sync, clock, _, _, odoo, rows, _ = _daily_env(tmp_path)
    extra = _gtin("842999999999")
    store.import_products(pd.DataFrame([{"MARCA": "Alpha", "NOMBRE": "No vigilado",
                                        "MODELO": "OTHER", "COLOR": "001", "EAN": extra}]))
    assert sync.run(watched_only=True).monitored == 2
    ean = rows[0][0]
    initial = sync.prices().set_index("ean").loc[ean]
    odoo.set_price(odoo.catalog_id(ean), 999)
    result = sync.run(watched_only=True)
    assert result.status == "partial" and result.issues == 1
    row = sync.prices().set_index("ean").loc[ean]
    assert row["price"] == initial["price"] and row["synced_at"] == initial["synced_at"]
    assert row["current_status"] == "stale" and not row["usable"]
    assert len(sync.price_history(ean)) == 1
    odoo.set_price(odoo.catalog_id(ean), None)
    assert sync.run(watched_only=True).issues == 1
    assert sync.prices().set_index("ean").loc[ean, "price"] == 140
    failing = VytriaPriceSync(store, FlakyReader(sync.reader, 99), clock=clock, sleep=lambda _: None)
    assert failing.run(watched_only=True).status == "error"
    assert not failing.prices().query("ean != @extra")["usable"].any()
    assert store.dashboard(now=clock())[0].query("ean == @ean")["own_price_eur"].isna().all()
    assert not monitor.alerts()["competitor"].eq("Vytria").any()


def test_daily_retry_cooldown_and_new_watched_products(tmp_path):
    store, _, _, _, sync, clock, _, _, odoo, rows, events = _daily_env(tmp_path, 2)
    sync.set_auto_enabled(True)
    odoo.set_price(odoo.catalog_id(rows[0][0]), None)
    assert sync.run_watched_scheduled().status == "partial"
    reads = len(events)
    assert sync.run_watched_scheduled() is None and len(events) == reads
    clock.advance(minutes=31)
    odoo.set_price(odoo.catalog_id(rows[0][0]), 140)
    assert sync.run_watched_scheduled().status == "ok"
    assert sync.run_watched_scheduled() is None
    new = (_gtin("842999999998"), "Alpha", "NEW", "001", "50-18")
    _watch(store, [new])
    odoo.product(new[0], 200, 140, maps=(odoo.map_rule("Vytria (30%)", 30),))
    assert sync.run_watched_scheduled().monitored == 3


def test_failed_daily_sync_keeps_competitor_updates_but_never_uses_stale_own_price(tmp_path, monkeypatch):
    store, _, locator, monitor, sync, clock, ids, _, _, _, _ = _daily_env(tmp_path, 1)
    locator.run(ids, max_jobs=6)
    sync.set_auto_enabled(True)
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    monitor.tick()
    initial = _counts(store)
    clock.advance(hours=25)
    monitor.vytria = VytriaPriceSync(store, FlakyReader(sync.reader, 99), clock=clock, sleep=lambda _: None)
    result = monitor.tick()
    assert result["vytria"]["status"] == "error"
    assert monitor.jobs()["state"].eq("done").sum() == 6
    inventory = monitor.inventory()
    assert inventory["own_price_eur"].isna().all()
    assert inventory["own_price_status"].eq("stale").all()
    assert inventory["difference_eur"].isna().all()
    assert sync.prices().iloc[0]["price"] == 140
    assert _counts(store)["vytria_price_history"] == initial["vytria_price_history"]
    alerts = monitor.alerts()
    assert len(alerts.query("kind == 'undercut'")) == 6  # no new comparison against stale Vytria
    assert set(alerts["kind"]) == {"undercut", "stale"}
    assert len(store.current_offers()) == 6


@pytest.mark.parametrize("block", [403, 429, "captcha", "denied", "budget", 410])
def test_daily_governance_is_not_bypassed(tmp_path, monkeypatch, block):
    store, access, locator, monitor, sync, _, ids, shops, _, _, events = _daily_env(tmp_path, 1)
    assert locator.run(ids, max_jobs=6).states == {"done": 6}
    sync.set_auto_enabled(True)
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    monitor.enqueue(ids)
    cid = ids[0]
    url = monitor.jobs().query("competitor_id == @cid").iloc[0]["url"]
    shop = shops[cid]
    shop.calls.clear()
    if block == "captcha":
        shop.pages[url] = "<html>verify you are human captcha</html>"
    elif block == "denied":
        access.set_method(cid, "ficha", "denied", actor="test", note="No automatizar")
    elif block == "budget":
        access.set_budget("competitor", cid, daily_limit=1, per_minute_limit=600, pause_seconds=0, actor="test")
    else:
        shop.status[url] = block
    events.clear()
    monitor.tick()
    assert events[0][0] == "vytria"
    first = len(shop.calls)
    monitor.tick()
    assert len(shop.calls) == first
    if block in ("denied", "budget"):
        assert first == 0
    if block == 410:
        assert monitor.jobs().query("competitor_id == @cid").iloc[0]["state"] == "gone"
        assert locator.jobs().query("competitor_id == @cid").iloc[0]["kind"] == "relocate"


def test_daily_sync_lock_prevents_competitor_work(tmp_path, monkeypatch):
    _, _, _, monitor, sync, _, _, _, _, _, events = _daily_env(tmp_path, 1)
    sync.set_auto_enabled(True)
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    events.clear()
    with FileLock(sync._lock_path, timeout=0):
        assert monitor.tick()["runs"] == []
    assert not events


def test_legacy_two_interval_validity_is_capped_on_read_without_rewriting_history(tmp_path):
    store, _, locator, monitor, _, clock, ids, _, _, _, _ = _daily_env(tmp_path, 1)
    locator.run(ids, max_jobs=6)
    monitor.run(ids)
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET last_ok_at=?,valid_until=?",
                    ((clock() - timedelta(hours=25)).isoformat(), (clock() + timedelta(hours=23)).isoformat()))
        before = "\n".join(con.iterdump())
    frame = monitor.inventory()
    assert frame["status"].eq("Precio caducado").all()
    assert not frame["comparable"].any()
    assert store.current_offers().empty
    assert store.dashboard(now=clock())[0]["competitor_count"].eq(0).all()
    with store._connect() as con:
        assert "\n".join(con.iterdump()) == before


def test_own_sync_interruption_after_apply_recovers_without_duplicate_history(tmp_path, monkeypatch):
    store, _, _, _, sync, _, _, _, _, _, _ = _daily_env(tmp_path, 1)
    sync.set_auto_enabled(True)
    original = sync._apply

    def apply_then_crash(*args):
        original(*args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_apply", apply_then_crash)
        with pytest.raises(Crash):
            sync.run_watched_scheduled()
    assert sync.last_run()["status"] == "running"
    assert sync.run_watched_scheduled().status == "ok"
    assert _counts(store)["vytria_price_history"] == 1
    with store._connect() as con:
        assert [r[0] for r in con.execute("SELECT status FROM vytria_sync_runs ORDER BY id")] == ["error", "ok"]


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "-1", "0"])
def test_nonfinite_or_nonpositive_source_never_replaces_valid_price(tmp_path, price):
    _, _, _, _, sync, _, _, _, _, rows, _ = _daily_env(tmp_path, 1)
    sync.run(watched_only=True)
    product = sync.reader.fetch([rows[0][0]])[0]
    bad = replace(product, prices=(replace(product.prices[0], price=Decimal(price)),))
    sync.reader.fetch = lambda eans: [bad]
    assert sync.run(watched_only=True).issues == 1
    assert sync.prices().iloc[0]["price"] == 140
    assert not sync.prices().iloc[0]["usable"]


@pytest.mark.parametrize("age,expected", [(23.99, True), (24, False), (25, False), (-1, False)])
def test_shared_daily_freshness_rule(age, expected):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame([{"ean": "x", "synced_at": (now - timedelta(hours=age)).isoformat(),
                           "stale_since": None, "validation_status": "validated", "comparable": 1}])
    assert bool(vytria_price_states(frame, 7, {"x"}, now=now).iloc[0]["usable"]) is expected
    if age >= 0:
        assert vytria_price_states(frame, 7, set(), now=now).iloc[0]["usable"]


def test_additive_migration_keeps_existing_database_and_history(tmp_path):
    store, _, _, _, sync, _, _, _, _, _, _ = _daily_env(tmp_path, 1)
    sync.run(watched_only=True)
    with store._connect() as con, con:
        con.execute("DROP TABLE vytria_price_alerts")
    with store._connect() as con:
        with sqlite3.connect(tmp_path / "before.sqlite3") as copy:
            con.backup(copy)
    reopened = PriceComparatorStore(tmp_path / "before.sqlite3")
    assert _counts(reopened)["vytria_price_history"] == 1
    with reopened._connect() as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='vytria_price_alerts'").fetchone()[0] == 0


def test_daily_ui_manual_own_refresh_requires_confirmation_and_does_not_query_shops(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    import app_pages.price_watch as ui

    store, _, _, _, sync, _, _, _, _, _, events = _daily_env(tmp_path, 1)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(ui, "VytriaPriceSync", lambda store: sync)

    def forbidden(*args, **kwargs):
        raise AssertionError("The own-price button must not send HTTP or run competitor jobs")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr(PriceMonitor, "run", forbidden)
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_monitor\n"
        "from price_comparator import PriceComparatorStore\n"
        "s=PriceComparatorStore()\n"
        "_render_monitor(s, dict(zip(s.competitors()['id'], s.competitors()['name'])))"
    ).run(timeout=30)
    assert not app.exception
    assert app.button(key="watch_vytria_sync").disabled
    assert not app.toggle(key="watch_vytria_daily").value
    app.checkbox(key="watch_vytria_approved").check().run(timeout=30)
    events.clear()
    app.button(key="watch_vytria_sync").click().run(timeout=30)
    assert not app.exception
    assert len(events) == 1 and events[0][0] == "vytria"
    assert _counts(store)["vytria_prices"] == 1 and _counts(store)["monitor_jobs"] == 0
    assert not sync.auto_enabled
