"""Supplied URL hypotheses and synthetic governed reads; never live shop requests."""
import pytest
import requests

from price_batch import WatchBatch
from price_monitor import PriceMonitor
from price_url_candidates import candidate_plan
from tests.test_price_locator import Site, _env, _jobs, _urls, _watch
from tests.web_fixtures import page


PRODUCT = {"ean": "2000000000008", "brand": "Miu Miu", "model": "MU 11ZS", "color": "16K5S0",
           "category": "sol", "caliber": "51-21"}


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Real HTTP forbidden; use synthetic transport")
    monkeypatch.setattr(requests.Session, "send", deny)


@pytest.mark.parametrize("changes,path", [
    ({}, "/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html"),
    ({"model": "MU11ZS", "caliber": "51"}, "/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html"),
    ({"color": "1AB5S0", "caliber": "54"}, "/es/gafas-de-sol/miu-miu-mu-11zs-1ab5s0-54.html"),
    ({"category": "montura", "model": "MU 01XV", "color": "VAU1O1", "caliber": "50"},
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
    assert provenance["review_date"] == "2026-11-06" and provenance["review_base"] == ""
    assert provenance["attributes"]["caliber"] == path.removesuffix(".html").rsplit("-", 1)[1]
    assert "not captured HTTP" in provenance["attributes"]["evidence_note"]


@pytest.mark.parametrize("caliber", ["", None, "unknown", "51 or 54"])
def test_domanci_never_borrows_size_from_supplied_url(caliber):
    plan = candidate_plan({**PRODUCT, "caliber": caliber}, "domanci.com")
    assert not plan.runnable and plan.reason_code == "missing_attribute"
    assert "calibre" in plan.reason


@pytest.mark.parametrize("model,color,first", [
    ("PR D01S", "0CD80L", "prada-pr-d01s-0cd80l.html"),
    ("PR06YS", "09Q5S0", "prada-pr06ys-09q5s0.html"),
    ("PR 27ZS", "16K08Z", "prada-pr27zs-16k08z.html"),
])
def test_congafas_generates_only_two_prefix_spellings(model, color, first):
    product = {**PRODUCT, "brand": "Prada", "model": model, "color": color, "caliber": ""}
    plan = candidate_plan(product, "www.congafasdesol.com")
    assert len(plan.runnable) == 2
    assert plan.runnable[0].url == "https://www.congafasdesol.com/" + first
    assert len({c.url for c in plan.runnable}) == 2
    assert all(c.url.endswith(color.lower() + ".html") for c in plan.runnable)
    assert all("caliber" not in c.attributes for c in plan.runnable)


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
