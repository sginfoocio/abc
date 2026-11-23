import json
import os
from pathlib import Path

import pytest

from price_diagnostic import EvidenceError, has_capacity, require_private_database, safe_url, sanitise
from price_web_fetch import Fetched


def test_real_mia_capture_retains_shipping_market_without_unrelated_html():
    from price_web_extract import extract_product

    url = "https://miaburton.com/es/gafas-de-sol/prada/pr-26zs-16k08z"
    content = (Path(__file__).parent / "fixtures" / "web_pages" / "mia_burton.html").read_bytes()
    original = extract_product(content.decode("utf-8"), url)
    metadata, artifact = sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)
    replay = extract_product(artifact.decode("utf-8"), url)
    assert original.market == replay.market == "ES"
    assert replay.price == original.price
    assert b"BARCODE" not in artifact
    assert metadata["unscoped_eans"] == ["8056597875226"]
    assert metadata["unscoped_barcodes"] == ["9053810072392"]
    assert not replay.ean_published


def test_observed_edel_microdata_can_be_retained_as_partial_evidence():
    from price_web_extract import extract_product

    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958"
    content = (Path(__file__).parent / "fixtures" / "web_pages" / "edel_optics.html").read_bytes()
    original = extract_product(content.decode("utf-8"), url)
    metadata, artifact = sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)
    replay = extract_product(artifact.decode("utf-8"), url)
    assert metadata["artifact_kind"] == "sanitizado_parcial_microdatos"
    assert metadata["sanitization_version"] != 3
    assert (replay.method, replay.variant_id, replay.ean_published, replay.price, replay.availability) == (
        original.method, original.variant_id, original.ean_published, original.price, original.availability)


def test_unrelated_edel_offer_does_not_become_a_sanitised_product():
    url = "https://www.edel-optics.es/example.html?selectedSize=1907958"
    content = (b'<html><head><title>Example</title></head><body>'
               b'<div class="eoDetailListTileSize1907958">EAN 8056597875349</div>'
               b'<aside><div class="eoProductPrice"><span itemprop="offers" itemscope '
               b'itemtype="https://schema.org/Offer"><meta itemprop="price" content="100">'
               b'<link itemprop="availability" href="https://schema.org/InStock"></span></div></aside>'
               b'</body></html>')
    with pytest.raises(EvidenceError):
        sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)


@pytest.mark.parametrize(("stock", "expected"), [
    ("InStock", "available"), ("OutOfStock", "unavailable"), ("BackOrder", "unknown"),
])
def test_edel_microdata_keeps_selected_stock_and_drops_unrelated_text(stock, expected):
    from dataclasses import asdict
    from price_web_extract import extract_product

    url = "https://www.edel-optics.es/example.html?selectedSize=1907958"
    content = ('<html><head><title>Prada PR 27ZS 16K08Z</title></head><body>'
               '<input type="radio" id="prd1907958" checked>'
               '<div class="eoDetailListTileSize1907958">EAN 8056597875349</div>'
               '<div class="eoProductPrice"><span itemprop="offers" itemscope '
               'itemtype="https://schema.org/Offer">'
               f'<link itemprop="url" href="{url}"><meta itemprop="price" content="200">'
               '<meta itemprop="priceCurrency" content="EUR">'
               f'<link itemprop="availability" href="https://schema.org/{stock}"></span></div>'
               '<aside><div class="eoDetailListTileSize777">EAN 889214029614</div>'
               '<div class="eoProductPrice"><span itemprop="offers" itemscope '
               'itemtype="https://schema.org/Offer"><meta itemprop="price" content="50">'
               '<link itemprop="availability" href="https://schema.org/InStock"></span></div></aside>'
               '<div itemscope itemtype="https://schema.org/Person">'
               '<meta itemprop="name" content="Private Customer">'
               '<meta itemprop="telephone" content="123456789"></div>'
               '<div>customer@example.com</div></body></html>').encode()
    original = extract_product(content.decode(), url)
    _, artifact = sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)
    assert asdict(extract_product(artifact.decode(), url)) == asdict(original)
    assert original.availability == expected
    assert b"customer@example.com" not in artifact
    assert b"Private Customer" not in artifact and b"123456789" not in artifact


def test_edel_microdata_rejects_restrictions_instead_of_discarding_them():
    url = "https://www.edel-optics.es/example.html?selectedSize=1907958"
    content = (b'<html><head><title>Prada PR 27ZS 16K08Z</title></head><body>'
               b'<div class="eoDetailListTileSize1907958">EAN 8056597875349</div>'
               b'<div class="eoProductPrice"><span itemprop="offers" itemscope '
               b'itemtype="https://schema.org/Offer"><meta itemprop="price" content="100">'
               b'<meta itemprop="priceCurrency" content="EUR">'
               b'<meta itemprop="eligibleCustomerType" content="private tier">'
               b'<link itemprop="availability" href="https://schema.org/InStock">'
               b'</span></div></body></html>')
    with pytest.raises(EvidenceError, match="Restricción"):
        sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)


def test_edel_conflicting_radio_is_not_promoted_by_sanitisation():
    url = "https://www.edel-optics.es/example.html?selectedSize=1907958"
    content = (b'<html><body><input type="radio" id="prd777" checked>'
               b'<div class="eoDetailListTileSize1907958">EAN 8056597875349</div>'
               b'<div class="eoProductPrice"><span itemprop="offers" itemscope '
               b'itemtype="https://schema.org/Offer"><meta itemprop="price" content="100">'
               b'<link itemprop="availability" href="https://schema.org/InStock"></span></div></body></html>')
    with pytest.raises(EvidenceError):
        sanitise(Fetched(url, 200, content, url, []), 2 * 1024 * 1024)


def test_sanitisation_keeps_unscoped_upc_without_treating_barcode_as_ean():
    url = "https://shop.example/product"
    text = ('<html><script type="application/ld+json">{"@type":"Product","name":"Example"}'
            '</script><div>UPC: 889652429687</div><div>BARCODE: 9053810072392</div></html>')
    metadata, artifact = sanitise(Fetched(url, 200, text.encode(), url, []), 1024 * 1024)
    assert metadata["unscoped_eans"] == ["889652429687"]
    assert metadata["unscoped_barcodes"] == []
    assert b"889652429687" not in artifact and b"9053810072392" not in artifact


def test_mia_barcodes_remain_distinct_and_unscoped():
    url = "https://miaburton.com/example"
    text = ('<html><script type="application/ld+json">{"@type":"Product","name":"Example"}'
            '</script><div>EAN: 8056597875226</div><div>BARCODE: 9053810072392</div>'
            '<div>BARCODE: 9053810072393</div></html>')
    metadata, artifact = sanitise(Fetched(url, 200, text.encode(), url, []), 1024 * 1024)
    assert metadata["unscoped_eans"] == ["8056597875226"]
    assert metadata["unscoped_barcodes"] == ["9053810072392", "9053810072393"]
    assert b"9053810072392" not in artifact and b"9053810072393" not in artifact


def test_mia_rejects_excess_unscoped_barcodes():
    url = "https://miaburton.com/example"
    text = ('<html><script type="application/ld+json">{"@type":"Product","name":"Example"}'
            '</script>' + ''.join(f'<div>BARCODE: {number:013d}</div>' for number in range(33))
            + '</html>')
    with pytest.raises(EvidenceError, match="Demasiados identificadores BARCODE"):
        sanitise(Fetched(url, 200, text.encode(), url, []), 1024 * 1024)


@pytest.mark.parametrize(("ean", "brand", "model", "color", "description", "stock"), [
    ("0843023181451", "Cartier", "CT0550S", "001", "gold", "OutOfStock"),
    ("0889652429687", "Saint Laurent", "SL 596 DUNE", "003", "brown", "InStock"),
])
def test_synthetic_mia_descriptive_color_and_canonical_do_not_select_variant(
        ean, brand, model, color, description, stock):
    from price_references import EXACT, classify
    from price_web_extract import extract_product, select_product_variant

    url = "https://miaburton.com/en/example-" + color
    canonical = "https://miaburton.com/en/example-001"
    node = {"@type": "Product", "name": f"Sunglasses {brand} {model} - Mia Burton",
            "brand": {"@type": "Brand", "name": brand}, "color": description,
            "offers": {"@type": "Offer", "url": url, "price": "223", "priceCurrency": "EUR",
                       "availability": "https://schema.org/" + stock}}
    text = ('<html><head><link rel="canonical" href="' + canonical + '">'
            '<script type="application/ld+json">' + json.dumps(node) + '</script></head></html>')
    metadata, artifact = sanitise(Fetched(url, 200, text.encode(), url, []), 2 * 1024 * 1024)
    page = extract_product(artifact.decode(), url)
    assert metadata["canonical_urls"] == [canonical]
    assert not page.color and not page.variant_id
    assert page.availability == ("unavailable" if stock == "OutOfStock" else "available")
    product = {"ean": ean, "brand": brand, "model": model, "color": color}
    page = select_product_variant(page, product)
    assert classify(product, page.candidate(), allow_reference_match=True).status != EXACT
    assert page.availability == "unknown" and not page.market


@pytest.mark.parametrize(("model", "color", "stock"), [
    ("CT0550S", "001", "OutOfStock"), ("SL 596 DUNE", "003", "InStock"),
])
@pytest.mark.parametrize("top_level_array", [False, True])
def test_synthetic_mia_explicit_main_entity_survives_sanitisation(model, color, stock, top_level_array):
    from price_references import EXACT, classify
    from price_web_extract import extract_product, select_product_variant

    url = "https://miaburton.com/en/example"
    brand = "Cartier" if model == "CT0550S" else "Saint Laurent"
    product = {"ean": "0843023181451" if brand == "Cartier" else "0889652429687",
               "brand": brand, "model": model, "color": color, "caliber": "54-18"}
    graph = {"@graph": [
        {"@type": "WebPage", "url": url, "name": "Private Page Owner",
         "mainEntity": {"@id": "#selected", "name": "Private Entity Owner"}},
        {"@type": "ProductGroup", "name": f"{brand} {model}", "brand": {"@type": "Brand", "name": brand},
         "model": model, "hasVariant": [
             {"@type": "Product", "@id": "#selected", "color": color, "size": "56-18",
              "offers": {"@type": "Offer", "url": url, "price": "223", "priceCurrency": "EUR",
                         "availability": "https://schema.org/" + stock}},
             {"@type": "Product", "@id": "#other", "color": "999",
              "offers": {"@type": "Offer", "url": url + "-other", "price": "99",
                         "priceCurrency": "EUR", "availability": "https://schema.org/InStock"}},
         ]},
        {"@type": "Person", "name": "Private Customer"},
    ]}
    text = ('<html><head><link rel="canonical" href="' + url + '-001">'
            '<script type="application/ld+json">' + json.dumps([graph] if top_level_array else graph)
            + '</script></head><body>customer@example.com</body></html>')
    fetched = Fetched(url, 200, text.encode(), url, [])
    metadata, artifact = sanitise(fetched, 2 * 1024 * 1024)
    original, replay = extract_product(text, url), extract_product(artifact.decode(), url)
    assert metadata["canonical_urls"] == [url + "-001"]
    assert b"Private Customer" not in artifact and b"customer@example.com" not in artifact
    assert b"Private Page Owner" not in artifact and b"Private Entity Owner" not in artifact
    assert (replay.color, replay.variant_id, replay.price, replay.availability) == (
        original.color, "selected", 223, "unavailable" if stock == "OutOfStock" else "available")
    assert replay.url_trace["selected_variant_source"] == "webpage_main_entity"
    assert classify(product, replay.candidate(), allow_reference_match=True).status == EXACT
    scoped = select_product_variant(replay, product)
    assert scoped.price == 223 and scoped.availability == "unknown"
    assert not scoped.ean_published and not scoped.market


def test_sanitisation_can_replay_sanitised_jsonld_array():
    from price_web_extract import extract_product

    url = "https://miaburton.com/en/example"
    product = {"@type": "Product", "name": "Cartier CT0550S",
               "offers": {"@type": "Offer", "price": "1100", "priceCurrency": "EUR",
                          "availability": "https://schema.org/OutOfStock"}}
    html = ('<html><script type="application/ld+json">' + json.dumps(product) +
            '</script></html>').encode()
    _, first = sanitise(Fetched(url, 200, html, url, []), 2 * 1024 * 1024)
    _, second = sanitise(Fetched(url, 200, first, url, []), 2 * 1024 * 1024)
    original, replay = extract_product(first.decode(), url), extract_product(second.decode(), url)
    assert replay is not None
    assert (replay.price, replay.currency, replay.availability) == (
        original.price, original.currency, original.availability)


@pytest.mark.parametrize("conflict", ["missing", "two", "other_offer", "other_ean", "missing_color",
                                      "group_color", "duplicate_id"])
@pytest.mark.parametrize("top_level_array", [False, True])
def test_synthetic_mia_main_entity_conflicts_remain_ambiguous(conflict, top_level_array):
    from price_web_extract import extract_product

    url = "https://miaburton.com/en/example"
    variant = {"@type": "Product", "@id": "#selected", "color": "003", "gtin13": "0889652429687",
               "offers": {"@type": "Offer", "url": url, "price": "223", "priceCurrency": "EUR",
                          "availability": "https://schema.org/InStock"}}
    entity = {"@id": "#selected"}
    if conflict == "missing":
        entity = {"@id": "#missing"}
    elif conflict == "two":
        entity = [{"@id": "#selected"}, {"@id": "#other"}]
    elif conflict == "other_offer":
        variant["offers"]["url"] = url + "-other"
    elif conflict == "other_ean":
        variant["offers"]["gtin13"] = "8056376451320"
    elif conflict == "missing_color":
        del variant["color"]
    group = {"@type": "ProductGroup", "hasVariant": [variant]}
    if conflict in {"missing_color", "group_color"}:
        group["color"] = "001"
    nodes = [{"@type": "WebPage", "url": url, "mainEntity": entity}, group]
    if conflict == "duplicate_id":
        nodes.append({"@type": "Product", "@id": "#selected", "color": "001"})
    graph = {"@graph": nodes}
    text = ('<html><script type="application/ld+json">' +
            json.dumps([graph] if top_level_array else graph) + '</script></html>')
    metadata, artifact = sanitise(Fetched(url, 200, text.encode(), url, []), 2 * 1024 * 1024)
    for source in (text, artifact.decode()):
        page = extract_product(source, url)
        assert page.ambiguous and page.price is None and page.availability == "unknown"


def test_synthetic_mia_person_entity_is_not_sanitised_as_product():
    url = "https://miaburton.com/en/example"
    graph = {"@graph": [
        {"@type": "WebPage", "url": url, "mainEntity": {"@type": "Person", "@id": "#selected"}},
        {"@type": "ProductGroup", "hasVariant": [{"@type": "Product", "@id": "#selected"}]},
    ]}
    text = '<html><script type="application/ld+json">' + json.dumps(graph) + '</script></html>'
    with pytest.raises(EvidenceError, match="Entidad principal"):
        sanitise(Fetched(url, 200, text.encode(), url, []), 2 * 1024 * 1024)


def test_synthetic_mia_sanitisation_cannot_merge_different_variant_ids():
    url = "https://miaburton.com/en/example"
    graph = {"@graph": [
        {"@type": "WebPage", "url": url, "mainEntity": {"@id": "#selected"}},
        {"@type": "ProductGroup", "hasVariant": [
            {"@type": "Product", "@id": url + "?other=1#selected", "color": "003",
             "offers": {"@type": "Offer", "url": url, "price": "223", "priceCurrency": "EUR"}},
        ]},
    ]}
    text = '<html><script type="application/ld+json">' + json.dumps(graph) + '</script></html>'
    with pytest.raises(EvidenceError, match="no conserva la selección"):
        sanitise(Fetched(url, 200, text.encode(), url, []), 2 * 1024 * 1024)


def test_sanitisation_preserves_restrictions_and_conflicting_tax_without_private_values():
    from price_web_extract import extract_product

    url = "https://shop.example/product"
    product = {"@type": "Product", "name": "Example", "offers": {
        "@type": "Offer", "url": url, "price": "120", "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock",
        "valueAddedTaxIncluded": False, "eligibleCustomerType": "private member",
    }}
    html = ('<html><script type="application/ld+json">' + json.dumps(product) + '</script></html>').encode()
    original = extract_product(html.decode(), url)
    _, artifact = sanitise(Fetched(url, 200, html, url, []), 2 * 1024 * 1024)
    replay = extract_product(artifact.decode(), url)
    assert original.price is None and replay.price is None
    assert b"private member" not in artifact

    del product["offers"]["eligibleCustomerType"]
    html = ('<html><script type="application/ld+json">' + json.dumps(product) + '</script></html>').encode()
    _, artifact = sanitise(Fetched(url, 200, html, url, []), 2 * 1024 * 1024)
    assert extract_product(artifact.decode(), url).includes_tax is False


def test_sanitisation_preserves_delivery_time_and_country_only():
    from price_web_extract import extract_product

    url = "https://shop.example/product"
    product = {"@type": "Product", "name": "Example", "offers": {
        "@type": "Offer", "price": "120", "priceCurrency": "EUR",
        "availability": "https://schema.org/BackOrder",
        "shippingDetails": {"@type": "OfferShippingDetails",
                            "shippingDestination": {"@type": "DefinedRegion",
                                                    "addressCountry": "FR",
                                                    "postalCode": "private postal code"},
                            "deliveryTime": {"@type": "ShippingDeliveryTime",
                                             "handlingTime": {"@type": "QuantitativeValue",
                                                              "minValue": 8, "maxValue": 15,
                                                              "unitCode": "DAY"}}},
    }}
    html = ('<html><script type="application/ld+json">' + json.dumps(product) + '</script></html>').encode()
    original = extract_product(html.decode(), url)
    _, artifact = sanitise(Fetched(url, 200, html, url, []), 2 * 1024 * 1024)
    replay = extract_product(artifact.decode(), url)
    assert replay.market == original.market == "FR"
    assert replay.delivery_evidence == original.delivery_evidence
    assert replay.availability == original.availability == "unknown"
    assert b"private postal code" not in artifact
    product["offers"]["shippingDetails"]["@type"] = "UnrecognisedShipping"
    html = ('<html><script type="application/ld+json">' + json.dumps(product) + '</script></html>').encode()
    with pytest.raises(EvidenceError, match="Tipo comercial no conservable"):
        sanitise(Fetched(url, 200, html, url, []), 2 * 1024 * 1024)


def test_sanitisation_preserves_relationships_and_redacts_irrelevant_content():
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "ProductGroup", "@id": "#g", "hasVariant": {"@id": "#v"}},
        {"@type": "Product", "@id": "#v", "sku": "A1", "offers": {"@id": "#o"}},
        {"@type": "Offer", "@id": "#o", "price": "120", "priceCurrency": "EUR"},
        {"@type": "Person", "name": "Private Name"},
    ]}
    text = ('<html><head><link rel="canonical" href="https://shop.example/p?token=private">'
            '<script type="application/ld+json">' + json.dumps(graph)
            + '</script></head><body><input name="csrf" value="private"></body></html>')
    fetched = Fetched("https://shop.example/p", 200, text.encode(), "https://shop.example/p", [])
    metadata, artifact = sanitise(fetched, 2 * 1024 * 1024)
    assert all(value in artifact for value in (b"#g", b"#v", b"#o", b'"price":"120"'))
    assert b"private" not in artifact.lower()
    assert metadata["canonical_urls"] == ["https://shop.example/p"]
    assert safe_url("https://shop.example/p?token=private&selectedSize=50") == "https://shop.example/p?selectedSize=50"
    graph["@graph"][1]["offers"] = {"@id": "offer?token=private"}
    text = '<html><script type="application/ld+json">' + json.dumps(graph) + '</script></html>'
    fetched = Fetched("https://shop.example/p", 200, text.encode(), "https://shop.example/p", [])
    _, artifact = sanitise(fetched, 2 * 1024 * 1024)
    assert b"private" not in artifact.lower()


def test_private_storage_fails_closed(tmp_path):
    database = tmp_path / "test.sqlite"
    database.write_bytes(b"")
    if os.name != "posix":
        with pytest.raises(EvidenceError, match="verificar"):
            require_private_database(database)
    else:
        Path(database).chmod(0o644)
        with pytest.raises(EvidenceError, match="privados"):
            require_private_database(database)
        Path(database).chmod(0o600)
        require_private_database(database)


def test_capacity_checks_both_quota_and_disk(tmp_path, monkeypatch):
    from collections import namedtuple

    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("price_diagnostic.shutil.disk_usage", lambda path: usage(100, 91, 9))
    assert not has_capacity(tmp_path / "test.sqlite", 0, 5, 100)
    assert not has_capacity(tmp_path / "test.sqlite", 97, 5, 100)
    monkeypatch.setattr("price_diagnostic.shutil.disk_usage", lambda path: usage(100, 90, 10))
    assert has_capacity(tmp_path / "test.sqlite", 95, 5, 100)
