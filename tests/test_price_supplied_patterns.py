"""Supplied URL hypotheses and synthetic governed reads; never live shop requests."""
import csv
import json
from pathlib import Path

import pytest
import requests

from price_batch import WatchBatch
from price_monitor import PriceMonitor
from price_url_candidates import candidate_plan, documented_measurement_records, resolve_product_attributes
from tests.test_price_locator import Site, _env, _jobs, _urls, _watch
from tests.web_fixtures import page


PRODUCT = {"ean": "2000000000008", "brand": "Miu Miu", "model": "MU 11ZS", "color": "16K5S0",
           "category": "sol", "caliber": "51-21"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Real HTTP forbidden; use synthetic transport")
    monkeypatch.setattr(requests.Session, "send", deny)


@pytest.mark.parametrize("changes,path", [
    ({}, "/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html"),
    ({"model": "MU11ZS", "caliber": "51"}, "/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html"),
    ({"color": "1AB5S0", "caliber": "54"}, "/es/gafas-de-sol/miu-miu-mu-11zs-1ab5s0-54.html"),
    ({"ean": "8056597981361", "category": "montura", "model": "MU 01XV",
      "color": "VAU1O1", "caliber": "50"},
     "/es/monturas/miu-miu-mu-01xv-vau1o1-50.html"),
    ({"brand": "Gucci", "model": "GG1660S", "color": "001", "caliber": "54"},
     "/es/gafas-de-sol/gucci-gg1660s-001-54.html"),
])
def test_domanci_uses_target_attributes_not_example_size(changes, path):
    product = {**PRODUCT, **changes}
    before = dict(product)
    plan = candidate_plan(product, "domanci.com")
    assert [c.url for c in plan.runnable] == ["https://domanci.com" + path]
    assert product == before
    provenance = plan.runnable[0].provenance()
    expected_date = "2026-11-04" if "ean" in changes else "2026-11-06"
    assert provenance["review_date"] == expected_date
    assert bool(provenance["review_base"]) == ("ean" in changes)
    assert provenance["attributes"]["caliber"] == path.removesuffix(".html").rsplit("-", 1)[1]
    if "ean" in changes:
        assert "E033" in provenance["evidence"]
    else:
        assert "not captured HTTP" in provenance["attributes"]["evidence_note"]


@pytest.mark.parametrize("caliber", ["", None, "unknown", "51 or 54"])
def test_domanci_never_borrows_size_from_supplied_url(caliber):
    plan = candidate_plan({**PRODUCT, "caliber": caliber}, "domanci.com")
    assert not plan.runnable and plan.reason_code == "missing_attribute"
    assert "calibre" in plan.reason


@pytest.mark.parametrize("model,color,expected_url", [
    ("PR D01S", "0CD80L", "https://www.congafasdesol.com/prada-pr-d01s-0cd80l.html"),
    ("PR06YS", "09Q5S0", "https://www.congafasdesol.com/prada-pr06ys-09q5s0.html"),
    ("PR 27ZS", "16K08Z", None),
])
def test_congafas_uses_only_exact_user_supplied_routes(model, color, expected_url):
    product = {**PRODUCT, "brand": "Prada", "model": model, "color": color, "caliber": ""}
    plan = candidate_plan(product, "www.congafasdesol.com")
    assert [candidate.url for candidate in plan.runnable] == ([expected_url] if expected_url else [])


def test_congafas_pattern_is_scoped_and_keeps_documented_fausto_name():
    gucci = {**PRODUCT, "brand": "Gucci", "model": "GG1660S", "color": "001"}
    plan = candidate_plan(gucci, "congafasdesol.com")
    assert not plan.runnable and plan.reason_code == "needs_authorized_discovery"
    assert "Prada" in plan.reason
    assert not candidate_plan({**gucci, "brand": "Prada", "category": "montura"},
                              "congafasdesol.com").runnable
    ford = {**gucci, "brand": "Tom Ford", "model": "FT0711", "color": "01A"}
    assert [c.url for c in candidate_plan(ford, "congafasdesol.com").runnable] == [
        "https://www.congafasdesol.com/tom-ford-fausto-ft0711-01a.html"]


def synthetic_env(tmp_path, shop, *, permission=True, robots="User-agent: *\nAllow: /\n"):
    name, base = (("Domanci", "https://domanci.com") if shop == "domanci" else
                  ("ConGafasDeSol", "https://www.congafasdesol.com"))
    site = Site(base=base, robots=robots)
    store, access, locator, clock, cid = _env(
        tmp_path, site, name=name, template=None, methods=("ficha",) if permission else ())
    product = PRODUCT if shop == "domanci" else {
        **PRODUCT, "brand": "Prada", "model": "PR 06YS", "color": "09Q5S0"}
    urls = [c.url for c in candidate_plan(product, base).runnable]
    _watch(store, [(product["ean"], product["brand"], product["model"], product["color"], product["caliber"])])
    monitor = PriceMonitor(store, clock=clock, transport=site)
    return store, access, locator, cid, site, product, urls, WatchBatch(store, locator=locator, monitor=monitor)


@pytest.mark.parametrize("shop", ["domanci", "congafas"])
def test_supplied_pattern_runs_through_governed_batch(tmp_path, shop):
    store, _, _, cid, site, product, urls, batch = synthetic_env(tmp_path, shop)
    # For ConGafasDeSol only the second spelling exists: no catalogue or search fallback is needed.
    url = urls[-1]
    site.pages[url] = page(name=f"{product['brand']} {product['model']} {product['color']}",
                           brand=product["brand"], gtin=product["ean"], price=123)
    preview = batch.preview([product["ean"]], [cid])
    assert preview.iloc[0]["ready"] and not site.calls
    result = batch.execute([product["ean"]], [cid], approved=True, sync_vytria=False)
    assert result["processed_pairs"] == 1 and result["vytria"] is None
    assert _urls(store, cid)["url"].tolist() == [url]
    assert _urls(store, cid)["status"].tolist() == ["verified"]
    assert result["runs"][0]["location_processed"] == 1
    assert result["runs"][0]["monitor_processed"] == 1
    assert all(call in {*urls, site.base + "/robots.txt"} for call in site.calls)


@pytest.mark.parametrize("shop", ["domanci", "congafas"])
@pytest.mark.parametrize("restriction", ["permission", "block", "budget", "robots"])
def test_new_candidates_do_not_authorize_reads(tmp_path, shop, restriction):
    store, access, locator, cid, site, product, urls, batch = synthetic_env(
        tmp_path, shop, permission=restriction != "permission",
        robots="User-agent: *\nDisallow: /\n" if restriction == "robots" else "User-agent: *\nAllow: /\n")
    if restriction == "block":
        access.report_block("competitor", cid, "captcha", method="ficha", detail="Synthetic block")
    elif restriction == "budget":
        access.set_budget("competitor", cid, daily_limit=0, per_minute_limit=600, pause_seconds=0, actor="test")
    preview = batch.preview([product["ean"]], [cid])
    assert bool(preview.iloc[0]["ready"]) == (restriction == "robots")
    locator.run([cid], eans=[product["ean"]])
    assert _urls(store, cid).empty
    assert not any(url in site.calls for url in urls)
    if restriction != "robots":
        assert not site.calls


@pytest.mark.parametrize("shop", ["domanci", "congafas"])
def test_generated_slug_does_not_override_wrong_colour(tmp_path, shop):
    store, _, locator, cid, site, product, urls, _ = synthetic_env(tmp_path, shop)
    site.pages[urls[0]] = page(name=f"{product['brand']} {product['model']} 16K08Z",
                             brand=product["brand"], gtin=product["ean"], price=123)
    locator.run([cid], eans=[product["ean"]])
    assert _jobs(store, cid)["state"].tolist() == ["review"]
    assert "verified" not in _urls(store, cid)["status"].tolist()


@pytest.mark.parametrize(("shop", "brand", "model", "color", "category", "ean", "url", "evidence"), [
    ("optica-optima.com", "Gucci", "GG1680S", "004", "sol", "0889652498782",
     "https://optica-optima.com/gafas-de-sol/gucci-gg1680s-004.html", "N004"),
    ("optica-optima.com", "Bottega Veneta", "BV1274S", "005", "sol", "0889652637075",
     "https://optica-optima.com/gafas-de-sol/bottega-veneta-bv1274s-005.html", "N005"),
    ("amevista.com", "Chloe", "CH0289S", "003", "sol", "0889652543574",
     "https://www.amevista.com/es/chlo-ch0289s-003", "N002"),
    ("amevista.com", "Ray Ban", "RB4441D", "667787", "sol", "8056262268902",
     "https://www.amevista.com/es/ray-ban-rb4441d-667787", "N003"),
    ("visiofactory.es", "Saint Laurent", "SL M160", "001", "sol", "0889652651774",
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
     "saint-laurent-monogram-sl-m160-001-53-19-negro.html", "N006"),
    ("visiofactory.es", "Miu Miu", "MU 53WV", "ZVN1O1", "montura", "8056262410929",
     "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/"
     "miu-miu-0mu-53wv-zvn-1o1-53-20-pale-gold.html", "N011"),
    ("visiofactory.es", "Tiffany & Co.", "TF3104D", "6216S4", "sol", "8056262010730",
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
     "tiffany-co-tf3104d-6216s4-56-18-oro.html", "N012"),
    ("edel-optics.es", "Ray Ban", "RB4441D", "667787", "sol", "8056262268902",
     "https://www.edel-optics.es/RB4441D-667787-de-Ray-Ban.html", "N013"),
])
def test_supplied_catalog_links_are_scoped_candidates_not_verified_offers(
        shop, brand, model, color, category, ean, url, evidence):
    plan = candidate_plan({"ean": ean, "brand": brand, "model": model, "color": color, "category": category}, shop)

    assert [candidate.url for candidate in plan.candidates] == [url]
    candidate = plan.candidates[0]
    assert evidence in candidate.evidence
    assert candidate.state == ("probable" if evidence in {"N002", "N012"} else "pending_verification")
    assert "Especificacion_100_EAN_6_competidores.json" in candidate.origin
    if evidence in {"N002", "N012"}:
        assert not plan.runnable and candidate.reason


@pytest.mark.parametrize(("model", "color", "ean", "suffix", "check_id"), [
    ("FT0711", "01A", "0889214029614", "fausto", "601"),
    ("FT1301", "52A", "0889214625625", "joey-02", "607"),
    ("FT1310", "52E", "0889214625991", "chloe-02", "608"),
    ("FT1044", "93E", "0889214684097", "bronson", "609"),
    ("GG1660S", "002", "0889652494838", "gg-logo", "614"),
])
def test_amevista_uses_only_the_historically_observed_commercial_suffix(model, color, ean, suffix, check_id):
    plan = candidate_plan(
        {"ean": ean, "brand": "Tom Ford" if model.startswith("FT") else "Gucci",
         "model": model, "color": color, "category": "sol"},
        "amevista.com",
    )

    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert candidate.url.endswith(f"-{suffix}")
    assert f"report2-check:{check_id}" in candidate.evidence
    assert "recibo histórico" in candidate.origin.lower()


def test_domanci_keeps_route_size_separate_from_ean_measurement():
    product = {"ean": "8056262410929", "brand": "Miu Miu", "model": "MU 53WV",
               "color": "ZVN1O1", "category": "montura"}
    resolved = resolve_product_attributes(product)
    plan = candidate_plan(product, "domanci.com")

    assert resolved.product["caliber"] == "53"
    assert resolved.product["bridge"] == "20"
    assert resolved.evidence["fuente_medidas"].endswith("#N011")
    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert candidate.url == "https://domanci.com/es/monturas/miu-miu-mu-53wv-zvn1o1-51.html"
    assert candidate.attributes["caliber"] == "53"
    assert candidate.attributes["bridge"] == "20"
    assert candidate.attributes["route_attributes"] == {"caliber": "51"}
    assert candidate.state == "probable"
    assert not plan.runnable


def test_domanci_bruce_link_does_not_infer_caliber_or_identity_from_slug():
    product = {"ean": "0889214403407", "brand": "Tom Ford", "model": "FT1026",
               "color": "05E", "category": "sol"}
    plan = candidate_plan(product, "domanci.com")

    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert candidate.url == "https://domanci.com/es/gafas-de-sol/tom-ford-bruce-ft1026-05e-61.html"
    assert candidate.attributes.get("caliber") is None
    assert candidate.attributes["route_attributes"] == {"caliber": "61"}
    assert candidate.state == "probable"
    assert not plan.runnable


def test_congafas_does_not_generate_unobserved_prada_slugs():
    product = {**PRODUCT, "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}

    plan = candidate_plan(product, "congafasdesol.com")

    assert not plan.candidates
    assert plan.reason_code == "needs_authorized_discovery"
    assert "historial" in plan.reason.lower() and "403" in plan.reason
    assert plan.discovery_plan["entrypoints"] == []


@pytest.mark.parametrize(("domain", "category"), [
    ("optica-optima.com", "sol"),
    ("amevista.com", "sol"),
    ("visiofactory.es", "montura"),
    ("edel-optics.es", "sol"),
    ("domanci.com", "sol"),
    ("congafasdesol.com", "sol"),
])
def test_catalog_plans_are_documentary_and_require_existing_active_sources(domain, category):
    plan = candidate_plan({**PRODUCT, "category": category}, domain)

    assert plan.discovery_plan["source_role"] == "documentary_proposal"
    assert plan.discovery_plan["execution_source"] == "active_discovery_sources_only"
    assert plan.discovery_plan["automatic_execution"] is False
    assert plan.discovery_plan["requires_existing_authorization"] is True
    assert "Especificacion_100_EAN_6_competidores.json" in plan.discovery_plan["evidence_source"]
    assert "2026-11-13" in plan.discovery_plan["evidence_source"]


def test_generated_matrix_preserves_manifest_scope_and_separates_unchecked_results():
    with (ROOT / "price_trial_manifest.json").open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    with (ROOT / "docs" / "PRICE_COVERAGE_100_EAN_6_STORES.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))

    expected_pairs = {(ean, competitor_id) for ean in manifest["eans"]
                      for competitor_id in manifest["competitors"]}
    actual_pairs = {(row["ean"], row["competidor_id"]) for row in rows}

    assert len(manifest["eans"]) == 100
    assert len(manifest["competitors"]) == 6
    assert len(rows) == 600 and len(actual_pairs) == 600
    assert actual_pairs == expected_pairs
    assert all(len(row["ean"]) == 13 and row["ean"].isdigit() for row in rows)
    assert all(row["descubrimiento_catalogo_implementado"] == "true" for row in rows)
    assert all(row["estado_ficha_en_esta_revision"] == "not_checked" for row in rows)
    assert all(row["estado_oferta_comparable_en_esta_revision"] == "not_checked" for row in rows)


@pytest.mark.parametrize(("shop", "category"), [
    ("optica-optima.com", "sol"),
    ("amevista.com", "montura"),
    ("visiofactory.es", "sol"),
    ("edel-optics.es", "sol"),
    ("domanci.com", "montura"),
    ("congafasdesol.com", "sol"),
])
def test_catalog_discovery_is_a_shared_plan_not_a_candidate_or_permission(shop, category):
    plan = candidate_plan({**PRODUCT, "category": category, "model": "UNLISTED-1"}, shop)

    assert plan.discovery_plan["shared"] is True
    assert plan.discovery_plan["requires_existing_authorization"] is True
    assert plan.discovery_plan["category"] == category
    if shop == "congafasdesol.com":
        assert plan.discovery_plan["entrypoints"] == []
        assert plan.discovery_plan["halted_after_403"] is True
    else:
        assert plan.discovery_plan["entrypoints"]
        assert all(url.startswith("https://") for url in plan.discovery_plan["entrypoints"])
    assert not any(url in [candidate.url for candidate in plan.candidates]
                   for url in plan.discovery_plan["entrypoints"])


def test_new_ean_measurements_preserve_published_visio_variant_sizes():
    records = documented_measurement_records()

    assert len(records) == 15
    assert records["8056262410929"]["calibre"] == "53"
    assert records["8056262410929"]["puente"] == "20"
    assert records["8056262410929"]["asociacion_medidas"] == "gtin_exact_match"


@pytest.mark.parametrize(("ean", "brand", "model", "color", "category", "size", "evidence", "url"), [
    ("0889652651774", "Saint Laurent", "SL M160", "001", "sol", "53-19", "N006",
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/"
     "saint-laurent-monogram-sl-m160-001-53-19-negro.html"),
    ("0889652359700", "Saint Laurent", "SL M94", "001", "sol", "53-20", "N007",
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/slm94-001-53-20.html"),
    ("8056597669016", "Miu Miu", "MU 04UV", "1AB1O1", "montura", "52-19", "N008",
     "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/mu-04uv-1ab1o1-52-19.html"),
    ("8056597981361", "Miu Miu", "MU 01XV", "VAU1O1", "montura", "50-18", "N009",
     "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/mu-01xv-vau-1o1-50-18.html"),
    ("8056262600078", "Miu Miu", "MU 01YV", "14L1O1", "montura", "51-16", "N010",
     "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/"
     "miu-miu-mu-01yv-14l1o1-51-16-havana-honey.html"),
    ("8056262410929", "Miu Miu", "MU 53WV", "ZVN1O1", "montura", "53-20", "N011",
     "https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/"
     "miu-miu-0mu-53wv-zvn-1o1-53-20-pale-gold.html"),
])
def test_indexed_measurements_reach_exact_visio_candidates(
        ean, brand, model, color, category, size, evidence, url):
    product = {"ean": ean, "brand": brand, "model": model, "color": color, "category": category}
    resolved = resolve_product_attributes(product)
    plan = candidate_plan(product, "visiofactory.es")

    assert not resolved.conflict
    assert f"{resolved.product['caliber']}-{resolved.product['bridge']}" == size
    assert resolved.evidence["fuente_medidas"].endswith(f"#{evidence}")
    assert [candidate.url for candidate in plan.runnable] == [url]
    assert evidence in plan.runnable[0].evidence
