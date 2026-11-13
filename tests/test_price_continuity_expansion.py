"""Offline scope transition; synthetic shops only, never Cloud or commercial HTTP."""
from datetime import timedelta
import json
import sqlite3

import pytest
import requests

from price_trial import TrialDenied, manifest, require_pair
from tests import test_price_trial as trial_helpers
from tests.test_price_trial import _activate_continuity
from tests.web_fixtures import page


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("No real network permitted")
    monkeypatch.setattr(requests.Session, "send", deny)


@pytest.fixture
def trial(tmp_path):
    return trial_helpers.trial.__wrapped__(tmp_path)


def ready(trial):
    service, store, access, _, _, clock, *_ = trial
    for shop in store.competitors().to_dict("records"):
        for method in ("ficha", "sitemap"):
            access.set_method(shop["id"], method, "authorized", actor="operator", note="Synthetic authorization")
    _activate_continuity(service, clock)
    return service, store, access, clock


def expand(service, **changes):
    return service.expand_continuity(**{
        "actor": "operator", "approved": True, "build_commit": "d" * 40,
        "note": "Explicit extension to the same 100 EAN and fourteen documented competitors", **changes,
    })


def test_expansion_preserves_all_existing_work_and_is_idempotent(trial):
    service, store, _, _ = ready(trial)
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_pairs SET state='review',defer_reason='Keep original review' WHERE id=1")
        before_pairs = [dict(r) for r in con.execute("SELECT * FROM price_continuity_pairs ORDER BY id")]
        before_row = dict(con.execute("SELECT * FROM price_continuity").fetchone())
        before_trial = dict(con.execute("SELECT * FROM price_trial").fetchone())
        requests_before = con.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0]
    after = expand(service)
    assert after["status"] == "active" and after["summary"]["selected_pairs"] == 1400
    assert after["manifest"]["eans"] == manifest()["eans"]
    assert len(after["manifest"]["competitors"]) == 14
    assert after["summary"]["distinct_eans"] == 100
    with store._connect() as con:
        persisted_pairs = [dict(r) for r in con.execute(
            "SELECT * FROM price_continuity_pairs WHERE id<=600 ORDER BY id")]
        assert persisted_pairs == before_pairs
        persisted = dict(con.execute("SELECT * FROM price_continuity").fetchone())
        assert {k: v for k, v in persisted.items() if k not in {"manifest", "limits"}} == {
            k: v for k, v in before_row.items() if k not in {"manifest", "limits"}}
        assert dict(con.execute("SELECT * FROM price_trial").fetchone()) == before_trial
        assert con.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == requests_before
    original_limits = json.loads(before_row["limits"])
    assert all(after["limits"][cid] == limits for cid, limits in original_limits.items())
    audit = [a for a in after["audits"] if a["action"] == "expand_scope"]
    assert len(audit) == 1 and json.loads(audit[0]["note"])["previous_manifest"] == json.loads(before_row["manifest"])
    again = expand(service)
    assert len(again["pairs"]) == 1400 and len(again["audits"]) == len(after["audits"])
    assert manifest()["competitors"] == json.loads(before_row["manifest"])["competitors"]


@pytest.mark.parametrize("changes", [{"approved": False}, {"actor": " "}, {"note": ""}, {"build_commit": "bad"}])
def test_expansion_requires_explicit_reviewable_decision(trial, changes):
    service, _, _, _ = ready(trial)
    with pytest.raises(ValueError):
        expand(service, **changes)
    assert service.continuity_report()["summary"]["selected_pairs"] == 600


def test_missing_authorization_and_inactive_shop_prevent_expansion(trial):
    service, store, access, _ = ready(trial)
    cid = "opticabassol-com"
    access.set_method(cid, "sitemap", "pending", actor="operator", note="Still unreviewed")
    with pytest.raises(ValueError, match="autorización"):
        expand(service)
    access.set_method(cid, "sitemap", "authorized", actor="operator", note="Synthetic approval")
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=0 WHERE id=?", (cid,))
    with pytest.raises(ValueError, match="Faltan competidores"):
        expand(service)
    assert len(service.continuity_report()["pairs"]) == 600


def test_expansion_is_atomic_when_pair_insertion_fails(trial):
    service, store, _, _ = ready(trial)
    with store._connect() as con, con:
        before = dict(con.execute("SELECT * FROM price_continuity").fetchone())
        con.execute("""CREATE TRIGGER reject_new_pair BEFORE INSERT ON price_continuity_pairs
            BEGIN SELECT RAISE(ABORT, 'Simulated storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        expand(service)
    with store._connect() as con:
        assert dict(con.execute("SELECT * FROM price_continuity").fetchone()) == before
        assert con.execute("SELECT COUNT(*) FROM price_continuity_pairs").fetchone()[0] == 600
        assert not con.execute("SELECT 1 FROM access_audit WHERE action='expand_scope'").fetchone()


def test_stricter_budgets_blocks_and_pending_reviews_are_preserved(trial):
    service, store, access, _ = ready(trial)
    cid = "opticabassol-com"
    access.set_budget("competitor", cid, daily_limit=15, per_minute_limit=3, pause_seconds=5, actor="operator")
    access.report_block("competitor", cid, "captcha", method="ficha", detail="Synthetic protection")
    with store._connect() as con:
        incidents = [dict(r) for r in con.execute("SELECT * FROM access_incidents")]
        methods = [dict(r) for r in con.execute("SELECT * FROM access_methods ORDER BY competitor_id,method")]
    result = expand(service)
    assert result["limits"][cid]["daily_limit"] == 15
    assert result["limits"][cid]["per_minute_limit"] == 3
    assert result["limits"][cid]["effective_pause"] == 5
    with store._connect() as con:
        assert [dict(r) for r in con.execute("SELECT * FROM access_incidents")] == incidents
        assert [dict(r) for r in con.execute("SELECT * FROM access_methods ORDER BY competitor_id,method")] == methods


def test_ui_requires_confirmation_and_activates_only_explicit_expansion(trial, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import build_info
    import price_trial

    service, store, _, _ = ready(trial)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(price_trial, "PriceTrial", lambda _store: service)
    monkeypatch.setattr(build_info, "load_build_info", lambda: {"commit": "d" * 40, "build": "actions-test"})
    app = AppTest.from_string(
        "from price_comparator import PriceComparatorStore\n"
        "from app_pages.price_watch import _render_continuity\n"
        "_render_continuity(PriceComparatorStore())\n"
    ).run(timeout=60)
    assert not app.exception
    app.button(key="FormSubmitter:continuity_expand_form-Activar ampliación a 14 tiendas").click().run(timeout=60)
    assert service.continuity_report()["summary"]["selected_pairs"] == 600
    app.text_input[1].set_value("Ruben")
    app.text_input[2].set_value("User-approved extension to fourteen competitors")
    app.checkbox[0].check()
    app.button(key="FormSubmitter:continuity_expand_form-Activar ampliación a 14 tiendas").click().run(timeout=60)
    assert not app.exception
    assert service.continuity_report()["summary"]["selected_pairs"] == 1400


def test_new_pairs_are_reserved_and_worker_uses_same_filtered_sync(trial):
    service, store, _, clock = ready(trial)
    # Authorizing other shops must not break a still-six-shop worker preview.
    old_cid = next(iter(manifest()["competitors"]))
    with service.execution(0, old_cid, manifest()["eans"][0], program="continuity", program_id=1):
        assert len(service._batch.preview([manifest()["eans"][0]], [old_cid])) == 1
    report = expand(service)
    cid, ean = "opticabassol-com", manifest()["eans"][0]
    with pytest.raises(TrialDenied, match="continuidad"):
        require_pair(store, cid, ean, clock())
    with service.execution(0, cid, ean, program="continuity", program_id=report["id"]):
        require_pair(store, cid, ean, clock())
        with pytest.raises(TrialDenied):
            require_pair(store, "unapproved-com", ean, clock())
    _, _, _, locator, _, _, _, site, reader = trial
    url = "https://www.opticabassol.com/products/gucci-gg1660s-001-54"
    site.pages["https://www.opticabassol.com/sitemap.xml"] = f"<urlset><url><loc>{url}</loc></url></urlset>"
    site.pages[url] = page(name="Gucci GG1660S 001", price=120, brand="Gucci", gtin=ean)
    locator.search.update_config(cid, tax_policy="included", sales_market="ES", market_verified=True,
                                 market_evidence_url="https://www.opticabassol.com/terms",
                                 market_note="Synthetic verified ES policy")
    with store._connect() as con, con:
        con.execute("UPDATE price_continuity_pairs SET next_check_at=? WHERE competitor_id<>? OR ean<>?",
                    ((clock() + timedelta(days=1)).isoformat(), cid, ean))
    reader.calls.clear()
    service.continuity_tick(max_pairs=1)
    result = service.continuity_report()
    assert reader.calls == [manifest()["eans"]]
    assert any(c["competitor_id"] == cid and c["ean"] == ean for c in result["checks"])
    assert any("opticabassol.com" in call for call in site.calls)
    service.pause_continuity(actor="operator")
    with pytest.raises(TrialDenied):
        require_pair(store, cid, ean, clock())
