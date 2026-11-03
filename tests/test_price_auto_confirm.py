"""Automatic confirmation of discovered fichas: brand + model + colour code decide identity; offline HTTP only."""
import json
from dataclasses import asdict
from urllib.parse import quote

import pytest
from streamlit.testing.v1 import AppTest

import price_discovery
from price_auto_confirm import AUTO_RULE, AutoDecision, evaluate
from price_discovery import PilotDiscovery
from price_pilot import PilotError
from price_web import Outcome
from price_web_extract import ProductPage
from tests.test_price_discovery import EAN, NO_COLOR_NAME, commerce, configure, only_pending, route, run_all_pending
from tests.web_fixtures import BASE, prada_page

pytest_plugins = ["tests.test_price_pilot"]
PRODUCT = {"ean": EAN, "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z", "caliber": "54-21"}
SECOND, THIRD = "8056597875226", "8056597877329"


def http_totals(store):
    with store._connect() as connection:
        return (connection.execute("SELECT COALESCE(SUM(requests),0) FROM pilot_budgets").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM pilot_http_log").fetchone()[0])


@pytest.mark.parametrize("overrides,state,origin", [
    ({}, "confirmed", "automatic"),                                          # exact, EAN published and equal
    ({"gtin": None}, "confirmed", "automatic"),                              # exact, no EAN published
    ({"name": "Prada PR 27ZS 16K08Z 52-20"}, "confirmed", "automatic"),      # different calibre
    ({"stock": False}, "confirmed", "automatic"),                            # out of stock, correct identity
    ({"price": 999}, "confirmed", "automatic"),                              # price never decides identity
    ({"name": "Prada PR 27ZS ZVN09T 54-21"}, "rejected", ""),                # wrong colour
    ({"name": "Prada PR 26ZS 16K08Z 54-21"}, "rejected", ""),                # wrong model
    ({"gtin": SECOND}, "pending_validation", ""),                            # contradictory published EAN
    ({"name": NO_COLOR_NAME}, "candidate_found", ""),                        # no colour code published
    ({"extra_offer": {"price": "200", "priceCurrency": "EUR", "availability": "InStock"}},
     "pending_validation", ""),                                              # ambiguous variants
])
def test_live_discovery_confirms_only_exact_brand_model_color(pilot, overrides, state, origin):
    service, store, search, site, cid, _ = pilot
    configure(search, cid)
    only_pending(store, cid, [EAN])
    url = route(site, EAN, **overrides)
    service.enable()
    before = commerce(store)
    discovery = PilotDiscovery(store)
    run_all_pending(discovery, search)
    candidate = discovery.candidates().iloc[0]
    assert (candidate.state, candidate.review_origin) == (state, origin)
    indexed = search._indexed(cid, EAN)
    if state == "confirmed":
        assert [(r["url"], r["status"], r["source"]) for r in indexed] == [(url, "verified", "auto_confirmed")]
        audit = json.loads(candidate.review_evidence)
        assert audit["rule"] == AUTO_RULE and all(audit["checks"].values()) and audit["reasons"] == []
        assert audit["catalogue"] == {"brand": "PRADA", "model": "PR27ZS", "color": "16K08Z"}
        assert set(audit["informational"]) == {"caliber", "price", "availability"}
        assert discovery.coverage().query("ean == @EAN and competitor_id == @cid").iloc[0].state == "confirmed"
    else:
        assert indexed == []
        assert candidate.review_evidence == "" and candidate.reviewed_at is None
    assert commerce(store) == before
    # Same HTTP sequence as before the feature: no extra request to confirm automatically.
    assert site.calls == [BASE + "/robots.txt", BASE + "/search?q=" + EAN, url]


def test_previously_discarded_url_is_never_confirmed_automatically(pilot):
    service, store, search, site, cid, _ = pilot
    configure(search, cid)
    only_pending(store, cid, [EAN])
    url = route(site, EAN, name=NO_COLOR_NAME)
    service.enable()
    discovery = PilotDiscovery(store)
    run_all_pending(discovery, search)
    old = int(discovery.candidates().iloc[0].id)
    discovery.review(old, accept=False, note="Revisor: ficha sin color, descartada")
    site.add(url, prada_page(EAN))  # The page now publishes a perfect identity.
    discovery.reset(EAN, cid, note="Revisor autoriza otro intento")
    site.calls.clear()
    run_all_pending(discovery, search)
    rows = discovery.candidates()
    assert len(rows) == 1 and rows.iloc[0].state == "discarded" and rows.iloc[0].review_origin == "manual"
    assert url not in site.calls and search._indexed(cid, EAN) == []
    assert discovery.reevaluate().empty


def _evidence(page: ProductPage, *, state="exact", method="web:json-ld") -> dict:
    return asdict(Outcome(EAN, "cid", "Visio Factory", state, page.url, method=method, page=page))


def _page(**changes) -> ProductPage:
    data = {"url": BASE + "/pr27zs-16k08z.html", "title": "Prada PR 27ZS 16K08Z 54-21", "brand": "Prada",
            "identifiers": ["PR 27ZS 16K08Z 54-21"], "ean_published": "", "price": 239, "currency": "EUR",
            "availability": "available"}
    data.update(changes)
    return ProductPage(**data)


def _decide(config, page=None, **kwargs):
    options = {"method": "ean", "fetched_ok": True, "previously_discarded": False, "previously_rejected": False}
    options.update(kwargs)
    evidence = options.pop("evidence", None) or _evidence(page or _page())
    return evaluate(PRODUCT, config, evidence=evidence, **options)


def test_rule_requires_every_condition(pilot):
    _, _, search, _, cid, _ = pilot
    configure(search, cid)
    config = search.config(cid)
    assert _decide(config).confirm
    cases = {
        "metodo_autorizado": _decide({**config, "search_verified": 0}),
        "dominio_competidor": _decide(config, _page(url="https://evil.example/pr27zs-16k08z.html")),
        "ficha_consultada": _decide(config, fetched_ok=False),
        "url_no_descartada": _decide(config, previously_discarded=True),
        "url_no_rechazada": _decide(config, previously_rejected=True),
        "ean_no_contradictorio": _decide(config, _page(ean_published=SECOND)),
        "sin_ambiguedad": _decide(config, _page(ambiguous=True)),
    }
    for check, decision in cases.items():
        assert not decision.confirm and not decision.checks[check], check
        assert decision.reasons
    unauthorized = _decide({**config, "search_mode": "model_color"}, method="ean")
    assert not unauthorized.confirm and "no autorizado" in unauthorized.reasons[0]
    card = _decide(config, evidence=_evidence(_page(source_url=BASE + "/search?q=x"),
                                              method="web:edel-optics-search-card"))
    assert not card.confirm and not card.checks["ficha_consultada"]
    no_page = _decide(config, evidence={"url": BASE + "/x.html", "state": "exact", "page": None})
    assert not no_page.confirm and "No hay evidencia" in no_page.reasons[0]


def test_rule_exact_ean_without_colour_still_needs_review(pilot):
    _, _, search, _, cid, _ = pilot
    configure(search, cid)
    decision = _decide(search.config(cid), _page(title="Prada PR 27ZS", identifiers=["PR 27ZS"], ean_published=EAN))
    assert not decision.confirm and not decision.checks["marca_modelo_color_exactos"]
    assert any("código de color" in reason for reason in decision.reasons)


def _legacy_candidates(pilot, monkeypatch):
    """Run discovery as before this feature (no automatic confirmation) to build historical candidates."""
    service, store, search, site, cid, _ = pilot
    configure(search, cid)
    only_pending(store, cid, [EAN, SECOND, THIRD])
    urls = {EAN: route(site, EAN)}
    for ean, name in ((SECOND, "Prada PR 26ZS 55-16"), (THIRD, None)):
        url = f"{BASE}/{ean}.html"
        site.add(BASE + "/search?q=" + quote(ean, safe=""), f'<a href="{url}">Prada {ean}</a>')
        site.add(url, prada_page(ean, **({"name": name} if name else {})))
        urls[ean] = url
    service.enable()
    discovery = PilotDiscovery(store)
    with monkeypatch.context() as patch:
        patch.setattr(price_discovery, "evaluate", lambda *args, **kwargs: AutoDecision(False, ["legacy"]))
        run_all_pending(discovery, search)
    frame = discovery.candidates().set_index("ean")
    assert set(frame.state) == {"candidate_found"} and set(frame.review_origin) == {""}
    return discovery, frame, urls


def test_historical_reevaluation_is_explicit_offline_and_audited(pilot, monkeypatch):
    service, store, search, site, cid, _ = pilot
    discovery, frame, urls = _legacy_candidates(pilot, monkeypatch)
    discovery.review(int(frame.loc[THIRD, "id"]), accept=False, note="Revisor descartó esta ficha")
    site.calls.clear()
    budgets = http_totals(store)
    before = commerce(store)

    preview = discovery.reevaluate()
    assert preview.set_index("ean")["result"].to_dict() == {EAN: "would_confirm", SECOND: "manual_review"}
    assert "código de color" in preview.set_index("ean").loc[SECOND, "reasons"]
    assert set(discovery.candidates().state) == {"candidate_found", "discarded"}
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pilot_candidate_reevaluations").fetchone()[0] == 0
    assert search._indexed(cid, EAN) == []

    with pytest.raises(PilotError, match="motivo"):
        discovery.reevaluate(apply=True)
    applied = discovery.reevaluate(note="Ana: aplicar regla de identidad comercial", apply=True)
    assert applied.set_index("ean")["result"].to_dict() == {EAN: "confirmed", SECOND: "manual_review"}

    rows = discovery.candidates().set_index("ean")
    assert (rows.loc[EAN, "state"], rows.loc[EAN, "review_origin"]) == ("confirmed", "automatic")
    assert "Reevaluación explícita" in rows.loc[EAN, "review_note"]
    assert rows.loc[SECOND, "state"] == "candidate_found"
    assert (rows.loc[THIRD, "state"], rows.loc[THIRD, "review_origin"]) == ("discarded", "manual")
    assert rows.loc[THIRD, "review_note"] == "Revisor descartó esta ficha"
    assert [(r["url"], r["source"]) for r in search._indexed(cid, EAN)] == [(urls[EAN], "auto_confirmed")]
    assert search._indexed(cid, THIRD) == []
    with store._connect() as connection:
        audit = [dict(r) for r in connection.execute(
            "SELECT * FROM pilot_candidate_reevaluations ORDER BY candidate_id")]
    assert [(r["result"], r["previous_state"], r["rule"]) for r in audit] == [
        ("confirmed", "candidate_found", AUTO_RULE), ("manual_review", "candidate_found", AUTO_RULE)]
    assert len({r["batch"] for r in audit}) == 1 and all(r["note"].startswith("Ana") for r in audit)
    assert json.loads(audit[1]["reasons"])["checks"]["marca_modelo_color_exactos"] is False
    assert site.calls == [] and http_totals(store) == budgets
    assert commerce(store) == before
    # Historical evidence is kept untouched and a new pass only proposes what still needs a person.
    assert discovery.reevaluate().set_index("ean")["result"].to_dict() == {SECOND: "manual_review"}
    assert discovery.coverage().query("ean == @EAN and competitor_id == @cid").iloc[0].state == "confirmed"


def test_reevaluation_respects_current_authorization(pilot, monkeypatch):
    _, store, search, site, cid, _ = pilot
    discovery, _, _ = _legacy_candidates(pilot, monkeypatch)
    search.update_config(cid, search_verified=False)
    preview = discovery.reevaluate()
    assert set(preview.result) == {"manual_review"}
    assert all("no verificada" in reasons for reasons in preview.reasons)


def test_reevaluation_waits_for_running_pilot(pilot, monkeypatch):
    from filelock import FileLock

    _, store, _, _, _, _ = pilot
    discovery, _, _ = _legacy_candidates(pilot, monkeypatch)
    with FileLock(f"{store.database_path}.pilot.lock", timeout=0):
        with pytest.raises(PilotError, match="incompatible"):
            discovery.reevaluate(note="Ana", apply=True)


def test_reevaluation_report_script_counts_without_touching_the_source(pilot, monkeypatch):
    import hashlib
    import importlib.util
    from pathlib import Path

    _, store, _, site, _, _ = pilot
    discovery, frame, _ = _legacy_candidates(pilot, monkeypatch)
    discovery.review(int(frame.loc[THIRD, "id"]), accept=False, note="Revisor descarta")
    site.calls.clear()
    files = [Path(str(store.database_path) + suffix) for suffix in ("", "-wal")]
    digest = lambda: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}  # noqa: E731
    before = digest()
    path = Path(__file__).resolve().parent.parent / "scripts" / "reevaluate_discovery_candidates.py"
    spec = importlib.util.spec_from_file_location("reevaluate_discovery_candidates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.report(store.database_path)
    assert (result["evaluated"], result["would_confirm"], result["manual_review"]) == (2, 1, 1)
    assert result["by_competitor"] == {"Visio Factory": {"would_confirm": 1, "manual_review": 1}}
    assert any("código de color" in reason for reason in result["manual_reasons"])
    assert digest() == before and site.calls == []
    assert set(discovery.candidates().state) == {"candidate_found", "discarded"}


def test_review_ui_separates_groups_and_reevaluates_with_preview(pilot, monkeypatch):
    import app_pages.price_comparator as page

    _, store, search, site, cid, _ = pilot
    discovery, frame, _ = _legacy_candidates(pilot, monkeypatch)
    discovery.review(int(frame.loc[THIRD, "id"]), accept=False, note="Revisor descarta")
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(page, "WebPriceSearch", lambda _: search)
    site.calls.clear()
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=60)
    assert not app.exception

    def metrics():
        return {metric.label: metric.value for metric in app.metric}

    assert {key: metrics()[key] for key in ("Confirmadas automáticamente", "Confirmadas manualmente",
                                            "Pendientes de revisión", "Ambiguas o rechazadas")} == {
        "Confirmadas automáticamente": "0", "Confirmadas manualmente": "0",
        "Pendientes de revisión": "2", "Ambiguas o rechazadas": "1"}
    assert any("código de color" in item.value for item in app.markdown)
    app.button(key="discovery_reevaluate_preview").click().run(timeout=60)
    assert metrics()["Se confirmarían automáticamente"] == "1"
    assert metrics()["Seguirían en revisión manual"] == "1"
    table = next(item.value for item in app.dataframe if "Resultado" in item.value.columns)
    assert list(table.columns) == ["Producto", "Competidor", "Resultado", "Motivos", "URL"]
    assert set(table["Resultado"]) == {"Se confirmaría automáticamente", "Seguiría en revisión manual"}
    assert set(table["Competidor"]) == {"Visio Factory"}
    assert app.button(key="discovery_reevaluate_apply").disabled is False
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pilot_candidate_reevaluations").fetchone()[0] == 0
    app.text_input(key="discovery_reevaluate_note").set_value("Ana: regla comercial acordada")
    app.button(key="discovery_reevaluate_apply").click().run(timeout=60)
    assert not app.exception
    assert any("1 fichas confirmadas automáticamente" in item.value for item in app.success)
    assert metrics()["Confirmadas automáticamente"] == "1" and metrics()["Pendientes de revisión"] == "1"
    app.radio(key="discovery_review_group").set_value("automatic").run(timeout=60)
    assert not app.exception
    assert app.selectbox(key="discovery_evidence_automatic").value == int(frame.loc[EAN, "id"])
    assert any(AUTO_RULE in str(item.value) for item in app.json)
    app.radio(key="discovery_review_group").set_value("doubtful").run(timeout=60)
    assert app.selectbox(key="discovery_evidence_doubtful").value == int(frame.loc[THIRD, "id"])
    assert site.calls == []
