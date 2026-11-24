"""Estructuras reales de fichas de competidores (observadas el 2026-10-30), reducidas y sin conexión."""
from pathlib import Path
import hashlib
import json

import pytest

from price_references import PROBABLE, caliber_from_code, classify, extract_lens
from price_web_extract import extract_product, product_page_rejection, select_product_variant

FIXTURES = Path(__file__).parent / "fixtures" / "web_pages"
PR27 = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z", "caliber": "54-21"}
PR26 = {"ean": "8056597875226", "brand": "Prada", "model": "PR 26ZS", "color": "16K08Z", "caliber": "55-16"}

# IVA efectivo incluye la decisión de negocio de 2026-11-12; no cambia el HTML histórico.
# nombre, URL, producto buscado, precio, IVA incluido, mercado, calibre extraído
CASES = [
    ("optica_optima", "https://optica-optima.com/gafas-de-sol/prada-pr-27zs-16k08z.html", PR27, 273.0, True, "", "54-21"),
    ("amevista", "https://www.amevista.com/es/prada-0pr-27zs-16k08z", PR27, 273.0, True, "", None),
    ("edel_optics", "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958", PR27, 243.5, True, "", None),
    ("otticait", "https://www.otticait.com/en/prada-pr-27zs-16k08z", PR27, 241.8, True, "", None),
    ("mia_burton", "https://miaburton.com/es/gafas-de-sol/prada/pr-26zs-16k08z", PR26, 247.0, True, "ES", None),
    ("congafasdesol", "https://www.congafasdesol.com/prada-pr-27zs-16k08z.html", PR27, 253.5, True, "", None),
    ("blickers", "https://www.blickers.com/en-es/glasses/prada-pr-27zs-c54/5649966192263168/", PR27, 273.0, True, "", "54"),
    ("domanci", "https://domanci.com/es/gafas-de-sol/prada-pr-27zs-16k08z-54.html", PR27, 240.5, True, "", "54-21"),
]


def load(name, url):
    return extract_product((FIXTURES / f"{name}.html").read_text(encoding="utf-8"), url)


@pytest.mark.parametrize("has_page", [False, True])
def test_rejected_observation_keeps_rejection_when_market_is_unknown(has_page):
    from price_web import Outcome, WebPriceSearch
    from price_web_extract import ProductPage

    config = {"name": "synthetic", "domain": "example.test", "currency": "EUR", "market": "ES"}
    page = ProductPage(url="https://example.test/rejected", price=49, currency="EUR",
                       includes_tax=True) if has_page else None
    outcome = Outcome(PR27["ean"], "synthetic", "synthetic", "rejected",
                      url="https://example.test/rejected", page=page, detail="Synthetic rejected identity")
    row = WebPriceSearch.frame_row(PR27, config, outcome, "2026-11-24T20:29:45+00:00")
    assert row["Estado coincidencia"] == "rejected"
    assert row["Precio"] == ""

@pytest.mark.parametrize("name,url,product", [
    ("otticait", "https://www.otticait.com/en/prada-pr-27zs-16k08z", PR27),
    ("blickers", "https://www.blickers.com/en-es/glasses/prada-pr-27zs-c54/5649966192263168/", PR27),
    ("visual_click", "https://visual-click.com/", PR27),
])
def test_confirmed_business_market_is_policy_not_observed_country(name, url, product):
    from competitor_requirements import BUSINESS_MARKET_DECISION
    from price_web import Outcome, WebPriceSearch
    from price_web_extract import ProductPage, resolve_market
    from urllib.parse import urlsplit

    # Otticait and Blickers use retained reduced HTML; Visual-Click is a synthetic extension.
    page = load(name, url) if name != "visual_click" else ProductPage(url=url, price=198.0, currency="EUR")
    assert page.market == "" and page.market_source == ""
    config = {"name": name, "domain": urlsplit(url).hostname.removeprefix("www."),
              "site_url": url, "currency": "EUR", "market": "ES",
              "market_verified": 0, "sales_market": ""}
    market, evidence, conflict = resolve_market(page, config)
    assert market == "ES" and not conflict
    assert BUSINESS_MARKET_DECISION["id"] in evidence and "no país observado" in evidence
    outcome = Outcome(product["ean"], name, name, "exact", url=url, page=page, method="web:fixture")
    row = WebPriceSearch.frame_row(product, config, outcome, "2026-10-30T12:00:00+00:00")
    assert row["Mercado"] == "ES" and row["Fecha consulta"] == "2026-10-30T12:00:00+00:00"
    assert page.market == "" and page.market_source == ""
    page.market, page.market_source = "FR", "synthetic explicit destination"
    assert resolve_market(page, config)[2]
    assert WebPriceSearch.frame_row(product, config, outcome, row["Fecha consulta"])["Estado coincidencia"] == "manual_review"


@pytest.mark.parametrize("number,url,product,digest,price,stock", [
    (7, "https://www.amevista.com/es/gucci-gg1660s-001",
     {"ean": "0889652494821", "brand": "Gucci", "model": "GG1660S", "color": "001"},
     "cf239f7c16c29e5d05743eef881dd34e349a76ecfb73948574aea5b0f5ffa533", 188.5, "available"),
    (8, "https://visual-click.com/products/miu-miu-mu-54ys-zvn20f-80",
     {"ean": "8056262142585", "brand": "Miu Miu", "model": "MU 54YS", "color": "ZVN20F"},
     "f2ba1ef3f70781330ae9d57f5052484867db919099262092467a4273a52e2397", 331.0, "unknown"),
    (9, "https://domanci.com/es/gafas-de-sol/miu-miu-mu-a51s-5ak20p-75.html",
     {"ean": "8056262412367", "brand": "Miu Miu", "model": "MU A51S", "color": "5AK20P"},
     "ca16dece3bb302298932d35dfc049795c888dbb8b4adae98f8dbd8720d3446fa", 305.5, "available"),
])
def test_sanitized_partial_diagnostic_captures(number, url, product, digest, price, stock):
    # These are partial JSON-LD captures, not complete pages or commercial market evidence.
    content = (FIXTURES / f"partial_20261117_{number}.html").read_bytes().rstrip(b"\r\n")
    assert hashlib.sha256(content).hexdigest() == digest
    page = select_product_variant(extract_product(content.decode("utf-8"), url), product)
    assert classify(product, page.candidate()).status == "exact_match"
    assert (page.price, page.currency, page.availability) == (price, "EUR", stock)
    if number == 7:
        assert page.ean_published == "0889652494821" == product["ean"]
        assert page.ean_published_raw == ["889652494821"]
    if number == 8:
        assert page.ean_published == ""
        assert page.url_trace["selected_variant_url"].endswith("?variant=48783648653611")


def test_partial_capture_gucci_descriptor_requires_group_and_matching_ean():
    url = "https://www.amevista.com/es/gucci-gg1660s-001"
    product = {"ean": "0889652494821", "brand": "Gucci", "model": "GG1660S", "color": "001"}
    page = select_product_variant(load("partial_20261117_7", url), product)
    candidate = page.candidate()
    candidate["group_reference"] = "GG1660S-002"
    assert classify(product, candidate).status != "exact_match"
    candidate["group_reference"] = "GG1660S-001"
    candidate["model"] = "OTHER999"
    assert classify(product, candidate).status != "exact_match"
    candidate["model"] = "Logotipo Gg"
    candidate["ean_published"] = "8056262412367"
    assert classify(product, candidate).status != "exact_match"


def test_partial_capture_composite_identifier_does_not_hide_conflicting_color():
    url = "https://domanci.com/es/gafas-de-sol/miu-miu-mu-a51s-5ak20p-75.html"
    product = {"ean": "8056262412367", "brand": "Miu Miu", "model": "MU A51S", "color": "5AK20P"}
    page = load("partial_20261117_9", url)
    candidate = page.candidate()
    candidate["identifiers"] = ["0MU_A51S5AK20Q7514"]
    assert classify(product, candidate).status != "exact_match"
    candidate = page.candidate()
    candidate["color"] = "5AK20Q"
    assert classify(product, candidate).status != "exact_match"


def test_partial_capture_sku_binding_requires_exact_unique_sku_and_no_gtin_conflict():
    url = "https://visual-click.com/products/miu-miu-mu-54ys-zvn20f-80"
    html = (FIXTURES / "partial_20261117_8.html").read_text(encoding="utf-8")
    mismatched = html.replace('"sku":"8056262142585-0000000013-@3-5","availability"',
                              '"sku":"different","availability"')
    conflicting = html.replace('"sku":"8056262142585-0000000013-@3-5","brand"',
                               '"gtin13":"invalid","sku":"8056262142585-0000000013-@3-5","brand"')
    for modified in (mismatched, conflicting):
        page = extract_product(modified, url)
        assert page.price is None
        assert page.availability == "unknown"


@pytest.mark.parametrize("name,url,product,price,tax,market,caliber", CASES)
def test_real_structures_are_extracted(name, url, product, price, tax, market, caliber):
    page = load(name, url)
    assert page is not None
    assert page.price == price
    assert page.currency == "EUR"
    assert page.availability == "available"
    assert page.ean_published == product["ean"]
    assert page.includes_tax is tax
    assert page.market == market
    if caliber:
        assert page.caliber == caliber
    match = classify(product, page.candidate())
    if name == "mia_burton":
        # An exact published EAN is sufficient when the page does not contradict it.
        assert match.status == "exact_match"
    else:
        assert match.status == "exact_match"
    if name == "edel_optics":
        assert page.variant_id == "1907958"
        assert "selectedSize" in page.url


def _edel_variant_node(variant_id, ean, price, availability):
    variant_url = ("https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
                   f"?selectedSize={variant_id}")
    return {
        "@type": "Product", "url": variant_url, "name": "Prada PR 27ZS 16K08Z",
        "brand": {"@type": "Brand", "name": "Prada"}, "model": "PR 27ZS", "color": "16K08Z",
        "gtin13": ean, "offers": {
            "@type": "Offer", "url": variant_url, "price": str(price), "priceCurrency": "EUR",
            "availability": f"https://schema.org/{availability}",
        },
    }


def test_edel_selected_radio_resolves_variant_id_and_scopes_offer_facts():
    target = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    html = (
        '<input type="radio" id="prd1907958" checked>'
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                _edel_variant_node("1907958", target["ean"], 243.5, "InStock"),
                _edel_variant_node("1907959", "8056597875226", 250, "OutOfStock"),
            ],
        }) + "</script>"
    )

    page = extract_product(html, url)

    assert page is not None
    assert page.variant_id == "1907958"
    assert page.url.endswith("?selectedSize=1907958")
    assert page.url_trace["final_url"] == url
    assert page.url_trace["selected_variant_url"] == page.url
    assert (page.ean_published, page.price, page.currency, page.availability) == (
        target["ean"], 243.5, "EUR", "available")
    assert classify(target, page.candidate()).status == "exact_match"
    assert any("Product Id 1907958 resuelto" in note for note in page.notes)


def test_edel_internal_selection_requires_radio_offer_and_valid_gtin():
    from dataclasses import replace
    from price_monitor import _accepted_edel_resolution
    from price_web_extract import scope_availability_to_ean
    from tests.test_price_locator import _gtin

    product = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    node = _edel_variant_node("1907958", product["ean"], 243.5, "InStock")
    html = ('<input type="radio" id="prd1907958" checked>'
            '<script type="application/ld+json">' + json.dumps(node) + '</script>')
    page = extract_product(html, url, url_trace={"candidate_url": url})
    assert _accepted_edel_resolution(page, url, product)
    other_ean = replace(page, ean_published=_gtin("123456789012"))
    assert _accepted_edel_resolution(other_ean, url, product)
    assert classify(product, other_ean.candidate()).status != "exact_match"
    assert classify(product, other_ean.candidate(), allow_reference_match=True).status == "exact_match"
    scope_availability_to_ean(other_ean, product["ean"])
    assert other_ean.availability == "unknown"
    missing_ean = replace(page, ean_published="")
    scope_availability_to_ean(missing_ean, product["ean"])
    assert missing_ean.availability == "unknown"
    assert classify(product, missing_ean.candidate()).status == "exact_match"
    assert _accepted_edel_resolution(missing_ean, url, product)
    invalid_ean = replace(page, ean_published="not-a-gtin")
    assert not _accepted_edel_resolution(invalid_ean, url, product)
    assert not _accepted_edel_resolution(replace(page, ambiguous=True), url, product)
    assert not _accepted_edel_resolution(
        replace(page, url_trace={**page.url_trace, "offer_variant_url": url + "?selectedSize=1907959"}),
        url, product)
    assert not _accepted_edel_resolution(
        replace(page, url_trace={**page.url_trace, "selected_variant_source": ""}), url, product)
    assert not _accepted_edel_resolution(page, url + "?selectedSize=1907959", product)


def test_edel_selected_product_offer_without_own_url_is_bound_by_checked_radio_and_gtin():
    from dataclasses import replace
    from price_monitor import _accepted_edel_resolution, _accepted_edel_resolution_reason

    product = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    selected_url = url + "?selectedSize=1907958"
    html = (
        '<input type="radio" id="prd1907958" checked>'
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org",
            "@type": "Product",
            "url": selected_url,
            "name": "Prada PR 27ZS 16K08Z",
            "brand": {"@type": "Brand", "name": "Prada"},
            "model": "PR 27ZS",
            "color": "16K08Z",
            "gtin13": "8056597875349",
            "offers": {
                "@type": "Offer", "price": "243.50", "priceCurrency": "EUR",
                "availability": "https://schema.org/InStock",
            },
        }) + "</script>"
    )

    page = extract_product(html, url, url_trace={"candidate_url": url})

    assert page is not None
    assert page.url == selected_url
    assert page.url_trace["final_url"] == url
    assert page.url_trace["selected_variant_source"] == "checked_radio"
    assert page.url_trace.get("offer_variant_url") is None
    assert page.offer_binding_source == "product_offer"
    assert page.ean_published == product["ean"]
    assert page.ean_published_raw == ["8056597875349"]
    assert classify(product, page.candidate()).status == "exact_match"
    assert _accepted_edel_resolution(page, url, product)
    assert _accepted_edel_resolution_reason(page, url, product) == ""
    unbound = replace(page, offer_binding_source="")
    assert _accepted_edel_resolution_reason(unbound, url, product) == (
        "la Offer no está vinculada a un Product seleccionado ni a su ProductGroup")


def test_edel_resolution_normalizes_valid_upc_to_catalogue_ean():
    from price_monitor import _accepted_edel_resolution

    product = {"ean": "0805289126577", "brand": "Ray Ban", "model": "RB2140", "color": "901"}
    url = "https://www.edel-optics.es/RB2140-901-de-Ray-Ban.html"
    node = _edel_variant_node("1907958", "0805289126577", 243.5, "InStock")
    node.update({"name": "Ray Ban RB2140 901", "brand": {"@type": "Brand", "name": "Ray Ban"},
                 "model": "RB2140", "color": "901"})
    variant_url = url + "?selectedSize=1907958"
    node["url"] = node["offers"]["url"] = variant_url
    node.pop("gtin13")
    node["gtin12"] = "805289126577"
    html = ('<input type="radio" id="prd1907958" checked>'
            '<script type="application/ld+json">' + json.dumps(node) + '</script>')

    page = extract_product(html, url, url_trace={"candidate_url": url, "final_url": url})

    assert page is not None
    assert page.ean_published == product["ean"]
    assert page.ean_published_raw == ["805289126577"]
    assert classify(product, page.candidate()).status == "exact_match"
    assert _accepted_edel_resolution(page, url, product)


def test_edel_redirect_trace_is_preserved_and_changed_final_url_is_rejected():
    from price_monitor import _accepted_edel_resolution_reason

    product = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    selected_url = url + "?selectedSize=1907958"
    redirect = {"url": url, "status": 302, "location": selected_url}
    html = ('<input type="radio" id="prd1907958" checked>'
            '<script type="application/ld+json">' +
            json.dumps(_edel_variant_node("1907958", product["ean"], 243.5, "InStock")) +
            '</script>')
    trace = {"requested_url": url, "candidate_url": url, "final_url": selected_url,
             "redirects": [redirect]}

    page = extract_product(html, selected_url, url_trace=trace)

    assert page is not None and page.variant_id == "1907958"
    assert page.url_trace["candidate_url"] == url
    assert page.url_trace["redirects"] == [redirect]
    assert page.url_trace["final_url"] == selected_url
    assert page.url_trace["selected_variant_url"] == selected_url
    assert _accepted_edel_resolution_reason(page, url, product) == (
        "la URL final no coincide con la ruta aceptada")


def test_edel_category_route_is_rejected_even_with_product_metadata():
    url = "https://www.edel-optics.es/Gafas-de-sol-Miu-Miu.html"
    html = ('<script type="application/ld+json">' + json.dumps({
        "@type": "Product", "url": url, "name": "Miu Miu MU A06S 14L09Z",
        "offers": {"@type": "Offer", "price": "250", "priceCurrency": "EUR"},
    }) + "</script>")

    rejection = product_page_rejection(html, url)

    assert rejection == "no_product: categoría Edel-Optics, no ficha de producto"
    assert not product_page_rejection("", "https://www.edel-optics.es/MU-A06S-14L09Z-de-Miu-Miu.html")


def test_edel_selected_variant_uses_common_model_color_price_and_its_own_stock():
    base = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    variants = []
    for variant_id, ean, size, availability in [
        ("111", "8056597875226", "55-16", "OutOfStock"),
        ("222", "8056597875349", "54-21", "InStock"),
    ]:
        variant_url = f"{base}?selectedSize={variant_id}"
        variants.append({
            "@type": "Product", "url": variant_url, "name": f"Prada PR 27ZS 16K08Z {size}",
            "brand": {"@type": "Brand", "name": "Prada"}, "model": "PR 27ZS", "color": "16K08Z",
            "size": size, "gtin13": ean,
            "offers": {"@type": "Offer", "url": variant_url,
                       "availability": f"https://schema.org/{availability}"},
        })
    html = (
        '<input type="radio" id="prd222" checked>'
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@type": "ProductGroup", "url": base,
            "offers": {"@type": "Offer", "price": "243.50", "priceCurrency": "EUR"},
            "hasVariant": variants,
        }) + "</script>"
    )

    page = extract_product(html, base)

    assert page is not None and not page.ambiguous
    assert page.variant_id == "222"
    assert (page.ean_published, page.caliber, page.price, page.currency, page.availability) == (
        "8056597875349", "54-21", 243.5, "EUR", "available")
    assert page.offer_binding_source == "variant_url"
    assert classify(
        {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS",
         "color": "16K08Z", "caliber": "54-21"},
        page.candidate(),
    ).status == "exact_match"


def test_edel_variant_selection_conflicts_remain_ambiguous_without_offer_facts():
    base = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    nodes = {
        "@context": "https://schema.org",
        "@graph": [
            _edel_variant_node("1907958", "8056597875349", 243.5, "InStock"),
            _edel_variant_node("1907959", "8056597875226", 250, "OutOfStock"),
        ],
    }
    conflicting = (
        '<input type="radio" id="prd1907958" checked>'
        '<script type="application/ld+json">' + json.dumps(nodes) + "</script>"
    )
    page = extract_product(conflicting, base + "?selectedSize=1907959")
    assert page is not None and page.ambiguous
    assert page.price is None and page.availability == "unknown"
    assert any("se contradicen" in note for note in page.notes)

    multiple = (
        '<input type="radio" id="prd1907958" checked>'
        '<input type="radio" id="prd1907959" checked>'
        '<script type="application/ld+json">' + json.dumps(nodes) + "</script>"
    )
    page = extract_product(multiple, base)
    assert page is not None and page.ambiguous
    assert page.price is None and page.availability == "unknown"
    assert any("más de una opción" in note for note in page.notes)


def test_edel_common_product_group_offer_is_bound_to_selected_product_and_gtin():
    from price_monitor import _accepted_edel_resolution

    product = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"}
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    selected_url = url + "?selectedSize=1907958"
    html = (
        '<input type="radio" id="prd1907958" checked>'
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org",
            "@type": "ProductGroup",
            "url": url,
            "offers": {"@type": "Offer", "price": "243.50", "priceCurrency": "EUR",
                       "availability": "https://schema.org/InStock"},
            "hasVariant": [{
                "@type": "Product",
                "url": selected_url,
                "name": "Prada PR 27ZS 16K08Z 54-21",
                "brand": {"@type": "Brand", "name": "Prada"},
                "model": "PR 27ZS",
                "color": "16K08Z",
                "size": "54-21",
                "gtin13": product["ean"],
            }],
        }) + "</script>"
    )

    page = extract_product(html, url, url_trace={"candidate_url": url})

    assert page is not None and not page.ambiguous
    assert page.url == selected_url
    assert page.offer_binding_source == "product_group_offer"
    assert page.ean_published == product["ean"]
    assert page.price == 243.5 and page.availability == "unknown"
    assert _accepted_edel_resolution(page, url, product)


@pytest.mark.parametrize(("product_id", "offer_id", "accepted"), [
    ("111", "222", False),
    ("222", "111", False),
    ("222", "222", True),
])
def test_edel_product_offer_and_selected_radio_must_share_selected_size(product_id, offer_id, accepted):
    base = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    product = _edel_variant_node(product_id, "8056597875349", 200, "InStock")
    product["url"] = base + f"?selectedSize={product_id}"
    product["offers"]["url"] = base + f"?selectedSize={offer_id}"
    html = (
        '<input type="radio" id="prd222" checked>'
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@graph": [product],
        }) + "</script>"
    )

    page = extract_product(html, base)

    assert page is not None and page.ambiguous is (not accepted)
    assert page.variant_id == "222"
    match = classify(
        {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"},
        page.candidate(),
    )
    if accepted:
        assert page.ean_published == "8056597875349"
        assert page.price == 200 and page.availability == "available"
        assert match.status == "exact_match"
    else:
        assert page.ean_published == ""
        assert page.price is None and page.availability == "unknown"
        assert match.status != "exact_match"


@pytest.mark.parametrize(("ean", "size", "availability"), [
    ("805289126577", "50-22", "InStock"),
    ("805289126584", "54-18", "OutOfStock"),
])
def test_shared_amevista_product_group_binds_upc_size_price_and_stock_by_ean(ean, size, availability):
    base = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    variants = [
        {"@type": "Product", "name": f"Ray-Ban RB2140 901 {other_size}", "model": "RB2140",
         "color": "901", "size": other_size, "gtin12": other_ean, "url": base,
         "offers": {"@type": "Offer", "url": base, "availability": f"https://schema.org/{other_stock}"}}
        for other_ean, other_size, other_stock in [
            ("805289126584", "54-18", "OutOfStock"),
            ("805289126577", "50-22", "InStock"),
        ]
    ]
    html = '<script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "ProductGroup", "url": base,
        "name": "Ray-Ban RB2140 901", "brand": {"@type": "Brand", "name": "Ray-Ban"},
        "model": "RB2140", "color": "901",
        "offers": {"@type": "Offer", "price": "99.60", "priceCurrency": "EUR",
                   "availability": "https://schema.org/OutOfStock"},
        "hasVariant": variants,
    }) + "</script>"

    shared = extract_product(html, base)
    target_ean = "0" + ean
    page = select_product_variant(shared, {
        "ean": target_ean, "brand": "Ray-Ban", "model": "RB2140", "color": "901", "caliber": size,
    })

    assert shared is not None and shared.ambiguous and len(shared.structured_variants) == 2
    assert page is not None and not page.ambiguous
    assert (page.ean_published, page.caliber, page.price, page.currency, page.availability) == (
        target_ean, size, 99.6, "EUR", "available" if availability == "InStock" else "unavailable")
    assert classify(
        {"ean": target_ean, "brand": "Ray-Ban", "model": "RB2140", "color": "901", "caliber": size},
        page.candidate(),
    ).status == "exact_match"
    assert any("no por orden de listas" in note for note in page.notes)


def test_shared_product_group_keeps_common_price_but_not_conflicting_variant_stock():
    base = "https://www.amevista.com/es/ray-ban-rb2140-901-wayfarer"
    html = '<script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "ProductGroup", "url": base,
        "hasVariant": [{
            "@type": "Product", "name": "Ray-Ban RB2140 901 50-22", "model": "RB2140",
            "color": "901", "size": "50-22", "gtin12": "805289126577", "url": base,
            "offers": {"@type": "Offer", "price": "99.60", "priceCurrency": "EUR",
                       "availability": "https://schema.org/InStock"},
        }, {
            "@type": "Product", "name": "Ray-Ban RB2140 901 54-18", "model": "RB2140",
            "color": "901", "size": "54-18", "gtin12": "805289126584", "url": base,
            "offers": {"@type": "Offer", "price": "99.60", "priceCurrency": "EUR",
                       "availability": "https://schema.org/OutOfStock"},
        }],
    }) + "</script>"

    page = select_product_variant(extract_product(html, base), {
        "ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140",
        "color": "901", "caliber": "54-18",
    })

    assert page is not None and not page.ambiguous
    assert page.ean_published == "0805289126577" and page.price == 99.6
    assert page.availability == "unknown" and page.stock_conflict
    assert classify({
        "ean": "0805289126577", "brand": "Ray-Ban", "model": "RB2140",
        "color": "901", "caliber": "54-18",
    }, page.candidate()).status == "exact_match"
    assert any("calibre difiere" in note.lower() for note in page.notes)


@pytest.mark.parametrize(("model", "color", "ean", "size", "price"), [
    ("SL 596 DUNE", "006", "889652429717", "58-14", 216.0),
    ("SL 872 ERIN", "001", "889652650838", "52-16", 174.0),
    ("SL 908", "002", "889652675602", "51-20", 126.0),
])
def test_edel_variant_control_binds_indexed_upc_and_availability_without_fixed_ids(
        model, color, ean, size, price):
    variant_id = "987654321"
    url = f"https://www.edel-optics.es/{model.replace(' ', '-')}-{color}-de-Saint-Laurent.html"
    node = {
        "@type": "Product", "url": url + f"?selectedSize={variant_id}",
        "name": f"Saint Laurent {model} {color} {size}",
        "brand": {"@type": "Brand", "name": "Saint Laurent"}, "model": model, "color": color,
        "size": size, "gtin12": ean,
        "offers": {"@type": "Offer", "url": url + f"?selectedSize={variant_id}",
                   "price": str(price), "priceCurrency": "EUR",
                   "availability": "https://schema.org/InStock"},
    }
    html = (
        f'<input type="radio" id="prd{variant_id}" checked>'
        f'<div class="eoDetailListTileSize{variant_id}">EAN {ean}</div>'
        '<script type="application/ld+json">' + json.dumps(node) + "</script>"
    )

    page = extract_product(html, url)
    target = {"ean": "0" + ean, "brand": "Saint Laurent", "model": model,
              "color": color, "caliber": size}

    assert page is not None and not page.ambiguous
    assert page.variant_id == variant_id
    assert page.ean_published == target["ean"]
    assert page.model == model and page.price == price and page.availability == "available"
    assert classify(target, page.candidate()).status == "exact_match"


def test_url_slug_alone_proves_nothing():
    # Misma URL de PR 27ZS pero ficha de otro EAN: no puede ser coincidencia exacta.
    page = load("mia_burton", "https://miaburton.com/es/gafas-de-sol/prada/pr-27zs-16k08z")
    assert classify(PR27, page.candidate()).status == "rejected"


def test_mia_burton_url_is_the_pr26zs_reference():
    page = load("mia_burton", "https://miaburton.com/es/gafas-de-sol/prada/pr-26zs-16k08z")
    assert page.ean_published == "8056597875226"
    assert page.color == ""  # "black" no es un código de color


def test_edel_optics_uses_sale_price_not_recommended_price():
    url = "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html"
    page = load("edel_optics", url)
    assert page.price == 243.5 and page.price != 390.0
    assert page.ambiguous and page.variant_id == ""
    assert page.availability == "unknown"
    assert classify(PR27, page.candidate()).status == PROBABLE


def test_universitaria_without_static_price_is_not_an_offer():
    page = load("optica_universitaria", "https://www.opticauniversitaria.es/gafas-de-sol/prada/prada-pr-27zs-16k08z/312245.html")
    assert page is None or page.price is None
    if page is not None:
        assert page.ean_published == "8056597875349"


def test_several_variant_eans_are_not_guessed():
    html = ('<html lang="es"><body><h1>Prada PR 27ZS</h1><div>EAN 8056597875349</div>'
            '<div>EAN 8056597877329</div><span itemprop="price" content="200"></span></body></html>')
    page = extract_product(html, "https://tienda.example/prada")
    assert page is None or not page.ean_published


@pytest.mark.parametrize("text,lens", [("Buy Prada PR 27ZS C54 16K08Z", "54"), ("PR 27ZS 16K08Z 54", "54")])
def test_lens_only_is_not_a_full_caliber(text, lens):
    assert extract_lens(text) == lens


def test_caliber_from_manufacturer_code():
    assert caliber_from_code("0PR_27ZS16K08Z5421") == "54-21"
    assert caliber_from_code("0PR_27ZS16K08Z54") == ""


def test_comment_closed_with_bang_does_not_swallow_page():
    page = load("edel_optics", "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html")
    assert page.price == 243.5


def test_json_ld_with_raw_newlines_is_parsed():
    page = load("amevista", "https://www.amevista.com/es/prada-0pr-27zs-16k08z")
    assert page.method == "json-ld"
