import hashlib
import json

import pytest
from sqlalchemy import create_engine, text
from streamlit.testing.v1 import AppTest

import bulk_image_search as bulk
from image_repository import ImageRepository
from provider_images import parse_ean_file, parse_ean_text
from provider_support import EANS, photo

UPC = "036000291452"
EAN = "0036000291452"


@pytest.fixture
def sources(tmp_path):
    root, state = tmp_path / "images", tmp_path / "state"
    repo = ImageRepository(root, state=state, backups=tmp_path / "backups")
    for i, view in enumerate(("frontal", "lateral", "detalle", "unknown")):
        repo.save(UPC, f"{view}.png", photo(i + 1), provider="kering", origin="fixture", view=view)
    for i, view in enumerate(("frontal", "perspectiva", "lateral")):
        repo.save(EANS[0], f"{view}.png", photo(i + 10), provider="fixture", origin="fixture", view=view)
    engine = create_engine(f"sqlite:///{(tmp_path / 'odoo.db').as_posix()}")
    with engine.begin() as c:
        for ddl in (
            "CREATE TABLE product_product(id INTEGER PRIMARY KEY, barcode TEXT, default_code TEXT, product_tmpl_id INTEGER)",
            "CREATE TABLE product_template(id INTEGER PRIMARY KEY, name TEXT, brand_id INTEGER)",
            "CREATE TABLE diagonal_product_brand(id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE product_supplierinfo(id INTEGER PRIMARY KEY, product_tmpl_id INTEGER, partner_id INTEGER)",
            "CREATE TABLE res_partner(id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE purchase_order(id INTEGER PRIMARY KEY, partner_id INTEGER, state TEXT)",
            "CREATE TABLE purchase_order_line(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, display_type TEXT)",
        ):
            c.execute(text(ddl))
        c.execute(text("INSERT INTO diagonal_product_brand VALUES (1, 'Marca simulada')"))
        c.execute(text("INSERT INTO product_template VALUES (1, 'Producto sin stock', 1), (2, 'Otro', 1)"))
        c.execute(text("INSERT INTO product_product VALUES (1, :ean, 'REF', 1), (2, :other, 'OTRA', 2)"),
                  {"ean": EAN, "other": EANS[0]})
        c.execute(text("INSERT INTO res_partner VALUES (10, 'Proveedor simulado'), (11, 'Otro proveedor')"))
        c.execute(text("INSERT INTO product_supplierinfo VALUES (1, 1, 10)"))
        c.execute(text("INSERT INTO purchase_order VALUES (1, 10, 'purchase')"))
        c.execute(text("INSERT INTO purchase_order_line VALUES (1, 1, 1, NULL)"))
    links = tmp_path / "links"
    links.mkdir()
    (links / "provider_odoo_links.json").write_text(json.dumps(
        {"marcolin": {"confirmed": True, "partner_ids": [10]}}), encoding="utf-8")
    return repo, state / ".catalog.sqlite3", engine, links


def snapshot(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def analyze(sources, values):
    repo, catalog, engine, links = sources
    return bulk.analyze(values, catalog=catalog, root=repo.root, engine_factory=lambda: engine, links_root=links)


def test_inputs_keep_zeros_equivalences_and_invalid_duplicates():
    values = parse_ean_text(f"{UPC}, {EAN}; {UPC}\n123")
    values += parse_ean_file(f"ean;otro\n{EAN};x\n".encode())
    parsed = bulk.group_inputs(values)
    assert parsed["total"] == 5
    assert parsed["groups"] == {UPC.zfill(14): [UPC, EAN, UPC, EAN]}
    assert parsed["duplicates"] == 3 and parsed["invalid"] == ["123"]
    with pytest.raises(ValueError):
        bulk.group_inputs([UPC] * (bulk.MAX_INPUTS + 1))


def test_read_only_identity_coverage_and_no_new_copies(sources, tmp_path):
    before = snapshot(tmp_path)
    result = analyze(sources, [UPC, EAN, EANS[0], EANS[1]])
    assert snapshot(tmp_path) == before
    partial, complete, absent = result["rows"]
    assert partial["equivalents"] == [UPC, EAN] and len(partial["records"]) == 4
    assert partial["coverage"]["label"] == "parciales"
    assert partial["coverage"]["missing_views"] == ["perspectiva"]
    assert partial["coverage"]["basis"] == "archivos_verificados"
    assert partial["product"]["product"] == "Producto sin stock"
    assert partial["proposal"]["status"] == "Portal candidato"
    assert complete["coverage"]["label"] == "completas"
    assert absent["coverage"]["label"] == "sin_registros"
    assert absent["proposal"]["status"] == "Sin correspondencia"
    assert len(bulk.photo_records(partial["records"], sources[0].root)) == 4


def test_missing_invalid_and_duplicate_images_do_not_supply_views(sources):
    repo, catalog, _, _ = sources
    repo.save(EANS[2], "detail.png", photo(40), provider="x", origin="x", view="detalle")
    repo.save(EANS[2], "unknown.png", photo(41), provider="x", origin="x", view="unknown")
    same = photo(42)
    first, _ = repo.save(EANS[3], "front.png", same, provider="x", origin="x", view="frontal")
    repo.save(EANS[3], "side.png", same, provider="x", origin="x", view="lateral")
    repo.save(EANS[4], "bad.png", b"no image", provider="x", origin="x", view="frontal", allow_invalid=True)
    result = analyze(sources, [EANS[2], EANS[3], EANS[4]])["rows"]
    assert result[0]["coverage"]["label"] == "sin_vistas_validas"
    assert result[1]["coverage"]["label"] == "parciales"
    assert any(i["issue"] == "huella_duplicada" for i in result[1]["coverage"]["issues"])
    assert result[2]["coverage"]["label"] == "sin_vistas_validas"
    first.path.unlink()
    assert analyze(sources, [EANS[3]])["rows"][0]["coverage"]["label"] == "sin_vistas_validas"
    assert bulk.read_catalog([UPC], catalog, repo.root / "missing")[0][UPC.zfill(14)]["coverage"]["label"] == "desconocida"


def test_unavailable_odoo_keeps_local_photos_and_unavailable_catalog_is_unknown(sources, tmp_path):
    repo, catalog, _, links = sources
    def unavailable():
        raise ValueError("secret must not appear")
    result = bulk.analyze([UPC], catalog=catalog, root=repo.root, engine_factory=unavailable, links_root=links)
    row = result["rows"][0]
    assert row["proposal"]["status"] == "Odoo no disponible"
    assert len(row["records"]) == 4 and len(bulk.verified_photo(bulk.photo_records(row["records"], repo.root)[0])) > 0
    assert "secret" not in repr(result)
    bad = bulk.read_catalog([UPC], tmp_path / "missing.sqlite", repo.root)[0]
    assert bad[UPC.zfill(14)]["coverage"]["label"] == "desconocida"


def test_routes_and_ambiguous_variants(sources):
    repo, _, engine, _ = sources
    with engine.begin() as c:
        c.execute(text("INSERT INTO product_product VALUES (3, :ean, 'AMBIGUA', 2)"), {"ean": UPC})
    row = analyze(sources, [UPC])["rows"][0]
    assert row["proposal"]["status"] == "Revisión" and row["product"] is None
    product = {"product_id": 1, "template_id": 1}
    supplier = {1: [{"partner_id": 10, "partner": "Proveedor simulado"}]}
    links = {"marcolin": {"confirmed": True, "partner_ids": [10]}}
    confirmed = [{"product_id": 1, "partner_id": 10, "portal": "marcolin",
                  "confirmed": True, "evidence": "Revisión explícita de variante"}]
    assert bulk.route([product], supplier, {}, links, confirmed)["status"] == "Ruta confirmada"
    assert bulk.route([product], supplier, {}, links)["status"] == "Portal candidato"
    assert bulk.route([product], {}, supplier, links)["status"] == "Revisión"
    scoped = {"marcolin": {"confirmed": True, "partner_ids": [10], "brands": ["Otra marca"]}}
    assert bulk.route([product], supplier, {}, scoped)["status"] == "Revisión"
    assert bulk.route([product], supplier, {}, {})["status"] == "Sin correspondencia"
    other = {1: [{"partner_id": 11, "partner": "Otro proveedor"}]}
    assert bulk.route([product], supplier, other, links)["status"] == "Revisión"
    assert repo.root.is_dir()


def test_confirmed_route_requires_explicit_variant_evidence(sources):
    repo, catalog, engine, links = sources
    result = bulk.analyze(
        [UPC], catalog=catalog, root=repo.root, engine_factory=lambda: engine, links_root=links,
        explicit_routes=[{"product_id": 1, "partner_id": 10, "portal": "marcolin", "confirmed": True,
                          "evidence": "Revisión explícita de variante"}],
    )
    assert result["rows"][0]["proposal"]["status"] == "Ruta confirmada"


def test_search_render_does_not_initialise_store_or_query_until_click(monkeypatch, sources, tmp_path):
    repo, catalog, engine, links = sources
    monkeypatch.setenv("IMAGE_REPOSITORY_ROOT", str(repo.root))
    monkeypatch.setenv("IMAGE_REPOSITORY_STATE_ROOT", str(catalog.parent))
    monkeypatch.setenv("PROVIDER_DATA_ROOT", str(links))
    monkeypatch.setattr("app_pages.image_library_page.service", lambda: pytest.fail("store inicializado"))
    monkeypatch.setattr("image_repository.ImageRepository.__init__", lambda *a, **k: pytest.fail("repositorio inicializado"))
    calls = []
    def factory():
        calls.append(True)
        return engine
    monkeypatch.setattr("bulk_image_search.read_odoo", lambda engine, codes: ({}, {}, {}))
    script = """
import streamlit as st
from app_pages.bulk_image_page import render_page
from tests_bulk_helpers import factory
render_page(lambda _: None, lambda: None, lambda: None, factory)
"""
    import sys
    import types
    module = types.ModuleType("tests_bulk_helpers")
    module.factory = factory
    monkeypatch.setitem(sys.modules, "tests_bulk_helpers", module)
    before = snapshot(tmp_path)
    app = AppTest.from_string(script).run()
    assert not app.exception and not calls
    app.text_area[0].set_value(UPC)
    next(b for b in app.button if b.label == "Analizar listado").click().run()
    assert not app.exception and len(calls) == 1
    assert app.dataframe and app.dataframe[0].value.loc[0, "Cobertura"] == "Parciales"
    app.run()
    assert not app.exception and len(calls) == 1
    assert snapshot(tmp_path) == before
