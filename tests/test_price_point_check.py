"""Supervised one-pass regressions, using synthetic shops only."""
from datetime import timedelta
import json

import pytest

from price_batch import WatchBatch
from price_locator import MAX_ATTEMPTS, RunReport
from price_monitor import MonitorReport, PriceMonitor
from price_pilot import PilotError, SAMPLE_EANS
from price_watch import PriceWatch
from tests.test_price_admin import _dump
from tests.test_price_locator import Site, _env, _gtin, _watch
from tests.test_price_monitor import _monitor_env
from tests.test_price_pilot import pilot


def test_preview_does_not_advertise_deferred_negative_as_ready(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site)
    ean = _gtin("841000000001")
    site.brands["alpha"] = []
    _watch(store, [(ean, "Alpha", "AL1000", "93E", "")])
    assert locator.run([cid]).states["not_found"] == 1
    batch = WatchBatch(store, locator=locator, monitor=PriceMonitor(store, clock=clock))
    site.calls.clear()
    row = batch.preview([ean], [cid]).iloc[0]
    assert not row["ready"]
    assert "not_before" in row["defer_reason"]
    assert not site.calls


def test_point_check_retries_selected_negative_once_without_erasing_history(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site)
    eans = [_gtin(f"841{i + 1:09d}") for i in range(2)]
    site.brands["alpha"] = []
    _watch(store, [(ean, "Alpha", f"AL{1000 + i}", "93E", "") for i, ean in enumerate(eans)])
    assert locator.run([cid]).processed == 2
    before = locator.jobs().set_index("ean").loc[eans[1]].to_dict()
    with store._connect() as con:
        history_before = con.execute("SELECT COUNT(*) FROM locate_events").fetchone()[0]
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    site.calls.clear()
    snapshot = _dump(store)
    assert batch.preview(eans[:1], [cid], point_check=True)["ready"].all()
    assert _dump(store) == snapshot and not site.calls
    result = batch.execute(eans[:1], [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["processed_pairs"] == 1 and result["deferred"] == 0
    assert result["runs"][0]["location_processed"] == 1
    assert result["pairs"][0]["category"] == "checked_now"
    assert result["pairs"][0]["outcomes"][0]["state"] == "not_found"
    assert site.count("/marca/") == 1
    assert locator.jobs().set_index("ean").loc[eans[1]].to_dict() == before
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM locate_events").fetchone()[0] > history_before


def test_current_verified_price_is_reused_by_default_and_read_now_only_when_explicit(tmp_path):
    store, _, locator, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    ean = monitor.inventory().iloc[0]["ean"]
    with store._connect() as con:
        before = con.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        schedule = [tuple(r) for r in con.execute("SELECT * FROM monitor_schedule")]
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    site.calls.clear()
    ordinary = batch.execute([ean], [cid], approved=True, sync_vytria=False)
    assert ordinary["pairs"][0]["category"] == "reused" and not site.calls
    clock.advance(minutes=1)
    assert batch.preview([ean], [cid], point_check=True)["ready"].all()
    point = batch.execute([ean], [cid], approved=True, sync_vytria=False, point_check=True)
    assert point["processed_pairs"] == 1
    assert point["runs"][0]["monitor_processed"] == 1
    assert point["pairs"][0]["category"] == "checked_now"
    assert len(site.calls) == 2 and not site.count("/marca/")
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == before
        assert [tuple(r) for r in con.execute("SELECT * FROM monitor_schedule")] == schedule


@pytest.mark.parametrize("kind", ["review", "attempts", "lease", "delay", "permission", "budget", "block", "lock"])
def test_point_check_never_overrides_controls(tmp_path, kind):
    store, access, locator, monitor, clock, cid, site = _monitor_env(tmp_path)
    ean = monitor.inventory().iloc[0]["ean"]
    monitor.enqueue([cid], [ean])
    with store._connect() as con, con:
        if kind == "review":
            con.execute("UPDATE monitor_jobs SET state='review'")
        elif kind == "attempts":
            con.execute("UPDATE monitor_jobs SET attempts=?", (MAX_ATTEMPTS,))
        elif kind == "lease":
            con.execute("UPDATE monitor_jobs SET state='running',lease_until=?",
                        ((clock() + timedelta(minutes=5)).isoformat(),))
        elif kind == "delay":
            con.execute("UPDATE monitor_jobs SET state='waiting',not_before=?",
                        ((clock() + timedelta(minutes=5)).isoformat(),))
    if kind == "permission":
        access.set_method(cid, "ficha", "denied", actor="test", note="Revoked")
    elif kind == "budget":
        access.set_budget("competitor", cid, daily_limit=0, per_minute_limit=600, pause_seconds=0, actor="test")
    elif kind == "block":
        access.report_block("competitor", cid, "captcha", method="ficha", url=site.base, detail="Fixture")
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    site.calls.clear()
    if kind != "lock":
        row = batch.preview([ean], [cid], point_check=True).iloc[0]
        assert not row["ready"] and row["defer_reason"]
        result = batch.execute([ean], [cid], approved=True, sync_vytria=False, point_check=True)
    else:
        with locator.search._lock(cid):
            result = batch.execute([ean], [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["processed_pairs"] == 0 and not site.calls
    assert result["pairs"][0]["category"] == "not_executed"


def test_legacy_pilot_handoff_is_explicit_locked_audited_and_uses_authorized_catalog(pilot):
    service, store, _, _, cid, _ = pilot
    service.enable()
    site = Site(base="https://www.visiofactory.es")
    site.brands["prada"] = []
    ean = SAMPLE_EANS[0]
    from tests.test_price_locator import Clock
    from price_access import AccessControl
    from price_locator import PriceLocator

    clock = Clock()
    access = AccessControl(store, clock=clock)
    access.ensure_defaults()
    for method in ("catalog", "ficha"):
        access.set_method(cid, method, "authorized", actor="test", note="Synthetic catalog authorized")
    locator = PriceLocator(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    locator.save_source(cid, "catalog", site.base + "/marca/{brand}", actor="test", note="Synthetic")
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    PriceWatch(store).add([ean], actor="test", source="manual")
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    assert not batch.preview([ean], [cid])["ready"].any()
    assert batch.preview([ean], [cid], point_check=True)["pilot_route"].all()
    scope_before = service.store.database_path
    with service.lock(), pytest.raises(PilotError, match="reserva"):
        with service.watch_check([(cid, ean)], approved=True):
            pass
    result = batch.execute([ean], [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["processed_pairs"] == 1 and result["pairs"][0]["category"] == "checked_now"
    assert result["runs"][0]["pilot_run_id"]
    assert site.count("/marca/prada") == 1
    assert not any("busqueda" in u or "search_query" in u for u in site.calls)
    with store._connect() as con:
        run = con.execute("SELECT * FROM pilot_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert run["operation"] == "watch_check" and run["status"] == "done"
        assert json.loads(run["selection"]) == [[cid, ean]]
        assert con.execute("SELECT outcome FROM pilot_jobs WHERE run_id=?", (run["id"],)).fetchone()[0] == "checked_now"
        assert con.execute("SELECT requests FROM pilot_budgets WHERE run_id=?", (run["id"],)).fetchone()[0] == len(site.calls)
        assert con.execute("SELECT enabled FROM price_pilot").fetchone()[0] == 1
    assert store.database_path == scope_before


def test_active_legacy_pilot_is_reported_without_starting_another(pilot):
    service, store, _, _, cid, _ = pilot
    service.enable()
    with store._connect() as con, con:
        con.execute("""INSERT INTO pilot_runs(started_at,status,scope,competitors)
            VALUES('2026-11-03','interrupted','[]','{}')""")
    with pytest.raises(PilotError, match="pendiente"):
        with service.watch_check([(cid, SAMPLE_EANS[0])], approved=True):
            pass
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM pilot_runs").fetchone()[0] == 1


def test_robot_only_attempt_does_not_count_as_commercial_check(tmp_path):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    ean = monitor.inventory().iloc[0]["ean"]
    site.robots = "User-agent: *\nDisallow: /gafas/\n"
    result = WatchBatch(store, locator=locator, monitor=monitor).execute(
        [ean], [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["runs"][0]["requests"] == 1
    assert result["processed_pairs"] == 1
    assert result["pairs"][0]["category"] == "not_executed"
    assert site.calls == [site.base + "/robots.txt"]


def test_point_check_does_not_clear_pending_review_during_prepare(tmp_path):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    ean = monitor.inventory().iloc[0]["ean"]
    monitor.enqueue([cid], [ean])
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET state='review',detail='Pending human decision'")
    before = _dump(store)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    batch.prepare([ean], [cid], point_check=True)
    assert _dump(store) == before and not site.calls


def test_verified_legacy_pair_still_requires_enabled_pilot_and_supervised_route(pilot):
    from price_access import AccessControl
    from tests.test_price_pilot import known

    service, store, _, _, cid, _ = pilot
    ean = SAMPLE_EANS[0]
    known(pilot, ean)
    access = AccessControl(store)
    access.ensure_defaults()
    access.set_method(cid, "ficha", "authorized", actor="test", note="Synthetic agreement")
    PriceWatch(store).add([ean], actor="test", source="manual")
    row = WatchBatch(store).preview([ean], [cid], point_check=True).iloc[0]
    assert row["pilot_route"] and not row["ready"]
    assert "Piloto desactivado" in row["defer_reason"]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM pilot_runs").fetchone()[0] == 0


def test_pilot_pause_and_budget_are_shared_across_methods_and_phases(pilot):
    from price_access import AccessControl
    from price_locator import PriceLocator
    from tests.test_price_locator import Clock

    service, store, _, _, cid, _ = pilot
    with store._connect() as con, con:
        settings = json.loads(con.execute("SELECT competitors FROM price_pilot").fetchone()[0])
        settings[cid]["pause_seconds"] = 60
        con.execute("UPDATE price_pilot SET competitors=?", (json.dumps(settings),))
        product = dict(con.execute("SELECT * FROM products WHERE ean=?", (SAMPLE_EANS[0],)).fetchone())
    service.enable()
    site = Site(base="https://www.visiofactory.es")
    site.product(product["brand"], product["model"], product["color"], gtin=product["ean"])
    clock = Clock()
    times = []

    def transport(*args):
        times.append(clock().timestamp())
        return site(*args)

    access = AccessControl(store, clock=clock)
    access.ensure_defaults()
    for method in ("catalog", "ficha"):
        access.set_method(cid, method, "authorized", actor="test", note="Synthetic agreement")
    access.set_budget("competitor", cid, daily_limit=200, per_minute_limit=600, pause_seconds=0, actor="test")
    locator = PriceLocator(store, clock=clock, transport=transport, sleep=lambda s: clock.advance(seconds=s))
    locator.save_source(cid, "catalog", site.base + "/marca/{brand}", actor="test", note="Synthetic")
    monitor = PriceMonitor(store, clock=clock, transport=transport, sleep=lambda s: clock.advance(seconds=s))
    PriceWatch(store).add([product["ean"]], actor="test", source="manual")
    result = WatchBatch(store, locator=locator, monitor=monitor).execute(
        [product["ean"]], [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["runs"][0]["location_processed"] == result["runs"][0]["monitor_processed"] == 1
    assert len(times) >= 5 and all(b - a >= 60 for a, b in zip(times, times[1:]))
    with store._connect() as con:
        assert con.execute("SELECT requests FROM pilot_budgets").fetchone()[0] == len(times)
        assert 0 < con.execute("SELECT COUNT(*) FROM pilot_http_log").fetchone()[0] <= len(times)


def test_failed_fresh_check_cannot_replay_previous_success_sample(tmp_path):
    from price_connectors import ConnectorError

    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    ean = monitor.inventory().iloc[0]["ean"]

    def failing(url, *args):
        if not url.endswith("/robots.txt"):
            raise ConnectorError("Synthetic transient failure")
        return site(url, *args)

    monitor._transport = failing
    failed = monitor.run([cid], eans=[ean], point_check=True)
    assert failed.states["pending"] == 1
    row = monitor.jobs().iloc[0]
    assert row["sample"] == "" and row["previous_sample"] != ""
    clock.advance(minutes=31)
    monitor._transport = site
    site.calls.clear()
    retried = monitor.run([cid], eans=[ean], point_check=True)
    assert retried.states["done"] == 1 and retried.requests == 2
    assert len(site.calls) == 2


def test_expired_locator_lease_does_not_override_attempt_cap(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site)
    ean = _gtin("841000000001")
    _watch(store, [(ean, "Alpha", "AL1000", "93E", "")])
    with store._connect() as con, con:
        con.execute("UPDATE locate_jobs SET state='running',attempts=?,lease_until=?",
                    (MAX_ATTEMPTS, (clock() - timedelta(minutes=1)).isoformat()))
    assert locator._claim(cid, [ean], point_check=True) is None
    assert locator.jobs().query("competitor_id == @cid").iloc[0]["state"] == "failed"
    assert not site.calls


def test_resolving_incident_restores_normal_queue_recovery(tmp_path):
    store, access, _, monitor, _, cid, site = _monitor_env(tmp_path)
    monitor.enqueue([cid])
    url = monitor.jobs().iloc[0]["url"]
    site.status[url] = 403
    assert monitor.run([cid]).states["blocked"] == 1
    access.resolve_incident(int(access.incidents(open_only=True).iloc[0]["id"]),
                            actor="test", note="Synthetic human resolution")
    site.status.clear()
    assert monitor.run([cid]).states["done"] == 1


def test_unrelated_source_does_not_mark_another_brand_checked(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site, template="https://eur.example/marca/alpha")
    site.brands["alpha"] = []
    eans = [_gtin(f"841{i + 1:09d}") for i in range(2)]
    _watch(store, [(eans[0], "Alpha", "AL1000", "93E", ""), (eans[1], "Beta", "BE1000", "93E", "")])
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    result = WatchBatch(store, locator=locator, monitor=monitor).execute(
        eans, [cid], approved=True, sync_vytria=False, point_check=True)
    pairs = {r["ean"]: r for r in result["pairs"]}
    assert pairs[eans[0]]["category"] == "checked_now"
    assert pairs[eans[1]]["category"] == "not_executed"
    assert pairs[eans[1]]["requests"] == 0
    assert "ninguna fuente aplicable" in pairs[eans[1]]["reason"]


def test_shared_search_url_is_read_once_per_point_check(tmp_path, monkeypatch):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site, methods=("ficha", "site_search"), template=None)
    url = site.base + "/search?q=alpha"
    site.pages[url] = "<html><body>No matches</body></html>"
    with store._connect() as con, con:
        con.execute("UPDATE competitor_web_config SET search_url_template=? WHERE competitor_id=?",
                    (site.base + "/search?q={query}", cid))
    monkeypatch.setattr("price_locator.product_search_urls", lambda *a, **k: [("model_color", url)])
    eans = [_gtin(f"841{i + 1:09d}") for i in range(2)]
    _watch(store, [(ean, "Alpha", "AL1000", "93E", "") for ean in eans])
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    result = WatchBatch(store, locator=locator, monitor=monitor).execute(
        eans, [cid], approved=True, sync_vytria=False, point_check=True)
    assert result["processed_pairs"] == 2 and site.count("/search") == 1
    assert {r["category"] for r in result["pairs"]} == {"checked_now"}


def test_zero_claims_remain_deferred_not_reported_as_completed(tmp_path, monkeypatch):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    ean = monitor.inventory().iloc[0]["ean"]
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    monkeypatch.setattr(locator, "run", lambda *a, **k: RunReport())
    monkeypatch.setattr(monitor, "run", lambda *a, **k: MonitorReport())
    result = batch.execute([ean], [cid], approved=True, sync_vytria=False)
    assert result["deferred"] == 1
    assert result["processed_pairs"] == 0
    assert result["pairs"][0]["category"] == "not_executed"
    assert not site.calls
