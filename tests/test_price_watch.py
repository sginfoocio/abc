import time

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from price_watch import DEFAULT_GROUP, PriceWatch, eans_from_frame, parse_eans
from tests.test_price_comparator import _store

EAN = "0036000291452"


def _gtin(body: str) -> str:
    check = (10 - sum(int(digit) * (3 if (len(body) - i) % 2 else 1) for i, digit in enumerate(body)) % 10) % 10
    return body + str(check)


def _catalog(store, rows):
    report = store.import_products(pd.DataFrame(rows))
    assert not report.rejected, report.rejected


@pytest.fixture()
def watch(tmp_path):
    store = _store(tmp_path)
    _catalog(store, [
        {"MARCA": "Prada", "NOMBRE": "Prada Symbole", "MODELO": "PR 17WS", "COLOR": "1AB5S0", "CATEGORIA": "Sol",
         "EAN": _gtin("805659787534")},
        {"MARCA": "prada", "NOMBRE": "Prada Linea Rossa", "MODELO": "PS 04XS", "COLOR": "DG05S0",
         "CATEGORIA": "Sol", "EAN": _gtin("805659787732")},
        {"MARCA": "Tom Ford", "NOMBRE": "Bronson", "MODELO": "FT1044", "COLOR": "93E", "CATEGORIA": "Sol",
         "EAN": _gtin("088965263707")},
        {"MARCA": "Tom Ford", "NOMBRE": "Sin color", "MODELO": "FT5000", "COLOR": "", "CATEGORIA": "Vista",
         "EAN": _gtin("088965254357")},
    ])
    return store, PriceWatch(store)


def test_individual_selection_validates_identity_and_catalog(watch):
    store, service = watch
    no_color = _gtin("088965254357")
    report = service.add([EAN, "036000291452", "123", _gtin("400000000000"), no_color], actor="ana")
    assert report.added == [EAN] and report.already == [EAN]  # UPC and EAN-13 forms are the same product
    reasons = dict(report.rejected)
    assert "GTIN" in reasons["123"]
    assert reasons[_gtin("400000000000")] == "No está en el catálogo del comparador"
    assert reasons[no_color] == "Identidad incompleta: falta color"
    row = service.watchlist().iloc[0]
    assert (row["source"], row["group_name"], row["added_by"]) == ("manual", DEFAULT_GROUP, "ana")
    with pytest.raises(ValueError):
        service.add([EAN], actor=" ")
    with pytest.raises(ValueError):
        service.add([EAN], actor="ana", group="No existe")


def test_removal_keeps_history_and_readding_reactivates(watch):
    store, service = watch
    service.add([EAN], actor="ana")
    assert service.remove([EAN], actor="luis") == 1
    assert service.watchlist().empty
    history = service.watchlist(include_inactive=True).iloc[0]
    assert (history["active"], history["removed_by"]) == (0, "luis")
    report = service.add([EAN], actor="ana", source="import")
    assert report.reactivated == [EAN]
    assert service.watchlist().iloc[0]["source"] == "import"


def test_bulk_import_parses_text_and_files():
    eans, rejected = parse_eans(f"{EAN}\n036000291452, 123;{EAN}\t\n")
    assert eans == [EAN] and rejected == [("123", "EAN/UPC debe ser GTIN-12 o GTIN-13 válido")]
    frame = pd.DataFrame({"ean": [EAN, "036000291452.0", None]})
    assert eans_from_frame(frame) == ([EAN], [])
    with pytest.raises(ValueError):
        eans_from_frame(pd.DataFrame({"codigo": [EAN]}))


def test_rules_preview_apply_explicitly_and_never_remove(watch):
    store, service = watch
    with pytest.raises(ValueError):
        service.save_rule("Todo", actor="ana")
    assert len(service.preview_rule(brand="PRADA")) == 2  # case-insensitive
    assert len(service.preview_rule(brand="Prada", model_prefix="ps")) == 1
    assert len(service.preview_rule(text_contains="bronson")) == 1
    service.save_group("Sol premium", 12)
    rule = service.save_rule("Prada", actor="ana", brand="Prada", group="Sol premium")
    assert service.watchlist().empty  # saving a rule adds nothing by itself
    reports = service.apply_rules(actor="ana")
    assert len(reports["Prada"].added) == 2
    watched = service.watchlist()
    assert set(watched["source"]) == {"rule"} and set(watched["group_name"]) == {"Sol premium"}
    assert set(watched["rule_name"]) == {"Prada"}
    service.save_rule("Prada", actor="ana", brand="Tom Ford", group="Sol premium")
    report = service.apply_rules(actor="ana", rule_ids=[rule])["Prada"]
    assert len(report.added) == 1 and len(service.watchlist()) == 3  # previous matches are kept
    assert service.rules().iloc[0]["last_matched"] == 2  # Tom Ford with colour + Tom Ford without colour
    assert len(report.rejected) == 1


def test_monitoring_frequency_by_group_then_competitor(watch):
    store, service = watch
    cid = store.competitors()["id"].iloc[0]
    service.add([EAN], actor="ana")
    assert service.monitor_interval_hours(EAN, cid) == 24
    with store._connect() as connection, connection:
        connection.execute("INSERT OR REPLACE INTO competitor_schedule(competitor_id,interval_hours) VALUES(?,48)",
                           (cid,))
    assert service.monitor_interval_hours(EAN, cid) == 48
    service.save_group("Rápido", 6)
    service.move([EAN], "Rápido")
    assert service.monitor_interval_hours(EAN, cid) == 6
    with pytest.raises(ValueError):
        service.save_group("Mal", 0.5)
    assert service.monitor_interval_hours(_gtin("805659787534"), cid) is None


def test_five_hundred_new_eans_need_no_urls_and_are_fast(tmp_path):
    store = _store(tmp_path)
    rows = [{"MARCA": "Marca", "NOMBRE": f"N{i}", "MODELO": f"M{i:03d}", "COLOR": "01", "CATEGORIA": "Sol",
             "EAN": _gtin(f"84{i:010d}")} for i in range(500)]
    _catalog(store, rows)
    service = PriceWatch(store)
    started = time.perf_counter()
    eans, rejected = parse_eans("\n".join(row["EAN"] for row in rows))
    report = service.add(eans, actor="ana", source="import")
    assert len(report.added) == 500 and not rejected and not report.rejected
    assert time.perf_counter() - started < 10
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM competitor_product_urls").fetchone()[0] == 0
    assert service.counts()["import"] == 500


def test_watch_tables_do_not_touch_the_pilot(watch):
    store, service = watch
    service.add([EAN], actor="ana")
    with store._connect() as connection:
        for table in ("price_pilot", "pilot_runs", "pilot_jobs", "scheduler_jobs"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_watch_admin_ui_adds_products_and_shows_authorizations(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    app.selectbox(key="price_admin_section").set_value("Vigilancia automática").run(timeout=30)
    assert not app.exception
    app.selectbox(key="price_watch_section").set_value("Añadir productos").run(timeout=30)
    app.text_area(key="watch_bulk_text").set_value(f"{EAN}\n123").run(timeout=30)
    app.button(key="watch_add_bulk").click().run(timeout=30)
    assert not app.exception
    assert PriceWatch(store).counts()["import"] == 1
    assert any("1 añadidos" in str(item.value) for item in app.warning)
    for section in ("Productos vigilados", "Reglas de vigilancia", "Grupos y frecuencia", "Localización automática",
                    "Autorizaciones por método", "Presupuestos", "Bloqueos e incidencias"):
        app.selectbox(key="price_watch_section").set_value(section).run(timeout=30)
        assert not app.exception, section
    app.selectbox(key="price_watch_section").set_value("Autorizaciones por método").run(timeout=30)
    matrix = next(frame.value for frame in app.dataframe if "Competidor" in frame.value.columns)
    matrix = matrix.set_index("Competidor")
    assert "Prohibido" in matrix.loc["Domanci"].tolist()
    assert "Autorizado" not in matrix.values.ravel().tolist()


def test_locator_ui_counts_queue_and_runs_without_requests_when_nothing_is_authorised(tmp_path, monkeypatch):
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("La pantalla no debe hacer peticiones HTTP")

    monkeypatch.setattr(requests, "get", forbidden)
    store = _store(tmp_path)
    store.seed_defaults()
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    report = PriceWatch(store).add([EAN], actor="ana")
    total = len(store.competitors())
    assert report.queued == total and f"{total} búsquedas en cola" in report.summary
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    app.selectbox(key="price_admin_section").set_value("Vigilancia automática").run(timeout=30)
    app.selectbox(key="price_watch_section").set_value("Localización automática").run(timeout=30)
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["Pendientes"] == str(total)
    app.button(key="locate_enqueue").click().run(timeout=30)
    assert any("0 búsquedas nuevas" in str(item.value) for item in app.success)
    app.button(key="locate_run").click().run(timeout=30)
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["Sin acceso / bloqueadas"] == str(total)
    from price_locator import PriceLocator
    assert set(PriceLocator(store).jobs()["state"]) == {"unavailable"}


def test_locator_ui_sources_and_schedule_without_requests(tmp_path, monkeypatch):
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("La pantalla no debe hacer peticiones HTTP")

    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.delenv("PRICE_LOCATOR_SCHEDULE_ENABLED", raising=False)
    store = _store(tmp_path)
    store.seed_defaults()
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    app.selectbox(key="price_admin_section").set_value("Vigilancia automática").run(timeout=30)
    app.selectbox(key="price_watch_section").set_value("Localización automática").run(timeout=30)
    assert not app.exception
    assert any("PRICE_LOCATOR_SCHEDULE_ENABLED" in str(item.value) for item in app.warning)
    domanci = store.competitors().set_index("name").loc["Domanci", "id"]
    app.selectbox(key="locate_source_competitor").set_value(domanci)
    app.selectbox(key="locate_source_kind").set_value("category")
    app.text_input(key="locate_source_url").set_value("https://domanci.com/es/gafas-de-sol")
    app.text_input(key="locate_source_follow").set_value(r"^/es/marca/[a-z-]+$")
    app.number_input(key="locate_source_depth").set_value(1)
    app.selectbox(key="locate_source_adapter").set_value("itemlist")
    app.button[[b.label for b in app.button].index("Guardar fuente")].click().run(timeout=30)
    assert not app.exception and any("Fuente guardada" in str(item.value) for item in app.success)
    app.selectbox(key="locate_schedule_competitor").set_value(domanci)
    app.number_input(key="locate_schedule_interval").set_value(48)
    app.checkbox(key="locate_schedule_enabled").check()
    app.button[[b.label for b in app.button].index("Guardar planificación")].click().run(timeout=30)
    assert not app.exception and any("Planificación guardada" in str(item.value) for item in app.success)
    from price_locator import PriceLocator
    locator = PriceLocator(store)
    source = locator.sources().set_index("url_template").loc["https://domanci.com/es/gafas-de-sol"]
    assert (source["kind"], source["max_depth"], source["adapter"]) == ("category", 1, "itemlist")
    plan = locator.schedule().set_index("competitor_id").loc[domanci]
    assert (plan["enabled"], plan["interval_hours"]) == (1, 48)
    assert locator.tick()["runs"] == []  # server switch off: nothing runs
