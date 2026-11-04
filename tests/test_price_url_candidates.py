"""Documentary URL checks and explicitly SYNTHETIC shop responses. No captured HTML or live HTTP."""
from dataclasses import asdict
import json
import sqlite3
from urllib.parse import urlparse

import pytest
import requests
from streamlit.testing.v1 import AppTest

from price_batch import WatchBatch
from price_comparator import PriceComparatorStore, normalize_gtin
from price_monitor import PriceMonitor
from price_url_candidates import EXAMPLES, candidate_plan
from tests.test_price_locator import Site, _env, _jobs, _urls, _watch
from tests.web_fixtures import page

PRADA = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z",
         "category": "All / Gafas de Sol"}
FORD = {"ean": "889214042422", "brand": "Tom Ford", "model": "FT5634B", "color": "001",
        "category": "All / Monturas"}


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


def test_only_dimensions_associated_to_exact_gtin_support_new_sized_candidate():
    original = dict(PRADA)
    plan = plan_for(PRADA, "bassol")
    assert urls(plan) == ["https://www.opticabassol.com/products/prada-pr-27zs-16k08z-54"]
    assert plan.runnable[0].attributes["dimension_source"].startswith("E041:")
    assert PRADA == original and "caliber" not in PRADA


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
    plan = plan_for({**PRADA, "color": "19J09Z"}, "amevista")
    assert urls(plan) == ["https://www.amevista.com/es/prada-pr-27zs-19j09z"]
    assert not plan_for({**PRADA, "model": "PR 65ZS", "color": "ZVN09T"}, "amevista").runnable
    tiffany = {**PRADA, "brand": "Tiffany & Co.", "model": "TF3104D", "color": "6216S4",
               "ean": "8056262010730"}
    assert all("/tiffany-" in url for url in urls(plan_for(tiffany, "amevista")))
    unknown = plan_for({**FORD, "model": "FT5737B", "color": "045"}, "optima")
    assert urls(unknown)[0].endswith("ft5737b-045.html")  # no universal "-B" rewrite


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
    assert not plan_for({**PRADA, "color": "002", "caliber": "54"}, "edel").runnable


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
