"""Offline regressions for accepted review reads and queue fairness."""
from dataclasses import asdict, replace
from datetime import timedelta
from io import BytesIO
import json

from filelock import FileLock, Timeout
import pandas as pd
import pytest
import requests

from price_monitor import MonitorReport
from price_recovery import apply_manual_reviews, preview_manual_reviews
from price_web import WebPriceSearch
from price_web_extract import ProductPage
from tests.test_price_locator import Crash, _gtin, _watch
from tests.test_price_review_refresh import _candidate, _expected, _review_env
from tests.test_price_trial import _diagnostic_review
from tests import test_price_trial as trial_helpers


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("No real network permitted")

    monkeypatch.setattr(requests.Session, "send", deny)


@pytest.fixture
def trial(tmp_path):
    return trial_helpers.trial.__wrapped__(tmp_path)


@pytest.fixture
def expanded_review(trial):
    service, store, clock, cid, ean, url, site = _diagnostic_review(trial)
    access = trial[2]
    for shop in store.competitors().to_dict("records"):
        for method in ("ficha", "sitemap"):
            access.set_method(shop["id"], method, "authorized", actor="operator", note="Synthetic authorization")
    service.heartbeat()
    service.expand_continuity(actor="operator", approved=True, build_commit="d" * 40,
                              note="Synthetic scope expansion")
    locator, monitor = trial[3], trial[4]
    row = _candidate(locator, ean)
    task = locator.review(ean, cid, url, accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(row))
    return service, store, clock, cid, ean, url, site, monitor, task


def test_accepted_review_uses_active_continuity_after_pilot_expiry(expanded_review):
    service, store, clock, cid, ean, url, site, monitor, task = expanded_review

    result = monitor.review_refresh_tick()

    with store._connect() as con:
        refresh = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert result["processed"] == 1, (result, dict(refresh))
    assert refresh["status"] == "done"
    assert url in site.calls
    assert service.report()["status"] == "expired"


@pytest.mark.parametrize("control", ["pause", "stop"])
def test_inactive_continuity_never_sends_http(expanded_review, control):
    service, store, _, _, _, _, site, monitor, task = expanded_review
    getattr(service, f"{control}_continuity")(actor="operator")
    monitor.review_refresh_tick()
    with store._connect() as con:
        refresh = con.execute("SELECT status,detail,attempts FROM manual_review_refreshes WHERE id=?",
                              (task,)).fetchone()
    assert refresh["status"] == ("deferred" if control == "pause" else "blocked")
    assert "continuidad" in refresh["detail"].lower()
    assert refresh["attempts"] == 0
    assert site.calls == []


def test_changed_acceptance_never_sends_http(expanded_review):
    _, store, clock, cid, ean, url, site, monitor, task = expanded_review
    clock.advance(seconds=1)
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET verified_at=? WHERE competitor_id=? AND ean=? AND url=?""",
                    (clock().isoformat(), cid, ean, url))
    monitor.review_refresh_tick()
    with store._connect() as con:
        refresh = con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert refresh["status"] == "blocked"
    assert site.calls == []


def test_changed_color_identity_never_sends_http(expanded_review):
    _, store, _, cid, ean, url, site, monitor, task = expanded_review
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET color_code='OTHER'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
    monitor.review_refresh_tick()
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == "blocked"
    assert site.calls == []


def test_caliber_change_alone_does_not_invalidate_acceptance(expanded_review):
    _, store, _, cid, ean, url, site, monitor, task = expanded_review
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET caliber='62-22'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
    assert monitor.review_refresh_tick()["processed"] == 1
    assert url in site.calls
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == "done"


def test_extracted_other_variant_is_not_recorded(expanded_review, monkeypatch):
    _, store, _, cid, ean, url, _, monitor, task = expanded_review
    from price_monitor import select_product_variant

    def other_variant(page, product):
        selected = select_product_variant(page, product)
        return replace(selected, url=url + "?variant=other")

    monkeypatch.setattr("price_monitor.select_product_variant", other_variant)
    monitor.review_refresh_tick()
    with store._connect() as con:
        row = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?", (ean, cid)).fetchone()[0] == 0
    assert row["status"] == "error" and "variante" in row["detail"].lower()


def test_identity_change_before_offer_commit_never_creates_observation(expanded_review, monkeypatch):
    _, store, _, cid, ean, url, _, monitor, task = expanded_review
    original = WebPriceSearch.record

    def change_identity(search, outcome, *, validate=None):
        with store._connect() as con, con:
            con.execute("""UPDATE competitor_product_urls SET color_code='OTHER'
                WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
        return original(search, outcome, validate=validate)

    monkeypatch.setattr(WebPriceSearch, "record", change_identity)
    monitor.review_refresh_tick()
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?", (ean, cid)).fetchone()[0] == 0
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] != "done"


def test_pair_outside_the_manifest_never_sends_http(expanded_review, trial):
    _, store, clock, cid, _, _, site, monitor, original = expanded_review
    locator = trial[3]
    other = _gtin("841999999999")
    _watch(store, [(other, "Gucci", "GG1660S", "001", "54-18")])
    url = site.product("Gucci", "GG1660S", "001", gtin=other, slug="outside.html")
    locator.enqueue([cid], [other])
    with store._connect() as con, con:
        con.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,match_status,status,verified_at)
            VALUES(?,?,?,'manual_review','probable',?)""", (cid, other, url, clock().isoformat()))
        con.execute("UPDATE locate_jobs SET state='review',url=? WHERE competitor_id=? AND ean=?",
                    (url, cid, other))
        con.execute("UPDATE manual_review_refreshes SET retry_at=?,status='deferred' WHERE id=?",
                    ((clock() + timedelta(days=1)).isoformat(), original))
    task = locator.review(other, cid, url, accept=True, actor="operator", note="Synthetic outside scope",
                          update_price=True, expected_candidate=_expected(_candidate(locator, other)))
    monitor.review_refresh_tick()
    with store._connect() as con:
        row = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert row["status"] == "blocked" and "manifiesto" in row["detail"].lower()
    assert site.calls == []


def test_eligible_request_advances_past_five_repeated_deferrals(tmp_path, monkeypatch):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path, count=6)
    tasks = []
    for row, url in zip(rows, urls):
        candidate = _candidate(locator, row[0])
        tasks.append(locator.review(row[0], cid, url, accept=True, actor="operator", note="Synthetic review",
                                    update_price=True, expected_candidate=_expected(candidate)))
    normal_run = monitor.run

    def defer_five(competitors, **kwargs):
        if kwargs["eans"][0] in {row[0] for row in rows[:5]}:
            return MonitorReport(stopped={cid: "Reserva sintética"})
        return normal_run(competitors, **kwargs)

    monkeypatch.setattr(monitor, "run", defer_five)
    assert monitor.review_refresh_tick()["deferred"] == 5
    clock.advance(seconds=31)
    monitor.review_refresh_tick()

    with store._connect() as con:
        last = con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (tasks[-1],)).fetchone()
    assert last["status"] == "done"
    assert urls[-1] in site.calls


def test_read_never_switches_to_a_different_verified_url(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                   update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    alternate = site.product("Alpha", "AL1000", "93E", gtin=ean, slug="alternate.html")
    clock.advance(seconds=1)
    with store._connect() as con, con:
        con.execute("""INSERT INTO competitor_product_urls
            (competitor_id,ean,url,match_status,status,verified_at)
            VALUES(?,?,?,'exact_match','verified',?)""", (cid, ean, alternate, clock().isoformat()))

    monitor.review_refresh_tick()
    assert alternate not in site.calls


def test_restart_reuses_saved_sample_without_a_second_http(tmp_path, monkeypatch):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                   update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    clock.advance(microseconds=1)

    def interrupt(*args):
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(monitor, "_commit_sample", interrupt)
        with pytest.raises(Crash):
            monitor.review_refresh_tick()
    assert site.calls.count(urls[0]) == 1
    clock.advance(minutes=11)
    monitor.review_refresh_tick()
    assert site.calls.count(urls[0]) == 1
    with store._connect() as con:
        task = con.execute("SELECT id,http_requests FROM manual_review_refreshes").fetchone()
        receipts = con.execute("SELECT COUNT(*) FROM access_requests WHERE purpose=?",
                               (f"manual_review:{task['id']}",)).fetchone()[0]
    assert task["http_requests"] == receipts == 2


def test_interrupted_request_without_sample_is_not_replayed(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    site.crash_after = 1
    with pytest.raises(Crash):
        monitor.review_refresh_tick()
    site.crash_after = None
    clock.advance(minutes=11)
    monitor.review_refresh_tick()
    assert site.calls == [f"{site.base}/robots.txt"]
    with store._connect() as con:
        row = con.execute("SELECT status,detail,http_requests FROM manual_review_refreshes WHERE id=?",
                          (task,)).fetchone()
    assert row["status"] == "blocked" and "reserva" in row["detail"]
    assert row["http_requests"] == 2


def _legacy_request(store, clock, task, *, deferred=False):
    with store._connect() as con, con:
        row = con.execute("SELECT * FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        timestamp = clock().isoformat(timespec="seconds")
        con.execute("DELETE FROM manual_review_identity WHERE refresh_id=?", (task,))
        con.execute("UPDATE products SET updated_at=? WHERE ean=?",
                    ((clock() - timedelta(days=1)).isoformat(), row["ean"]))
        con.execute("""UPDATE competitor_product_urls SET verified_at=?
            WHERE competitor_id=? AND ean=? AND url=?""",
                    (timestamp, row["competitor_id"], row["ean"], row["url"]))
        con.execute("""UPDATE manual_review_refreshes SET requested_at=?,status=?,detail=?,retry_at=?
            WHERE id=?""",
                    (timestamp, "deferred" if deferred else "pending",
                     "Par reservado al piloto de 72 h o ventana finalizada" if deferred else "Pendiente",
                     (clock() + timedelta(seconds=30)).isoformat() if deferred else None, task))


def test_audited_recovery_is_idempotent_and_does_not_run_http(expanded_review):
    _, store, clock, cid, _, url, site, monitor, task = expanded_review
    _legacy_request(store, clock, task, deferred=True)
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert preview["counts"] == {"recoverable": 1, "already_resolved": 0, "excluded": 0}
    assert preview["requests"][0]["action"] == "requeue"
    assert monitor.review_refresh_tick()["processed"] == 0
    assert site.calls == []

    args = {"actor": "operator", "note": "Synthetic recovery", "build_commit": "e" * 40,
            "approved": True, "now": clock()}
    applied = apply_manual_reviews(store.database_path, **args)
    assert applied["changed"] == [task] and site.calls == []
    assert apply_manual_reviews(store.database_path, **args)["changed"] == []
    with store._connect() as con:
        row = con.execute("SELECT * FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        assert row["attempts"] == 0 and row["reason"] == "Synthetic review"
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='recover_manual_review'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM manual_review_recovery_grants").fetchone()[0] == 1
    clock.advance(seconds=1)
    assert monitor.review_refresh_tick()["processed"] == 1
    assert url in site.calls


def test_recovery_respects_competitor_lock(expanded_review):
    _, store, clock, cid, _, _, site, _, task = expanded_review
    _legacy_request(store, clock, task, deferred=True)
    with FileLock(f"{store.database_path}.competitor-{cid}.lock", timeout=0):
        with pytest.raises(Timeout):
            apply_manual_reviews(store.database_path, actor="operator", note="Synthetic", build_commit="e" * 40,
                                 approved=True, now=clock())
    assert site.calls == []
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == "deferred"
        assert con.execute("SELECT COUNT(*) FROM manual_review_recovery_grants").fetchone()[0] == 0


def test_recovery_grant_does_not_authorize_a_replacement_continuity(expanded_review):
    _, store, clock, _, _, _, site, monitor, task = expanded_review
    _legacy_request(store, clock, task)
    args = {"actor": "operator", "note": "Synthetic", "build_commit": "e" * 40,
            "approved": True, "now": clock()}
    assert apply_manual_reviews(store.database_path, **args)["changed"] == [task]
    with store._connect() as con, con:
        con.execute("""INSERT INTO price_continuity
            (status,prepared_at,started_at,actor,build_commit,manifest,limits,interval_hours)
            SELECT status,prepared_at,started_at,actor,build_commit,manifest,limits,interval_hours
            FROM price_continuity WHERE id=1""")
        con.execute("""INSERT INTO price_continuity_pairs(continuity_id,competitor_id,ean,state)
            SELECT 2,competitor_id,ean,state FROM price_continuity_pairs WHERE continuity_id=1""")
    assert monitor.review_refresh_tick()["processed"] == 0
    assert site.calls == []
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert preview["requests"][0]["action"] == "authorize"
    assert apply_manual_reviews(store.database_path, **args)["changed"] == [task]
    assert apply_manual_reviews(store.database_path, **args)["changed"] == []
    with store._connect() as con:
        assert con.execute("SELECT continuity_id FROM manual_review_recovery_grants").fetchone()[0] == 2
        audits = con.execute("""SELECT previous FROM access_audit WHERE action='recover_manual_review'
            ORDER BY id""").fetchall()
    assert json.loads(audits[-1]["previous"])["previous_grant"]["continuity_id"] == 1


def test_single_request_recovery_does_not_release_the_rest(expanded_review):
    _, store, clock, _, _, url, site, monitor, first = expanded_review
    _legacy_request(store, clock, first)
    with store._connect() as con, con:
        second = con.execute("""INSERT INTO manual_review_refreshes
            (locate_job_id,monitor_job_id,ean,competitor_id,url,actor,reason,status,detail,requested_at,updated_at)
            SELECT locate_job_id,monitor_job_id,ean,competitor_id,url,actor,reason,status,detail,requested_at,
                updated_at FROM manual_review_refreshes WHERE id=?""", (first,)).lastrowid
    args = {"actor": "operator", "note": "Synthetic", "build_commit": "e" * 40,
            "approved": True, "now": clock()}
    assert apply_manual_reviews(store.database_path, request_ids=[first], **args)["changed"] == [first]
    clock.advance(seconds=1)
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        row = con.execute("SELECT status,attempts FROM manual_review_refreshes WHERE id=?", (second,)).fetchone()
    assert row["status"] == "pending" and row["attempts"] == 0
    before = list(site.calls)
    assert url in before
    args["now"] = clock()
    assert apply_manual_reviews(store.database_path, request_ids=[second], **args)["changed"] == [second]
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (second,)).fetchone()[0] == "done"
    assert monitor.review_refresh_tick()["processed"] == 0 and site.calls == before


def test_recovery_reuses_a_later_result_without_a_new_request(expanded_review):
    service, store, clock, cid, ean, url, site, monitor, task = expanded_review
    _legacy_request(store, clock, task)
    clock.advance(seconds=1)
    with service.execution(0, cid, ean, program="continuity", program_id=1):
        assert monitor.run([cid], eans=[ean], max_jobs=1).processed == 1
    calls = list(site.calls)
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert preview["counts"]["already_resolved"] == 1
    result = apply_manual_reviews(store.database_path, actor="operator", note="Reuse", build_commit="e" * 40,
                                  approved=True, now=clock())
    assert result["changed"] == [task] and site.calls == calls
    with store._connect() as con:
        row = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert row["status"] == "done" and "fecha original" in row["detail"]


def test_recovery_does_not_reuse_an_old_result(expanded_review):
    _, store, clock, cid, _, url, site, monitor, task = expanded_review
    _legacy_request(store, clock, task)
    old = clock() - timedelta(minutes=5)
    page = ProductPage(url=url, price=120, currency="EUR", availability="available",
                       includes_tax=True, url_trace={"candidate_url": url})
    sample = {"page": asdict(page), "at": old.isoformat(), "own_price": None, "gap": ""}
    with store._connect() as con, con:
        con.execute("""UPDATE monitor_jobs SET state='done',sample=?,sampled_at=?,last_ok_at=?,valid_until=?,
            not_before=? WHERE competitor_id=?""",
                    (json.dumps(sample), old.isoformat(), old.isoformat(), (old + timedelta(days=1)).isoformat(),
                     (old + timedelta(days=1)).isoformat(), cid))
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert preview["counts"]["recoverable"] == 1 and preview["counts"]["already_resolved"] == 0
    apply_manual_reviews(store.database_path, actor="operator", note="Old result", build_commit="e" * 40,
                         approved=True, now=clock())
    clock.advance(seconds=1)
    assert monitor.review_refresh_tick()["processed"] == 1
    assert url in site.calls


def test_inventory_and_export_include_more_than_100_requests(expanded_review):
    _, store, clock, _, _, _, _, monitor, task = expanded_review
    _legacy_request(store, clock, task)
    with store._connect() as con, con:
        for _ in range(104):
            con.execute("""INSERT INTO manual_review_refreshes
                (locate_job_id,monitor_job_id,ean,competitor_id,url,actor,reason,status,detail,requested_at,updated_at)
                SELECT locate_job_id,monitor_job_id,ean,competitor_id,url,actor,reason,status,detail,requested_at,
                    updated_at FROM manual_review_refreshes WHERE id=?""", (task,))
    assert len(monitor.review_refreshes()) == 100
    complete = monitor.review_refreshes(limit=None)
    assert len(complete) == 105
    exported = pd.read_csv(BytesIO(complete.to_csv(index=False).encode("utf-8-sig")), dtype={"ean": str})
    assert len(exported) == 105 and exported["ean"].eq(complete["ean"].iloc[0]).all()
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert len(preview["requests"]) == 105
    assert preview["counts"] == {"recoverable": 105, "already_resolved": 0, "excluded": 0}
