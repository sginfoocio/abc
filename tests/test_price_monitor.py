"""Phase 4: full self-hosted watch circuit, deterministic shops, never real HTTP."""
from datetime import date, datetime, timedelta, timezone
import json
from filelock import FileLock
import numpy as np
import pandas as pd
import pytest
import sqlite3

from price_comparator import PriceComparatorStore
from price_locator import LEASE
from price_monitor import PriceMonitor, SCHEDULE_ENV, _inventory_datetime
from price_watch import PriceWatch
from price_web import WebPriceSearch
from tests.test_price_locator import BASE, Crash, Site, _catalogue_500, _env, _gtin, _watch
from tests.web_fixtures import page

EAN = _gtin("841000000001")


@pytest.mark.parametrize("value", [
    "2026-11-03T18:00:00+00:00", "2026-11-03T19:00:00+01:00",
    "2026-11-03T18:00:00Z", "2026-11-03 18:00:00", "2026-11-03T18:00:00.000000",
    datetime(2026, 11, 3, 18), datetime(2026, 11, 3, 19, tzinfo=timezone(timedelta(hours=1))),
    pd.Timestamp("2026-11-03T19:00:00+01:00"), np.datetime64("2026-11-03T18:00:00"),
])
def test_inventory_datetime_normalizes_to_utc(value):
    assert _inventory_datetime(value) == datetime(2026, 11, 3, 18, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["2026-11-03", date(2026, 11, 3)])
def test_inventory_date_only_is_midnight_utc(value):
    assert _inventory_datetime(value) == datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [
    None, "", " ", "not-a-date", "2026-02-30", "NaT", pd.NaT, pd.NA, float("nan"),
    float("inf"), 1793730000, True, [], {"year": 2026},
])
def test_inventory_datetime_rejects_missing_or_invalid_values(value):
    with pytest.raises(ValueError, match="Fecha de vigencia"):
        _inventory_datetime(value)


def _monitor_env(tmp_path, *, count=1):
    site = Site()
    store, access, locator, clock, cid = _env(tmp_path, site)
    clock.now = datetime.now(timezone.utc)
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=(id=?)", (cid,))
        con.execute("UPDATE competitor_web_config SET tax_policy='included' WHERE competitor_id=?", (cid,))
    rows = []
    for i in range(count):
        ean = _gtin(f"841{i + 1:09d}")
        model = f"AL{1000 + i}"
        rows.append((ean, "Alpha", model, "93E", "50-18"))
        site.product("Alpha", model, "93E", caliber="58-20")
    _watch(store, rows)
    locator.run([cid], max_jobs=count)
    monitor = PriceMonitor(store, transport=site, clock=clock, sleep=lambda s: clock.advance(seconds=s))
    site.calls.clear()
    return store, access, locator, monitor, clock, cid, site


def _edit(site, monitor, *, price=120, stock=True, **kwargs):
    job = monitor.jobs().iloc[0]
    site.pages[job["url"]] = page(name="Alpha AL1000 93E 60-20", price=price, brand="Alpha", stock=stock, **kwargs)


def _advance(clock):
    clock.advance(hours=25)


def test_verified_watch_only_and_no_search_on_subsequent_updates(tmp_path):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    assert monitor.run([cid]).states == {"done": 1}
    assert len(site.calls) == 2  # robots + verified product, no listing/search
    job = monitor.jobs().iloc[0]
    assert job["eligible"] == 1 and job["last_offer_id"] is not None
    assert monitor.inventory().iloc[0]["comparable"]
    assert store.dashboard()[0].set_index("ean").loc[EAN, "competitor_count"] == 0  # no Vytria price yet
    site.calls.clear()
    assert monitor.run([cid]).processed == 0 and not site.calls
    _advance(clock)
    monitor.run([cid])
    assert len(site.calls) == 2 and not any("/marca/" in u for u in site.calls)
    assert len(store.offer_history(EAN)) == 1  # unchanged prices only refresh offer_checks
    PriceWatch(store).remove([EAN], actor="ana")
    site.calls.clear()
    assert monitor.run([cid]).processed == 0 and not site.calls


def test_price_stock_alerts_and_persistent_deduplication(tmp_path):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    store.import_own_prices(pd.DataFrame([{"EAN": EAN, "Mercado": "ES", "Precio": "115", "Moneda": "EUR",
                                          "Origen": "Vytria", "Fecha consulta": clock().isoformat()}]))
    monitor.run([cid])
    _edit(site, monitor, price=110)
    _advance(clock)
    store.import_own_prices(pd.DataFrame([{"EAN": EAN, "Mercado": "ES", "Precio": "115", "Moneda": "EUR",
                                          "Origen": "Vytria", "Fecha consulta": clock().isoformat()}]))
    monitor.run([cid])
    assert set(monitor.alerts()["kind"]) == {"price_down", "undercut", "stale"}
    _edit(site, monitor, price=130, stock=False)
    _advance(clock)
    monitor.run([cid])
    assert "availability" in set(monitor.alerts()["kind"])
    assert not monitor.inventory().iloc[0]["comparable"]
    assert monitor.inventory().iloc[0]["status"] == "Sin stock"
    assert store.current_offers().empty
    assert len(store.offer_history(EAN)) == 3
    # Restarting the process is not a new event.
    restored = PriceMonitor(PriceComparatorStore(store.database_path), transport=site, clock=clock)
    count = len(restored.alerts())
    assert restored.run([cid]).processed == 0 and len(restored.alerts()) == count
    assert "envío automático desactivado" in restored.email_summary()
    _edit(site, monitor, price=130)
    _advance(clock)
    monitor.run([cid])
    _edit(site, monitor, price=140)
    _advance(clock)
    monitor.run([cid])
    assert "price_up" in set(monitor.alerts()["kind"])


@pytest.mark.parametrize("changes", [
    {"name": "Alpha AL1000 99Z"}, {"name": "Alpha AL9999 93E"},
    {"gtin": "4006381333931"}, {"name": "Alpha AL1000"},
    {"extra_offer": {"@type": "Offer", "price": "99", "priceCurrency": "EUR"}},
])
def test_identity_contradictions_require_review_and_invalidate_old_offer(tmp_path, changes):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    url = monitor.jobs().iloc[0]["url"]
    args = {"name": "Alpha AL1000 93E", "brand": "Alpha", "price": 120, **changes}
    site.pages[url] = page(**args)
    _advance(clock)
    monitor.run([cid])
    assert monitor.jobs().iloc[0]["state"] == "review"
    assert store.current_offers().empty
    assert len(store.offer_history(EAN)) == 1
    before = len(site.calls)
    monitor.run([cid])
    assert len(site.calls) == before


@pytest.mark.parametrize("kwargs,tax", [
    ({"currency": "USD"}, "included"), ({"market": "IT"}, "included"),
    ({}, "unknown"), ({}, "excluded"), ({"price": None}, "included"),
])
def test_unconfirmed_commercial_data_never_enters_comparison(tmp_path, kwargs, tax):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    with store._connect() as con, con:
        con.execute("UPDATE competitor_web_config SET tax_policy=? WHERE competitor_id=?", (tax, cid))
    url = monitor.jobs().iloc[0]["url"]
    site.pages[url] = page(**{"name": "Alpha AL1000 93E", "brand": "Alpha", "price": 100, **kwargs})
    _advance(clock)
    monitor.run([cid])
    assert not monitor.inventory().iloc[0]["comparable"]
    assert store.current_offers().empty


@pytest.mark.parametrize("block", [403, 429, "captcha"])
def test_block_suspends_competitor_and_429_resumes(tmp_path, block):
    _, access, _, monitor, clock, cid, site = _monitor_env(tmp_path, count=3)
    monitor.enqueue([cid])
    url = monitor.jobs().sort_values("id").iloc[0]["url"]
    if block == "captcha":
        site.pages[url] = "<html>verify you are human captcha</html>"
    else:
        site.status[url] = block
    report = monitor.run([cid])
    assert report.processed == 1 and cid in report.stopped
    assert len(site.calls) == 2
    assert len(access.incidents(open_only=True)) == 1
    site.calls.clear()
    monitor.run([cid])
    assert not site.calls
    if block == 429:
        site.status.clear()
        clock.advance(minutes=3)
        assert monitor.run([cid]).states == {"done": 3}


@pytest.mark.parametrize("status", [404, 410])
def test_gone_preserves_history_and_enqueues_relocation(tmp_path, status):
    store, _, locator, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    url = monitor.jobs().iloc[0]["url"]
    site.status[url] = status
    _advance(clock)
    monitor.run([cid])
    assert monitor.jobs().iloc[0]["state"] == "gone"
    assert store.current_offers().empty and len(store.offer_history(EAN)) == 1
    assert locator.jobs().iloc[0]["kind"] == "relocate"
    assert list(monitor.alerts()["kind"]).count("gone") == 1
    monitor.run([cid])
    assert list(monitor.alerts()["kind"]).count("gone") == 1


def test_saved_receipt_recovers_without_repeating_http_or_alerts(tmp_path, monkeypatch):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    original = monitor._commit_sample

    def crash(*args):
        original(*args)
        # Simulate importer committed but final completion transaction not committed.
        with store._connect() as con, con:
            con.execute("UPDATE monitor_jobs SET state='running',lease_until=?", ((clock() + LEASE).isoformat(),))
        raise Crash()

    monkeypatch.setattr(monitor, "_commit_sample", crash)
    with pytest.raises(Crash):
        monitor.run([cid])
    assert monitor.jobs().iloc[0]["sample"]
    calls = len(site.calls)
    clock.advance(minutes=11)
    restored = PriceMonitor(PriceComparatorStore(store.database_path), transport=site, clock=clock)
    assert restored.run([cid]).states == {"done": 1}
    assert len(site.calls) == calls and len(store.offer_history(EAN)) == 1
    assert not restored.alerts().duplicated("event_key").any()


def test_budget_authorization_robots_and_shared_lock(tmp_path):
    _, access, _, monitor, clock, cid, site = _monitor_env(tmp_path, count=3)
    access.set_budget("competitor", cid, daily_limit=2, per_minute_limit=600, pause_seconds=0, actor="ana")
    # Location consumed an earlier budget; reset only the simulated audit window.
    clock.advance(days=2)
    report = monitor.run([cid])
    assert report.requests == 2 and len(site.calls) == 2 and cid in report.stopped
    site.calls.clear()
    monitor.run([cid])
    assert not site.calls
    clock.advance(days=2)
    access.set_method(cid, "ficha", "denied", actor="ana", note="No automatizar")
    assert monitor.run([cid]).requests == 0 and not site.calls
    access.set_method(cid, "ficha", "authorized", actor="ana", note="Acuerdo")
    with FileLock(f"{monitor.store.database_path}.competitor-{cid}.lock", timeout=0):
        assert cid in monitor.run([cid]).stopped
    site.robots = "User-agent: *\nDisallow: /gafas/\n"
    clock.advance(days=2)
    report = monitor.run([cid])
    assert report.requests == 1 and site.calls == [BASE + "/robots.txt"]


def test_frequency_group_override_stale_alert_and_schedule_switch(tmp_path, monkeypatch):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.configure(cid, enabled=True, interval_hours=48, actor="ana")
    monkeypatch.delenv(SCHEDULE_ENV, raising=False)
    assert monitor.tick()["runs"] == [] and not site.calls
    monitor.run([cid])
    clock.advance(hours=25)
    assert monitor.run([cid]).processed == 0
    monitor.watch.save_group("Rápido", 2)
    monitor.watch.move([EAN], "Rápido")
    assert monitor.run([cid]).processed == 1
    earlier_episodes = len(monitor.alerts().query("kind == 'stale'"))
    clock.advance(hours=5)
    assert monitor.check_stale() == 1
    assert monitor.check_stale() == 0
    assert monitor.inventory().iloc[0]["status"] == "Precio caducado"
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    assert monitor.tick()["runs"] == []  # daily competitor work also requires the own-price switch
    monitor.vytria.set_auto_enabled(True)
    monkeypatch.setattr(monitor.vytria, "run_watched_scheduled", lambda: None)
    assert len(monitor.tick()["runs"]) == 1
    assert monitor.tick()["runs"][0]["summary"].startswith("0 trabajos")
    assert len(monitor.alerts().query("kind == 'stale'")) == earlier_episodes + 1
    assert not store.competitors().empty


def test_full_500_ean_location_monitoring_changes_recovery_and_pilot_isolation(tmp_path):
    site = Site()
    other_site = Site(base="https://usd.example")
    store, access, locator, clock, cid = _env(tmp_path, site)
    clock.now = datetime.now(timezone.utc)
    other = store.competitors().set_index("name").loc["Tienda USD", "id"]
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=(id IN (?,?))", (cid, other))
    locator.search.ensure_config()
    with store._connect() as con, con:
        con.execute("UPDATE competitor_web_config SET tax_policy='included',pause_seconds=0")
    for method in ("ficha", "catalog"):
        access.set_method(other, method, "authorized", actor="ana", note="Simulación autorizada")
    access.set_budget("competitor", other, daily_limit=100000, per_minute_limit=600, pause_seconds=0, actor="ana")
    locator.save_source(other, "catalog", other_site.base + "/marca/{brand}", actor="ana")

    def transport(url, headers, timeout):
        return (other_site if url.startswith(other_site.base) else site)(url, headers, timeout)

    locator._transport = transport
    rows = _catalogue_500(site)
    _catalogue_500(other_site)
    _watch(store, rows)
    locator.enqueue()
    ids = [cid, other]
    assert locator.run(ids, max_jobs=1000).states == {"done": 1000}
    discovery_calls = site.count("/marca/") + other_site.count("/marca/")
    monitor = PriceMonitor(store, transport=transport, clock=clock, sleep=lambda s: clock.advance(seconds=s))
    assert monitor.run(ids, max_jobs=1000).states == {"done": 1000}
    assert len(store.current_offers()) > 0
    assert len(monitor.inventory()) == 1000
    assert monitor.run(ids, max_jobs=1000).processed == 0
    assert site.count("/marca/") + other_site.count("/marca/") == discovery_calls
    _advance(clock)
    urls = monitor.jobs().set_index(["competitor_id", "ean"])["url"].to_dict()
    for target, shop in [(cid, site), (other, other_site)]:
        for ean, brand, model, color, _ in rows:
            url = urls[target, ean]
            shop.pages[url] = page(name=f"{brand} {model} {color} 59-21", brand=brand, price=100, stock=True)
    site.crash_after = len(site.calls) + 71
    with pytest.raises(Crash):
        monitor.run(ids, max_jobs=1000)
    site.crash_after = None
    clock.advance(minutes=11)
    report = monitor.run(ids, max_jobs=1000)
    assert report.states["done"] > 900 and set(monitor.jobs()["state"]) == {"done"}
    assert site.count("/marca/") + other_site.count("/marca/") == discovery_calls
    assert len(monitor.inventory().query("comparable")) == 1000
    alerts = monitor.alerts(limit=2000)
    assert set(alerts["kind"]) == {"price_down", "availability", "stale"}
    assert not alerts.duplicated("event_key").any()
    assert monitor.run(ids, max_jobs=1000).processed == 0
    assert len(monitor.alerts(limit=2000)) == len(alerts)
    with store._connect() as con:
        for table in ("price_pilot", "pilot_runs", "pilot_jobs", "pilot_budgets"):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_migration_from_phase3_preserves_data_and_inventory_without_jobs(tmp_path):
    store, _, _, monitor, _, _, _ = _monitor_env(tmp_path)
    with store._connect() as con, con:
        for table in ("price_alerts", "monitor_events", "monitor_jobs", "monitor_schedule"):
            con.execute(f"DROP TABLE {table}")
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("products", "offers", "competitor_product_urls", "watch_products", "locate_jobs")}
    with store._connect() as con:
        with sqlite3.connect(tmp_path / "snapshot.sqlite3") as copy:
            con.backup(copy)
    restored = PriceComparatorStore(tmp_path / "snapshot.sqlite3")
    PriceComparatorStore(restored.database_path)  # second start is idempotent
    with restored._connect() as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        assert all(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == n for t, n in counts.items())
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='monitor_jobs'").fetchone()[0] == 0
    assert PriceWatch(restored).counts()["watched"] == 1
    assert WebPriceSearch(restored).configs().shape[0] > 0
    inventory = PriceMonitor(restored).inventory()
    assert set(inventory["status"]) == {"Ficha localizada"}
    assert not inventory["comparable"].any()


def test_expired_price_excluded_from_existing_dashboard_but_history_kept(tmp_path):
    store, _, _, monitor, _, cid, _ = _monitor_env(tmp_path)
    monitor.run([cid])
    assert len(store.current_offers()) == 1
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET valid_until='2020-01-01T00:00:00+00:00'")
    assert store.current_offers().empty
    assert store.dashboard()[1]["confirmed"] == 0
    assert len(store.offer_history(EAN)) == 1


def test_receipt_recovery_after_import_before_completion_and_duplicate_ean_url(tmp_path, monkeypatch):
    store, _, locator, monitor, clock, cid, site = _monitor_env(tmp_path)
    other = _gtin("841000000009")
    _watch(store, [(other, "Alpha", "AL1000", "93E", "62-22")])
    locator.run([cid])
    site.calls.clear()
    original = WebPriceSearch.record

    def record_then_crash(search, outcome):
        original(search, outcome)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(WebPriceSearch, "record", record_then_crash)
        with pytest.raises(Crash):
            monitor.run([cid])
    assert len(site.calls) == 2 and len(store.offer_history(EAN)) == 1
    clock.advance(minutes=11)
    assert monitor.run([cid]).states == {"done": 2}
    assert len(site.calls) == 2  # both EAN/calibres share one saved product receipt
    assert len(store.offer_history(EAN)) == len(store.offer_history(other)) == 1


@pytest.mark.parametrize("price,expected", [(114.01, False), (114, True)])
def test_exact_alert_threshold(tmp_path, price, expected):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    store.set_setting("alert_threshold_pct", "5")
    monitor.run([cid])
    _advance(clock)
    _edit(site, monitor, price=price)
    monitor.run([cid])
    assert ("price_down" in set(monitor.alerts()["kind"])) is expected


def test_missing_price_does_not_refresh_price_freshness(tmp_path):
    _, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    initial = monitor.jobs().iloc[0]["last_ok_at"]
    _advance(clock)
    _edit(site, monitor, price=None)
    monitor.run([cid])
    assert monitor.jobs().iloc[0]["last_ok_at"] == initial
    assert monitor.run([cid]).processed == 0
    assert monitor.inventory().iloc[0]["status"] == "Precio caducado"
    _advance(clock)
    assert monitor.check_stale() == 0  # the first missed daily update already persisted the alert
    assert monitor.inventory().iloc[0]["status"] == "Precio caducado"


def test_monitoring_ui_shows_prices_and_alerts_without_network(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    store, _, _, monitor, _, _, _ = _monitor_env(tmp_path)
    monitor.run()
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.delenv(SCHEDULE_ENV, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Opening the screen must not send requests")

    monkeypatch.setattr("requests.get", forbidden)
    app = AppTest.from_string("from app_pages.price_watch import render_admin\n"
                              "from price_comparator import PriceComparatorStore\n"
                              "render_admin(PriceComparatorStore(), {})").run(timeout=30)
    from tests.admin_navigation import watch_section
    for section in ("Precios y monitorización", "Alertas"):
        watch_section(app, section)
        assert not app.exception
    assert any("envío automático desactivado" in str(item.value) for item in app.info)
    watch_section(app, "Precios y monitorización")
    assert app.button(key="monitor_run").disabled
    app.button(key="monitor_enqueue").click().run(timeout=30)
    assert not app.exception
    assert any(SCHEDULE_ENV in str(item.value) for item in app.warning)


def test_inventory_mixed_five_completed_and_unprocessed_jobs_is_read_only(tmp_path, monkeypatch):
    store, _, _, monitor, _, cid, site = _monitor_env(tmp_path, count=6)
    assert monitor.run([cid], max_jobs=5).states == {"done": 5}
    assert len(site.calls) == 6  # robots + five simulated product pages
    with store._connect() as con:
        for job in con.execute("SELECT * FROM monitor_jobs WHERE state='done'"):
            for key in ("lease_until", "last_ok_at", "sampled_at", "not_before", "valid_until"):
                if job[key] is not None:
                    assert isinstance(job[key], str)
                    assert datetime.fromisoformat(job[key]).tzinfo is not None
            assert isinstance(json.loads(job["sample"])["at"], str)
        before = "\n".join(con.iterdump())

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering results must not query competitors or enqueue jobs")

    monkeypatch.setattr(monitor, "run", forbidden)
    monkeypatch.setattr(monitor, "enqueue", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    site.calls.clear()
    for _ in range(2):
        frame = monitor.inventory()
        assert len(frame) == 6
        assert len(frame.query("comparable")) == 5
        assert list(frame["status"]).count("Pendiente") == 1
        assert not frame["date_issue"].any()
    assert not site.calls
    with store._connect() as con:
        assert "\n".join(con.iterdump()) == before
        assert con.execute("SELECT COUNT(*) FROM offers WHERE method='web:monitor'").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM monitor_jobs").fetchone()[0] == 6


@pytest.mark.parametrize("value,expected", [
    (None, "Sin fecha de vigencia"), ("", "Sin fecha de vigencia"), ("  ", "Sin fecha de vigencia"),
    ("invalid", "Fecha de vigencia inválida"), (123, "Fecha de vigencia inválida"),
])
def test_inventory_missing_or_corrupt_validity_is_visible_and_not_comparable(tmp_path, value, expected, caplog):
    store, _, _, monitor, _, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET valid_until=?", (value,))
        before = "\n".join(con.iterdump())
    site.calls.clear()
    frame = monitor.inventory()
    assert frame.iloc[0]["status"] == expected
    assert frame.iloc[0]["date_issue"] == expected
    assert not frame["comparable"].any()
    assert len(store.offer_history(EAN)) == 1
    if "inválida" in expected:
        assert "Invalid monitor valid_until" in caplog.text
    assert not site.calls
    with store._connect() as con:
        assert "\n".join(con.iterdump()) == before


@pytest.mark.parametrize("offset,kind,expired", [
    (-1, "naive", True), (0, "naive", False), (1, "naive", False),
    (-1, "aware", True), (0, "aware", False), (1, "aware", False),
    (-1, "timestamp", True), (1, "timestamp", False),
    (-1, "numpy", True), (1, "numpy", False),
])
def test_inventory_validity_types_and_expiry_boundary(tmp_path, monkeypatch, offset, kind, expired):
    store, _, _, monitor, clock, cid, _ = _monitor_env(tmp_path)
    monitor.run([cid])
    at = clock() + timedelta(seconds=offset)
    value = (at.replace(tzinfo=None) if kind == "naive" else
             at.astimezone(timezone(timedelta(hours=2))) if kind == "aware" else
             pd.Timestamp(at) if kind == "timestamp" else np.datetime64(at.replace(tzinfo=None)))
    read_sql = pd.read_sql_query

    def typed_read(sql, *args, **kwargs):
        frame = read_sql(sql, *args, **kwargs)
        if "j.last_ok_at,j.valid_until,j.eligible" in sql:
            frame["valid_until"] = pd.Series([value], dtype=object)
        return frame

    monkeypatch.setattr(pd, "read_sql_query", typed_read)
    # Legacy naive clocks are interpreted as UTC, not the machine's local zone.
    monkeypatch.setattr(monitor, "_now", lambda: clock().replace(tzinfo=None))
    result = monitor.inventory().iloc[0]
    assert bool(result["comparable"]) is not expired
    assert result["status"] == ("Precio caducado" if expired else "Actualizado")


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, pd.NaT, np.datetime64("NaT")])
def test_inventory_nullable_pandas_scalars(tmp_path, monkeypatch, value):
    _, _, _, monitor, _, cid, _ = _monitor_env(tmp_path)
    monitor.run([cid])
    read_sql = pd.read_sql_query

    def nullable_read(sql, *args, **kwargs):
        frame = read_sql(sql, *args, **kwargs)
        if "j.last_ok_at,j.valid_until,j.eligible" in sql:
            frame["valid_until"] = pd.Series([value], dtype=object)
        return frame

    monkeypatch.setattr(pd, "read_sql_query", nullable_read)
    row = monitor.inventory().iloc[0]
    assert row["status"] == row["date_issue"] == "Sin fecha de vigencia"
    assert not row["comparable"]


def test_monitoring_ui_mixed_dates_and_invalid_validity_without_writes(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    store, _, _, monitor, _, cid, site = _monitor_env(tmp_path, count=6)
    monitor.run([cid], max_jobs=5)
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET valid_until='invalid' WHERE id=(SELECT MIN(id) FROM monitor_jobs)")
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not send HTTP or prepare jobs")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr(PriceMonitor, "run", forbidden)
    monkeypatch.setattr(PriceMonitor, "enqueue", forbidden)
    site.calls.clear()
    app = AppTest.from_string("from app_pages.price_watch import render_admin\n"
                             "from price_comparator import PriceComparatorStore\n"
                             "render_admin(PriceComparatorStore(), {})").run(timeout=30)
    # The existing admin entry point seeds default configuration on its first opening.
    with store._connect() as con:
        before = "\n".join(con.iterdump())
    from tests.admin_navigation import watch_section
    watch_section(app, "Precios y monitorización")
    assert not app.exception
    app.selectbox(key="monitor_competitor").set_value(cid).run(timeout=30)
    assert any("1 registros sin fecha de vigencia válida" in str(item.value) for item in app.warning)
    assert not site.calls
    with store._connect() as con:
        assert "\n".join(con.iterdump()) == before


def test_failed_job_can_be_reopened_explicitly_and_audited(tmp_path):
    store, _, _, monitor, _, cid, site = _monitor_env(tmp_path)
    monitor.enqueue([cid])
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET attempts=5")
    assert monitor.run([cid]).states == {"failed": 1}
    assert not site.calls
    with pytest.raises(ValueError):
        monitor.retry_failed(cid, actor="")
    assert monitor.retry_failed(cid, actor="ana") == 1
    assert monitor.run([cid]).states == {"done": 1}
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM monitor_events WHERE detail LIKE '%ana%'").fetchone()[0] == 1
