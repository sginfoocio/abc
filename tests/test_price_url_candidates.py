"""Documentary URL checks and explicitly SYNTHETIC shop responses. No captured HTML or live HTTP."""
from dataclasses import asdict
import csv
import json
import sqlite3
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests
from streamlit.testing.v1 import AppTest

from price_batch import WatchBatch, batch_pairs_export
from price_comparator import PriceComparatorStore, normalize_gtin
from price_monitor import PriceMonitor
from price_url_candidates import EXAMPLES, candidate_plan
from tests.test_price_locator import Site, _env, _jobs, _urls, _watch
from tests.web_fixtures import page

PRADA = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z",
         "category": "All / Gafas de Sol"}
FORD = {"ean": "889214042422", "brand": "Tom Ford", "model": "FT5634B", "color": "001",
        "category": "All / Monturas"}
GUCCI = {"ean": "0889652494821", "brand": "Gucci", "model": "GG1660S", "color": "001",
         "category": "All / Gafas de Sol"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Real HTTP forbidden; use the explicitly synthetic transport")
    monkeypatch.setattr(requests.Session, "send", deny)


def plan_for(product, shop):
    example = next(example for example in EXAMPLES if example.shop == shop)
    return candidate_plan(product, urlparse(example.url).netloc)


def urls(plan):
    return [candidate.url for candidate in plan.runnable]


def dump(store):
    with store._connect() as con:
        return "\n".join(con.iterdump())


def test_documentary_registry_is_complete_and_contains_no_offers_or_permissions():
    assert len(EXAMPLES) == 47
    assert len({example.shop for example in EXAMPLES}) == 14
    assert {example.id for example in EXAMPLES} == {f"E{i:03d}" for i in range(1, 48)}
    for example in EXAMPLES:
        assert example.url.startswith("https://")
        assert not {"price", "availability", "market", "authorized", "caliber"} & asdict(example).keys()


@pytest.mark.parametrize("shop,product,expected", [
    ("optima", PRADA, "/gafas-de-sol/prada-pr-27zs-16k08z.html"),
    ("optima", FORD, "/gafas-graduadas/tom-ford-ft5634-b-001.html"),
    ("otticait", FORD, "/tom-ford-ft5634-b-001"),
    ("bassol", {**PRADA, "color": "16K08A", "caliber": "54-21"},
     "/products/prada-pr-27zs-16k08a-54"),
    ("visual", {**PRADA, "color": "16K08A", "caliber": "54"},
     "/products/prada-pr-27zs-16k08a-54"),
    ("visio", {**PRADA, "color": "16K08A", "caliber": "54-21"},
     "/gafas-de-sol/todas-las-solares/pr27zs-16k08a-54-21.html"),
])
def test_bounded_generation_and_specific_aliases(shop, product, expected):
    plan = plan_for(product, shop)
    assert len(plan.runnable) == 1 and urls(plan)[0].endswith(expected)
    provenance = plan.runnable[0].provenance()
    assert provenance["evidence"] and provenance["rule"]
    assert provenance["attributes"]["ean"] == normalize_gtin(product["ean"])
    assert provenance["attributes"]["color"] == product["color"]
    assert provenance["review_date"] == "2026-11-04"
    assert provenance["origin"].endswith("Evidencias_47.csv")


@pytest.mark.parametrize("shop", ["bassol", "visual", "visio"])
def test_no_measurement_guesses_from_other_colour_or_gtin(shop):
    plan = plan_for({**PRADA, "color": "16K08A"}, shop)
    assert not plan.runnable
    assert plan.reason_code == "missing_attribute" and "calibre" in plan.reason
    if shop == "visio":
        assert "puente" in plan.reason
    other_ean = plan_for({**PRADA, "ean": "8056597875226", "color": "16K08A"}, shop)
    assert not other_ean.runnable


def test_bridge_is_required_only_to_construct_visio_and_is_not_invented():
    plan = plan_for({**PRADA, "color": "16K08A", "caliber": "54"}, "visio")
    assert not plan.runnable and "puente" in plan.reason and "calibre" not in plan.reason
    assert len(plan_for({**PRADA, "color": "16K08A", "caliber": "54"}, "bassol").runnable) == 1


@pytest.mark.parametrize("product,expected", [
    ({**PRADA, "brand": "Tom Ford", "model": "FT1044", "color": "01E", "caliber": "60-12"},
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/ft1044-s-01e-60-12.html"),
    ({**PRADA, "brand": "Tom Ford", "model": "FT1044/S", "color": "01E",
      "caliber": "60", "bridge": "12"},
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/ft1044-s-01e-60-12.html"),
    ({**PRADA, "brand": "Miu Miu", "model": "MU 52YS", "color": "ZVN5D1", "caliber": "54-21"},
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/mu-52ys-zvn-5d1-54-21.html"),
    ({**PRADA, "brand": "Ray Ban", "model": "RB2140", "color": "902", "caliber": "50"},
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-902-50.html"),
    ({**PRADA, "brand": "Ray Ban", "model": "RB2140", "color": "902/58", "caliber": "50-22"},
     "https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-902-58-50-22.html"),
])
def test_visio_additional_documented_formats_use_explicit_attributes(product, expected):
    plan = plan_for(product, "visio")

    assert urls(plan) == [expected]
    candidate = plan.runnable[0]
    assert candidate.rule == "sample_pattern:visio"
    assert candidate.evidence == ["user:visio-public-fiche-format-examples"]
    assert candidate.attributes["ean"] == normalize_gtin(product["ean"])
    assert candidate.attributes["caliber"] == product["caliber"].split("-")[0]
    assert candidate.origin.startswith("User-supplied manual observations")
    assert candidate.review_date == ""


@pytest.mark.parametrize("product,reason", [
    ({**PRADA, "brand": "Tom Ford", "model": "FT1044", "color": "01E", "caliber": "60"},
     "puente"),
    ({**PRADA, "brand": "Miu Miu", "model": "MU52YS", "color": "ZVN5D1", "caliber": "54"},
     "puente"),
    ({**PRADA, "brand": "Ray Ban", "model": "RB2140", "color": "902/58", "caliber": "50"},
     "puente"),
])
def test_visio_additional_formats_do_not_infer_missing_bridge(product, reason):
    plan = plan_for(product, "visio")

    assert not plan.runnable
    assert plan.reason_code == "missing_attribute"
    assert reason in plan.reason


def test_visio_additional_formats_remain_narrow_and_do_not_infer_colour_aliases():
    unsupported_model = {**PRADA, "brand": "Tom Ford", "model": "FT1045", "color": "01E",
                         "caliber": "60-12"}
    unsupported_miu_colour = {**PRADA, "brand": "Miu Miu", "model": "MU52YS", "color": "1AB5S0",
                              "caliber": "54-21"}

    assert not plan_for(unsupported_model, "visio").runnable
    unsupported = plan_for(unsupported_miu_colour, "visio")
    assert not unsupported.runnable and "separador ZVN-5D1" in unsupported.reason


def test_only_dimensions_associated_to_exact_gtin_support_new_sized_candidate():
    original = dict(PRADA)
    plan = plan_for(PRADA, "bassol")
    assert urls(plan) == ["https://www.opticabassol.com/products/prada-pr-27zs-16k08z-54"]
    assert plan.runnable[0].attributes["dimension_source"].startswith("E041:")
    assert PRADA == original and "caliber" not in PRADA


def test_ean_linked_measurement_evidence_drives_candidates_with_traceable_source():
    evidence_path = Path(__file__).parents[1] / "docs" / "PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv"
    with evidence_path.open(encoding="utf-8-sig", newline="") as stream:
        evidence = {row["ean"]: row for row in csv.DictReader(stream, delimiter=";")}

    assert len(evidence) == 9
    assert "8056266001833" not in evidence
    assert evidence["8056262833902"]["calibre"] == "48"
    assert evidence["8056262833902"]["puente"] == ""

    def product(ean):
        row = evidence[ean]
        return {
            "ean": row["ean"], "brand": row["marca"], "model": row["modelo"],
            "color": row["color"], "category": "All / Gafas de Sol",
            "caliber": row["calibre"], "bridge": row["puente"],
            "measurement_source": row["fuente_medidas"],
        }

    domanci = candidate_plan(product("0889652429717"), "domanci.com").runnable[0]
    assert domanci.url.endswith("/saint-laurent-sl-596-dune-006-58.html")
    assert domanci.attributes["dimension_source"] == evidence["0889652429717"]["fuente_medidas"]

    visio = candidate_plan(product("8056597886956"), "visiofactory.es").runnable[0]
    assert visio.url.endswith("/mu-52ys-zvn-5d1-54-21.html")
    assert visio.attributes["dimension_source"] == evidence["8056597886956"]["fuente_medidas"]


@pytest.mark.parametrize("shop,brand,category", [
    ("optima", "Gucci", "sol"), ("optima", "Prada", "montura"),
    ("bassol", "Tom Ford", "sol"), ("visual", "Gucci", "sol"),
    ("otticait", "Tom Ford", "sol"), ("visio", "Cartier", "sol"),
    ("visio", "Ray Ban", "sol"), ("visio", "Prada", "montura"),
])
def test_no_generation_outside_demonstrated_brand_category(shop, brand, category):
    plan = plan_for({**PRADA, "brand": brand, "category": category, "model": "NEW123", "caliber": "54-21"}, shop)
    assert not plan.runnable and plan.reason_code == "needs_authorized_discovery"


def test_aliases_do_not_expand_to_other_products():
    assert urls(plan_for(PRADA, "amevista")) == ["https://www.amevista.com/es/prada-0pr-27zs-16k08z"]
    documented_pr = plan_for({**PRADA, "color": "19J09Z"}, "amevista")
    assert urls(documented_pr) == ["https://www.amevista.com/es/prada-pr-27zs-19j09z"]
    generated_pr = plan_for({**PRADA, "model": "PR 65ZS", "color": "ZVN09T"}, "amevista")
    assert urls(generated_pr) == ["https://www.amevista.com/es/prada-pr-65zs-zvn09t"]
    assert generated_pr.runnable[0].attributes["model_alias_rule"].startswith("Observed PR model prefix")
    tiffany = {**PRADA, "brand": "Tiffany & Co.", "model": "TF3104D", "color": "6216S4",
               "ean": "8056262010730"}
    assert urls(plan_for(tiffany, "amevista")) == [
        "https://www.amevista.com/es/tiffany-tf3104d-6216s4"
    ]
    assert urls(plan_for({**tiffany, "model": "TF3105D"}, "amevista")) == [
        "https://www.amevista.com/es/tiffany-tf3105d-6216s4"
    ]
    unknown = plan_for({**FORD, "model": "FT5737B", "color": "045"}, "optima")
    assert urls(unknown)[0].endswith("ft5737b-045.html")  # no universal "-B" rewrite


@pytest.mark.parametrize("product,expected,evidence", [
    ({**PRADA, "model": "PR 15WS", "color": "01R0A6"},
     "https://www.amevista.com/es/prada-pr-15ws-01r0a6", ["E017", "E018"]),
    ({**FORD, "brand": "Tom Ford", "model": "FT0711", "color": "01A",
      "category": "All / Gafas de Sol"},
     "https://www.amevista.com/es/tom-ford-ft0711-01a", ["E019"]),
])
def test_amevista_uses_documented_brand_model_color_patterns(product, expected, evidence):
    plan = plan_for(product, "amevista")

    assert urls(plan) == [expected]
    assert plan.runnable[0].rule == "sample_pattern:amevista"
    assert plan.runnable[0].evidence == evidence
    assert plan.runnable[0].attributes["locale"] == "es (Spanish-market catalog; E021 documents a separate US URL)"


@pytest.mark.parametrize("brand,category", [
    ("Tom Ford", "montura"), ("Bottega Veneta", "montura"), ("Ray Ban", "montura"),
])
def test_amevista_excludes_undocumented_brand_category_combinations(brand, category):
    product = {**PRADA, "brand": brand, "category": category, "model": "MODEL123", "color": "001"}

    plan = plan_for(product, "amevista")

    assert not plan.runnable
    assert plan.reason_code == "needs_authorized_discovery"
    if category != "sol":
        if brand == "Bottega Veneta":
            assert "solo cubre gafas de sol" in plan.reason
        elif brand == "Ray Ban":
            assert "RB2140 y RB4472" in plan.reason
        else:
            assert "solo para gafas de sol" in plan.reason
    else:
        assert "Marca fuera" in plan.reason


@pytest.mark.parametrize("product,expected", [
    ({**PRADA, "ean": "0805289126577", "brand": "Ray Ban", "model": "RB2140", "color": "901"},
     "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"),
    ({**PRADA, "ean": "0805289126584", "brand": "Ray Ban", "model": "RB2140", "color": "901"},
     "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"),
    ({**PRADA, "ean": "8056266001833", "brand": "Ray Ban", "model": "RB2140", "color": "901S31"},
     "https://www.amevista.com/es/ray-ban-rb2140-901s31-wayfarer"),
    ({**PRADA, "ean": "8056262833902", "brand": "Ray Ban", "model": "RB4472", "color": "1359/2"},
     "https://www.amevista.com/es/ray-ban-rb4472-1359-2"),
    ({**PRADA, "ean": "0889652429717", "brand": "Saint Laurent", "model": "SL 596 DUNE", "color": "006"},
     "https://www.edel-optics.es/SL-596-DUNE-006-de-Saint-Laurent.html"),
    ({**PRADA, "ean": "0889652650838", "brand": "Saint Laurent", "model": "SL 872 ERIN", "color": "001"},
     "https://www.edel-optics.es/SL-872-ERIN-001-de-Saint-Laurent.html"),
    ({**PRADA, "ean": "0889652675602", "brand": "Saint Laurent", "model": "SL 908", "color": "002"},
     "https://www.edel-optics.es/SL-908-002-de-Saint-Laurent.html"),
])
def test_seven_pending_products_get_only_documented_manual_routes(product, expected):
    shop = "amevista" if product["brand"] == "Ray Ban" else "edel"
    plan = plan_for(product, shop)

    assert urls(plan) == [expected]
    assert plan.runnable[0].origin.startswith("User-supplied manual observations")
    assert plan.runnable[0].review_date == ""
    assert plan.runnable[0].attributes["ean"] == normalize_gtin(product["ean"])
    assert "price" not in plan.runnable[0].attributes
    assert "selectedSize_product_id" not in plan.runnable[0].attributes


def test_rayban_route_aliases_do_not_generalize_to_other_models_or_colors():
    unknown_model = {**PRADA, "brand": "Ray Ban", "model": "RB4441D", "color": "667787"}
    unknown_color = {**PRADA, "brand": "Ray Ban", "model": "RB4472", "color": "002"}
    unobserved_rb2140_color = {**PRADA, "brand": "Ray Ban", "model": "RB2140", "color": "90158"}

    assert not plan_for(unknown_model, "amevista").runnable
    assert not plan_for(unknown_color, "amevista").runnable
    assert not plan_for(unobserved_rb2140_color, "amevista").runnable


def test_edel_saint_laurent_route_preserves_commercial_model_names_and_scope():
    dune = {**PRADA, "brand": "Saint Laurent", "model": "SL 596 DUNE", "color": "003"}
    unknown = {**dune, "model": "SL 999"}

    assert urls(plan_for(dune, "edel")) == [
        "https://www.edel-optics.es/SL-596-DUNE-003-de-Saint-Laurent.html"
    ]
    assert not plan_for(unknown, "edel").runnable


@pytest.mark.parametrize("product,expected,evidence", [
    ({**PRADA, "ean": "8056262398043", "brand": "Miu Miu", "model": "MU 04ZS",
      "color": "14L4I0", "category": "All / Gafas de Sol"},
     "https://www.amevista.com/es/miu-miu-mu-04zs-14l4i0", "manual:amevista-miu-miu-sunglasses"),
    ({**PRADA, "ean": "8056597981361", "brand": "Miu Miu", "model": "MU 01XV",
      "color": "VAU1O1", "category": "All / Monturas"},
     "https://www.amevista.com/es/miu-miu-mu-01xv-vau1o1", "manual:amevista-miu-miu-frames"),
    ({**PRADA, "ean": "0889652637075", "brand": "Bottega Veneta", "model": "BV1274S",
      "color": "005", "category": "All / Gafas de Sol"},
     "https://www.amevista.com/es/bottega-veneta-bv1274s-005", "manual:amevista-bottega-veneta-sunglasses"),
])
def test_amevista_manual_brand_category_patterns_are_attribute_based(product, expected, evidence):
    plan = plan_for(product, "amevista")

    assert urls(plan) == [expected]
    candidate = plan.runnable[0]
    assert candidate.rule == "sample_pattern:amevista"
    assert candidate.evidence == [evidence]
    assert candidate.origin.startswith("User-supplied manual observations")
    assert candidate.attributes["ean"] == normalize_gtin(product["ean"])
    assert "not captured HTTP" in candidate.attributes["evidence_note"]
    assert candidate.review_date == ""
    alternate_ean = plan_for({**product, "ean": "8056597886918"}, "amevista")
    assert urls(alternate_ean) == [expected]


def test_amevista_miu_miu_pattern_is_category_scoped():
    sun = {**PRADA, "brand": "Miu Miu", "model": "MU 04ZS", "color": "14L4I0",
           "category": "All / Gafas de Sol"}
    frames = {**sun, "model": "MU 01XV", "color": "VAU1O1", "category": "All / Monturas"}

    sun_plan = plan_for(sun, "amevista")
    frame_plan = plan_for(frames, "amevista")
    assert sun_plan.runnable[0].attributes["category"] == sun["category"]
    assert frame_plan.runnable[0].attributes["category"] == frames["category"]
    assert sun_plan.runnable[0].evidence != frame_plan.runnable[0].evidence
    assert plan_for({**sun, "brand": "Bottega Veneta", "category": "All / Monturas"},
                    "amevista").runnable == []
    assert plan_for({**sun, "brand": "Tom Ford", "category": "All / Monturas"},
                    "amevista").runnable == []


@pytest.mark.parametrize("product,expected", [
    ({**PRADA, "ean": "8056262398043", "brand": "Miu Miu", "model": "MU 04ZS",
      "color": "14L4I0", "category": "All / Gafas de Sol"},
     "https://www.edel-optics.es/MU-04ZS-14L4I0-de-Miu-Miu.html"),
    ({**PRADA, "ean": "0889652637075", "brand": "Bottega Veneta", "model": "BV1274S",
      "color": "005", "category": "All / Gafas de Sol"},
     "https://www.edel-optics.es/BV1274S-005-de-Bottega-Veneta.html"),
])
def test_edel_attribute_route_leaves_variant_resolution_to_the_ficha(product, expected):
    plan = plan_for(product, "edel")

    assert urls(plan) == [expected]
    candidate = plan.runnable[0]
    assert candidate.rule == "sample_pattern:edel"
    assert candidate.attributes["variant_resolution"].startswith("Resolve the unique checked prd<id> control")
    assert "selectedSize" not in candidate.url
    assert candidate.attributes["ean"] == normalize_gtin(product["ean"])


@pytest.mark.parametrize("product,expected", [
    (GUCCI, "https://www.amevista.com/es/gucci-gg1660s-001"),
    ({**GUCCI, "model": "GG1660S", "color": "002", "ean": "0889652494838"},
     "https://www.amevista.com/es/gucci-gg1660s-002"),
    ({**GUCCI, "model": "GG1680S", "color": "004", "ean": "0889652498782"},
     "https://www.amevista.com/es/gucci-gg1680s-004"),
])
def test_amevista_gucci_candidates_are_scoped_and_not_ean_specific(product, expected):
    plan = plan_for(product, "amevista")

    assert urls(plan) == [expected]
    provenance = plan.runnable[0].provenance()
    assert provenance["rule"] == "sample_pattern:amevista"
    assert provenance["evidence"] == ["user-provided candidate URL"]
    assert provenance["origin"] == "user-provided candidate URL"
    assert "not identity evidence" in provenance["attributes"]["evidence_note"]
    assert not plan_for({**product, "category": "montura"}, "amevista").runnable
    assert not plan_for({**product, "brand": "Ray Ban"}, "amevista").runnable


def test_amevista_gucci_candidate_flows_through_preview_prepare_and_verified_identity(tmp_path):
    site = Site(base="https://www.amevista.com", robots="User-agent: *\nAllow: /\n")
    store, _, locator, _, cid = _env(tmp_path, site, name="Amevista", template=None, methods=("ficha",))
    ean = normalize_gtin(GUCCI["ean"])
    url = "https://www.amevista.com/es/gucci-gg1660s-001"
    site.pages[url] = page(name="Gucci GG1660S 001", brand="Gucci", gtin=ean, price=123)
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "")])
    batch = WatchBatch(store, locator=locator, monitor=PriceMonitor(store, transport=site))

    preview = batch.preview([ean], [cid])

    assert preview.iloc[0]["ready"]
    assert preview.iloc[0]["candidate_count"] == 1
    assert preview.iloc[0]["local_candidates"][0]["url"] == url
    assert not site.calls and _urls(store, cid).empty
    prepared = batch.prepare([ean], [cid])
    assert prepared.iloc[0]["queue_state"] == "pending"
    assert not site.calls and _urls(store, cid).empty
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0

    report = locator.run([cid], eans=[ean])

    assert report.states["done"] == 1
    assert report.outcomes[0]["candidate_plan"]["candidates"][0]["url"] == url
    assert report.outcomes[0]["candidate_attempts"][0]["identity_status"] == "exact_match"
    assert _urls(store, cid)["status"].tolist() == ["verified"]
    assert _urls(store, cid)["url"].tolist() == [url]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0
    assert site.calls == ["https://www.amevista.com/robots.txt", url]


def test_amevista_gucci_supervised_batch_locates_then_monitors_without_vytria_sync(tmp_path):
    site = Site(base="https://www.amevista.com", robots="User-agent: *\nAllow: /\n")
    store, _, locator, _, cid = _env(tmp_path, site, name="Amevista", template=None, methods=("ficha",))
    ean = normalize_gtin(GUCCI["ean"])
    url = "https://www.amevista.com/es/gucci-gg1660s-001"
    site.pages[url] = page(name="Gucci GG1660S 001", brand="Gucci", gtin=ean, price=123)
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "")])
    monitor = PriceMonitor(store, transport=site)
    batch = WatchBatch(store, locator=locator, monitor=monitor)

    preview = batch.preview([ean], [cid])
    assert preview.iloc[0]["ready"] and preview.iloc[0]["candidate_count"] == 1
    prepared = batch.prepare([ean], [cid])
    assert prepared.iloc[0]["ready"] and prepared.iloc[0]["queue_state"] == "pending"
    assert site.calls == []

    result = batch.execute([ean], [cid], approved=True, sync_vytria=False)

    assert result["vytria"] is None
    assert result["processed_pairs"] == 1
    pair = result["pairs"][0]
    assert pair["processed"] and pair["category"] == "checked_now"
    assert pair["outcomes"][0]["candidate_plan"]["candidates"][0]["url"] == url
    assert pair["outcomes"][0]["candidate_attempts"][0]["identity_status"] == "exact_match"
    assert _urls(store, cid)["status"].tolist() == ["verified"]
    assert _urls(store, cid)["url"].tolist() == [url]
    assert result["runs"][0]["location_processed"] == 1
    assert result["runs"][0]["monitor_processed"] == 1
    assert "https://www.amevista.com/robots.txt" in site.calls
    assert url in site.calls
    csv_pair = next(csv.DictReader(StringIO(batch_pairs_export(result["pairs"]).to_csv(index=False))))
    json_result = json.loads(json.dumps(result, ensure_ascii=False))
    assert json.loads(csv_pair["outcomes"]) == json_result["pairs"][0]["outcomes"]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?",
                           (ean, cid)).fetchone()[0] == 1


@pytest.mark.parametrize("page_html,expected_state", [
    (lambda ean: page(name="Gucci GG1660S 002", brand="Gucci", gtin=ean, price=123), "review"),
    (lambda ean: '<script type="application/ld+json">' + json.dumps({
        "@type": "Product", "name": "Gucci GG1660S 001", "brand": "Gucci",
        "gtin13": ean, "gtin12": "889652494838",
        "offers": {"@type": "Offer", "price": "123", "priceCurrency": "EUR",
                   "availability": "https://schema.org/InStock"},
    }) + "</script>", "review"),
    (lambda ean: '<script type="application/ld+json">' + json.dumps({
        "@type": "CollectionPage", "name": "Gucci GG1660S 001",
        "url": "https://www.amevista.com/es/gucci-gg1660s-001",
    }) + "</script>", "unavailable"),
])
def test_amevista_gucci_candidate_does_not_confirm_contradictory_variants(tmp_path, page_html, expected_state):
    site = Site(base="https://www.amevista.com", robots="User-agent: *\nAllow: /\n")
    store, _, locator, _, cid = _env(tmp_path, site, name="Amevista", template=None, methods=("ficha",))
    ean = normalize_gtin(GUCCI["ean"])
    url = "https://www.amevista.com/es/gucci-gg1660s-001"
    site.pages[url] = page_html(ean)
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "")])

    report = locator.run([cid], eans=[ean])

    assert _jobs(store, cid)["state"].tolist() == [expected_state]
    attempt = report.outcomes[0]["candidate_attempts"][0]
    assert attempt["candidate_url"] == url
    assert site.calls == ["https://www.amevista.com/robots.txt", url]
    if expected_state == "review":
        assert _urls(store, cid)["status"].tolist() == ["probable"]
        assert _urls(store, cid)["url"].tolist() == [url]
    else:
        assert attempt["check_state"] == "no_product"
        assert _urls(store, cid).empty
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_amevista_gucci_candidate_does_not_bypass_missing_ficha_permission(tmp_path):
    site = Site(base="https://www.amevista.com", robots="User-agent: *\nAllow: /\n")
    store, _, locator, _, cid = _env(tmp_path, site, name="Amevista", template=None, methods=())
    ean = normalize_gtin(GUCCI["ean"])
    _watch(store, [(ean, "Gucci", "GG1660S", "001", "")])
    batch = WatchBatch(store, locator=locator, monitor=PriceMonitor(store, transport=site))

    preview = batch.preview([ean], [cid])
    prepared = batch.prepare([ean], [cid])

    assert preview.iloc[0]["candidate_count"] == 1
    assert not preview.iloc[0]["ready"] and not prepared.iloc[0]["ready"]
    assert "fichas de producto" in preview.iloc[0]["defer_reason"].casefold()
    assert site.calls == []


def test_opaque_ids_are_mapped_not_calculated_and_locales_are_not_rewritten():
    blickers = plan_for(PRADA, "blickers")
    assert len(blickers.runnable) == 1
    assert "/en-gb/" in urls(blickers)[0] and "5649966192263168" in urls(blickers)[0]
    assert "5313688741478400" not in urls(blickers)[0]
    other = plan_for({**PRADA, "color": "11L08Z"}, "blickers")
    assert "5313688741478400" in urls(other)[0]
    assert not plan_for({**PRADA, "color": "002"}, "blickers").runnable
    edel = plan_for(PRADA, "edel")
    assert urls(edel)[0].endswith("?selectedSize=1907958")
    assert "caliber" not in edel.runnable[0].attributes
    unobserved_variant = plan_for({**PRADA, "color": "002", "caliber": "54"}, "edel")
    assert urls(unobserved_variant) == [
        "https://www.edel-optics.es/PR-27ZS-002-de-Prada.html"
    ]
    assert "selectedSize" not in unobserved_variant.runnable[0].url
    assert "caliber" not in unobserved_variant.runnable[0].attributes


def test_commercial_names_and_dune_opt_remain_distinct():
    miu = {**PRADA, "ean": "8056597981361", "brand": "Miu Miu", "model": "MU 01XV",
           "color": "VAU1O1", "category": "montura"}
    assert "/regard-mu-01xv-vau1o1" in urls(plan_for(miu, "mia"))[0]
    dune = {**PRADA, "ean": "889652429687", "brand": "Saint Laurent", "model": "SL 596 DUNE", "color": "003"}
    assert "/dune-sl-596-003" in urls(plan_for(dune, "mia"))[0]
    assert not plan_for({**dune, "model": "SL 596 DUNE OPT"}, "mia").runnable
    assert not plan_for({**miu, "model": "MU 01YV"}, "mia").runnable
    fausto = {**PRADA, "ean": "889214029614", "brand": "Tom Ford", "model": "FT0711", "color": "01A"}
    assert "fausto-ft0711-01a" in urls(plan_for(fausto, "congafas"))[0]
    assert "ft0711-fausto-75s" in urls(plan_for({**fausto, "color": "75S"}, "congafas"))[0]


@pytest.mark.parametrize("brand,category", [
    ("Miu Miu", "sol"), ("Bottega Veneta", "sol"), ("Prada", "montura"),
])
def test_congafas_supplied_pattern_is_limited_to_documented_prada_sunglasses(brand, category):
    product = {**PRADA, "brand": brand, "model": "MODEL123", "color": "001",
               "category": category}

    plan = plan_for(product, "congafas")

    assert not plan.runnable
    assert plan.reason_code == "needs_authorized_discovery"
    assert "Prada" in plan.reason or "monturas" in plan.reason


def test_congafas_supplied_pattern_remains_available_for_prada_sunglasses():
    product = {**PRADA, "model": "PR 99ZS", "color": "01A"}

    plan = plan_for(product, "congafas")

    assert [candidate.url for candidate in plan.runnable] == [
        "https://www.congafasdesol.com/prada-pr99zs-01a.html",
        "https://www.congafasdesol.com/prada-pr-99zs-01a.html",
    ]


def test_conflicting_documentary_identifiers_and_content_never_become_runnable():
    chloe = {**PRADA, "ean": "889652543574", "brand": "Chloe", "model": "CH0289S", "color": "003"}
    plan = plan_for(chloe, "mia")
    assert not plan.runnable and plan.candidates[0].state == "probable" and "GTIN" in plan.reason
    ford = {**PRADA, "ean": "889214402530", "brand": "Tom Ford", "model": "FT1044", "color": "01E"}
    plan = plan_for(ford, "opticalh")
    assert len(plan.runnable) == 1 and urls(plan)[0].endswith("60-12")
    assert any("contradictorios" in candidate.reason for candidate in plan.candidates)


def test_parent_handles_and_historical_urls_are_pending_not_product_absence():
    for shop in ("celine", "universitaria"):
        plan = plan_for({**PRADA, "model": "PR 65ZS", "color": "ZVN09T"}, shop)
        if shop == "universitaria":
            assert any("PRADA-65ZS.html" in candidate.url and not candidate.runnable for candidate in plan.candidates)
        assert plan.reason_code == "needs_authorized_discovery"
    celine = plan_for(PRADA, "celine")
    assert len(celine.candidates) == 1 and not celine.runnable and "variante" in celine.reason
    chloe = {**PRADA, "ean": "889652543574", "brand": "Chloe", "model": "CH0289S", "color": "003"}
    domanci = plan_for(chloe, "domanci")
    assert len(domanci.candidates) == 1 and not domanci.runnable
    assert "histórica" in domanci.reason and "no demuestra ausencia" in domanci.reason


def test_two_rb2140_upcs_are_kept_separate_without_assigning_dimensions():
    products = [{**PRADA, "ean": ean, "brand": "Ray Ban", "model": "RB2140", "color": "901"}
                for ean in ("805289126577", "805289126584")]
    plans = [plan_for(product, "visio") for product in products]
    assert all(not plan.runnable for plan in plans)
    assert len({plan.candidates[0].attributes["ean"] for plan in plans}) == 2
    assert all("caliber" not in plan.candidates[0].attributes for plan in plans)


@pytest.mark.parametrize("shop,brand,model,color,ean,category", [
    ("bassol", "Miu Miu", "MU 04UV", "1AB1O1", "8056597669016", "montura"),
    ("bassol", "Gucci", "GG1660S", "002", "889652494838", "sol"),
    ("visual", "Miu Miu", "MU 04UV", "1AB1O1", "8056597669016", "montura"),
])
def test_other_represented_brands_and_categories_require_explicit_sizes(shop, brand, model, color, ean, category):
    product = {"ean": ean, "brand": brand, "model": model, "color": color, "category": category}
    assert not plan_for(product, shop).runnable
    plan = plan_for({**product, "caliber": "50-18"}, shop)
    assert len(plan.runnable) == 1 and urls(plan)[0].endswith("-50")


@pytest.mark.parametrize("field", ["brand", "model", "color", "category", "ean"])
def test_missing_attributes_are_explicit(field):
    product = {**PRADA, field: ""}
    plan = plan_for(product, "optima")
    assert not plan.runnable and plan.reason_code == "missing_attribute" and field in plan.reason


def synthetic_env(tmp_path, *, permission=True, robots="User-agent: *\nAllow: /\n"):
    site = Site(base="https://optica-optima.com", robots=robots)
    store, access, locator, clock, cid = _env(
        tmp_path, site, name="Óptica Óptima", template=None, methods=("ficha",) if permission else ())
    # Fictional response at the documentary-pattern URL, never a capture from Optima.
    url = urls(plan_for(PRADA, "optima"))[0]
    site.pages[url] = page(name="Prada PR 27ZS 16K08Z 54-21", price=123, brand="Prada",
                           gtin=PRADA["ean"])
    _watch(store, [(PRADA["ean"], "Prada", "PR 27ZS", "16K08Z", "")])
    return store, access, locator, clock, cid, site, url


def test_preview_is_read_only_and_later_authorized_run_preserves_provenance(tmp_path):
    store, access, locator, clock, cid, site, url = synthetic_env(tmp_path)
    monitor = PriceMonitor(store, clock=clock, transport=site)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    before = dump(store)
    preview = batch.preview([PRADA["ean"]], [cid])
    assert dump(store) == before and not site.calls
    assert preview["candidate_count"].tolist() == [1] and preview["ready"].all()
    assert not _urls(store, cid).shape[0] and not monitor.jobs().shape[0]
    locator.run([cid], eans=[PRADA["ean"]])
    assert _jobs(store, cid)["state"].tolist() == ["done"]
    assert _urls(store, cid)["status"].tolist() == ["verified"]
    assert _urls(store, cid)["url"].tolist() == [url]
    with store._connect() as con:
        row = con.execute("SELECT candidate_evidence FROM discovery_index WHERE url=?", (url,)).fetchone()
        evidence = json.loads(row[0])[PRADA["ean"]]
        assert evidence["rule"] == "sample_pattern:optima" and evidence["evidence"] == ["E001"]
        assert evidence["attributes"]["color"] == "16K08Z"
        audit = json.loads(con.execute(
            "SELECT detail FROM locate_events WHERE method='local_candidates'").fetchone()[0])
        assert audit["candidates"][0]["attributes"]["ean"] == PRADA["ean"]
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0
    after = dump(store)
    assert locator.run([cid]).processed == 0 and dump(store) == after
    assert len(site.calls) == 2  # synthetic robots + one synthetic ficha, no search
    assert batch.preview([PRADA["ean"]], [cid])["candidate_count"].tolist() == [0]


def test_candidate_trace_keeps_category_rejection_and_url_lineage(tmp_path):
    store, _, locator, _, cid, site, url = synthetic_env(tmp_path)
    site.pages[url] = (
        '<script type="application/ld+json">'
        + json.dumps({"@context": "https://schema.org", "@type": "CollectionPage", "url": url})
        + "</script>"
    )

    report = locator.run([cid], eans=[PRADA["ean"]])

    assert report.outcomes[0]["state"] == "unavailable"
    assert report.outcomes[0]["candidate_plan"]["candidates"][0]["url"] == url
    attempt = report.outcomes[0]["candidate_attempts"][0]
    assert attempt["candidate_url"] == url
    assert attempt["requested_url"] == url
    assert attempt["final_url"] == url
    assert attempt["check_state"] == "no_product"
    assert "CollectionPage" in attempt["rejection"]
    assert any("sin autorizar" in reason for reason in report.outcomes[0]["discovery"]["restrictions"])
    assert attempt["requests"] == 2  # synthetic robots.txt plus the candidate ficha
    assert site.calls == ["https://optica-optima.com/robots.txt", url]
    assert _urls(store, cid).empty
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_batch_csv_export_preserves_nested_outcomes_as_json():
    outcomes = [{"ean": PRADA["ean"], "state": "unavailable",
                 "candidate_attempts": [{"candidate_url": "https://example.test/p",
                                         "rejection": "no_product: CollectionPage"}]}]
    pairs = [{"ean": PRADA["ean"], "outcomes": outcomes}]
    frame = batch_pairs_export(pairs)
    csv_row = next(csv.DictReader(StringIO(frame.to_csv(index=False))))
    json_payload = json.dumps({"pairs": pairs}, ensure_ascii=False)

    assert "[object Object]" not in csv_row["outcomes"]
    assert json.loads(csv_row["outcomes"]) == outcomes
    assert json.loads(json_payload)["pairs"] == pairs


@pytest.mark.parametrize("restriction", ["permission", "incident", "legacy_block", "legacy_robots", "budget", "robots"])
def test_generation_never_bypasses_access_controls(tmp_path, restriction):
    store, access, locator, clock, cid, site, url = synthetic_env(
        tmp_path, permission=restriction != "permission",
        robots="User-agent: *\nDisallow: /gafas-de-sol/\n" if restriction == "robots" else "User-agent: *\nAllow: /\n")
    if restriction == "incident":
        access.report_block("competitor", cid, "http_403", method="site_search", detail="Synthetic persistent block")
    elif restriction in {"legacy_block", "legacy_robots"}:
        with store._connect() as con, con:
            con.execute("UPDATE competitor_web_config SET access_status=? WHERE competitor_id=?",
                        ("blocked" if restriction == "legacy_block" else "robots_restricted", cid))
    elif restriction == "budget":
        access.set_budget("competitor", cid, daily_limit=0, per_minute_limit=600, pause_seconds=0, actor="test")
    batch = WatchBatch(store, locator=locator, monitor=PriceMonitor(store, clock=clock, transport=site))
    assert batch.preview([PRADA["ean"]], [cid])["ready"].any() == (restriction == "robots")
    locator.run([cid])
    assert not _urls(store, cid).shape[0]
    assert url not in site.calls
    if restriction != "robots":
        assert not site.calls


def test_wrong_colour_response_is_rejected_not_verified_from_generated_slug(tmp_path):
    store, _, locator, _, cid, site, url = synthetic_env(tmp_path)
    site.pages[url] = page(name="Prada PR 27ZS 11L08Z 54-21", price=123, brand="Prada", gtin=PRADA["ean"])
    locator.run([cid])
    assert _urls(store, cid)["status"].tolist() == ["probable"]
    assert _jobs(store, cid)["state"].tolist() == ["review"]
    assert "candidate_evidence" in dump(store)


def test_conflicting_live_gtins_remain_under_review_even_if_one_matches_target(tmp_path):
    store, _, locator, _, cid, site, url = synthetic_env(tmp_path)
    # Explicitly synthetic JSON-LD with two non-equivalent GTINs.
    site.pages[url] = '<script type="application/ld+json">' + json.dumps({
        "@type": "Product", "name": "Prada PR 27ZS 16K08Z", "brand": "Prada",
        "gtin13": PRADA["ean"], "gtin12": "889652543574",
        "offers": {"@type": "Offer", "price": "123", "priceCurrency": "EUR", "availability": "InStock"},
    }) + "</script>"
    locator.run([cid])
    assert _jobs(store, cid)["state"].tolist() == ["review"]
    assert _urls(store, cid)["status"].tolist() == ["probable"]


@pytest.mark.parametrize("shop_name,base,handle", [
    ("Optical H", "https://opticalh.com", "/products/synthetic-handle-without-numeric-id"),
    ("Óptica Universitaria", "https://www.opticauniversitaria.es", "/gafas-de-sol/prada/SYNTHETIC-REF-A1.html"),
    ("Céline Opticien Lunetier", "https://www.celine-opticien-lunetier.com", "/products/synthetic-parent?variant=synthetic-blue"),
])
def test_authorized_sources_resolve_unknown_handles_without_numeric_id_assumptions(tmp_path, shop_name, base, handle):
    site = Site(base=base)
    store, _, locator, _, cid = _env(tmp_path, site, name=shop_name, template=base + "/marca/{brand}")
    url = site.product("Prada", "PR B06S", "16K08Z", gtin="8056262397619")
    site.pages[base + handle] = site.pages.pop(url)
    if "variant=" in handle:
        nodes = [{"@type": "Product", "name": f"Prada PR B06S {color}", "brand": "Prada", "color": color,
                  "gtin13": ean, "url": target, "offers": {
                      "@type": "Offer", "url": target, "price": str(price), "priceCurrency": "EUR",
                      "availability": "https://schema.org/InStock"}}
                 for target, color, ean, price in [
                     (base + "/products/synthetic-parent?variant=synthetic-red", "OTHER", PRADA["ean"], 222),
                     (base + handle, "16K08Z", "8056262397619", 123)]]
        site.pages[base + handle] = '<script type="application/ld+json">' + json.dumps(nodes) + "</script>"
    site.brands["prada"] = [(base + handle, "Prada PR B06S 16K08Z")]
    _watch(store, [("8056262397619", "Prada", "PR B06S", "16K08Z", "")])
    locator.run([cid])
    assert _jobs(store, cid)["state"].tolist() == ["done"]
    assert _urls(store, cid)["url"].tolist() == [base + handle]
    assert _urls(store, cid)["status"].tolist() == ["verified"]


def test_verified_and_previously_rejected_links_are_not_overwritten_or_retried(tmp_path):
    store, _, locator, _, cid, site, url = synthetic_env(tmp_path)
    locator.run([cid])
    before = dump(store)
    locator.enqueue([cid], [PRADA["ean"]])
    locator.run([cid])
    assert dump(store) == before and len(site.calls) == 2
    with store._connect() as con, con:
        con.execute("UPDATE competitor_product_urls SET status='rejected' WHERE url=?", (url,))
        con.execute("UPDATE locate_jobs SET state='pending',not_before=NULL WHERE competitor_id=?", (cid,))
    site.calls.clear()
    locator.run([cid])
    assert not site.calls and _urls(store, cid)["status"].tolist() == ["rejected"]


def test_legacy_index_gets_only_additive_metadata_migration(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE discovery_index(competitor_id TEXT,url TEXT,label TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL,method TEXT NOT NULL,source_url TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,checked_at TEXT,check_state TEXT NOT NULL DEFAULT '',
            facts TEXT NOT NULL DEFAULT '',PRIMARY KEY(competitor_id,url))""")
        con.execute("""INSERT INTO discovery_index(competitor_id,url,signature,method,first_seen,last_seen,facts)
            VALUES('old','https://legacy.example/p','PR 27ZS 16K08Z','catalog','2026-11-01','2026-11-01','{}')""")
    store = PriceComparatorStore(path)
    with store._connect() as con:
        assert tuple(con.execute("SELECT candidate_evidence,facts,label FROM discovery_index").fetchone()) == ("{}", "{}", "")
    before = dump(store)
    PriceComparatorStore(path)
    assert dump(store) == before


@pytest.mark.parametrize("name,base,product", [
    ("Domanci", "https://domanci.com", {**PRADA, "ean": "889652543574", "brand": "Chloe",
                                      "model": "CH0289S", "color": "003"}),
    ("Mia Burton", "https://miaburton.com", {**PRADA, "ean": "889652543574", "brand": "Chloe",
                                          "model": "CH0289S", "color": "003"}),
    ("Céline Opticien Lunetier", "https://www.celine-opticien-lunetier.com", PRADA),
])
def test_nonrunnable_documentary_plan_is_audited_without_fetching_or_linking(tmp_path, name, base, product):
    site = Site(base=base)
    store, _, locator, _, cid = _env(tmp_path, site, name=name, template=None, methods=("ficha",))
    ean = normalize_gtin(product["ean"])
    _watch(store, [(ean, product["brand"], product["model"], product["color"], "")])
    locator.run([cid])
    assert not site.calls and _urls(store, cid).empty
    with store._connect() as con:
        detail = json.loads(con.execute(
            "SELECT detail FROM locate_events WHERE method='local_candidates'").fetchone()[0])
        assert detail["reason_code"] == "needs_authorized_discovery"
        assert detail["candidates"] and all(candidate["state"] == "probable" for candidate in detail["candidates"])
        assert all(candidate["attributes"]["ean"] == ean for candidate in detail["candidates"])
        assert all(candidate["evidence"] and candidate["reason"] for candidate in detail["candidates"])


def test_batch_ui_shows_local_candidates_without_preparing_jobs_or_http(tmp_path, monkeypatch):
    store, _, _, _, cid, site, _ = synthetic_env(tmp_path)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    with store._connect() as con, con:
        con.execute("DELETE FROM locate_jobs")
    before = dump(store)
    app = AppTest.from_string(
        "from price_comparator import PriceComparatorStore\n"
        "from app_pages.price_watch import _render_batch\n"
        f"_render_batch(PriceComparatorStore(), {{{cid!r}: 'Óptica Óptima'}})").run(timeout=30)
    app.multiselect(key="batch_eans").set_value([PRADA["ean"]])
    app.multiselect(key="batch_competitors").set_value([cid]).run(timeout=30)
    assert not app.exception and not site.calls and dump(store) == before
    assert any("Candidatas locales" in expander.label for expander in app.expander)
    assert any("URL candidata" in frame.value.columns for frame in app.dataframe)
