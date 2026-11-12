"""Estructuras reales de fichas de competidores (observadas el 2026-10-30), reducidas y sin conexión."""
from pathlib import Path
import json

import pytest

from price_references import PROBABLE, caliber_from_code, classify, extract_lens
from price_web_extract import extract_product, select_product_variant

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


def test_shared_product_group_keeps_ean_size_contradiction_ambiguous():
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

    assert page is not None and page.ambiguous
    assert page.ean_published == "" and page.price is None and page.availability == "unknown"
    assert any("calibre" in note for note in page.notes)


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
