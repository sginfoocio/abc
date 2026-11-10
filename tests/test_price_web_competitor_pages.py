"""Estructuras reales de fichas de competidores (observadas el 2026-10-30), reducidas y sin conexión."""
from pathlib import Path
import json

import pytest

from price_references import PROBABLE, caliber_from_code, classify, extract_lens
from price_web_extract import extract_product

FIXTURES = Path(__file__).parent / "fixtures" / "web_pages"
PR27 = {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z", "caliber": "54-21"}
PR26 = {"ean": "8056597875226", "brand": "Prada", "model": "PR 26ZS", "color": "16K08Z", "caliber": "55-16"}

# nombre, URL, producto buscado, precio, IVA incluido, mercado, calibre extraído
CASES = [
    ("optica_optima", "https://optica-optima.com/gafas-de-sol/prada-pr-27zs-16k08z.html", PR27, 273.0, True, "", "54-21"),
    ("amevista", "https://www.amevista.com/es/prada-0pr-27zs-16k08z", PR27, 273.0, None, "", None),
    ("edel_optics", "https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958", PR27, 243.5, None, "", None),
    ("otticait", "https://www.otticait.com/en/prada-pr-27zs-16k08z", PR27, 241.8, None, "", None),
    ("mia_burton", "https://miaburton.com/es/gafas-de-sol/prada/pr-26zs-16k08z", PR26, 247.0, None, "ES", None),
    ("congafasdesol", "https://www.congafasdesol.com/prada-pr-27zs-16k08z.html", PR27, 253.5, None, "", None),
    ("blickers", "https://www.blickers.com/en-es/glasses/prada-pr-27zs-c54/5649966192263168/", PR27, 273.0, None, "", "54"),
    ("domanci", "https://domanci.com/es/gafas-de-sol/prada-pr-27zs-16k08z-54.html", PR27, 240.5, True, "", "54-21"),
]


def load(name, url):
    return extract_product((FIXTURES / f"{name}.html").read_text(encoding="utf-8"), url)


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
