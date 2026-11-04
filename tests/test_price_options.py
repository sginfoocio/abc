import pandas as pd
from streamlit.testing.v1 import AppTest

from app_pages.price_options import natural_key, ordered, product_eans
from app_pages.price_watch import ADMIN_SECTIONS
from price_analytics import STATE_OPTIONS
from price_watch import PriceWatch
from tests.test_price_daily import _daily_env


def test_natural_order_ignores_case_accents_and_sorts_numbers():
    values = ["Óptica 10", "zócalo", "optica 2", "Ámevista", "EDEL-OPTICS"]
    assert ordered(values) == ["Ámevista", "EDEL-OPTICS", "optica 2", "Óptica 10", "zócalo"]
    assert natural_key("ÓPTICA 2") == natural_key("optica 2")
    assert ordered(["Colección 10", "coleccion 2"]) == ["coleccion 2", "Colección 10"]
    assert ordered(["Grupo 10", "grúpo 2"]) == ["grúpo 2", "Grupo 10"]
    assert ordered(["Categoría 10", "categoria 2"]) == ["categoria 2", "Categoría 10"]


def test_equal_labels_use_stable_identifiers_not_input_order():
    names = {"id10": "Óptica", "id2": "optica", "id1": "ÓPTICA"}
    expected = ["id1", "id2", "id10"]
    assert ordered(names, names.get) == expected
    assert ordered(reversed(names), names.get) == expected
    assert set(ordered(names, names.get)) == set(names)


def test_product_order_uses_brand_model_color_then_ean_without_modifying_frame():
    frame = pd.DataFrame([
        {"ean": "002", "brand": "Óptica", "model": "M10", "color": "001"},
        {"ean": "009", "brand": "optica", "model": "M2", "color": "10"},
        {"ean": "008", "brand": "ÓPTICA", "model": "M2", "color": "02"},
        {"ean": "007", "brand": "optica", "model": "M2", "color": "2"},
        {"ean": "010", "brand": "Álpha", "model": "M100", "color": "001"},
    ])
    before = frame.copy(deep=True)
    assert product_eans(frame) == ["010", "007", "008", "009", "002"]
    assert product_eans(frame.iloc[::-1]) == product_eans(frame)
    assert product_eans(frame, ["002", "008", "002"]) == ["008", "002"]
    pd.testing.assert_frame_equal(frame, before)


def test_existing_widget_selections_survive_switch_to_sorted_options():
    app = AppTest.from_string("""
import streamlit as st
from app_pages.price_options import ordered
names = {"z": "Zeta", "a": "Álpha", "b": "Beta"}
ids = ordered(names, names.get) if st.toggle("Ordenar", key="sort") else list(names)
st.selectbox("Competidor", ids, format_func=names.get, key="competitor")
st.multiselect("Competidores", ids, format_func=names.get, key="competitors")
""").run()
    app.selectbox(key="competitor").set_value("b").run()
    app.multiselect(key="competitors").set_value(["z", "b"]).run()
    app.toggle(key="sort").set_value(True).run()
    assert not app.exception
    assert app.selectbox(key="competitor").options == ["Álpha", "Beta", "Zeta"]
    assert app.selectbox(key="competitor").value == "b"
    assert app.multiselect(key="competitors").value == ["z", "b"]


def test_admin_batch_order_and_selection_are_offline_and_do_not_change_settings(tmp_path, monkeypatch):
    store, _, _, _, _, _, ids, shops, _, rows, events = _daily_env(tmp_path, 3)
    with store._connect() as con, con:
        for cid, name in zip(ids, ["Óptica 10", "optica 2", "Ámevista", "Zeta", "beta", "Edel"]):
            con.execute("UPDATE competitors SET name=? WHERE id=?", (name, cid))
        for (ean, *_), brand, model in zip(rows, ["Zeta", "Álpha", "alpha"], ["M1", "M10", "M2"]):
            con.execute("UPDATE products SET brand=?,model=? WHERE ean=?", (brand, model, ean))
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No real HTTP")))
    with store._connect() as con:
        before = list(con.iterdump())
    app = AppTest.from_string("""
from price_comparator import PriceComparatorStore
from app_pages.price_watch import _render_batch
store = PriceComparatorStore()
frame = store.competitors()
names = {r["id"]: r["name"] for r in frame[frame["active"].eq(1)].to_dict("records")}
_render_batch(store, names)
""").run(timeout=30)
    expected = product_eans(PriceWatch(store).watchlist())
    assert app.multiselect(key="batch_eans").options == [
        next(label for label in app.multiselect(key="batch_eans").options if ean in label) for ean in expected]
    assert app.multiselect(key="batch_competitors").options == [
        "Ámevista", "beta", "Edel", "optica 2", "Óptica 10", "Zeta"]
    selected = expected[:2]
    app.multiselect(key="batch_eans").set_value(selected).run()
    app.multiselect(key="batch_competitors").set_value(ids[:2]).run()
    app.run()
    assert not app.exception
    assert app.multiselect(key="batch_eans").value == selected
    assert app.multiselect(key="batch_competitors").value == ids[:2]
    assert not events and not any(shop.calls for shop in shops.values())
    with store._connect() as con:
        assert list(con.iterdump()) == before


def test_process_options_keep_their_functional_order():
    assert ADMIN_SECTIONS == ("Panel general", "Productos en vigilancia", "Competidores",
                              "Actualizaciones", "Alertas e incidencias", "Configuración y diagnóstico",
                              "Fichas confirmadas")
    assert STATE_OPTIONS[0] == "Todos"
    assert STATE_OPTIONS[-1] == "Sin comparación"
