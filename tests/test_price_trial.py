"""Synthetic shops/Odoo only. No Cloud data or captured commercial HTML."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import runpy
import sys

import pytest
import requests

from price_access import AccessDenied, GovernedFetcher
from price_batch import WatchBatch, batch_pairs_export
from price_comparator import PriceComparatorStore
from price_monitor import PriceMonitor
from price_trial import PriceTrial, TrialDenied, continuity_config, manifest, require_pair, trial_row
import price_trial
from price_web_extract import extract_product, product_page_rejection
from price_web_fetch import PersistentRobotsCache, RobotsDenied, WebFetcher
from tests.test_price_locator import Crash, Site, _env, _watch
from tests.web_fixtures import page
from vytria_price_sync import SqlOdooPriceReader, VytriaPriceSync


@pytest.fixture(autouse=True)
def forbid_http(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("No real commercial HTTP is allowed")
    monkeypatch.setattr(requests.Session, "send", deny)


class Reader:
    def __init__(self):
        self.calls = []

    def fetch(self, eans):
        self.calls.append(list(eans))
        return []


@pytest.fixture
def trial(tmp_path):
    site = Site(base="https://www.amevista.com", robots="User-agent: *\nAllow: /\n")
    store, access, locator, clock, cid = _env(
        tmp_path, site, name="Amevista", methods=("ficha",), template=None)
    frozen = manifest()
    # Fictional product identities isolate the lifecycle and scope, not commercial coverage.
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "") for ean in frozen["eans"]])
    reader = Reader()
    sync = VytriaPriceSync(store, reader=reader, clock=clock, sleep=lambda _: None)
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda s: clock.advance(seconds=s))
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    service = PriceTrial(store, clock=clock, batch=batch, sync=sync)
    service.heartbeat()
    service.activate(actor="test", build_commit="a" * 40)
    return service, store, access, locator, monitor, clock, cid, site, reader


def only_pair(store, cid, ean, cycle=0):
    with store._connect() as con, con:
        con.execute("UPDATE price_trial_pairs SET state='done' WHERE cycle=?", (cycle,))
        con.execute("""UPDATE price_trial_pairs SET state='pending' WHERE cycle=? AND competitor_id=? AND ean=?""",
                    (cycle, cid, ean))


def test_real_script_entrypoint_uses_canonical_sync_context(trial, monkeypatch):
    service, _, _, _, _, _, _, _, reader = trial
    called = []

    def supervisor(command):
        called.append(command)
        # Run the actual synchronizer and trial guard, not a no-op sync fixture.
        service.tick(max_pairs=0)
        return 0

    monkeypatch.setattr(price_trial, "serve", supervisor)
    monkeypatch.setattr(sys, "argv", ["price_trial.py", "--serve", "synthetic-app", "--flag"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(price_trial.__file__)), run_name="__main__")
    assert exc.value.code == 0
    assert called == [["synthetic-app", "--flag"]]
    assert reader.calls == [manifest()["eans"]]
    assert service.report()["status"] == "active"


def initial_failure(service, store):
    with store._connect() as con, con:
        con.execute("UPDATE price_trial SET status='failed',error=?,stopped_at=?",
                    (price_trial.INITIAL_SYNC_FAILURE, service.clock().isoformat()))
        con.execute("UPDATE price_trial_cycles SET sync_state='running' WHERE cycle=0")


def test_resume_pre_io_failure_archives_state_without_extending_window(trial):
    service, store, _, _, _, _, _, _, reader = trial
    initial_failure(service, store)
    before = service.report()  # The old report passively closed untouched pending pairs.
    assert all(p["state"] == "done" for p in before["pairs"])
    resumed = service.resume_initial_sync_failure(actor="operator", build_commit="b" * 40)
    for field in ("started_at", "expires_at", "manifest", "limits", "build_commit"):
        assert resumed[field] == before[field]
    assert resumed["status"] == "active" and len(resumed["recoveries"]) == 1
    assert resumed["recoveries"][0]["note"]["previous"]["trial"]["error"] == price_trial.INITIAL_SYNC_FAILURE
    assert all(p["state"] == "pending" for p in resumed["pairs"])
    assert not reader.calls
    service.tick(max_pairs=0)
    assert reader.calls == [manifest()["eans"]]
    service.tick(max_pairs=0)
    assert len(reader.calls) == 1
    with pytest.raises(ValueError):
        service.resume_initial_sync_failure(actor="operator", build_commit="b" * 40)


@pytest.mark.parametrize("unsafe", ["expired", "stopped", "different_error", "old_build", "sync", "pair", "receipt", "http"])
def test_recovery_never_replays_uncertain_work_or_restarts_window(trial, unsafe):
    service, store, _, _, _, clock, cid, _, _ = trial
    initial_failure(service, store)
    build = "a" * 40 if unsafe == "old_build" else "b" * 40
    if unsafe == "expired":
        clock.advance(hours=73)
        service.heartbeat()
    with store._connect() as con, con:
        if unsafe == "stopped":
            con.execute("UPDATE price_trial SET status='stopped'")
        elif unsafe == "different_error":
            con.execute("UPDATE price_trial SET error='Other failure'")
        elif unsafe == "sync":
            con.execute("""INSERT INTO vytria_sync_runs(trigger,started_at,status)
                VALUES('trial:72h',?,'running')""", (clock().isoformat(),))
        elif unsafe == "pair":
            con.execute("UPDATE price_trial_pairs SET started_at=? WHERE cycle=0", (clock().isoformat(),))
        elif unsafe == "receipt":
            con.execute("UPDATE price_trial_pairs SET receipt=? WHERE cycle=0", (json.dumps({"segments": []}),))
        elif unsafe == "http":
            con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,url,reserved_at)
                VALUES('competitor',?,?,'ficha','trial','https://example.test/p',?)""", (cid, cid, clock().isoformat()))
    with store._connect() as con:
        before = "\n".join(con.iterdump())
    with pytest.raises(ValueError):
        service.resume_initial_sync_failure(actor="operator", build_commit=build)
    with store._connect() as con:
        assert "\n".join(con.iterdump()) == before


def test_recovery_ui_requires_explicit_approval_and_does_not_run_queries(trial, monkeypatch):
    import build_info
    from streamlit.testing.v1 import AppTest

    service, store, _, _, _, _, _, site, reader = trial
    initial_failure(service, store)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(price_trial, "PriceTrial", lambda store: service)
    monkeypatch.setattr(build_info, "load_build_info", lambda: {"commit": "b" * 40, "build": "actions-test"})
    app = AppTest.from_string(
        "from price_comparator import PriceComparatorStore\n"
        "from app_pages.price_watch import _render_trial\n"
        "_render_trial(PriceComparatorStore())").run(timeout=30)
    assert not app.exception and app.button(key="trial_resume").disabled
    app.checkbox(key="trial_resume_approval").set_value(True).run(timeout=30)
    assert not app.button(key="trial_resume").disabled
    app.button(key="trial_resume").click().run(timeout=30)
    assert not app.exception
    assert service.report()["status"] == "active"
    assert not reader.calls and not site.calls


def test_manifest_and_persisted_window_do_not_expand_after_restart(trial):
    service, store, _, _, _, clock, _, _, _ = trial
    before = trial_row(store)
    report = service.report()
    assert len(report["manifest"]["eans"]) == 100
    assert len({(p["competitor_id"], p["ean"]) for p in report["manifest"]["pairs"]}) == 600
    assert "otticait-com" not in report["manifest"]["competitors"]
    assert len(report["pairs"]) == 1800
    assert report["expires_at_madrid"].endswith("+01:00")
    clock.advance(hours=71)
    restarted = PriceTrial(PriceComparatorStore(store.database_path), clock=clock)
    assert trial_row(restarted.store)["expires_at"] == before["expires_at"]
    with pytest.raises(ValueError, match="ya tiene ventana"):
        restarted.heartbeat()
        restarted.activate(actor="test", build_commit="b" * 40)
    clock.advance(hours=1)
    restarted.tick()
    assert restarted.report()["status"] == "expired"
    assert not any(p["state"] == "pending" for p in restarted.report()["pairs"])


def test_full_first_pair_receipt_and_exact_odoo_scope_survive_new_session(trial):
    service, store, _, _, _, _, cid, site, reader = trial
    ean = "0889652494821"
    only_pair(store, cid, ean)
    url = site.base + "/es/gucci-gg1660s-001"
    site.pages[url] = page(name="Gucci GG1660S 001", brand="Gucci", gtin=ean, price=123, pretax=123 / 1.21)
    service.tick()
    assert reader.calls == [manifest()["eans"]]
    saved = next(p for p in service.report()["pairs"] if p["cycle"] == 0 and p["competitor_id"] == cid and p["ean"] == ean)
    segment = saved["receipt"]["segments"][0]
    assert segment["runs"][0]["location_processed"] == 1
    assert segment["runs"][0]["monitor_processed"] == 1
    assert segment["pairs"][0]["outcomes"][0]["candidate_attempts"][0]["identity_status"] == "exact_match"
    assert segment["pairs"][0]["category"] == "checked_now"
    total_calls = list(site.calls)
    service.tick()
    assert site.calls == total_calls and len(reader.calls) == 1
    reopened = PriceTrial(PriceComparatorStore(store.database_path), clock=service.clock).report()
    assert json.loads(json.dumps(reopened)) == reopened
    assert reopened["summary"][f"0:{cid}"]["new_fiches"] == 1
    assert reopened["summary"][f"0:{cid}"]["comparable"] == 1
    csv_pair = batch_pairs_export(segment["pairs"]).iloc[0]
    assert json.loads(csv_pair["outcomes"]) == json.loads(json.dumps(segment))["pairs"][0]["outcomes"]


def test_expiry_between_robots_and_fiche_prevents_new_http_and_claims(trial):
    service, store, access, locator, monitor, clock, cid, site, _ = trial
    ean = "0889652494821"
    locator.enqueue([cid], [ean])
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        clock.advance(hours=72)
        return 200, {}, b"User-agent: *\nAllow: /\n"

    fetcher = GovernedFetcher(access, cid, "ficha", purpose="locate", ean=ean,
                              transport=transport, sleep=lambda s: clock.advance(seconds=s))
    with service.execution(0, cid, ean):
        with pytest.raises(AccessDenied, match="caducado"):
            fetcher.get(site.base + "/es/gucci-gg1660s-001")
        assert locator._claim(cid, [ean]) is None
        assert monitor._claim(cid, [ean]) is None
    assert calls == [site.base + "/robots.txt"]
    service.tick()
    assert service.report()["status"] == "expired"


@pytest.mark.parametrize("restriction", ["permission", "robots", "budget", "block", "review", "lease", "delay"])
def test_controls_and_queue_eligibility_still_apply(trial, restriction):
    service, store, access, locator, _, clock, cid, site, _ = trial
    ean = "0889652494821"
    only_pair(store, cid, ean)
    locator.enqueue([cid], [ean])
    if restriction == "permission":
        access.set_method(cid, "ficha", "denied", actor="test", note="Synthetic restriction")
    elif restriction == "robots":
        site.robots = "User-agent: *\nDisallow: /es/\n"
    elif restriction == "budget":
        access.set_budget("competitor", cid, daily_limit=0, per_minute_limit=10, pause_seconds=3, actor="test")
    elif restriction == "block":
        access.report_block("competitor", cid, "captcha", method="ficha", detail="Synthetic")
    else:
        with store._connect() as con, con:
            if restriction == "review":
                con.execute("UPDATE locate_jobs SET state='review' WHERE competitor_id=? AND ean=?", (cid, ean))
            elif restriction == "lease":
                con.execute("""UPDATE locate_jobs SET state='running',lease_until=?
                    WHERE competitor_id=? AND ean=?""", ((clock() + timedelta(minutes=5)).isoformat(), cid, ean))
            else:
                con.execute("UPDATE locate_jobs SET not_before=? WHERE competitor_id=? AND ean=?",
                            ((clock() + timedelta(days=1)).isoformat(), cid, ean))
    service.tick()
    assert not any("/es/" in url for url in site.calls)
    assert len(site.calls) == (1 if restriction == "robots" else 0)
    saved = next(p for p in service.report()["pairs"] if p["cycle"] == 0 and p["competitor_id"] == cid and p["ean"] == ean)
    pair = saved["receipt"]["segments"][0]["pairs"][0]
    assert pair["reason"] and pair["category"] == "not_executed"


def test_other_triggers_cannot_consume_owned_pairs_but_unrelated_pairs_remain_free(trial):
    service, store, access, locator, monitor, clock, cid, _, reader = trial
    ean = "0889652494821"
    locator.enqueue([cid], [ean])
    assert locator.run([cid], eans=[ean]).processed == 0
    assert monitor.run([cid], eans=[ean]).processed == 0
    assert locator._claim(cid, [ean]) is None
    with pytest.raises(TrialDenied):
        require_pair(store, cid, ean, clock())
    with pytest.raises(AccessDenied):
        access.reserve(cid, "ficha", purpose="manual", ean=ean)
    with pytest.raises(TrialDenied):
        service._sync.run(eans=[ean])
    with service.execution(0, cid, ean):
        with pytest.raises(TrialDenied):
            require_pair(store, "domanci-com", ean, clock())
        with pytest.raises(TrialDenied):
            require_pair(store, cid, manifest()["eans"][0], clock())
        limits = access.budget("competitor", cid)
        assert limits["daily_limit"] == 200 and limits["per_minute_limit"] == 10
        assert limits["effective_pause"] >= 3
    require_pair(store, cid, "8410000000013", clock())
    assert not reader.calls


def test_interrupted_pair_is_not_replayed_and_parallel_ticks_are_excluded(trial):
    service, store, _, _, _, _, cid, _, _ = trial
    ean = "0889652494821"
    only_pair(store, cid, ean)
    calls = []
    real_batch = service._batch

    class Interrupted:
        preview = real_batch.preview

        def execute(self, *args, **kwargs):
            calls.append(args)
            raise Crash()

    service._batch = Interrupted()
    with service.lock():
        service.tick()
    assert not calls
    with pytest.raises(Crash):
        service.tick()
    assert len(calls) == 1
    service.tick()
    assert len(calls) == 1
    saved = next(p for p in service.report()["pairs"] if p["cycle"] == 0 and p["competitor_id"] == cid and p["ean"] == ean)
    assert "interrumpido" in saved["receipt"]["closure_reason"]


def test_home_redirect_cannot_extract_a_product_card_but_canonical_difference_is_not_a_rejection():
    html = page(name="Gucci GG1660S 001", brand="Gucci", gtin="0889652494821", price=123)
    assert "portada" in product_page_rejection(html, "https://www.congafasdesol.com/")
    assert extract_product(html, "https://www.congafasdesol.com/") is None
    valid = html.replace("</head>", '<link rel="canonical" href="https://www.congafasdesol.com/otra-ficha"></head>')
    assert extract_product(valid, "https://www.congafasdesol.com/gucci-gg1660s-001.html") is not None


def test_daily_sync_runs_once_per_absolute_cycle_and_never_after_expiry(trial):
    service, store, _, _, _, clock, cid, _, reader = trial
    with store._connect() as con, con:
        con.execute("UPDATE price_trial_pairs SET state='done'")
    service.tick()
    service.tick()
    assert len(reader.calls) == 1
    clock.advance(hours=24)
    service.tick()
    service.tick()
    assert len(reader.calls) == 2
    clock.advance(hours=24)
    service.tick()
    assert len(reader.calls) == 3
    clock.advance(hours=24)
    service.tick()
    assert len(reader.calls) == 3
    assert trial_row(store)["status"] == "expired"


def test_guard_is_called_before_each_odoo_sql_query(tmp_path):
    from tests.test_vytria_price_sync import OdooFixture
    fixture = OdooFixture(tmp_path / "odoo.sqlite3")
    fixture.product("0889652494821", 200, 150)
    reader = SqlOdooPriceReader(engine_factory=fixture.engine_factory)
    checks = []

    def guard():
        checks.append(True)
        if len(checks) == 3:
            raise TrialDenied("Caducidad durante lectura Odoo")

    before = len(fixture.statements)
    with pytest.raises(TrialDenied, match="Caducidad"):
        reader.fetch_guarded(["0889652494821"], guard)
    assert len(checks) == 3
    assert len(fixture.statements) - before <= 2


def test_legacy_pilot_claim_authorization_does_not_override_not_before():
    from price_locator import job_deferral
    from tests.test_price_locator import Clock
    clock = Clock()
    job = {"state": "pilot", "attempts": 0, "not_before": (clock() + timedelta(days=1)).isoformat()}
    assert "not_before" in job_deferral(job, clock(), pilot_authorized=True)
    assert not job_deferral({**job, "not_before": None}, clock(), pilot_authorized=True)


def test_redirected_congafas_home_is_traced_without_product_or_offer(tmp_path):
    site = Site(base="https://www.congafasdesol.com", robots="User-agent: *\nAllow: /\n")
    store, _, locator, _, cid = _env(tmp_path, site, name="ConGafasDeSol", template=None, methods=("ficha",))
    ean = "0889214029614"
    _watch(store, [(ean, "Tom Ford", "FT0711", "01A", "")])
    url = site.base + "/tom-ford-fausto-ft0711-01a.html"
    home = site.base + "/"
    site.pages[home] = page(name="Gucci GG1660S 001", brand="Gucci", gtin=ean, price=123)

    def transport(target, headers, timeout):
        if target == url:
            site.calls.append(target)
            return 301, {"Location": home}, b""
        return site(target, headers, timeout)

    locator._transport = transport
    result = locator.run([cid], eans=[ean])
    attempt = result.outcomes[0]["candidate_attempts"][0]
    assert attempt["candidate_url"] == url and attempt["requested_url"] == url
    assert attempt["final_url"] == home
    assert attempt["redirects"] and "portada" in attempt["rejection"]
    with store._connect() as con:
        assert not con.execute("SELECT 1 FROM offers").fetchone()
        assert not con.execute("SELECT 1 FROM competitor_product_urls WHERE status='verified'").fetchone()


def test_partial_deadline_receipt_resumes_exact_pair_without_erasing_prior_segment(trial):
    service, store, _, _, _, _, cid, _, _ = trial
    ean = "0889652494821"
    only_pair(store, cid, ean)
    batch = service._batch
    calls = []

    class Partial:
        preview = batch.preview

        def execute(self, eans, competitors, **kwargs):
            calls.append((eans, competitors))
            return {"runs": [{"location_deferred": "Plazo de localización agotado" if len(calls) == 1 else "",
                              "monitor_deferred": ""}],
                    "pairs": [{"ean": eans[0], "competitor_id": competitors[0], "processed": False,
                               "category": "not_executed", "requests": 0, "outcomes": [],
                               "reason": "Plazo agotado" if len(calls) == 1 else "Sin localizar"}]}

    service._batch = Partial()
    service.tick()
    with store._connect() as con:
        assert con.execute("""SELECT state FROM price_trial_pairs WHERE cycle=0 AND competitor_id=? AND ean=?""",
                           (cid, ean)).fetchone()[0] == "pending"
    service.tick()
    saved = next(p for p in service.report()["pairs"] if p["cycle"] == 0 and p["competitor_id"] == cid and p["ean"] == ean)
    assert saved["state"] == "done"
    assert len(saved["receipt"]["segments"]) == 2
    assert calls == [([ean], [cid]), ([ean], [cid])]


def test_http_403_stops_store_without_retrying_later_pairs(trial):
    service, store, _, _, _, _, cid, site, _ = trial
    first, second = "0889652494821", "0889652494838"
    only_pair(store, cid, first)
    with store._connect() as con, con:
        con.execute("""UPDATE price_trial_pairs SET state='pending'
            WHERE cycle=0 AND competitor_id=? AND ean=?""", (cid, second))
    site.status[site.base + "/es/gucci-gg1660s-001"] = 403
    service.tick()
    assert site.calls == [site.base + "/robots.txt", site.base + "/es/gucci-gg1660s-001"]
    service.tick()
    assert len(site.calls) == 2
    assert service.report()["summary"][f"0:{cid}"]["http"] == 2


def test_expired_restart_closes_interrupted_reservation_preserving_receipt_and_unknown_http(trial):
    service, store, access, _, _, clock, cid, _, _ = trial
    ean = "0889652494821"
    only_pair(store, cid, ean)
    with store._connect() as con, con:
        con.execute("""UPDATE price_trial_pairs SET state='running',started_at=?,receipt=?
            WHERE cycle=0 AND competitor_id=? AND ean=?""",
                    (clock().isoformat(), json.dumps({"segments": [], "marker": "preserved"}), cid, ean))
    with service.execution(0, cid, ean):
        access.reserve(cid, "ficha", purpose="locate", ean=ean)
    clock.advance(hours=72)
    service.tick()
    report = service.report()
    saved = next(p for p in report["pairs"] if p["cycle"] == 0 and p["competitor_id"] == cid and p["ean"] == ean)
    assert saved["state"] == "done" and saved["receipt"]["marker"] == "preserved"
    assert "interrumpido" in saved["receipt"]["closure_reason"]
    assert report["summary"][f"0:{cid}"]["http"] == 0
    assert report["summary"][f"0:{cid}"]["unconfirmed_http"] == 1


def test_stop_prevents_new_http_without_changing_saved_controls(trial):
    service, store, access, _, _, clock, cid, site, reader = trial
    before = access.budget("competitor", cid)
    service.stop(actor="test")
    service.tick()
    with service.execution(0, cid, "0889652494821"):
        with pytest.raises(AccessDenied):
            access.reserve(cid, "ficha", purpose="locate", ean="0889652494821")
    assert access.budget("competitor", cid) == before
    assert not site.calls and not reader.calls


@pytest.mark.parametrize("legacy_pair", [True, False])
def test_existing_legacy_scope_does_not_exclude_additional_trial_pairs_and_keeps_legacy_route(trial, monkeypatch, legacy_pair):
    from price_pilot import PricePilot, SAMPLE_EANS, pilot_scope
    from tests.test_price_review_fixes import _vytria
    service, store, _, _, _, clock, cid, site, _ = trial
    for ean in SAMPLE_EANS:
        _vytria(store, ean, 300)
    with store._connect() as con, con:
        con.execute("UPDATE vytria_prices SET synced_at=?", (datetime.now().astimezone().isoformat(),))
    pilot = PricePilot(store)
    pilot.save(list(SAMPLE_EANS), {r["id"]: {"pause_seconds": 3, "max_http_requests": 200}
                                  for r in pilot.competitors()})
    pilot.enable()
    class Date(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock()

    monkeypatch.setattr("price_pilot.datetime", Date)
    ean = "0889652494821" if legacy_pair else next(e for e in manifest()["eans"] if e not in SAMPLE_EANS)
    only_pair(store, cid, ean)
    site.pages[site.base + "/es/gucci-gg1660s-001"] = page(
        name="Gucci GG1660S 001", brand="Gucci", gtin=ean, price=123, pretax=123 / 1.21)
    scope_before = pilot_scope(store)
    service.tick()
    assert service.report()["status"] == "active"
    assert service.report()["summary"][f"0:{cid}"]["processed_pairs"] == 1
    assert pilot_scope(store) == scope_before
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM pilot_runs WHERE operation='watch_check'").fetchone()[0] == int(legacy_pair)


def _activate_continuity(service, clock):
    clock.advance(hours=73)
    service.tick(max_pairs=0)
    prepared = service.prepare_continuity(actor="operator")
    assert prepared["status"] == "prepared"
    return service.activate_continuity(actor="operator", build_commit="c" * 40)


def test_continuity_manifest_is_persisted_once_and_activation_is_explicit(trial):
    service, store, _, _, _, clock, _, _, _ = trial
    config = continuity_config()
    assert config["require_explicit_activation"] is True
    assert config["offer_check_interval_hours"] == 24
    dockerfile = Path(price_trial.__file__).parents[0].joinpath("Dockerfile").read_text(encoding="utf-8")
    assert "price_watch_continuity.json" in dockerfile
    prepared = _activate_continuity(service, clock)
    assert prepared["status"] == "active"
    assert prepared["manifest"]["eans"] == manifest()["eans"]
    assert prepared["manifest"]["competitors"] == manifest()["competitors"]
    assert len(prepared["pairs"]) == 600
    assert prepared["summary"]["selected_pairs"] == 600
    assert prepared["summary"]["distinct_eans"] == 100
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM price_continuity").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM price_continuity_pairs").fetchone()[0] == 600
        assert con.execute("""SELECT COUNT(DISTINCT ean) FROM price_continuity_pairs""").fetchone()[0] == 100
    with pytest.raises(ValueError, match="pendiente de aprobación"):
        service.activate_continuity(actor="operator", build_commit="c" * 40)
    assert service.pause_continuity(actor="operator")["status"] == "paused"
    assert service.resume_continuity(actor="operator")["status"] == "active"
    stopped = service.stop_continuity(actor="operator")
    assert stopped["status"] == "stopped" and len(stopped["pairs"]) == 600


def test_continuity_ui_keeps_prepared_plan_inactive_until_explicit_approval(trial, monkeypatch):
    from streamlit.testing.v1 import AppTest

    import build_info
    service, store, _, _, _, clock, _, _, _ = trial
    clock.advance(hours=73)
    service.tick(max_pairs=0)
    service.prepare_continuity(actor="operator")
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(price_trial, "PriceTrial", lambda _store: service)
    monkeypatch.setattr(build_info, "load_build_info",
                        lambda: {"commit": "d" * 40, "build": "actions-test"})
    app = AppTest.from_string(
        "from price_comparator import PriceComparatorStore\n"
        "from app_pages.price_watch import _render_continuity\n"
        "_render_continuity(PriceComparatorStore())").run(timeout=30)
    assert not app.exception
    assert app.button(key="continuity_activate").disabled
    app.checkbox(key="continuity_activation_approval").set_value(True).run(timeout=30)
    assert not app.button(key="continuity_activate").disabled
    assert service.continuity_report()["status"] == "prepared"


def test_restricted_pair_reevaluation_is_audited_and_never_queries_or_rewrites_history(trial):
    import pandas as pd
    from price_trial import active_execution

    service, store, _, _, _, clock, cid, site, _ = trial
    active = _activate_continuity(service, clock)
    eligible_ean, blocked_ean = manifest()["eans"][:2]
    with store._connect() as con, con:
        con.execute("""UPDATE price_continuity_pairs SET state='restricted',defer_reason=?
            WHERE continuity_id=? AND competitor_id=? AND ean IN (?,?)""",
                    ("Restricción anterior", active["id"], cid, eligible_ean, blocked_ean))
        con.execute("""INSERT INTO price_continuity_checks
            (continuity_id,competitor_id,ean,started_at,finished_at,state,category,requests,
             defer_reason,receipt) VALUES(?,?,?,?,?,'restricted','not_executed',0,?,'{}')""",
                    (active["id"], cid, eligible_ean, clock().isoformat(), clock().isoformat(),
                     "Restricción anterior"))

    class Batch:
        def preview(self, eans, competitors):
            execution = active_execution(store)
            assert execution and execution.program == "continuity"
            assert execution.competitor_id == competitors[0] and execution.ean == eans[0]
            eligible = eans[0] == eligible_ean
            return pd.DataFrame([{
                "ready": eligible, "candidate_count": 1,
                "candidate_reason": "", "defer_reason": "" if eligible else "Acceso bloqueado",
                "access": "Autorizado" if eligible else "Bloqueado",
                "local_candidates": [{"url": "https://www.amevista.com/es/gucci-gg1660s-001"}],
                "next_check_at": None,
            }])

    service._batch = Batch()
    with store._connect() as con:
        before_access_requests = con.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0]
    preview = service.reevaluate_restricted_pairs()

    preview_by_ean = {item["ean"]: item for item in preview}
    assert preview_by_ean[eligible_ean]["eligible"]
    assert not preview_by_ean[blocked_ean]["eligible"]
    with store._connect() as con:
        states = {row["ean"]: row["state"] for row in con.execute("""SELECT ean,state
            FROM price_continuity_pairs WHERE continuity_id=? AND competitor_id=? AND ean IN (?,?)""",
                                                                  (active["id"], cid, eligible_ean, blocked_ean))}
        assert states == {eligible_ean: "restricted", blocked_ean: "restricted"}
        assert con.execute("SELECT COUNT(*) FROM price_continuity_checks WHERE continuity_id=?",
                          (active["id"],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='reevaluate_restricted_pairs'")\
            .fetchone()[0] == 0

    with pytest.raises(ValueError, match="Responsable y motivo"):
        service.reevaluate_restricted_pairs(apply=True)
    applied = service.reevaluate_restricted_pairs(actor="operator", note="Regla de cobertura corregida", apply=True)

    applied_by_ean = {item["ean"]: item for item in applied}
    assert applied_by_ean[eligible_ean]["applied"]
    assert not applied_by_ean[blocked_ean]["applied"]
    assert site.calls == []
    with store._connect() as con:
        rows = {row["ean"]: dict(row) for row in con.execute("""SELECT ean,state,next_check_at,defer_reason
            FROM price_continuity_pairs WHERE continuity_id=? AND competitor_id=? AND ean IN (?,?)""",
                                                              (active["id"], cid, eligible_ean, blocked_ean))}
        assert rows[eligible_ean]["state"] == "pending"
        assert rows[eligible_ean]["next_check_at"] == clock().isoformat()
        assert rows[blocked_ean]["state"] == "restricted"
        assert con.execute("SELECT COUNT(*) FROM price_continuity_checks WHERE continuity_id=?",
                          (active["id"],)).fetchone()[0] == 1
        audit = con.execute("""SELECT previous,current,actor,note FROM access_audit
            WHERE action='reevaluate_restricted_pairs'""").fetchone()
        assert audit["actor"] == "operator" and audit["note"] == "Regla de cobertura corregida"
        previous = json.loads(audit["previous"])
        current = json.loads(audit["current"])
        assert next(item for item in previous if item["ean"] == eligible_ean)["defer_reason"] == "Restricción anterior"
        assert next(item for item in current if item["ean"] == eligible_ean)["state"] == "pending"
        assert con.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == before_access_requests

    service.reevaluate_restricted_pairs(actor="operator", note="Sin cambios", apply=True)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='reevaluate_restricted_pairs'")\
            .fetchone()[0] == 1


def test_continuity_due_time_survives_restart_and_rechecks_after_expiry(trial):
    import pandas as pd

    service, store, _, _, _, clock, cid, _, reader = trial
    active = _activate_continuity(service, clock)
    target_ean = manifest()["eans"][0]
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_pairs SET state='review' WHERE continuity_id=?",
                    (active["id"],))
        con.execute("""UPDATE price_continuity_pairs SET state='pending',defer_reason='',next_check_at=NULL
            WHERE continuity_id=? AND competitor_id=? AND ean=?""", (active["id"], cid, target_ean))

    class Batch:
        def __init__(self):
            self.previews = 0
            self.executions = 0

        def preview(self, eans, competitors):
            self.previews += 1
            due_at = (clock() + timedelta(hours=24)).isoformat() if self.previews % 2 == 0 else None
            return pd.DataFrame([{"action": "Monitorizar", "next_check_at": due_at,
                                  "access_retry_at": None, "not_before": None, "valid_until": due_at,
                                  "defer_reason": ""}])

        def execute(self, eans, competitors, **kwargs):
            self.executions += 1
            return {"pairs": [{"ean": eans[0], "competitor_id": competitors[0], "processed": True,
                               "category": "checked_now", "requests": 0, "outcomes": [], "reason": ""}]}

    batch = Batch()
    service._batch = batch
    service.continuity_tick(max_pairs=1)
    assert batch.executions == 1
    with store._connect() as con:
        saved = con.execute("""SELECT state,next_check_at FROM price_continuity_pairs
            WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                            (active["id"], cid, target_ean)).fetchone()
    assert saved["state"] == "pending"
    assert saved["next_check_at"] == (clock() + timedelta(hours=24)).isoformat()

    restarted = PriceTrial(store, clock=clock, batch=batch, sync=service._sync)
    restarted.continuity_tick(max_pairs=1)
    assert batch.executions == 1
    assert len(reader.calls) == 1
    clock.advance(hours=24)
    restarted.continuity_tick(max_pairs=1)
    assert batch.executions == 2
    with store._connect() as con:
        assert con.execute("""SELECT COUNT(*) FROM price_continuity_pairs
            WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                           (active["id"], cid, target_ean)).fetchone()[0] == 1
        assert con.execute("""SELECT COUNT(*) FROM price_continuity_checks
            WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                           (active["id"], cid, target_ean)).fetchone()[0] == 2


def test_interrupted_continuity_reservation_is_reviewed_and_never_replayed(trial):
    service, store, access, _, _, clock, cid, site, _ = trial
    active = _activate_continuity(service, clock)
    ean = manifest()["eans"][0]
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_pairs SET state='review' WHERE continuity_id=?",
                    (active["id"],))
        con.execute("""UPDATE price_continuity_pairs SET state='running',started_at=?
            WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                    (clock().isoformat(), active["id"], cid, ean))
    with service.execution(0, cid, ean, program="continuity", program_id=active["id"]):
        access.reserve(cid, "ficha", purpose="monitor", ean=ean, url="https://shop.example/item")
    restarted = PriceTrial(store, clock=clock, batch=service._batch, sync=service._sync)
    restarted.continuity_tick(max_pairs=1)
    restarted.continuity_tick(max_pairs=1)
    saved = next(pair for pair in restarted.continuity_report()["pairs"]
                 if pair["competitor_id"] == cid and pair["ean"] == ean)
    assert saved["state"] == "review"
    assert "no se repite" in saved["defer_reason"]
    assert restarted.continuity_report()["summary"]["http_unconfirmed_unique"] == 1
    assert site.calls == []
    with store._connect() as con:
        assert con.execute("""SELECT COUNT(*) FROM price_continuity_checks
            WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                           (active["id"], cid, ean)).fetchone()[0] == 1


def test_trial_carries_unexpired_pair_deadlines_into_the_next_cycle(trial):
    import pandas as pd

    service, store, _, _, _, clock, cid, site, _ = trial
    ean = manifest()["eans"][0]
    next_check = clock() + timedelta(hours=47)
    with store._connect() as con, con:
        con.execute("UPDATE price_trial_pairs SET state='done' WHERE cycle=0")
        con.execute("""UPDATE price_trial_pairs SET state='pending',next_check_at=?,defer_reason=?
            WHERE cycle=0 AND competitor_id=? AND ean=?""",
                    (next_check.isoformat(), "Vigencia todavía activa", cid, ean))
    clock.advance(hours=24)
    service.tick(max_pairs=0)
    with store._connect() as con, con:
        inherited = con.execute("""SELECT state,next_check_at,defer_reason FROM price_trial_pairs
            WHERE cycle=1 AND competitor_id=? AND ean=?""", (cid, ean)).fetchone()
        con.execute("UPDATE price_trial_pairs SET state='done' WHERE cycle=1")
        con.execute("""UPDATE price_trial_pairs SET state='pending'
            WHERE cycle=1 AND competitor_id=? AND ean=?""", (cid, ean))
    assert inherited["state"] == "pending"
    assert inherited["next_check_at"] == next_check.isoformat()
    assert inherited["defer_reason"] == "Vigencia todavía activa"


    class Batch:
        previews = 0
        executions = 0

        def preview(self, eans, competitors):
            self.previews += 1
            next_at = (clock() + timedelta(hours=24)).isoformat() if self.previews % 2 == 0 else None
            return pd.DataFrame([{"action": "Monitorizar", "next_check_at": next_at,
                                  "access_retry_at": None, "not_before": None, "valid_until": next_at,
                                  "defer_reason": ""}])

        def execute(self, eans, competitors, **kwargs):
            self.executions += 1
            return {"pairs": [{"ean": eans[0], "competitor_id": competitors[0], "processed": True,
                               "category": "checked_now", "requests": 0, "outcomes": [], "reason": ""}]}

    batch = Batch()
    service._batch = batch
    service.tick(max_pairs=1)
    assert batch.executions == 0
    clock.advance(hours=23)
    service.tick(max_pairs=1)
    assert batch.executions == 1
    assert site.calls == []


def test_verified_offer_is_rechecked_when_its_24_hour_frequency_expires(trial):
    import pandas as pd

    service, store, _, _, _, clock, cid, _, _ = trial
    service.tick(max_pairs=0)
    target_ean = manifest()["eans"][0]
    with store._connect() as con, con:
        con.execute("UPDATE price_trial_pairs SET state='done' WHERE cycle=0")
        con.execute("""UPDATE price_trial_pairs SET state='pending' WHERE cycle=0
            AND competitor_id=? AND ean=?""", (cid, target_ean))

    class Batch:
        executions = 0

        def preview(self, eans, competitors):
            valid_until = (clock() + timedelta(hours=48)).isoformat()
            return pd.DataFrame([{
                "located": True,
                "last_ok_at": clock().isoformat(),
                "interval_hours": 48,
                "next_check_at": valid_until,
                "valid_until": valid_until,
                "defer_reason": "",
                "action": "Monitorizar",
            }])

        def execute(self, eans, competitors, **kwargs):
            self.executions += 1
            return {"runs": [], "pairs": [{
                "ean": eans[0], "competitor_id": competitors[0], "processed": True,
                "category": "checked_now", "outcomes": [],
            }]}

    batch = Batch()
    service._batch = batch
    service.tick(max_pairs=1)
    with store._connect() as con:
        first = con.execute("""SELECT state,next_check_at FROM price_trial_pairs
            WHERE cycle=0 AND competitor_id=? AND ean=?""", (cid, target_ean)).fetchone()
    assert first["state"] == "pending"
    assert first["next_check_at"] == (clock() + timedelta(hours=24)).isoformat()

    clock.advance(hours=24)
    service.tick(max_pairs=1)
    with store._connect() as con:
        second = con.execute("""SELECT state FROM price_trial_pairs
            WHERE cycle=1 AND competitor_id=? AND ean=?""", (cid, target_ean)).fetchone()
        copies = con.execute("""SELECT COUNT(*) FROM price_trial_pairs
            WHERE cycle=1 AND competitor_id=? AND ean=?""", (cid, target_ean)).fetchone()[0]
    assert batch.executions == 2
    assert second["state"] == "pending"
    assert copies == 1


def test_trial_report_deduplicates_shared_http_receipts(trial):
    service, store, _, _, _, _, cid, _, _ = trial
    eans = manifest()["eans"][:2]
    request = {"id": 991, "result": "HTTP 200", "url": "https://shop.example/robots.txt"}
    for ean in eans:
        pair = [{"ean": ean, "processed": True, "category": "checked_now", "outcomes": []}]
        segment = {"pairs": pair, "http_receipts": [request],
                   "inventory_before": {"located": False, "action": "Monitorizar"},
                   "inventory_after": {}}
        with store._connect() as con, con:
            con.execute("""UPDATE price_trial_pairs SET state='done',receipt=?
                WHERE cycle=0 AND competitor_id=? AND ean=?""",
                        (json.dumps({"segments": [segment]}), cid, ean))
    for ean, state in zip(manifest()["eans"][2:4], ("unavailable", "not_found")):
        pair = [{"ean": ean, "processed": True, "category": "checked_now",
                 "outcomes": [{"state": state}]}]
        segment = {"pairs": pair, "http_receipts": [],
                   "inventory_before": {"located": False, "action": "Monitorizar"},
                   "inventory_after": {}}
        with store._connect() as con, con:
            con.execute("""UPDATE price_trial_pairs SET state='done',receipt=?
                WHERE cycle=0 AND competitor_id=? AND ean=?""",
                        (json.dumps({"segments": [segment]}), cid, ean))
    totals = service.report()["summary"][f"0:{cid}"]
    assert totals["reserved_http"] == 1
    assert totals["http"] == 1
    assert totals["http_robots"] == 1
    assert totals["http_commercial"] == 0
    assert totals["not_located"] == 1
    assert totals["restricted_or_unavailable"] == 1


def test_continuity_budget_enforces_rolling_200_http_and_minimum_pacing(trial):
    service, store, access, _, _, clock, cid, _, _ = trial
    active = _activate_continuity(service, clock)
    ean = manifest()["eans"][0]
    access.set_budget("competitor", cid, daily_limit=1000, per_minute_limit=600,
                      pause_seconds=0, actor="operator")
    with service.execution(0, cid, ean, program="continuity", program_id=active["id"]):
        budget = access.budget("competitor", cid)
        assert budget["daily_limit"] == 200
        assert budget["per_minute_limit"] == 10
        assert budget["effective_pause"] >= 3
        first = clock()
        for _ in range(200):
            access.reserve(cid, "ficha", purpose="monitor", ean=ean, url="https://shop.example/item")
            clock.advance(seconds=6)
        with pytest.raises(AccessDenied) as denied:
            access.reserve(cid, "ficha", purpose="monitor", ean=ean, url="https://shop.example/item")
        assert denied.value.decision.code == "budget_daily"
        clock.now = first + timedelta(hours=24) - timedelta(seconds=1)
        assert access.check(cid, "ficha").code == "budget_daily"
        clock.advance(seconds=1)
        assert access.check(cid, "ficha").allowed


def test_persistent_robots_cache_reuses_response_but_evaluates_each_path_and_agent(trial):
    service, store, _, _, _, clock, _, _, _ = trial
    base = "https://www.amevista.com"
    site = Site(base=base, robots="User-agent: *\nDisallow: /private\nAllow: /\n")
    public = base + "/public"
    site.pages[public] = page(name="Public product", price=123)
    cache = PersistentRobotsCache(store, clock=clock)
    fetcher = WebFetcher(transport=site, robots_cache=cache, sleep=lambda _: None)
    with pytest.raises(RobotsDenied):
        fetcher.get(base + "/private")
    fetcher.get(public)
    assert site.calls == [base + "/robots.txt", public]

    other_agent = WebFetcher(transport=site, robots_cache=cache, agent_token="DifferentAgent",
                             sleep=lambda _: None)
    other_agent.get(public)
    assert site.calls == [base + "/robots.txt", public, base + "/robots.txt", public]


def test_persistent_robots_cache_obeys_expiry_and_does_not_cache_errors(trial):
    service, store, _, _, _, clock, _, _, _ = trial
    base = "https://www.amevista.com"
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, {"Cache-Control": "max-age=1"}, b"User-agent: *\nAllow: /\n"
        return 200, {}, b"page"

    cache = PersistentRobotsCache(store, clock=clock)
    first = WebFetcher(transport=transport, robots_cache=cache, sleep=lambda _: None)
    first.rules(base + "/item")
    clock.advance(seconds=2)
    second = WebFetcher(transport=transport, robots_cache=cache, sleep=lambda _: None)
    second.rules(base + "/item")
    assert calls == [base + "/robots.txt", base + "/robots.txt"]
    clock.advance(seconds=2)

    failed_calls = []

    def failed(url, headers, timeout):
        failed_calls.append(url)
        return 503, {}, b""

    uncached = WebFetcher(transport=failed, robots_cache=cache, sleep=lambda _: None)
    assert uncached.rules(base + "/item").deny_all
    again = WebFetcher(transport=failed, robots_cache=cache, sleep=lambda _: None)
    assert again.rules(base + "/item").deny_all
    assert failed_calls == [base + "/robots.txt", base + "/robots.txt"]
    agent = "Cache-Control-Test"
    cache.put(base, agent, 200, {"Cache-Control": "max-age=60", "Age": "60"}, b"expired")
    assert cache.get(base, agent) is None
    cache.put(base, agent, 200, {"Cache-Control": "private, max-age=60"}, b"private")
    assert cache.get(base, agent) is None
