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


def _visit(tmp_path, target, url, body, tax="included", at=NOW):
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
    search = WebPriceSearch(store, fetcher_factory=site.factory, clock=lambda: at)
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
    ("included", None, True), ("unknown", None, True),
    ("included", False, False), ("excluded", True, True),
])
def test_product_group_preserves_policy_but_never_overrides_offer_tax(policy, published, expected):
    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    node = _amevista_group(url, tax=published, model="RB2140", title="Ray-Ban RB2140 901")
    page = select_product_variant(extract_product(_ld(node), url, tax_policy=policy),
                                  {"ean": "0805289126577"})
    assert page.includes_tax is expected


@pytest.mark.parametrize("policy,reason,tax", [
    ("included", "", True), ("unknown", "", True),
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


def test_business_tax_decision_is_explicit_auditable_and_bounded_to_14_competitors():
    from competitor_requirements import BUSINESS_TAX_DECISION, COMPETITORS, business_tax_policy_for

    policies = [business_tax_policy_for(url) for _, url in COMPETITORS]
    assert len(policies) == len({policy["domain"] for policy in policies}) == 14
    assert all(policy["includes_tax"] is True and policy["vat_rate"] is None for policy in policies)
    assert "responsable de negocio" in BUSINESS_TAX_DECISION["source"]
    assert BUSINESS_TAX_DECISION["recorded_on"] == "2026-11-12"
    for url in ("https://unregistered.es/es/", "https://amevista.com.evil.example/es/",
                "https://unregistered.amevista.com/es/", "http://www.amevista.com/es/",
                "https://www.amevista.com:8443/es/", "https://user@www.amevista.com/es/"):
        assert business_tax_policy_for(url) is None


@pytest.mark.parametrize("ean,other,size,other_size,brand,model,color,slug,price,check_id", [
    ("8056262410929", "8056262410912", "53", "51", "Miu Miu", "MU 53WV", "ZVN1O1",
     "miu-miu-mu-53wv-zvn1o1", 252, 1015),
    ("8056262600078", "8056262600085", "51", "53", "Miu Miu", "MU 01YV", "14L1O1",
     "miu-miu-mu-01yv-14l1o1", 224, 1024),
    ("8056262833902", "8056262833919", "48", "51", "Ray-Ban", "RB4472", "1359/2",
     "ray-ban-rb4472-1359-2", 89.1, 1025),
    ("8056597669016", "8056262667484", "52", "50", "Miu Miu", "MU 04UV", "1AB1O1",
     "miu-miu-mu-04uv-1ab1o1", 224, 1027),
    ("8056597669023", "8056262667491", "52", "50", "Miu Miu", "MU 04UV", "VAU1O1",
     "miu-miu-mu-04uv-vau1o1", 224, 1028),
    ("8056597669047", "8056262667514", "52", "50", "Miu Miu", "MU 04UV", "2AZ1O1",
     "miu-miu-mu-04uv-2az1o1", 224, 1029),
])
def test_six_saved_amevista_vat_cases_reach_selected_offer(
        tmp_path, ean, other, size, other_size, brand, model, color, slug, price, check_id):
    """Reduced facts from report (2), cited check IDs; generated HTML is synthetic, never a saved response."""
    url = f"https://www.amevista.com/es/{slug}"
    target = {"ean": ean, "brand": brand, "model": model, "color": color, "caliber": size}
    nodes = [{
        "@type": "Product", "url": url, "name": f"{brand} {model} {color}", "brand": brand,
        "gtin13": gtin, "size": caliber,
        "offers": {"@type": "Offer", "url": url, "price": str(price), "priceCurrency": "EUR",
                   "availability": "InStock", "shippingDetails": {
                       "@type": "OfferShippingDetails", "shippingDestination": {
                           "@type": "DefinedRegion", "addressCountry": "ES"}}},
    } for gtin, caliber in [(other, other_size), (ean, size)]]
    outcome, store, site = _visit(tmp_path, target, url, _ld(nodes), tax="unknown")
    assert outcome.state == "exact" and outcome.reason == "", check_id
    assert outcome.page.ean_published == ean and outcome.page.includes_tax is True
    assert outcome.page.vat_rate is None and outcome.page.price == price
    assert outcome.page.tax_policy_evidence.startswith("business-vat-included-14-2026-11-12")
    assert len(site.calls) == 2
    row = store.offer_history(ean).iloc[0]
    assert row["price_includes_tax"] == 1 and pd.isna(row["vat_rate"])
    assert "responsable de negocio" in row["evidence"]


def test_explicit_tax_exclusion_stays_in_review_through_transport_and_import(tmp_path):
    from price_web import comparability_gap

    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}
    outcome, store, _ = _visit(
        tmp_path, target, url, _ld(_amevista_group(url, tax=False)), tax="unknown")
    assert outcome.state == "probable" and outcome.match_status == "manual_review"
    assert outcome.reason == "tax_conflict"
    assert outcome.page.includes_tax is False and outcome.page.tax_conflict
    search = WebPriceSearch(store, clock=lambda: NOW)
    assert comparability_gap(outcome.page, search.config(outcome.competitor_id)) == "tax_conflict"
    assert search.record(outcome) == ""
    row = store.offer_history(target["ean"]).iloc[0]
    assert row["match_status"] == "manual_review" and row["price_includes_tax"] == 0
    assert store.dashboard(now=NOW)[1]["confirmed"] == 0


def test_import_normalization_applies_policy_without_rewriting_history(tmp_path):
    from price_web import WebPriceSearch

    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}
    outcome, store, _ = _visit(tmp_path, target, url, _ld(_amevista_group(url)), tax="unknown")
    search = WebPriceSearch(store, clock=lambda: NOW)
    row = search.frame_row(target, search.config(outcome.competitor_id), outcome, NOW.isoformat())
    row["IVA incluido"] = ""
    assert not store.import_offers(pd.DataFrame([row])).rejected
    assert len(store.offer_history(target["ean"])) == 1
    row["IVA incluido"] = "no"
    assert not store.import_offers(pd.DataFrame([row])).rejected
    history = store.offer_history(target["ean"])
    assert len(history) == 2
    assert set(zip(history["match_status"], history["price_includes_tax"])) == {
        ("exact_match", 1), ("manual_review", 0)}


@pytest.mark.parametrize("defect", ["stock", "market", "currency", "identity", "age"])
def test_business_tax_never_bypasses_other_comparability_gates(tmp_path, defect):
    from datetime import timedelta

    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}
    node = _amevista_group(url, model="RB2140", title="Ray-Ban RB2140 901")
    if defect == "stock":
        node["hasVariant"][0]["offers"]["availability"] = "OutOfStock"
    elif defect == "market":
        node["hasVariant"][0]["offers"]["eligibleRegion"] = "US"
    elif defect == "currency":
        node["offers"]["priceCurrency"] = "USD"
    elif defect == "identity":
        node["model"] = "RB4472"
    outcome, store, _ = _visit(tmp_path, target, url, _ld(node), tax="unknown")
    assert outcome.page.includes_tax is True
    assert store.dashboard(1, now=NOW + timedelta(days=2) if defect == "age" else NOW)[1]["confirmed"] == 0


def test_common_group_price_does_not_mix_size_stock_or_require_size_prices():
    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    page = extract_product(_ld(_amevista_group(url)), url)
    available = select_product_variant(page, {"ean": "0805289126577", "caliber": "50"})
    unavailable = select_product_variant(page, {"ean": "0805289126584", "caliber": "54"})
    assert available.price == unavailable.price == 200
    assert (available.availability, unavailable.availability) == ("available", "unavailable")
    assert available.includes_tax is unavailable.includes_tax is True


def _observed_edel_layout(url, ean, product_id, title, second_title, price, *, different_stock=False):
    """Synthetic reduction of 2026-11-12 public HTML layout, not the original HTTP response."""
    offer = ('<div class="eoProductPrice"><span itemprop="offers" itemscope '
             'itemtype="http://schema.org/Offer">'
             f'<span itemprop="price" content="{price}">{price}</span>'
             '<meta itemprop="priceCurrency" content="EUR">'
             '<link itemprop="availability" href="http://schema.org/InStock">'
             '<span itemprop="shippingDetails" itemscope itemtype="http://schema.org/OfferShippingDetails">'
             '<span itemprop="shippingDestination" itemscope itemtype="http://schema.org/DefinedRegion">'
             '<meta itemprop="addressCountry" content="ES"></span></span></span></div>')
    widgets = "".join(
        f'<div class="eoProductsPriceBySize eoProductsPriceBySize-{product_id}">'
        + (offer.replace("InStock", "OutOfStock") if different_stock and index == 2 else offer) + "</div>"
        for index in range(3))
    return (
        f'<div itemscope itemtype="http://schema.org/Product"><meta itemprop="name" content="{title}">'
        f'<span itemprop="name">{second_title}</span><meta itemprop="sku" content="{product_id}">'
        f'<meta itemprop="url" content="{url.replace("https:", "http:")}">'
        f'<!-- FrameId = {ean[1:]}--!><input type="radio" id="prd{product_id}" checked>'
        f'<div class="eoDetailListTileSize{product_id}">EAN {ean[1:]}</div>'
        '<div class="eoDetailListTileSize777">EAN 8056597875349</div>'
        + widgets
        + '<div class="eoProductsPriceBySize-777">'
        + offer.replace("InStock", "OutOfStock") + "</div></div>"
    )


@pytest.mark.parametrize("ean,model,color,product_id,title,second_title,price", [
    ("0889214029614", "FT0711", "01A", "708602",
     "Tom Ford Fausto FT 0711 01A", "FAUSTO (FT0711 - 01A)", 201.6),
    ("0889214281791", "FT0884", "01E", "1461733",
     "Tom Ford Falconer-02 FT 0884 01E", "FALCONER-02 (FT0884 - 01E)", 201.6),
    ("0889652429717", "SL 596 DUNE", "006", "1971610",
     "Saint Laurent SL 596 DUNE 006", "SL 596 DUNE (006)", 216),
    ("0889652650838", "SL 872 ERIN", "001", "2352387",
     "Saint Laurent SL 872 ERIN 001", "SL 872 ERIN (001)", 174),
    ("0889652675602", "SL 908", "002", "2423524",
     "Saint Laurent SL 908 002", "SL 908 (002)", 126),
])
def test_observed_edel_duplicate_widgets_are_bound_to_selected_product(
        tmp_path, ean, model, color, product_id, title, second_title, price):
    brand = "Tom Ford" if model.startswith("FT") else "Saint Laurent"
    url = f"https://www.edel-optics.es/{model.replace(' ', '-')}-{color}-de-{brand.replace(' ', '-')}.html"
    target = {"ean": ean, "brand": brand, "model": model, "color": color}
    body = _observed_edel_layout(url, ean, product_id, title, second_title, price)
    outcome, store, _ = _visit(tmp_path, target, url, body, tax="unknown")
    assert outcome.state == "exact" and outcome.reason == ""
    assert outcome.page.ean_published == ean and outcome.page.price == price
    assert outcome.page.variant_id == product_id and outcome.page.availability == "available"
    assert outcome.page.market == "ES" and outcome.page.includes_tax is True
    assert len(store.offer_history(ean)) == 1


def test_edel_duplicate_widgets_with_contradictory_stock_remain_blocked():
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    page = extract_product(_observed_edel_layout(
        url, "0889214029614", "708602", "Tom Ford FT0711 01A", "FT0711 01A", 201.6,
        different_stock=True), url)
    assert page.ambiguous and page.price is None and page.ean_published == ""
    assert page.availability == "unknown"
    assert any("contradictorias" in note for note in page.notes)


def test_edel_duplicate_titles_do_not_hide_a_contradictory_model(tmp_path):
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    target = {"ean": "0889214029614", "brand": "Tom Ford", "model": "FT0711", "color": "01A"}
    body = _observed_edel_layout(
        url, target["ean"], "708602", "Tom Ford FT0711 01A", "FT0884 01A", 201.6)
    outcome, store, _ = _visit(tmp_path, target, url, body, tax="unknown")
    assert outcome.state != "exact" and store.offer_history(target["ean"]).empty


def test_sale_price_specification_cannot_hide_a_contradictory_tax_statement(tmp_path):
    url = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    target = {"ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140", "color": "901"}
    group = _amevista_group(url, tax=True)
    group["offers"]["priceSpecification"] = {
        "@type": "UnitPriceSpecification", "priceType": "SalePrice",
        "price": "200", "valueAddedTaxIncluded": False}
    outcome, store, _ = _visit(tmp_path, target, url, _ld(group), tax="unknown")
    assert outcome.state == "probable" and outcome.reason == "tax_conflict"
    assert outcome.page.tax_conflict
    assert store.dashboard(now=NOW)[1]["confirmed"] == 0


@pytest.mark.parametrize("extra", [
    '<meta itemprop="sku" content="999">',
    '<meta itemprop="gtin12" content="889214281791">',
])
def test_edel_offer_identity_cannot_contradict_its_selected_container(extra):
    url = "https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html"
    body = _observed_edel_layout(
        url, "0889214029614", "708602", "Tom Ford FT0711 01A", "FT0711 01A", 201.6)
    body = body.replace('<meta itemprop="priceCurrency" content="EUR">', extra
                        + '<meta itemprop="priceCurrency" content="EUR">')
    page = extract_product(body, url)
    assert page.ambiguous and page.price is None and page.ean_published == ""
    assert page.availability == "unknown"
