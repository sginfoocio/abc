"""Offline regressions for accepted review reads and queue fairness."""
from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
from threading import Event

from filelock import FileLock, Timeout
import pandas as pd
import pytest
import requests

from price_monitor import MonitorReport, PriceMonitor
from price_comparator import PriceComparatorStore
from price_recovery import (_corrected_error_rule, _known_recovery_alias_reason, apply_manual_reviews,
                            preview_manual_reviews)
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


def test_opticalh_miu_merged_descriptor_reuses_only_exact_catalogue_identity():
    detail = "Distinto modelo: catálogo MUA06S, ficha GAFASDESOLMIUMIUA06S"
    task = {"status": "error", "monitor_state": "review", "detail": detail, "monitor_detail": detail}
    identity = {"domain": "opticalh.com", "ean": "8056262407691", "brand": "Miu Miu",
                "model": "MU A06S", "color": "14L09Z"}
    rule = _corrected_error_rule(task, identity)
    assert rule == "miu-a06s-merged-descriptor"
    assert _known_recovery_alias_reason(detail, rule)
    for field, value in (("domain", "other.example"), ("ean", "8056262407684"),
                         ("brand", "Other"), ("model", "MU A06S2"), ("color", "14L09X")):
        assert not _corrected_error_rule(task, {**identity, field: value}), field


def test_accepted_review_uses_active_continuity_after_pilot_expiry(expanded_review):
    service, store, clock, cid, ean, url, site, monitor, task = expanded_review

    result = monitor.review_refresh_tick()

    with store._connect() as con:
        refresh = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert result["processed"] == 1, (result, dict(refresh))
    assert refresh["status"] == "done"
    assert url in site.calls
    assert service.report()["status"] == "expired"


@pytest.mark.parametrize("lock_kind", ["pair", "continuity"])
def test_busy_lock_defers_same_approval_without_attempt_then_retries(expanded_review, lock_kind):
    service, store, clock, cid, ean, url, site, monitor, task = expanded_review
    lock = (service.continuity_pair_lock(cid, ean) if lock_kind == "pair"
            else service.continuity_lock())
    with lock:
        result = monitor.review_refresh_tick()
    with store._connect() as con:
        deferred = con.execute("""SELECT status,detail,attempts,http_requests,retry_at
            FROM manual_review_refreshes WHERE id=?""", (task,)).fetchone()
    assert result["deferred"] == 1
    assert deferred["status"] == "deferred" and "bloqueo" in deferred["detail"].lower()
    assert deferred["attempts"] == deferred["http_requests"] == 0
    assert deferred["retry_at"] == (clock() + timedelta(seconds=30)).isoformat(timespec="microseconds")
    assert site.calls == []
    preview = preview_manual_reviews(store.database_path, now=clock())
    item = next(row for row in preview["requests"] if row["id"] == task)
    assert item["category"] == "recoverable" and item["action"] == "already_queued"
    recovery = apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Previsualización idempotente",
        build_commit="e" * 40, approved=True, now=clock())
    assert recovery["changed"] == []
    with store._connect() as con:
        unchanged = con.execute("SELECT status,retry_at FROM manual_review_refreshes WHERE id=?",
                                (task,)).fetchone()
    assert unchanged["status"] == "deferred" and unchanged["retry_at"] == deferred["retry_at"]

    clock.advance(seconds=30)
    result = monitor.review_refresh_tick()
    with store._connect() as con:
        retried = con.execute("""SELECT status,attempts,http_requests FROM manual_review_refreshes
            WHERE id=?""", (task,)).fetchone()
    assert result["processed"] == 1 and retried["status"] == "done"
    assert retried["attempts"] == 1 and retried["http_requests"] > 0
    assert url in site.calls


def test_preview_reuses_an_active_manual_read_without_requeueing(expanded_review):
    _, store, clock, _, _, _, site, _, task = expanded_review
    lease_until = (clock() + timedelta(minutes=2)).isoformat()
    with store._connect() as con, con:
        con.execute("""UPDATE manual_review_refreshes SET status='running',lease_until=? WHERE id=?""",
                    (lease_until, task))
    preview = preview_manual_reviews(store.database_path, now=clock())
    item = next(row for row in preview["requests"] if row["id"] == task)
    assert item["category"] == "recoverable" and item["action"] == "already_queued"
    assert "en curso" in item["reason"]
    assert site.calls == []


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
        trace = json.loads(con.execute("""SELECT current FROM access_audit
            WHERE scope='manual_review' AND scope_id=? AND action='record_read_trace'""", (str(task),)).fetchone()[0])
    assert row["status"] == "error" and "variante" in row["detail"].lower()
    assert trace["receipt_ids"] and trace["url_sha256"]["candidate_url"]


def _accepted_ft1044_review(trial):
    service, store, access, locator, monitor, clock, cid, site, _ = trial
    _, _, _, _, ean, old_url, _ = trial_helpers._diagnostic_review(trial)
    url = site.product("Tom Ford", "FT1044", "001", gtin=ean)
    with store._connect() as con, con:
        con.execute("UPDATE products SET brand='Tom Ford',model='FT1044',color='001' WHERE ean=?", (ean,))
        con.execute("""UPDATE competitor_product_urls SET url=?,model_code='FT1044',color_code='001',
            match_status='probable_match',status='probable'
            WHERE competitor_id=? AND ean=? AND url=?""", (url, cid, ean, old_url))
        con.execute("UPDATE locate_jobs SET url=?,state='review' WHERE competitor_id=? AND ean=?",
                    (url, cid, ean))
    for shop in store.competitors().to_dict("records"):
        for method in ("ficha", "sitemap"):
            access.set_method(shop["id"], method, "authorized", actor="operator", note="Synthetic authorization")
    service.heartbeat()
    service.expand_continuity(actor="operator", approved=True, build_commit="d" * 40,
                              note="Synthetic 14-competitor scope")
    task = locator.review(ean, cid, url, accept=True, actor="operator", note="Aprobación vigente",
                          update_price=True)
    return service, store, clock, cid, ean, url, site, monitor, task


def _record_corrected_amevista_error(review, html):
    import base64
    import hashlib

    _, store, clock, cid, ean, url, site, _, task = review
    detail = "Distinto modelo: catálogo FT1044, ficha BRONSON, FT1044"
    captured_at = (clock() + timedelta(seconds=1)).isoformat()
    payload = html.encode("utf-8")
    current = {
        "competitor_id": cid,
        "ean": ean,
        "requested_url": url,
        "final_url": url,
        "captured_at": captured_at,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "html_base64": base64.b64encode(payload).decode("ascii"),
    }
    with store._connect() as con, con:
        refresh = con.execute("SELECT monitor_job_id FROM manual_review_refreshes WHERE id=?",
                              (task,)).fetchone()
        con.execute("""UPDATE manual_review_refreshes SET status='error',detail=?,finished_at=?,updated_at=?
            WHERE id=?""", (detail, clock().isoformat(), clock().isoformat(), task))
        con.execute("UPDATE monitor_jobs SET state='review',detail=? WHERE id=?",
                    (detail, refresh["monitor_job_id"]))
        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
            VALUES('competitor',?,'capture_continuity_html','',?,'operator','synthetic retained HTML',?)""",
                    (cid, json.dumps(current), captured_at))
    return store, clock, cid, ean, url, site, task


def test_corrected_error_replays_exact_saved_html_and_is_idempotent(trial):
    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, cid, ean, _, site, task = _record_corrected_amevista_error(review, html)
    now = clock() + timedelta(seconds=2)

    preview = preview_manual_reviews(store.database_path, request_ids=[task], now=now)
    item = preview["requests"][0]
    assert item["action"] == "recover_corrected_error", item
    assert item["evidence"]["approval_audits"] == 1
    assert item["evidence"]["artifact_evaluations"][0]["result"] == "exact_identity"
    assert item["evidence"]["artifact_evaluations"][0]["offer_comparable"] is False
    assert item["http_requests"] == 0
    assert site.calls == []

    applied = apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Revisión local",
        build_commit="f" * 40, approved=True, now=now,
    )
    assert applied["changed"] == [task]
    with store._connect() as con:
        refresh_status = con.execute(
            "SELECT status FROM manual_review_refreshes WHERE id=?", (task,)
        ).fetchone()[0]
        monitor_state = con.execute(
            "SELECT state FROM monitor_jobs WHERE competitor_id=? AND ean=?", (cid, ean)
        ).fetchone()[0]
        request_count = con.execute(
            "SELECT COUNT(*) FROM manual_review_refreshes WHERE competitor_id=? AND ean=?",
            (cid, ean),
        ).fetchone()[0]
    assert refresh_status == "pending"
    assert monitor_state == "pending"
    assert request_count == 1
    repeat = preview_manual_reviews(store.database_path, request_ids=[task], now=now)
    assert repeat["requests"][0]["action"] == "already_queued"
    assert apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Repetición idempotente",
        build_commit="f" * 40, approved=True, now=now,
    )["changed"] == []
    assert site.calls == []


def test_versioned_corrected_error_can_follow_legacy_grant_once(trial):
    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, cid, ean, url, site, task = _record_corrected_amevista_error(review, html)
    with store._connect() as con, con:
        continuity_id = con.execute(
            "SELECT id FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()[0]
        con.execute("""DELETE FROM access_audit WHERE scope='product_review'
            AND action='candidate_confirmed' AND scope_id=?""",
                    (f"{cid}:{ean}:{url}",))
        con.execute("""INSERT INTO manual_review_recovery_grants
            (refresh_id,continuity_id,actor,note,build_commit,granted_at)
            VALUES(?,?,'operator','legacy recovery','a','2026-01-01T00:00:00+00:00')""",
                    (task, continuity_id))

    now = clock() + timedelta(seconds=2)
    preview = preview_manual_reviews(store.database_path, request_ids=[task], now=now)
    item = preview["requests"][0]
    assert item["action"] == "recover_corrected_error", (
        item["reason"], item["identity_valid"], item["approval_audits"],
        item["monitor_state"], item["attempts"])
    assert item["evidence"]["approval_source"] == "locate_event"
    assert item["approval_audits"] == 1
    assert item["recovery_key"] and item["evidence"]["recovery_version"] == "approved-read-errors-2"
    applied = apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Nueva regla corregida",
        build_commit="f" * 40, approved=True, now=now,
    )
    assert applied["changed"] == [task]
    with store._connect() as con:
        grant = con.execute(
            "SELECT actor,note,build_commit,granted_at FROM manual_review_recovery_grants WHERE refresh_id=?",
            (task,)).fetchone()
        event = con.execute("""SELECT recovery_key,approval_ref,rules_version,recovery_rule,
            identity_digest,evidence_digest,historical_error,outcome FROM manual_review_recovery_events
            WHERE refresh_id=?""", (task,)).fetchone()
    assert tuple(grant) == ("operator", "legacy recovery", "a", "2026-01-01T00:00:00+00:00")
    assert event["recovery_key"] == item["recovery_key"]
    assert event["approval_ref"] == item["evidence"]["approval_ref"]
    assert event["rules_version"] == "approved-read-errors-2"
    assert event["outcome"] == "queued" and event["historical_error"]

    queued = preview_manual_reviews(store.database_path, request_ids=[task], now=now)["requests"][0]
    assert queued["action"] == "already_queued"
    assert apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Repetición",
        build_commit="f" * 40, approved=True, now=now,
    )["changed"] == []
    with store._connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM manual_review_recovery_events WHERE refresh_id=?", (task,)
        ).fetchone()[0] == 1
    assert site.calls == []


def test_failed_versioned_recovery_is_reported_as_already_recovered(trial):
    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, cid, ean, _, site, task = _record_corrected_amevista_error(review, html)
    now = clock() + timedelta(seconds=2)
    assert apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Primera recuperación",
        build_commit="f" * 40, approved=True, now=now,
    )["changed"] == [task]

    with store._connect() as con, con:
        monitor_job_id = con.execute(
            "SELECT monitor_job_id FROM manual_review_refreshes WHERE id=?", (task,)
        ).fetchone()[0]
        con.execute("""UPDATE manual_review_refreshes
            SET status='error',detail='Error distinto de la lectura nueva' WHERE id=?""", (task,))
        con.execute("""UPDATE monitor_jobs
            SET state='review',detail='Error distinto de la lectura nueva' WHERE id=?""", (monitor_job_id,))

    item = preview_manual_reviews(store.database_path, request_ids=[task], now=now)["requests"][0]
    assert item["category"] == "already_resolved"
    assert "recuperación versionada ya se aplicó" in item["reason"]
    repeated = apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Repetición tras error",
        build_commit="f" * 40, approved=True, now=now,
    )
    with store._connect() as con:
        recovery_events = con.execute(
            "SELECT COUNT(*) FROM manual_review_recovery_events WHERE refresh_id=?", (task,)
        ).fetchone()[0]
    assert repeated["changed"] == [] and recovery_events == 1
    assert site.calls == []


def test_versioned_recovery_event_authorizes_new_continuity_without_replacing_grant(trial):
    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, _cid, _ean, url, site, task = _record_corrected_amevista_error(review, html)
    monitor = review[7]
    with store._connect() as con, con:
        con.execute("""INSERT INTO manual_review_recovery_grants
            (refresh_id,continuity_id,actor,note,build_commit,granted_at)
            VALUES(?,1,'operator','legacy recovery','a','2026-01-01T00:00:00+00:00')""", (task,))
        con.execute("""INSERT INTO price_continuity
            (status,prepared_at,started_at,actor,build_commit,manifest,limits,interval_hours)
            SELECT status,prepared_at,started_at,actor,build_commit,manifest,limits,interval_hours
            FROM price_continuity WHERE id=1""")
        con.execute("""INSERT INTO price_continuity_pairs(continuity_id,competitor_id,ean,state)
            SELECT 2,competitor_id,ean,state FROM price_continuity_pairs WHERE continuity_id=1""")

    now = clock() + timedelta(seconds=2)
    preview = preview_manual_reviews(store.database_path, request_ids=[task], now=now)["requests"][0]
    assert preview["action"] == "recover_corrected_error", preview["reason"]
    assert apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="Nueva continuidad",
        build_commit="f" * 40, approved=True, now=now,
    )["changed"] == [task]
    with store._connect() as con, con:
        grant = con.execute(
            "SELECT continuity_id,build_commit FROM manual_review_recovery_grants WHERE refresh_id=?",
            (task,)).fetchone()
        recovery = con.execute(
            "SELECT continuity_id FROM manual_review_recovery_events WHERE refresh_id=?", (task,)).fetchone()
    assert tuple(grant) == (1, "a")
    assert recovery["continuity_id"] == 2
    result = monitor.review_refresh_tick()
    with store._connect() as con:
        status = con.execute(
            "SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0]
    assert result["processed"] == 1 and status == "done", (result, status)
    assert site.calls.count(url) == 1


def test_versioned_corrected_error_preserves_monitor_attempt_limit(trial):
    from price_locator import MAX_ATTEMPTS

    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, cid, ean, _, site, task = _record_corrected_amevista_error(review, html)
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET attempts=? WHERE competitor_id=? AND ean=?",
                    (MAX_ATTEMPTS, cid, ean))

    now = clock() + timedelta(seconds=2)
    item = preview_manual_reviews(store.database_path, request_ids=[task], now=now)["requests"][0]
    assert item["category"] == "excluded"
    assert item["reason"] == "Máximo de intentos del monitor alcanzado"
    result = apply_manual_reviews(
        store.database_path, request_ids=[task], actor="operator", note="No superar intentos",
        build_commit="f" * 40, approved=True, now=now,
    )
    assert result["changed"] == []
    with store._connect() as con:
        status = con.execute(
            "SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0]
        attempts = con.execute(
            "SELECT attempts FROM monitor_jobs WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone()[0]
        recovery_events = con.execute(
            "SELECT COUNT(*) FROM manual_review_recovery_events WHERE refresh_id=?", (task,)).fetchone()[0]
    assert status == "error" and attempts == MAX_ATTEMPTS and recovery_events == 0
    assert site.calls == []


def test_corrected_error_with_contradictory_saved_html_stays_excluded(trial):
    review = _accepted_ft1044_review(trial)
    _, _, _, _, ean, url, site, _, _ = review
    conflicting_url = site.product("RayBan", "RB123", "001", gtin=ean)
    store, clock, _, _, _, _, task = _record_corrected_amevista_error(review, site.pages[conflicting_url])
    preview = preview_manual_reviews(
        store.database_path, request_ids=[task], now=clock() + timedelta(seconds=2))
    item = preview["requests"][0]
    assert item["category"] == "excluded", (
        item["reason"], item["evidence"].get("artifact_evaluations"))
    assert "contradice" in item["reason"].lower() or "distinto" in item["reason"].lower()
    assert item["action"] == ""
    assert site.calls == []


def test_corrected_error_without_retained_artifact_does_not_claim_offline_replay(trial):
    review = _accepted_ft1044_review(trial)
    html = review[6].pages[review[5]]
    store, clock, cid, _, url, site, task = _record_corrected_amevista_error(review, html)
    with store._connect() as con, con:
        con.execute("""DELETE FROM access_audit WHERE scope='competitor' AND scope_id=?
            AND action='capture_continuity_html' AND json_extract(current,'$.requested_url')=?""",
                    (cid, url))
    preview = preview_manual_reviews(
        store.database_path, request_ids=[task], now=clock() + timedelta(seconds=2))
    item = preview["requests"][0]
    assert item["action"] == "recover_corrected_error"
    assert item["evidence"]["artifact_evaluations"] == []
    assert "Sin artefacto reevaluable suficiente" in item["reason"]
    assert site.calls == []


def test_sanitized_diagnostic_artifact_is_replayed_without_becoming_an_observation(trial):
    from price_diagnostic import sanitise
    from price_web_fetch import Fetched

    review = _accepted_ft1044_review(trial)
    alias_url = review[6].product("Tom Ford", "Bronson", "001", gtin=review[4])
    html = review[6].pages[alias_url]
    store, clock, cid, ean, url, site, task = _record_corrected_amevista_error(review, html)
    with store._connect() as con, con:
        con.execute("""DELETE FROM access_audit WHERE scope='competitor' AND scope_id=?
            AND action='capture_continuity_html' AND json_extract(current,'$.requested_url')=?""",
                    (cid, url))
    metadata, artifact = sanitise(Fetched(url, 200, html.encode(), url, []), 1024 * 1024)
    metadata["captured_at"] = (clock() + timedelta(seconds=1)).isoformat()
    metadata["status"] = "captured"
    with store._connect() as con, con:
        continuity_id = con.execute(
            "SELECT id FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()["id"]
        con.execute("""INSERT INTO price_diagnostic_requests(
            continuity_id,competitor_id,ean,url,actor,note,build_commit,status,created_at,
            finished_at,result,artifact,expires_at
        ) VALUES(?,?,?,?,?,'synthetic diagnostic','a','captured',?,?,?,?,?)""",
                    (continuity_id, cid, ean, url, "operator", clock().isoformat(),
                     metadata["captured_at"], json.dumps(metadata), artifact,
                     (clock() + timedelta(days=1)).isoformat()))
    preview = preview_manual_reviews(
        store.database_path, request_ids=[task], now=clock() + timedelta(seconds=2))
    item = preview["requests"][0]
    assert item["action"] == "recover_corrected_error"
    assert item["evidence"]["artifact_evaluations"][0]["source"] == "sanitized_jsonld"
    assert item["evidence"]["artifact_evaluations"][0]["result"] == "exact_identity"
    assert item["status"] == "error"
    assert site.calls == []


def test_approved_read_recovery_ui_previews_without_http(expanded_review):
    from streamlit.testing.v1 import AppTest

    _, store, _, _, _, _, site, _, _ = expanded_review
    names = dict(zip(store.competitors()["id"], store.competitors()["name"]))
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_approved_read_recovery\n"
        "from price_comparator import PriceComparatorStore\n"
        f"store = PriceComparatorStore({str(store.database_path)!r})\n"
        f"names = {names!r}\n"
        "_render_approved_read_recovery(store, names)\n"
    ).run()
    app.button(key="approved_read_preview_button").click().run()
    assert not app.exception
    assert any(metric.label == "Reprogramaciones" for metric in app.metric)
    assert site.calls == []


def test_accepted_edel_base_url_records_checked_variant_without_new_http(tmp_path):
    from price_monitor import PriceMonitor
    from tests.test_price_locator import Site, _env
    from tests.test_price_web_competitor_pages import _edel_variant_node

    site = Site(base="https://www.edel-optics.es")
    store, _, locator, clock, cid = _env(tmp_path, site, name="Edel-Optics", methods=("ficha",), template=None)
    ean = "8056597875349"
    url = site.base + "/PR-27ZS-16K08Z-de-Prada.html"
    node = _edel_variant_node("1907958", ean, 243.5, "InStock")
    site.pages[url] = ('<input type="radio" id="prd1907958" checked>'
                       '<script type="application/ld+json">' + json.dumps(node) + '</script>')
    _watch(store, [(ean, "Prada", "PR 27ZS", "16K08Z", "54-21")])
    locator.enqueue([cid], [ean])
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET market='ES',currency='EUR' WHERE id=?", (cid,))
        con.execute("""UPDATE competitor_web_config SET tax_policy='included',
            sales_market='ES',market_verified=1,market_evidence_url='synthetic://evidence',
            market_note='Synthetic ES market' WHERE competitor_id=?""", (cid,))
        con.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,match_status,status,verified_at)
            VALUES(?,?,?,'manual_review','probable',?)""", (cid, ean, url, clock().isoformat()))
        con.execute("UPDATE locate_jobs SET state='review',url=? WHERE competitor_id=? AND ean=?", (url, cid, ean))
    task = locator.review(ean, cid, url, accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    monitor = PriceMonitor(store, clock=clock, transport=site, sleep=lambda seconds: clock.advance(seconds=seconds))
    monitor.review_refresh_tick()
    with store._connect() as con:
        request = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        job = con.execute("SELECT sample,last_offer_id FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                          (cid, ean)).fetchone()
        offer = con.execute("SELECT url,captured_at FROM offers WHERE id=?", (job["last_offer_id"],)).fetchone()
    assert request["status"] == "done", request["detail"]
    sample = json.loads(job["sample"])
    assert sample["page"]["url"] == url
    assert sample["page"]["url_trace"]["offer_variant_url"] == url + "?selectedSize=1907958"
    assert offer and offer["url"] == url + "?selectedSize=1907958"
    assert site.calls.count(url) == 1
    assert not any("?selectedSize=" in call for call in site.calls)
    calls = list(site.calls)
    monitor.review_refresh_tick()
    assert site.calls == calls
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone()[0] == 1


def test_diagnostic_can_capture_verified_url_with_failed_read(expanded_review, monkeypatch):
    service, store, _, cid, ean, url, site, monitor, task = expanded_review
    from price_monitor import select_product_variant

    def other_variant(page, product):
        return replace(select_product_variant(page, product), url=url + "?variant=other")

    monkeypatch.setattr("price_monitor.select_product_variant", other_variant)
    monkeypatch.setattr("price_diagnostic.require_private_database", lambda path: None)
    monitor.review_refresh_tick()
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == "error"
        assert con.execute("SELECT status FROM competitor_product_urls WHERE competitor_id=? AND ean=? AND url=?",
                           (cid, ean, url)).fetchone()[0] == "verified"
    assert url in service.diagnostic_candidates(cid, ean)
    request = service.request_diagnostic(cid, ean, url, actor="operator", note="review failed read",
                                         build_commit="b" * 40, approved=True)
    service.diagnostic_tick()
    with store._connect() as con:
        row = con.execute("SELECT status,result FROM price_diagnostic_requests WHERE id=?", (request["id"],)).fetchone()
        assert row["status"] == "captured", row["result"]
        assert con.execute("SELECT status FROM competitor_product_urls WHERE competitor_id=? AND ean=? AND url=?",
                           (cid, ean, url)).fetchone()[0] == "verified"
    with store._connect() as con, con:
        con.execute("UPDATE competitor_product_urls SET color_code='OTHER' WHERE competitor_id=? AND ean=? AND url=?",
                    (cid, ean, url))
    assert url not in service.diagnostic_candidates(cid, ean)


def test_rejected_post_acceptance_read_keeps_human_identity(expanded_review, monkeypatch):
    service, store, _, cid, ean, url, _, monitor, task = expanded_review
    from price_monitor import select_product_variant

    def contradicted(page, product):
        return replace(select_product_variant(page, product), color="OTHER999")

    monkeypatch.setattr("price_monitor.select_product_variant", contradicted)
    monitor.review_refresh_tick()
    with store._connect() as con:
        identity = con.execute("""SELECT status,source,note FROM competitor_product_urls
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url)).fetchone()
        request = con.execute("SELECT status,detail FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?", (ean, cid)).fetchone()[0] == 0
    assert identity["status"] == "verified" and identity["source"] == "user"
    assert "Synthetic review" in identity["note"]
    assert request["status"] == "error" and "color" in request["detail"].lower()
    assert url in service.diagnostic_candidates(cid, ean)


def test_identity_change_before_offer_commit_never_creates_observation(expanded_review, monkeypatch):
    _, store, _, cid, ean, url, _, monitor, task = expanded_review
    original = WebPriceSearch.record

    def change_identity(search, outcome, *, validate=None, review_sample=None):
        with store._connect() as con, con:
            con.execute("""UPDATE competitor_product_urls SET color_code='OTHER'
                WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
        return original(search, outcome, validate=validate, review_sample=review_sample)

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
        row = con.execute("SELECT status,detail,http_requests,attempts FROM manual_review_refreshes WHERE id=?",
                          (task,)).fetchone()
    assert row["status"] == "blocked" and "reserva" in row["detail"]
    assert row["http_requests"] == 2
    assert row["attempts"] == 0


def test_restart_after_product_response_before_sample_never_repeats_http(tmp_path, monkeypatch):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))

    def interrupt(*args, **kwargs):
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr("price_monitor.extract_product", interrupt)
        with pytest.raises(Crash):
            monitor.review_refresh_tick()
    assert site.calls.count(urls[0]) == 1
    clock.advance(minutes=11)
    monitor.review_refresh_tick()
    assert site.calls.count(urls[0]) == 1
    with store._connect() as con:
        row = con.execute("SELECT status,detail,attempts FROM manual_review_refreshes WHERE id=?",
                          (task,)).fetchone()
        assert row["status"] == "blocked" and "reserva" in row["detail"] and row["attempts"] == 1
        assert con.execute("SELECT status FROM competitor_product_urls WHERE competitor_id=? AND ean=?",
                           (cid, ean)).fetchone()[0] == "verified"


@pytest.mark.parametrize("existing_offer", [False, True])
def test_restart_after_offer_import_does_not_count_an_extra_observation(tmp_path, monkeypatch, existing_offer):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                   update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    clock.advance(microseconds=1)
    original = WebPriceSearch.record

    def interrupt_after_import(search, outcome, *, validate=None, review_sample=None):
        if existing_offer:
            assert original(search, outcome, validate=validate) == ""
        assert original(search, outcome, validate=validate, review_sample=review_sample) == ""
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(WebPriceSearch, "record", interrupt_after_import)
        with pytest.raises(Crash):
            monitor.review_refresh_tick()
    with store._connect() as con:
        before = [tuple(row) for row in con.execute(
            "SELECT last_offer_id,last_ok_at,checks FROM offer_checks WHERE competitor_id=? AND ean=?", (cid, ean))]
        offers_before = con.execute("SELECT COUNT(*) FROM offers WHERE competitor_id=? AND ean=?",
                                    (cid, ean)).fetchone()[0]
    clock.advance(minutes=11)
    monitor.review_refresh_tick()
    with store._connect() as con:
        after = [tuple(row) for row in con.execute(
            "SELECT last_offer_id,last_ok_at,checks FROM offer_checks WHERE competitor_id=? AND ean=?", (cid, ean))]
        offers_after = con.execute("SELECT COUNT(*) FROM offers WHERE competitor_id=? AND ean=?",
                                   (cid, ean)).fetchone()[0]
    assert site.calls.count(urls[0]) == 1
    assert offers_after == offers_before
    assert after == before
    assert len(before) == int(existing_offer)


def test_rejected_import_does_not_record_the_sample_receipt(tmp_path):
    store, locator, _, clock, cid, _, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    frame = pd.DataFrame([{"EAN": ean, "Competidor": cid, "Precio": "invalid", "Moneda": "EUR",
                           "Mercado": "ES", "URL": urls[0], "Disponibilidad": "available",
                           "Estado coincidencia": "exact_match", "Evidencia": "Synthetic", "Método": "web:monitor"}])
    report = store.import_offers(frame, validate=lambda con: None,
                                 review_sample=(task, "synthetic", clock().isoformat(), "synthetic", "{}"))
    assert report.rejected
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_sample_events").fetchone()[0] == 0


def test_observation_identity_is_idempotent_and_preserves_legacy_receipts(tmp_path):
    store, locator, _, clock, cid, _, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="operator", note="Synthetic review",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    frame = pd.DataFrame([{"EAN": ean, "Competidor": cid, "Precio": 120, "Moneda": "EUR",
                           "Mercado": "ES", "URL": urls[0], "Disponibilidad": "available",
                           "Estado coincidencia": "exact_match", "Evidencia": "Synthetic", "Método": "web:monitor"}])
    at = clock().isoformat()
    with store._connect() as con, con:
        con.execute("INSERT INTO manual_review_recorded_samples VALUES(?,?,?)", (task, at, "old-digest"))
    store = PriceComparatorStore(store.database_path)
    assert store.import_offers(frame, validate=lambda con: None,
                               review_sample=(task, f"legacy:{at}", at, "old-digest", "{}")).imported == 0
    observation = (task, "new-execution", at, "new-digest", "{}")
    store.import_offers(frame, validate=lambda con: None, review_sample=observation)
    with store._connect() as con:
        before = [tuple(row) for row in con.execute("SELECT * FROM offer_checks")]
    store.import_offers(frame, validate=lambda con: None, review_sample=observation)
    with pytest.raises(ValueError, match="muestra registrada"):
        store.import_offers(frame, validate=lambda con: None,
                            review_sample=(task, "new-execution", "2099-01-01", "new-digest", "{}"))
    with store._connect() as con:
        assert [tuple(row) for row in con.execute("SELECT * FROM offer_checks")] == before
        assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_sample_events WHERE refresh_id=?",
                           (task,)).fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_samples WHERE refresh_id=?",
                           (task,)).fetchone()[0] == 1


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
    item = next(row for row in preview["requests"] if row["id"] == task)
    assert preview["counts"]["recoverable"] == 1
    assert item["action"] == "local_reevaluation"
    assert item["classification"] == "2_reevaluable_local"
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


@pytest.fixture
def approval_without_request(trial):
    service, store, clock, cid, ean, url, site = _diagnostic_review(trial)
    access = trial[2]
    for shop in store.competitors().to_dict("records"):
        for method in ("ficha", "sitemap"):
            access.set_method(shop["id"], method, "authorized", actor="operator", note="Synthetic authorization")
    service.heartbeat()
    service.expand_continuity(actor="operator", approved=True, build_commit="d" * 40,
                              note="Synthetic scope expansion")
    locator = trial[3]
    assert preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"] == []

    with store._connect() as con, con:
        con.execute("UPDATE products SET updated_at=? WHERE ean=?",
                    ((clock() - timedelta(days=1)).isoformat(), ean))
    row = _candidate(locator, ean)
    assert locator.review(ean, cid, url, accept=True, actor="operator", note="Synthetic review",
                          update_price=False, expected_candidate=_expected(row)) is None
    return service, store, clock, cid, ean, url, site, trial[4]


def test_preview_inventories_current_approval_without_matching_request(approval_without_request):
    _, store, clock, cid, ean, url, site, _ = approval_without_request
    preview = preview_manual_reviews(store.database_path, now=clock())
    assert preview["requests"] == []
    assert len(preview["approvals_without_request"]) == 1
    approval = preview["approvals_without_request"][0]
    assert (approval["competitor_id"], approval["ean"], approval["url"]) == (cid, ean, url)
    assert approval["approval_audits"] == 1
    assert approval["active_requests"] == 0
    assert approval["action"] == "create", approval["reason"]
    assert site.calls == []


def test_recover_approval_without_request_to_comparator(approval_without_request):
    _, store, clock, cid, ean, url, site, monitor = approval_without_request
    approval = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]
    args = {"actor": "operator", "note": "Synthetic recovery", "build_commit": "e" * 40,
            "approved": True, "now": clock(), "approval_ids": [approval["approval_id"]], "request_ids": []}
    result = apply_manual_reviews(store.database_path, **args)
    task = result["approval_results"][0]["request_id"]
    assert result["approval_results"][0]["action"] == "created"
    assert apply_manual_reviews(store.database_path, **args)["approval_results"][0]["request_id"] == task
    assert site.calls == []
    own = pd.DataFrame([{"EAN": ean, "Precio": 150, "Moneda": "EUR", "Mercado": "ES",
                         "Fecha consulta": clock().isoformat(), "Origen": "Synthetic"}])
    assert store.import_own_prices(own).imported == 1
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        refresh = con.execute("SELECT * FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
        assert refresh["status"] == "done" and refresh["attempts"] == 1
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes WHERE ean=? AND competitor_id=?",
                           (ean, cid)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='recover_approved_identity'").fetchone()[0] == 1
    assert site.calls.count(url) == 1
    assert not store.current_offers(now=clock()).query("ean == @ean and competitor_id == @cid").empty


def _recover_approval(store, clock, approval_id):
    return apply_manual_reviews(store.database_path, actor="operator", note="Synthetic recovery",
                                build_commit="e" * 40, approved=True, now=clock(),
                                approval_ids=[approval_id], request_ids=[])


def test_recover_approval_reuses_existing_sample_without_http(approval_without_request):
    service, store, clock, cid, ean, url, site, monitor = approval_without_request
    clock.advance(seconds=1)
    with service.execution(0, cid, ean, program="continuity", program_id=1):
        assert monitor.run([cid], eans=[ean], max_jobs=1).processed == 1
    calls = list(site.calls)
    with store._connect() as con, con:
        sampled = con.execute("SELECT sampled_at FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                              (cid, ean)).fetchone()[0]
        con.execute("UPDATE monitor_jobs SET attempts=999 WHERE competitor_id=? AND ean=?", (cid, ean))
    item = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]
    assert item["action"] == "reuse_saved_sample", item["reason"]
    task = _recover_approval(store, clock, item["approval_id"])["approval_results"][0]["request_id"]
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        result = con.execute("SELECT status,attempts,http_requests,detail FROM manual_review_refreshes WHERE id=?",
                             (task,)).fetchone()
        assert result["status"] == "done" and result["attempts"] == result["http_requests"] == 0
        assert sampled in result["detail"]
    assert site.calls == calls and url in calls


def test_recover_approval_reuses_compatible_sample_from_before_approval(approval_without_request, trial):
    service, store, clock, cid, ean, url, site, monitor = approval_without_request
    clock.advance(seconds=1)
    with service.execution(0, cid, ean, program="continuity", program_id=1):
        assert monitor.run([cid], eans=[ean], max_jobs=1).processed == 1
    calls = list(site.calls)
    with store._connect() as con, con:
        sampled = con.execute("SELECT sampled_at FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                              (cid, ean)).fetchone()[0]
        con.execute("""UPDATE competitor_product_urls SET status='probable',source='auto_watch'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
        con.execute("UPDATE locate_jobs SET state='review' WHERE competitor_id=? AND ean=?", (cid, ean))
    clock.advance(seconds=1)
    locator = trial[3]
    locator.review(ean, cid, url, accept=True, actor="operator", note="New synthetic approval",
                   update_price=False, expected_candidate=_expected(_candidate(locator, ean)))
    item = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]
    assert item["action"] == "reuse_saved_sample", item["reason"]
    task = _recover_approval(store, clock, item["approval_id"])["approval_results"][0]["request_id"]
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        result = con.execute("SELECT status,attempts,http_requests,detail FROM manual_review_refreshes WHERE id=?",
                             (task,)).fetchone()
    assert result["status"] == "done" and result["attempts"] == result["http_requests"] == 0
    assert sampled in result["detail"] and site.calls == calls


def test_concurrent_recovery_creates_one_request(approval_without_request):
    _, store, clock, cid, ean, _, site, _ = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]

    def recover():
        try:
            return _recover_approval(store, clock, approval_id)
        except Timeout:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(), range(2)))
    assert any(result is not None for result in results)
    final = _recover_approval(store, clock, approval_id)
    with store._connect() as con:
        rows = con.execute("SELECT id FROM manual_review_refreshes WHERE competitor_id=? AND ean=?",
                           (cid, ean)).fetchall()
    assert len(rows) == 1 and final["approval_results"][0]["request_id"] == rows[0]["id"]
    assert site.calls == []


@pytest.mark.parametrize("change", ["superseded", "contradiction"])
def test_recovery_excludes_changed_approval(approval_without_request, change):
    _, store, clock, cid, ean, url, site, _ = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]
    with store._connect() as con, con:
        if change == "superseded":
            con.execute("UPDATE competitor_product_urls SET status='rejected' WHERE competitor_id=? AND ean=? AND url=?",
                        (cid, ean, url))
        else:
            con.execute("UPDATE competitor_product_urls SET color_code='other' WHERE competitor_id=? AND ean=? AND url=?",
                        (cid, ean, url))
    result = _recover_approval(store, clock, approval_id)["approval_results"][0]
    assert result["action"] == "excluded"
    assert ("sustituida" if change == "superseded" else "Contradicción") in result["reason"]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes").fetchone()[0] == 0
    assert site.calls == []


def test_recovery_excludes_future_sample_and_unverifiable_product(approval_without_request):
    _, store, clock, cid, ean, url, site, _ = approval_without_request
    with store._connect() as con, con:
        con.execute("""INSERT INTO monitor_jobs(ean,competitor_id,url,state,sampled_at,updated_at)
            VALUES(?,?,?,'done',?,?)""",
                    (ean, cid, url, (clock() + timedelta(days=1)).isoformat(), clock().isoformat()))
    item = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]
    assert item["action"] == "excluded" and "futura" in item["reason"]
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET sampled_at=NULL WHERE competitor_id=? AND ean=?", (cid, ean))
        con.execute("UPDATE products SET updated_at=? WHERE ean=?",
                    ((clock() + timedelta(seconds=1)).isoformat(), ean))
    result = _recover_approval(store, clock, item["approval_id"])["approval_results"][0]
    assert result["action"] == "excluded" and "histórica" in result["reason"]
    assert site.calls == []


def test_interrupted_existing_monitor_reservation_is_not_repeated(approval_without_request):
    _, store, clock, cid, ean, url, site, monitor = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]
    task = _recover_approval(store, clock, approval_id)["approval_results"][0]["request_id"]
    with store._connect() as con, con:
        con.execute("""UPDATE monitor_jobs SET state='running',lease_until=?,updated_at=?
            WHERE competitor_id=? AND ean=?""",
                    ((clock() - timedelta(seconds=1)).isoformat(), clock().isoformat(), cid, ean))
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,url,reserved_at)
            VALUES('competitor',?,?,'ficha','monitor',?,?,?)""", (cid, cid, ean, url, clock().isoformat()))
    result = monitor.review_refresh_tick()
    with store._connect() as con:
        row = con.execute("SELECT status,detail,attempts FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert row["status"] == "blocked" and "incierto" in row["detail"] and row["attempts"] == 0
    assert result["errors"] and site.calls == []


def test_recovered_approval_defers_busy_lock_then_retries(approval_without_request):
    service, store, clock, _, _, url, site, monitor = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]
    task = _recover_approval(store, clock, approval_id)["approval_results"][0]["request_id"]
    with service.continuity_lock():
        assert monitor.review_refresh_tick()["deferred"] == 1
    with store._connect() as con:
        row = con.execute("SELECT status,retry_at,attempts,http_requests FROM manual_review_refreshes WHERE id=?",
                          (task,)).fetchone()
    assert row["status"] == "deferred" and row["retry_at"]
    assert row["attempts"] == row["http_requests"] == 0 and site.calls == []
    assert _recover_approval(store, clock, approval_id)["approval_results"][0]["request_id"] == task
    clock.advance(seconds=30)
    assert monitor.review_refresh_tick()["processed"] == 1
    assert site.calls.count(url) == 1


def test_recovered_approval_waits_for_existing_monitor(approval_without_request):
    service, store, clock, cid, ean, url, site, monitor = approval_without_request
    with store._connect() as con, con:
        con.execute("""INSERT INTO monitor_jobs(ean,competitor_id,url,state,lease_until,updated_at)
            VALUES(?,?,?,'running',?,?)""",
                    (ean, cid, url, (clock() + timedelta(minutes=2)).isoformat(), clock().isoformat()))
    item = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]
    task = _recover_approval(store, clock, item["approval_id"])["approval_results"][0]["request_id"]
    assert monitor.review_refresh_tick()["deferred"] == 1 and site.calls == []
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET state='pending',lease_until=NULL WHERE competitor_id=? AND ean=?",
                    (cid, ean))
    clock.advance(seconds=1)
    with service.execution(0, cid, ean, program="continuity", program_id=1):
        assert monitor.run([cid], eans=[ean], max_jobs=1).processed == 1
    calls = list(site.calls)
    clock.advance(minutes=2)
    assert monitor.review_refresh_tick()["processed"] == 1 and site.calls == calls
    with store._connect() as con:
        result = con.execute("SELECT status,attempts,http_requests FROM manual_review_refreshes WHERE id=?",
                             (task,)).fetchone()
    assert result["status"] == "done" and result["attempts"] == result["http_requests"] == 0


def test_result_finished_after_claim_is_reused_before_http(approval_without_request, monkeypatch):
    _, store, clock, cid, ean, url, site, monitor = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]
    task = _recover_approval(store, clock, approval_id)["approval_results"][0]["request_id"]
    run = monitor.run

    def finish_during_wait(*args, **kwargs):
        if kwargs.get("review_id") == task:
            clock.advance(seconds=1)
            assert run([cid], eans=[ean], max_jobs=1).processed == 1
        return run(*args, **kwargs)

    monkeypatch.setattr(monitor, "run", finish_during_wait)
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        result = con.execute("SELECT status,attempts,http_requests FROM manual_review_refreshes WHERE id=?",
                             (task,)).fetchone()
    assert result["status"] == "done" and result["attempts"] == result["http_requests"] == 0
    assert site.calls.count(url) == 1


def test_two_workers_process_recovered_approval_once(approval_without_request):
    _, store, clock, cid, _, url, site, monitor = approval_without_request
    approval_id = preview_manual_reviews(store.database_path, now=clock())["approvals_without_request"][0]["approval_id"]
    task = _recover_approval(store, clock, approval_id)["approval_results"][0]["request_id"]
    entered, release = Event(), Event()

    def transport(target, headers, timeout):
        if target == url:
            entered.set()
            assert release.wait(10)
        return site(target, headers, timeout)

    worker = PriceMonitor(store, clock=clock, transport=transport, sleep=lambda seconds: clock.advance(seconds=seconds))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.review_refresh_tick)
        try:
            assert entered.wait(10)
            assert monitor.review_refresh_tick()["skipped"]
        finally:
            release.set()
        assert future.result(timeout=30)["processed"] == 1
    with store._connect() as con:
        assert con.execute("SELECT status FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == "done"
        assert con.execute("SELECT COUNT(*) FROM access_requests WHERE purpose=? AND url=?",
                           (f"manual_review:{task}", url)).fetchone()[0] == 1
    assert site.calls.count(url) == 1
