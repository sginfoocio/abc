"""Synthetic reproductions of saved continuity facts, not captured shop HTML."""
import json
from urllib.parse import urlparse

import pandas as pd
import pytest
import requests

from price_comparator import PriceComparatorStore
from price_web import WebPriceSearch
from price_web_extract import extract_product, select_product_variant
from price_references import EXACT, classify
from tests.web_fixtures import FakeSite, NOW


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Commercial HTTP is forbidden in these synthetic regressions")

    monkeypatch.setattr(requests.Session, "send", reject)


def _ld(node):
    return '<script type="application/ld+json">' + json.dumps(node) + "</script>"


def _visit(tmp_path, target, url, body, tax="included"):
    site = FakeSite()
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    site.add(origin + "/robots.txt", "User-agent: *\nDisallow:\n")
    site.add(url, body)
    store = PriceComparatorStore(tmp_path / "synthetic.sqlite3")
    store.import_products(pd.DataFrame([{
        "EAN": target["ean"], "MARCA": target["brand"], "MODELO": target["model"],
        "COLOR": target["color"], "CALIBRE": target.get("caliber", ""),
    }]))
    store.import_competitors(pd.DataFrame([{"Competidor": "Synthetic shop", "Página web": origin}]))
    search = WebPriceSearch(store, fetcher_factory=site.factory, clock=lambda: NOW)
    cid = store.competitors().iloc[0]["id"]
    search.update_config(cid, tax_policy=tax, sales_market="ES", market_verified=True,
                         market_evidence_url=origin + "/terms", market_note="Synthetic verified ES policy")
    config = search.config(cid)
    outcome = search._visit(target, config, site.factory(config), url, "synthetic", False)
    if outcome.state == "exact":
        search.record(outcome)
    return outcome, store, site


def _amevista_group(url, *, model="WAYFARER", title="Ray-Ban RB2140 WAYFARER 901", color="901",
                    eans=("805289126577", "805289126584"), tax=None):
    common = {"@type": "Offer", "price": "200", "priceCurrency": "EUR"}
    if tax is not None:
        common["valueAddedTaxIncluded"] = tax
    return {
        "@type": "ProductGroup", "url": url, "name": title, "brand": "Ray-Ban", "model": model,
        "color": color, "offers": common,
        "hasVariant": [
            {"@type": "Product", "url": url, "gtin12" if len(ean) == 12 else "gtin13": ean,
             "offers": {"@type": "Offer", "availability": stock, "eligibleRegion": "ES"}}
            for ean, stock in zip(eans, ("InStock", "OutOfStock"))
        ],
    }


@pytest.mark.parametrize("ean,color", [
    ("0805289126577", "901"), ("0805289126584", "901"), ("8056266001833", "901S31"),
])
def test_amevista_wayfarer_and_verified_tax_survive_full_variant_flow(tmp_path, ean, color):
    url = f"https://www.amevista.com/es/ray-ban-rb2140-{color.lower()}-wayfarer"
    eans = ("805289126577", "805289126584") if color == "901" else (ean, "805289126577")
    target = {"ean": ean, "brand": "Ray Ban", "model": "RB2140", "color": color}
    body = _ld(_amevista_group(url, title=f"Ray-Ban RB2140 WAYFARER {color}", color=color, eans=eans))

    outcome, store, site = _visit(tmp_path, target, url, body)

    assert outcome.state == "exact" and outcome.reason == ""
    assert outcome.page.ean_published == ean and outcome.page.includes_tax is True
    assert outcome.page.price == 200
    assert outcome.page.availability == ("unavailable" if ean == "0805289126584" else "available")
    assert not outcome.page.ambiguous
    assert len(site.calls) == 2
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1


def test_amevista_fausto_is_a_scoped_commercial_name(tmp_path):
    url = "https://www.amevista.com/es/tom-ford-ft0711-01a-fausto"
    target = {"ean": "0889214029614", "brand": "Tom Ford", "model": "FT0711", "color": "01A"}
    node = {"@type": "Product", "name": "Tom Ford FT0711 FAUSTO 01A", "brand": "Tom Ford",
            "model": "FAUSTO", "gtin12": "889214029614", "url": url,
            "offers": {"@type": "Offer", "price": 200, "priceCurrency": "EUR",
                       "availability": "InStock", "eligibleRegion": "ES"}}

    outcome, _, _ = _visit(tmp_path, target, url, _ld(node))

    assert outcome.state == "exact" and outcome.page.ean_published == target["ean"]


@pytest.mark.parametrize("title,model", [
    ("Ray-Ban RB2140 RB4472 WAYFARER 901", "WAYFARER"),
    ("Ray-Ban RB2140 WAYFARER 901 RB4472", "WAYFARER"),
    ("Ray-Ban RB2140 WAYFARER 901", "RB4472"),
    ("Ray-Ban RB2140 UNKNOWN 901", "UNKNOWN"),
    ("Ray-Ban RB4472 WAYFARER 901", "WAYFARER"),
])
def test_commercial_alias_never_hides_another_model(title, model):
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}
    assert classify(target, {"title": title, "model": model,
                             "ean_published": target["ean"]}).status != EXACT


@pytest.mark.parametrize("policy,published,expected", [
    ("included", None, True), ("unknown", None, None),
    ("included", False, False), ("excluded", True, True),
])
def test_product_group_preserves_policy_but_never_overrides_offer_tax(policy, published, expected):
    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    node = _amevista_group(url, tax=published, model="RB2140", title="Ray-Ban RB2140 901")
    page = select_product_variant(extract_product(_ld(node), url, tax_policy=policy),
                                  {"ean": "0805289126577"})
    assert page.includes_tax is expected


@pytest.mark.parametrize("policy,reason,tax", [
    ("included", "", True), ("unknown", "vat_unconfirmed", None),
])
def test_saved_rb4472_variant_facts_with_synthetic_transport(tmp_path, policy, reason, tax):
    """Reduced extracted facts from report (2), check 1025; this is not its original HTTP body."""
    url = "https://www.amevista.com/es/ray-ban-rb4472-1359-2"
    nodes = [{
        "@type": "Product", "url": url, "name": "Ray-Ban RB4472 1359/2", "brand": "Ray-Ban",
        "color": "Beige", "gtin13": ean, "size": size,
        "offers": {"@type": "Offer", "url": url, "price": "89.1", "priceCurrency": "EUR",
                   "availability": "InStock", "shippingDetails": {
                       "@type": "OfferShippingDetails",
                       "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "ES"}}},
    } for ean, size in [("8056262833902", "48"), ("8056262833919", "51")]]
    target = {"ean": "8056262833902", "brand": "Ray-Ban", "model": "RB4472", "color": "1359/2"}

    outcome, _, _ = _visit(tmp_path, target, url, _ld(nodes), tax=policy)

    assert outcome.state == "exact" and outcome.reason == reason
    assert outcome.page.includes_tax is tax
    assert outcome.page.ean_published == target["ean"] and outcome.page.price == 89.1


def test_tax_policy_does_not_make_another_market_comparable(tmp_path):
    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    node = _amevista_group(url, model="RB2140", title="Ray-Ban RB2140 901")
    for variant in node["hasVariant"]:
        variant["offers"]["eligibleRegion"] = "US"
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}

    outcome, store, _ = _visit(tmp_path, target, url, _ld(node))

    assert outcome.reason == "market_currency"
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE match_status='exact_match'").fetchone()[0] == 0


def _edel_html(url, variant_id, ean, title, *, offer_id=None, extra="", second_id=False):
    offer_url = url + f"?selectedSize={offer_id or variant_id}"
    return (
        f"<html><head><title>{title}</title></head><body>"
        f'<input type="radio" id="prd{variant_id}" checked>'
        f'<div class="eoDetailListTileSize{variant_id}">EAN {ean}</div>'
        + ('<div class="eoDetailListTileSize777">EAN 8056597875349</div>' if second_id else "")
        + '<div class="eoProductPrice"><span itemprop="offers" itemscope '
        'itemtype="https://schema.org/Offer">'
        f'<link itemprop="url" href="{offer_url}">'
        '<span itemprop="price" content="200">200</span>'
        '<meta itemprop="priceCurrency" content="EUR">'
        '<link itemprop="availability" href="https://schema.org/InStock">'
        "</span></div>" + extra + "</body></html>"
    )


@pytest.mark.parametrize("ean,model,color,variant_id", [
    ("0889214029614", "FT0711", "01A", "708602"),
    ("0889214281791", "FT0884", "01E", "1461733"),
    ("0889652429717", "SL 596 DUNE", "006", "1971610"),
    ("0889652650838", "SL 872 ERIN", "001", "2352387"),
    ("0889652675602", "SL 908", "002", "2423524"),
])
def test_edel_scoped_microdata_reaches_identity_and_offer(tmp_path, ean, model, color, variant_id):
    brand = "Tom Ford" if model.startswith("FT") else "Saint Laurent"
    url = f"https://www.edel-optics.es/{model.replace(' ', '-')}-{color}-de-{brand.replace(' ', '-')}.html"
    target = {"ean": ean, "brand": brand, "model": model, "color": color}
    body = _edel_html(url, variant_id, ean[1:], f"{brand} {model} {color}", second_id=True)

    outcome, store, _ = _visit(tmp_path, target, url, body)

    assert outcome.state == "exact" and outcome.reason == ""
    assert outcome.page.variant_id == variant_id
    assert (outcome.page.ean_published, outcome.page.price, outcome.page.availability) == (ean, 200, "available")
    assert not any("No hay datos de variante" in note for note in outcome.page.notes)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1


def test_edel_microdata_contradictory_offer_is_not_accepted(tmp_path):
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    target = {"ean": "0889214029614", "brand": "Tom Ford", "model": "FT0711", "color": "01A"}
    body = _edel_html(url, "708602", target["ean"], "Tom Ford FT0711 01A", offer_id="999")
    outcome, store, _ = _visit(tmp_path, target, url, body)
    assert outcome.state != "exact" and outcome.page.ambiguous
    assert outcome.page.price is None and outcome.page.availability == "unknown"
    assert outcome.page.ean_published == ""
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


def test_edel_radio_and_unrelated_metadata_are_not_variant_evidence():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = ('<title>Tom Ford FT0711 01A</title><input type="radio" id="prd708602" checked>'
            '<aside><meta itemprop="gtin12" content="889214029614">'
            '<meta itemprop="price" content="200"><meta itemprop="priceCurrency" content="EUR">'
            '<link itemprop="availability" href="https://schema.org/InStock"></aside>')
    page = extract_product(body, url)
    assert page.ambiguous and page.ean_published == "" and page.price is None
    assert page.availability == "unknown"


def test_edel_unscoped_stock_of_multiple_sizes_stays_pending():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = _edel_html(url, "708602", "889214029614", "Tom Ford FT0711 01A", second_id=True)
    body = body.replace(f'<link itemprop="url" href="{url}?selectedSize=708602">', "")
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.availability == "unknown"
    assert any("oferta/stock" in note for note in page.notes)
    assert not any("No hay datos de variante" in note for note in page.notes)


@pytest.mark.parametrize("product_id,offer_id,selected_id", [
    ("111", "222", "222"), ("222", "111", "222"), ("111", "111", "222"),
])
def test_edel_microdata_product_offer_and_radio_must_agree(product_id, offer_id, selected_id):
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = _edel_html(url, selected_id, "889214029614", "Tom Ford FT0711 01A", offer_id=offer_id)
    body = (f'<div itemscope itemtype="https://schema.org/Product">'
            f'<link itemprop="url" href="{url}?selectedSize={product_id}">' + body + "</div>")
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.availability == "unknown"
    assert page.ean_published == ""


def test_edel_related_widget_never_supplies_main_variant_facts():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = "<aside>" + _edel_html(url, "708602", "889214029614", "Tom Ford FT0711 01A") + "</aside>"
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.ean_published == ""


def test_edel_related_ean_cannot_supply_identity_for_the_main_offer():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = _edel_html(url, "708602", "889214029614", "Tom Ford FT0711 01A")
    tile = '<div class="eoDetailListTileSize708602">EAN 889214029614</div>'
    body = body.replace(tile, "<aside>" + tile + "</aside>")
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.ean_published == ""


def test_edel_product_upc_cannot_contradict_the_selected_variant_tile():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = _edel_html(url, "708602", "889214029614", "Tom Ford FT0711 01A")
    body = ('<div itemscope itemtype="https://schema.org/Product">'
            '<meta itemprop="gtin12" content="889214281791">' + body + "</div>")
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.ean_published == ""
    assert page.availability == "unknown"


def test_selected_group_variant_keeps_future_delivery_as_unknown_stock():
    url = "https://www.amevista.com/es/ray-ban-rb2140-901s31-wayfarer"
    group = _amevista_group(url, color="901S31", eans=("8056266001833", "805289126577"))
    group["hasVariant"][0]["offers"].update(
        availability="PreOrder", shippingDetails={"deliveryTime": "2026-12-09"})
    page = select_product_variant(extract_product(_ld(group), url),
                                  {"ean": "8056266001833"})
    assert page.availability == "unknown"
    assert "2026-12-09" in str(page.delivery_evidence)
