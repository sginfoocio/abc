"""Synthetic continuity and historical report; no production data or HTTP."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

from filelock import FileLock, Timeout
import pytest
import requests

from price_comparator import PriceComparatorStore
from price_recovery import (EXPECTED_COUNTS, REASON, apply, apply_selected, load_allowlist, preview,
                            preview_selected, RETIRED_PILOT_REASON, preview_retired_pilot_gate,
                            apply_retired_pilot_gate)
from price_trial import manifest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("No commercial HTTP")
    monkeypatch.setattr(requests.Session, "send", deny)


@pytest.fixture
def recovery(tmp_path):
    store = PriceComparatorStore(tmp_path / "synthetic.sqlite3")
    store.seed_defaults()
    eans = manifest()["eans"]
    started = datetime(2026, 11, 14, tzinfo=timezone.utc)
    closed = started + timedelta(seconds=30)
    with store._connect() as con, con:
        shops = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM competitors")}
        assert len(shops) == 14
        con.executemany("INSERT INTO products(ean,original_ean,updated_at) VALUES(?,?,?)",
                        [(e, e, started.isoformat()) for e in eans])
        pairs = [(cid, ean) for cid in shops for ean in eans]
        frozen = {"eans": eans, "competitors": shops,
                  "pairs": [{"competitor_id": cid, "ean": ean} for cid, ean in pairs]}
        con.execute("""INSERT INTO price_continuity(id,status,prepared_at,actor,build_commit,manifest,limits,
            interval_hours,started_at) VALUES(1,'active',?,'test',?,?,'{}',24,?)""",
                    (started.isoformat(), "a" * 40, json.dumps(frozen), started.isoformat()))
        con.executemany("INSERT INTO price_continuity_pairs(continuity_id,competitor_id,ean) VALUES(1,?,?)", pairs)
        entries, report_pairs, checks = [], [], []
        index = 0
        for cid, count in EXPECTED_COUNTS.items():
            for _ in range(count):
                ean = eans[index % 85]
                index += 1
                pair_id = con.execute("SELECT id FROM price_continuity_pairs WHERE competitor_id=? AND ean=?",
                                      (cid, ean)).fetchone()[0]
                con.execute("""UPDATE price_continuity_pairs SET state='review',started_at=?,checked_at=?,
                    defer_reason=? WHERE id=?""", (started.isoformat(), closed.isoformat(), REASON, pair_id))
                receipt = json.dumps({"http_receipts": [], "closure_reason": REASON})
                cursor = con.execute("""INSERT INTO price_continuity_checks
                    (continuity_id,competitor_id,ean,started_at,finished_at,state,category,defer_reason,receipt)
                    VALUES(1,?,?,?,?,'review','interrupted',?,?)""",
                                     (cid, ean, started.isoformat(), closed.isoformat(), REASON, receipt))
                check_id = cursor.lastrowid
                check = dict(con.execute("SELECT * FROM price_continuity_checks WHERE id=?", (check_id,)).fetchone())
                pair = dict(con.execute("SELECT * FROM price_continuity_pairs WHERE id=?", (pair_id,)).fetchone())
                pair["latest_check"] = check
                checks.append(check)
                report_pairs.append(pair)
                entries.append({"continuity_id": 1, "pair_id": pair_id, "competitor_id": cid, "ean": ean,
                                "expected_latest_check_id": check_id, "expected_state": "review",
                                "expected_category": "interrupted", "expected_reason": REASON,
                                "attempt_started_at": started.isoformat(), "closed_at": closed.isoformat()})
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"pairs": report_pairs, "checks": checks}), encoding="utf-8")
    allowlist = {"kind": "offline_recovery_review_allowlist_not_execution_authorization", "selected_pairs": 1400,
                 "technical_pairs": 182, "distinct_eans_in_technical_pairs": 85,
                 "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "pairs": entries}
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
    return store, allowlist_path, report_path, entries, started


def dump(store):
    with store._connect() as con:
        return "\n".join(con.iterdump())


@pytest.fixture
def retired_gate(recovery):
    from price_access import AccessControl
    from price_watch import PriceWatch
    from price_web import WebPriceSearch
    from price_web_fetch import user_agent

    store, _, _, entries, started = recovery
    entry = entries[0]
    cid, ean = entry["competitor_id"], entry["ean"]
    now = started + timedelta(days=10)
    with store._connect() as con, con:
        site = con.execute("SELECT url FROM competitors WHERE id=?", (cid,)).fetchone()[0].rstrip("/")
        con.execute("UPDATE products SET brand='Synthetic',model='TEST01',color='001' WHERE ean=?", (ean,))
        con.execute("UPDATE competitors SET active=1 WHERE id=?", (cid,))
        limits = {r[0]: {"daily_limit": 10, "per_minute_limit": 6, "effective_pause": 3}
                  for r in con.execute("SELECT id FROM competitors")}
        con.execute("UPDATE price_continuity SET limits=?", (json.dumps(limits),))
    url = site + "/synthetic-original-product"
    search = WebPriceSearch(store, clock=lambda: now)
    search.ensure_config()
    search.update_config(cid, terms_reviewed=True, terms_note="Synthetic permission",
                         access_status="robots_allowed", pause_seconds=1)
    access = AccessControl(store, clock=lambda: now)
    access.set_method(cid, "ficha", "authorized", actor="synthetic", note="Synthetic original authorization")
    access.set_budget("competitor", cid, daily_limit=10, per_minute_limit=6, pause_seconds=1,
                      actor="synthetic")
    assert PriceWatch(store, clock=lambda: now).add([ean], actor="synthetic", source="manual").added == [ean]
    receipt_pair = {"ean": ean, "competitor_id": cid, "processed": False,
                    "requests": 0, "category": "not_executed", "outcomes": [],
                    "reason": RETIRED_PILOT_REASON}
    with store._connect() as con, con:
        con.execute("""INSERT INTO price_trial(id,status,actor,build_commit,manifest,limits,
            started_at,expires_at) VALUES(1,'expired','synthetic',?,'{}','{}',?,?)""",
                    ("a" * 40, started.isoformat(), (started + timedelta(days=3)).isoformat()))
        con.execute("UPDATE price_continuity_pairs SET defer_reason=? WHERE id=?",
                    (RETIRED_PILOT_REASON, entry["pair_id"]))
        con.execute("""UPDATE price_continuity_checks SET category='not_executed',defer_reason=?,
            receipt=? WHERE id=?""", (RETIRED_PILOT_REASON,
                                     json.dumps({"pair": receipt_pair, "result": {"pairs": [receipt_pair]}}),
                                     entry["expected_latest_check_id"]))
        con.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,status,source,
            match_status,note,verified_at,ean_published) VALUES(?,?,?,'verified','known_urls','exact_match',?,?,?)""",
                    (cid, ean, url, "Synthetic recorded original literal-EAN verification", started.isoformat(), ean))
        con.execute("""INSERT INTO monitor_jobs(competitor_id,ean,url,state,updated_at)
            VALUES(?,?,?,'pending',?)""", (cid, ean, url, started.isoformat()))
        con.execute("""INSERT INTO price_robots_cache(origin,agent,status,rules_text,fetched_at,expires_at)
            VALUES(?,?,200,'User-agent: *\nDisallow:\n',?,?)""",
                    (site, user_agent(), now.isoformat(), (now + timedelta(hours=1)).isoformat()))
    ids = {"continuity_id": 1, "pair_id": entry["pair_id"], "check_id": entry["expected_latest_check_id"]}
    return store, ids, now, cid, ean


def test_retired_gate_reuses_job_and_preserves_dates_history_and_approval(retired_gate):
    store, ids, now, cid, ean = retired_gate
    before = dump(store)
    plan = preview_retired_pilot_gate(store.database_path, **ids, now=now)
    assert plan["eligible"], plan
    assert not plan["approval_refs"] and plan["verification_refs"] and plan["http_requests"] == 0
    assert dump(store) == before
    with store._connect() as con:
        job_before = dict(con.execute("SELECT * FROM monitor_jobs WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone())
        pair_before = dict(con.execute("SELECT * FROM price_continuity_pairs WHERE id=?", (ids["pair_id"],)).fetchone())
    options = {"actor": "synthetic", "note": "Reviewed exact plan", "approved": True,
               "build_commit": "b" * 40, "plan_hash": plan["plan_hash"], "now": now}
    assert apply_retired_pilot_gate(store.database_path, **ids, **options)["applied"]
    assert apply_retired_pilot_gate(store.database_path, **ids, **options)["already_applied"]
    with store._connect() as con:
        pair = dict(con.execute("SELECT * FROM price_continuity_pairs WHERE id=?", (ids["pair_id"],)).fetchone())
        job = dict(con.execute("SELECT * FROM monitor_jobs WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone())
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='reconcile_retired_pilot_gate'").fetchone()[0] == 1
    assert job == job_before
    assert pair["state"] == "pending"
    assert {k: v for k, v in pair.items() if k not in {"state", "defer_reason"}} == {
        k: v for k, v in pair_before.items() if k not in {"state", "defer_reason"}}


@pytest.mark.parametrize("control", ["uncertain", "lease", "exhausted", "robots", "approval", "pilot", "receipt",
                                     "job_error", "attempts"])
def test_retired_gate_keeps_legitimate_controls(retired_gate, control):
    store, ids, now, cid, ean = retired_gate
    with store._connect() as con, con:
        if control == "uncertain":
            con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
                VALUES('competitor',?,?,'ficha','synthetic',?,?,'reserved')""", (cid, cid, ean, now.isoformat()))
        elif control == "lease":
            con.execute("UPDATE monitor_jobs SET state='running',lease_until=?",
                        ((now + timedelta(minutes=5)).isoformat(),))
        elif control == "exhausted":
            con.execute("UPDATE access_budgets SET daily_limit=0 WHERE scope='competitor' AND scope_id=?", (cid,))
        elif control == "robots":
            con.execute("UPDATE price_robots_cache SET rules_text='User-agent: *\nDisallow: /\n'")
        elif control == "approval":
            con.execute("UPDATE competitor_product_urls SET note='' WHERE competitor_id=? AND ean=?", (cid, ean))
        elif control == "receipt":
            con.execute("UPDATE price_continuity_checks SET receipt='{}' WHERE id=?", (ids["check_id"],))
        elif control == "job_error":
            con.execute("UPDATE monitor_jobs SET state='error',attempts=4,detail='Synthetic identity contradiction'")
        elif control == "attempts":
            from price_locator import MAX_ATTEMPTS
            con.execute("UPDATE monitor_jobs SET attempts=?", (MAX_ATTEMPTS,))
        else:
            con.execute("UPDATE price_trial SET status='active'")
    before = dump(store)
    plan = preview_retired_pilot_gate(store.database_path, **ids, now=now)
    assert not plan["eligible"], plan
    if control == "exhausted":
        assert "presupuesto" in plan["reason"].lower()
    assert dump(store) == before


def test_retired_gate_rejects_changed_plan_and_busy_pair_without_writes(retired_gate):
    from price_recovery import _ReadOnlyStore
    from price_trial import PriceTrial

    store, ids, now, cid, ean = retired_gate
    plan = preview_retired_pilot_gate(store.database_path, **ids, now=now)
    options = {"actor": "synthetic", "note": "Reviewed plan", "approved": True,
               "build_commit": "b" * 40, "plan_hash": plan["plan_hash"], "now": now}
    with PriceTrial(_ReadOnlyStore(store.database_path)).continuity_pair_lock(cid, ean):
        before = dump(store)
        with pytest.raises(Timeout):
            apply_retired_pilot_gate(store.database_path, **ids, **options)
        assert dump(store) == before
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET detail='Concurrent change' WHERE competitor_id=? AND ean=?", (cid, ean))
    before = dump(store)
    with pytest.raises(ValueError, match="Plan obsoleto"):
        apply_retired_pilot_gate(store.database_path, **ids, **options)
    assert dump(store) == before


def test_retired_gate_preview_does_not_expire_incidents_or_expose_config(retired_gate):
    store, ids, now, cid, _ = retired_gate
    secret = "synthetic-private-config-marker"
    with store._connect() as con, con:
        con.execute("UPDATE competitor_web_config SET market_note=? WHERE competitor_id=?", (secret, cid))
        con.execute("""INSERT INTO access_incidents(scope,scope_id,kind,detected_at,retry_after)
            VALUES('competitor',?,'http_429',?,?)""",
                    (cid, (now - timedelta(hours=2)).isoformat(), (now - timedelta(hours=1)).isoformat()))
    before = dump(store)
    plan = preview_retired_pilot_gate(store.database_path, **ids, now=now)
    assert plan["eligible"], plan
    assert secret not in json.dumps(plan)
    assert dump(store) == before


def test_preview_is_read_only_and_validates_snapshot_and_scope(recovery):
    store, allowlist, report, entries, _ = recovery
    before = dump(store)
    rows = preview(store.database_path, allowlist, report)
    assert len(rows) == 182 and all(r["eligible"] for r in rows)
    assert preview(store.database_path, allowlist.read_bytes(), report.read_bytes()) == rows
    assert dump(store) == before
    altered = json.loads(allowlist.read_text(encoding="utf-8"))
    altered["pairs"][0]["expected_latest_check_id"] = altered["pairs"][1]["expected_latest_check_id"]
    allowlist.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError):
        preview(store.database_path, allowlist, report)
    assert dump(store) == before


def test_apply_revalidates_requests_and_reviews_preserves_history_and_is_idempotent(recovery):
    store, allowlist, report, entries, started = recovery
    before = preview(store.database_path, allowlist, report)
    assert all(r["eligible"] for r in before)
    request, review = entries[:2]
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('competitor',?,?,'ficha','test',?,?,'reserved')""",
                    (request["competitor_id"], request["competitor_id"], request["ean"], started.isoformat()))
        con.execute("""INSERT INTO locate_jobs(ean,competitor_id,state,created_at,updated_at)
            VALUES(?,?,'review',?,?)""", (review["ean"], review["competitor_id"], started.isoformat(),
                                         started.isoformat()))
        original_checks = [tuple(r) for r in con.execute("SELECT * FROM price_continuity_checks ORDER BY id")]
    rows = apply(store.database_path, allowlist, report, actor="operator", note="reviewed",
                 build_commit="b" * 40, approved=True)
    assert sum(r["eligible"] for r in rows) == 180
    assert "reserva" in rows[0]["reason"] and "revisión" in rows[1]["reason"]
    with store._connect() as con:
        assert [tuple(r) for r in con.execute("SELECT * FROM price_continuity_checks ORDER BY id")] == original_checks
        assert con.execute("SELECT COUNT(*) FROM price_continuity_pairs WHERE state='pending'").fetchone()[0] == 1398
        audits = con.execute("SELECT previous,current,note FROM access_audit WHERE action='recover_interrupted_pairs'").fetchall()
        assert len(audits) == 1 and len(json.loads(audits[0]["current"])) == 180
        assert len(json.loads(audits[0]["note"])["excluded"]) == 2
    again = apply(store.database_path, allowlist, report, actor="operator", note="reviewed",
                  build_commit="b" * 40, approved=True)
    assert not any(r["eligible"] for r in again)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='recover_interrupted_pairs'").fetchone()[0] == 1
        noop = con.execute("SELECT actor,note FROM access_audit WHERE action='recover_interrupted_pairs_noop'").fetchone()
        assert noop["actor"] == "operator" and len(json.loads(noop["note"])["excluded"]) == 182


def test_apply_refuses_parallel_worker_and_requires_confirmation(recovery):
    store, allowlist, report, _, _ = recovery
    before = dump(store)
    with FileLock(f"{store.database_path}.continuity.lock", timeout=0):
        with pytest.raises(Timeout):
            apply(store.database_path, allowlist, report, actor="operator", note="reason",
                  build_commit="b" * 40, approved=True)
    with pytest.raises(ValueError):
        apply(store.database_path, allowlist, report, actor="operator", note="reason",
              build_commit="b" * 40)
    assert dump(store) == before


def test_report_hash_is_required(recovery):
    _, allowlist, report, _, _ = recovery
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="huella"):
        load_allowlist(allowlist, report)


def test_unknown_ean_reservation_and_later_evidence_are_excluded(recovery):
    store, allowlist, report, entries, started = recovery
    unknown, later = entries[0], entries[70]
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('competitor',?,?,'robots','test','',?,'HTTP 200')""",
                    (unknown["competitor_id"], unknown["competitor_id"], started.isoformat()))
        con.execute("""INSERT INTO search_results(ean,competitor_id,market,captured_at,method,match_status,evidence,fingerprint)
            VALUES(?,?,'ES',?,'test','probable','test','fixture')""",
                    (later["ean"], later["competitor_id"], (started + timedelta(seconds=1)).isoformat()))
    rows = preview(store.database_path, allowlist, report)
    assert "sin EAN" in rows[0]["reason"]
    assert "Evidencia" in rows[70]["reason"]
    assert not rows[0]["eligible"] and not rows[70]["eligible"]


def test_missing_competitor_in_reservation_and_pending_identity_are_excluded(recovery):
    store, allowlist, report, entries, started = recovery
    unknown, review = entries[0], entries[70]
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('provider','unknown','','ficha','test',?,?,'reserved')""",
                    (unknown["ean"], started.isoformat()))
        con.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,match_status,status,verified_at)
            VALUES(?,?,'https://example.test/ambiguous','probable','pending_verification',?)""",
                    (review["competitor_id"], review["ean"], (started - timedelta(days=1)).isoformat()))
    rows = preview(store.database_path, allowlist, report)
    assert "reserva" in rows[0]["reason"]
    assert "identidad pendiente" in rows[70]["reason"]
    assert not rows[0]["eligible"] and not rows[70]["eligible"]


def test_unrelated_competitor_scope_does_not_block_recovery(recovery):
    store, allowlist, report, entries, started = recovery
    pair = entries[0]
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('competitor','unrelated','','ficha','test',?,?,'reserved')""",
                    (pair["ean"], started.isoformat()))
    assert preview(store.database_path, allowlist, report)[0]["eligible"]


def test_unattributed_competitor_reservation_blocks_recovery(recovery):
    store, allowlist, report, entries, started = recovery
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('competitor','','','ficha','test','',?,'reserved')""", (started.isoformat(),))
    rows = preview(store.database_path, allowlist, report)
    assert all(not row["eligible"] and "reserva" in row["reason"] for row in rows)


def test_selected_recovery_revalidates_exact_ids_and_is_idempotent(recovery):
    store, _, _, entries, started = recovery
    entry = entries[0]
    selection = {"continuity_id": entry["continuity_id"], "pair_id": entry["pair_id"],
                 "check_id": entry["expected_latest_check_id"]}
    before = dump(store)
    assert preview_selected(store.database_path, **selection)["eligible"]
    assert dump(store) == before
    with store._connect() as con, con:
        con.execute("""INSERT INTO access_requests(scope,scope_id,competitor_id,method,purpose,ean,reserved_at,result)
            VALUES('competitor','','','ficha','test','',?,'reserved')""", (started.isoformat(),))
    result = apply_selected(store.database_path, **selection, actor="operator", note="reviewed",
                            build_commit="b" * 40, approved=True)
    assert not result["eligible"] and "reserva" in result["reason"]
    with store._connect() as con, con:
        con.execute("DELETE FROM access_requests WHERE purpose='test'")
    result = apply_selected(store.database_path, **selection, actor="operator", note="reviewed",
                            build_commit="b" * 40, approved=True)
    assert result["eligible"]
    repeated = apply_selected(store.database_path, **selection, actor="operator", note="reviewed",
                              build_commit="b" * 40, approved=True)
    assert not repeated["eligible"]
    with store._connect() as con:
        audit = con.execute("""SELECT note FROM access_audit WHERE action='recover_selected_interrupted_pair'
            ORDER BY id""").fetchall()
        assert [json.loads(row["note"])["eligible"] for row in audit] == [False, True, False]
        assert con.execute("SELECT COUNT(*) FROM price_continuity_checks").fetchone()[0] == 182
    with pytest.raises(ValueError):
        preview_selected(store.database_path, **{**selection, "check_id": entries[1]["expected_latest_check_id"]})


def test_selected_recovery_refuses_newer_check(recovery):
    store, _, _, entries, started = recovery
    entry = entries[0]
    with store._connect() as con, con:
        con.execute("""INSERT INTO price_continuity_checks
            (continuity_id,competitor_id,ean,started_at,finished_at,state,category,receipt)
            VALUES(?,?,?,?,?,'review','identity','{}')""",
                    (entry["continuity_id"], entry["competitor_id"], entry["ean"], started.isoformat(),
                     started.isoformat()))
    row = preview_selected(store.database_path, continuity_id=entry["continuity_id"],
                           pair_id=entry["pair_id"], check_id=entry["expected_latest_check_id"])
    assert not row["eligible"] and "comprobación" in row["reason"]


def test_selected_recovery_preserves_review_and_obeys_shared_locks(recovery):
    from filelock import FileLock, Timeout

    store, _, _, entries, _ = recovery
    entry = entries[0]
    selection = {"continuity_id": entry["continuity_id"], "pair_id": entry["pair_id"],
                 "check_id": entry["expected_latest_check_id"]}
    with FileLock(f"{store.database_path}.competitor-{entry['competitor_id']}.lock", timeout=0):
        with pytest.raises(Timeout):
            apply_selected(store.database_path, **selection, actor="operator", note="reviewed",
                           build_commit="b" * 40, approved=True)
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_checks SET category='identity' WHERE id=?",
                    (entry["expected_latest_check_id"],))
    result = apply_selected(store.database_path, **selection, actor="operator", note="reviewed",
                            build_commit="b" * 40, approved=True)
    assert not result["eligible"]
    with store._connect() as con:
        state = con.execute("SELECT state FROM price_continuity_pairs WHERE id=?", (entry["pair_id"],)).fetchone()[0]
    assert state == "review"


def test_apply_rolls_back_every_change_and_preserves_future_deadline(recovery):
    store, allowlist, report, entries, started = recovery
    future = (started + timedelta(days=30)).isoformat()
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_pairs SET next_check_at=? WHERE id=?", (future, entries[0]["pair_id"]))
        con.execute(f"""CREATE TRIGGER reject_recovery BEFORE UPDATE ON price_continuity_pairs
            WHEN NEW.id={entries[1]["pair_id"]} AND NEW.state='pending'
            BEGIN SELECT RAISE(ABORT, 'storage failure'); END""")
    before = dump(store)
    with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
        apply(store.database_path, allowlist, report, actor="operator", note="reviewed",
              build_commit="b" * 40, approved=True)
    assert dump(store) == before
    with store._connect() as con, con:
        con.execute("DROP TRIGGER reject_recovery")
    apply(store.database_path, allowlist, report, actor="operator", note="reviewed",
          build_commit="b" * 40, approved=True)
    with store._connect() as con:
        row = con.execute("SELECT state,next_check_at FROM price_continuity_pairs WHERE id=?",
                          (entries[0]["pair_id"],)).fetchone()
    assert row["state"] == "pending" and row["next_check_at"] == future
