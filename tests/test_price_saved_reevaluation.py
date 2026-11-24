from datetime import datetime, timedelta, timezone
import json
import requests

import pytest

from price_comparator import PriceComparatorStore
from price_saved_reevaluation import SavedSampleReevaluation


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Local reevaluation must not use HTTP")
    monkeypatch.setattr(requests.Session, "send", deny)


def _evidence(tmp_path, *, market="ES", page_market="", tax="unknown", sampled=True, before_request=False,
              availability="available", binding="product_offer", variant_scoped=False,
              published_ean=""):
    at = datetime(2026, 11, 17, 17, 9, 4, 92181, tzinfo=timezone.utc)
    requested = at + timedelta(hours=1) if before_request else at - timedelta(minutes=30)
    page = {
        "url": "https://opticalh.com/products/rb2140",
        "ean_published": published_ean,
        "brand": "Ray-Ban",
        "model": "RB2140",
        "color": "901",
        "ambiguous": False,
        "offer_binding_unresolved": False,
        "offer_binding_source": binding,
        "variant_scoped": variant_scoped,
        "availability": availability,
        "currency": "EUR",
        "price": 109.85,
        "includes_tax": True,
        "configured_tax_policy": tax,
        "caliber": "50-22",
        "market": page_market,
        "market_source": "ficha explícita" if page_market else "",
    }
    case = {
        "id": 1, "ean": "0805289126577", "competitor_id": "opticalh-com",
        "url": "https://opticalh.com/products/rb2140",
        "requested_at": requested.isoformat(),
        "sampled_at": at.isoformat(),
        "sample": {"at": at.isoformat(), "page": page, "gap": "market_currency"},
        "commercial_config": {
            "sales_market": market, "market_verified": 1,
            "market_evidence_url": "https://opticalh.com/",
            "market_note": "Ruben; confirmación ES",
            "tax_policy": "included",
        },
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    return path


def _store(tmp_path):
    store = PriceComparatorStore(tmp_path / "price.sqlite3")
    store.seed_defaults()
    with store._connect() as con, con:
        con.execute("INSERT INTO products(ean,original_ean,brand,model,color,caliber,commercial_name,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    ("0805289126577", "0805289126577", "Ray-Ban", "RB2140", "901", "50-22",
                     "RB2140", "2026-11-17T00:00:00Z"))
        con.execute("INSERT OR IGNORE INTO competitor_web_config(competitor_id) VALUES('opticalh-com')")
        con.execute("UPDATE competitor_web_config SET tax_policy='included',sales_market='ES',"
                    "market_verified=1,market_evidence_url='https://opticalh.com/',"
                    "market_note='Ruben; confirmación ES' WHERE competitor_id='opticalh-com'")
        con.execute("""INSERT INTO monitor_jobs(ean,competitor_id,url,state,sample,previous_sample,
            sampled_at,last_ok_at,valid_until,eligible,updated_at)
            VALUES(?,?,?,'done',?,?,?,?,'2026-11-18T00:00:00Z',0,'2026-11-17T17:09:04Z')""",
                    ("0805289126577", "opticalh-com", "https://opticalh.com/products/rb2140",
                     "", "", "2026-11-17T17:09:04Z", "2026-11-17T17:09:04Z"))
    return store


def _persist_sample(store, evidence):
    data = json.loads(evidence.read_text(encoding="utf-8"))
    sample = data["cases"][0]["sample"]
    with store._connect() as con, con:
        con.execute("UPDATE monitor_jobs SET sample=?,previous_sample='' WHERE id=1",
                    (json.dumps(sample, ensure_ascii=False),))


def test_optical_h_local_preview_and_apply_are_audited_without_http(tmp_path):
    evidence = _evidence(tmp_path)
    store = _store(tmp_path)
    at = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
    service = SavedSampleReevaluation(store, evidence, clock=lambda: at)
    _persist_sample(store, evidence)
    with store._connect() as con:
        data = json.loads(evidence.read_text())
        assert json.loads(con.execute("SELECT sample FROM monitor_jobs WHERE id=1").fetchone()[0]) == data["cases"][0]["sample"]
    preview = service.preview(1)
    assert preview["category"] == "eligible"
    assert preview["http_required"] is False
    assert preview["previous_tax_policy"] == "unknown"
    assert preview["current_tax_policy"] == "included"
    result = service.apply(1, actor="Ruben", note="Reevaluación ES e IVA", build_commit="a" * 40,
                           approved=True, now=at)
    assert result["applied"]
    repeated = service.apply(1, actor="Ruben", note="Reevaluación ES e IVA", build_commit="a" * 40,
                             approved=True, now=at)
    assert not repeated["applied"]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
        offer = con.execute("SELECT match_status,match_basis,ean_published,availability FROM offers").fetchone()
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='reevaluate_saved_sample'").fetchone()[0] == 1
        assert offer["match_status"] == "exact_match" and offer["match_basis"] == "reference"
        assert offer["ean_published"] == "" and offer["availability"] == "available"
        audit = con.execute("SELECT previous,current,note FROM access_audit "
                            "WHERE action='reevaluate_saved_sample'").fetchone()
        assert json.loads(audit["current"])["sampled_at"].startswith("2026-11-17T17:09:04.092181")
        assert json.loads(audit["note"])["http_requests"] == 0


@pytest.mark.parametrize(("evidence_kwargs", "category", "reason"), [
    ({"availability": "unknown"}, "evidence", "Disponibilidad no acreditada"),
    ({"binding": ""}, "identity", "vínculo verificable"),
    ({"binding": "product_group_offer"}, "identity", "variante seleccionada"),
])
def test_saved_sample_requires_known_stock_and_bound_offer(tmp_path, evidence_kwargs, category, reason):
    evidence = _evidence(tmp_path, **evidence_kwargs)
    service = SavedSampleReevaluation(_store(tmp_path), evidence,
                                      clock=lambda: datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))
    result = service.preview(1)
    assert result["category"] == category and reason in result["reason"]


def test_saved_sample_with_other_ean_keeps_price_but_not_variant_stock(tmp_path):
    evidence = _evidence(tmp_path, published_ean="0805289126584")
    store = _store(tmp_path)
    _persist_sample(store, evidence)
    service = SavedSampleReevaluation(store, evidence,
                                      clock=lambda: datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))

    result = service.preview(1)

    assert result["category"] == "evidence"
    assert result["reason"] == "Disponibilidad no acreditada"
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_saved_sample_preserves_explicit_foreign_market_contradiction(tmp_path):
    evidence = _evidence(tmp_path, page_market="FR")
    result = SavedSampleReevaluation(_store(tmp_path), evidence,
                                     clock=lambda: datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)).preview(1)
    assert result["category"] == "contradiction"
    assert "Mercado contradictorio" in result["reason"]
    assert result["current_market"] == "FR"


def test_saved_sample_apply_rejects_tampered_persisted_evidence(tmp_path):
    evidence = _evidence(tmp_path)
    store = _store(tmp_path)
    _persist_sample(store, evidence)
    with store._connect() as con, con:
        sample = json.loads(con.execute("SELECT sample FROM monitor_jobs WHERE id=1").fetchone()[0])
        sample["page"]["price"] = 1
        con.execute("UPDATE monitor_jobs SET sample=? WHERE id=1", (json.dumps(sample),))
    service = SavedSampleReevaluation(store, evidence,
                                      clock=lambda: datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="muestra persistida"):
        service.apply(1, actor="Ruben", note="Reevaluación ES e IVA", build_commit="a" * 40,
                      approved=True)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_unavailable_price_is_imported_as_non_comparable_and_monitor_ineligible(tmp_path):
    evidence = _evidence(tmp_path, availability="unavailable")
    store = _store(tmp_path)
    _persist_sample(store, evidence)
    at = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
    service = SavedSampleReevaluation(store, evidence, clock=lambda: at)
    assert service.preview(1)["category"] == "eligible"
    result = service.apply(1, actor="Ruben", note="Reevaluación ES e IVA", build_commit="a" * 40,
                           approved=True, now=at)
    assert result["applied"]
    with store._connect() as con:
        assert con.execute("SELECT availability FROM offers").fetchone()[0] == "unavailable"
        assert con.execute("SELECT eligible FROM monitor_jobs WHERE id=1").fetchone()[0] == 0


def test_expired_or_pre_request_samples_are_never_imported(tmp_path):
    store = _store(tmp_path)
    before = _evidence(tmp_path, before_request=True)
    service = SavedSampleReevaluation(store, before, clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert service.preview(1)["category"] == "contradiction"
    expired = _evidence(tmp_path)
    service = SavedSampleReevaluation(store, expired,
                                      clock=lambda: datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert service.preview(1)["category"] == "expired"
    with pytest.raises(ValueError):
        service.apply(1, actor="Ruben", note="No renovar", build_commit="a" * 40, approved=True)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0

