"""Synthetic offline regressions, NOT captured responses from the twelve pilot reviews."""
from dataclasses import asdict
import json

import pytest
import requests

from price_references import EXACT, PROBABLE, classify, parse_reference
from price_monitor import PriceMonitor
from price_locator import _Session
from price_web_extract import ProductPage, extract_product
from price_web_fetch import WebFetcher
from tests.test_price_locator import _jobs, _urls
from tests.test_price_url_candidates import PRADA, dump, synthetic_env
from tests.web_fixtures import page

BASE = "https://optica-optima.com"
CATEGORY = BASE + "/gafas-de-sol/"
PRODUCT = BASE + "/gafas-de-sol/synthetic-product.html"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Commercial HTTP is forbidden in these synthetic regressions")
    monkeypatch.setattr(requests.Session, "send", deny)


def ld(node):
    return '<script type="application/ld+json">' + json.dumps(node) + "</script>"


def product_node(url=PRODUCT):
    return {"@type": "Product", "url": url, "name": "Prada PR 27ZS 16K08Z",
            "brand": "Prada", "gtin13": PRADA["ean"],
            "offers": {"@type": "Offer", "price": "123", "priceCurrency": "EUR",
                       "availability": "InStock"}}


@pytest.mark.parametrize("body", [
    "<title>Gafas de sol</title>",
    '<title>Gafas de sol</title><article><meta itemprop="gtin13" content="8056597875349">'
    '<meta itemprop="priceCurrency" content="EUR"><span class="price">123</span></article>',
    ld(product_node()),
    ld(product_node(CATEGORY)),
])
def test_known_category_never_supplies_product_identity_or_listed_price(body):
    assert extract_product(body, CATEGORY, price_selector="span.price") is None


@pytest.mark.parametrize("node", [
    {"@type": "ItemList", "itemListElement": [product_node()]},
    {"@graph": [{"@type": "CollectionPage", "url": BASE + "/synthetic-collection"}, product_node()]},
])
def test_structured_listing_does_not_fall_back_to_title_or_card_metadata(node):
    body = ('<title>Prada PR 27ZS 16K08Z</title><meta itemprop="gtin13" content="8056597875349">'
            '<meta itemprop="price" content="123"><meta itemprop="priceCurrency" content="EUR">')
    assert extract_product(ld(node) + body, BASE + "/synthetic-collection") is None


def test_product_with_related_list_remains_scoped_to_its_own_url():
    nodes = [product_node(), {"@type": "ItemList", "itemListElement": [product_node(BASE + "/other")]}]
    found = extract_product(ld(nodes), PRODUCT)
    assert found and found.price == 123 and classify(PRADA, found.candidate()).status == EXACT


def test_fetch_trace_preserves_actual_redirects_without_following_canonical():
    requested = BASE + "/synthetic-candidate?variant=blue"
    final = PRODUCT + "?variant=blue"
    canonical = PRODUCT
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, {}, b"User-agent: *\nAllow: /\n"
        if url == requested:
            return 302, {"Location": final}, b""
        assert url == final
        return 200, {}, (ld(product_node(final)) + f'<link rel="canonical" href="{canonical}">').encode()

    fetched = WebFetcher(transport=transport, sleep=lambda _: None, pause_seconds=0).get(requested)
    found = extract_product(fetched.text, fetched.url, url_trace=fetched.trace)
    assert found.url == final
    assert found.url_trace == {
        "requested_url": requested, "final_url": final,
        "redirects": [{"url": requested, "status": 302, "location": final}],
        "canonical_urls": [canonical],
    }
    assert calls == [BASE + "/robots.txt", requested, final]
    assert ProductPage(**asdict(found)).url_trace == found.url_trace
    assert ProductPage(url=PRODUCT).url_trace == {}  # old persisted evidence has no invented trace


def test_redirected_category_is_not_saved_as_product_and_keeps_trace(tmp_path):
    store, _, locator, _, cid, site, requested = synthetic_env(tmp_path)
    inner = locator._transport

    def transport(url, headers, timeout):
        if url == requested:
            return 302, {"Location": CATEGORY}, b""
        if url == CATEGORY:
            return 200, {}, b'<title>Gafas de sol</title><link rel="canonical" href="/gafas-de-sol/">'
        return inner(url, headers, timeout)

    locator._transport = transport
    locator.run([cid], eans=[PRADA["ean"]])
    assert _urls(store, cid).empty
    assert _jobs(store, cid)["state"].tolist() == ["unavailable"]
    with store._connect() as con:
        row = con.execute("SELECT check_state,facts,candidate_evidence FROM discovery_index WHERE url=?",
                          (requested,)).fetchone()
        assert row["check_state"] == "no_product"
        facts = json.loads(row["facts"])
        assert facts["candidate_url"] == facts["requested_url"] == requested
        assert facts["final_url"] == CATEGORY
        assert facts["canonical_urls"] == [CATEGORY]
        assert facts["redirects"] == [{"url": requested, "status": 302, "location": CATEGORY}]
        assert "no_product" in facts["rejection"]
        assert PRADA["ean"] in json.loads(row["candidate_evidence"])
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_locator_retains_specific_ambiguity_reason(tmp_path):
    store, _, locator, _, cid, site, url = synthetic_env(tmp_path)
    site.pages[url] = page(name="Prada PR 27ZS 16K08Z", price=123, gtin=PRADA["ean"],
                           extra_offer={"@type": "Offer", "price": "234", "priceCurrency": "EUR"})
    locator.run([cid], eans=[PRADA["ean"]])
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == "review"
    assert "varias ofertas distintas" in job["detail"]
    assert _urls(store, cid)["status"].tolist() == ["probable"]


def test_cached_category_is_ignored_without_rewriting_history_or_http(tmp_path):
    store, _, locator, clock, cid, site, _ = synthetic_env(tmp_path)
    historical = ProductPage(url=CATEGORY, title="Gafas de sol", price=123, currency="EUR")
    row = {"checked_at": clock().isoformat(), "check_state": "product",
           "facts": json.dumps(asdict(historical))}
    before = dump(store)
    assert _Session(locator, cid)._facts(row, PRADA["ean"], False) == (None, 0)
    assert not site.calls and dump(store) == before


def test_successful_locator_and_monitor_persist_trace_without_canonical_rewrite(tmp_path):
    store, _, locator, clock, cid, site, requested = synthetic_env(tmp_path)
    canonical = requested + "?variant=another"
    site.pages[requested] += f'<link rel="canonical" href="{canonical}">'
    locator.run([cid], eans=[PRADA["ean"]])
    with store._connect() as con:
        facts = json.loads(con.execute("SELECT facts FROM discovery_index WHERE url=?", (requested,)).fetchone()[0])
    trace = facts["url_trace"]
    assert trace["candidate_url"] == trace["requested_url"] == trace["final_url"] == requested
    assert trace["redirects"] == [] and trace["canonical_urls"] == [canonical]
    monitor = PriceMonitor(store, transport=site, clock=clock, sleep=lambda s: clock.advance(seconds=s))
    assert monitor.run([cid], eans=[PRADA["ean"]]).states == {"done": 1}
    sample = json.loads(monitor.jobs().iloc[0]["sample"])
    assert sample["page"]["url_trace"] == trace
    offer = store.offer_history(PRADA["ean"]).iloc[0]
    assert offer["url"] == requested
    assert json.dumps(trace, ensure_ascii=False) in offer["evidence"]


def test_ft_compact_reference_is_not_silently_split_or_accepted_from_gtin():
    # This title is documented for Otticait, NOT a captured Optima title.
    ref = parse_reference("Tom Ford FT5634-B53001", brands=["Tom Ford"], models=["FT5634B"])
    assert ref.model == "FT5634" and ref.color == "B53001"
    product = {"ean": "0889214042422", "brand": "Tom Ford", "model": "FT5634B", "color": "001"}
    candidate = {"ean_published": product["ean"], "title": "Tom Ford FT5634-B53001",
                 "model": "FT5634B", "color": "001"}
    match = classify(product, candidate)
    assert match.status == PROBABLE
    assert match.normalized["listing"]["models"] == ["FT5634", "FT5634B"]
    assert match.normalized["listing"]["colors"] == ["001", "B53001"]
    assert classify(product, {**candidate, "color": "002"}).status != EXACT


def test_ambiguous_miu_reference_stays_under_review_even_with_identical_gtin():
    product = {"ean": "8056597684446", "brand": "Miu Miu", "model": "MU 11WS", "color": "1AB5S0"}
    title = "Miu Miu MU 11WS 1AB5S0 y MU 11WS 7S0"
    match = classify(product, {"title": title, "ean_published": product["ean"]})
    assert match.status == PROBABLE
    assert any("título" in reason and title in reason for reason in match.reasons)
    ambiguous_page = ProductPage(url=PRODUCT, title="Miu Miu MU 11WS 1AB5S0",
                                 ean_published=product["ean"], ambiguous=True)
    assert classify(product, ambiguous_page.candidate()).status == PROBABLE
