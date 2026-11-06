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
from price_trial import PriceTrial, TrialDenied, manifest, require_pair, trial_row
import price_trial
from price_web_extract import extract_product, product_page_rejection
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
    ean = "0889652494821"
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "")])
    url = site.base + "/gucci-gg1660s-001.html"
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
