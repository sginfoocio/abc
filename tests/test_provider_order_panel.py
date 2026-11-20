from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, Mock
from zipfile import ZipFile

from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from streamlit.testing.v1 import AppTest

from app_pages import provider_order_snapshot as data
from app_pages import provider_order_actions as actions
from app_pages.provider_orders_page import filter_orders
from image_repository import ImageRepository
from kering_images import ImageStore
from provider_images import ProviderStore
from provider_images_ui import build_connector
from provider_odoo_orders import OdooUnavailable, read_purchase_page, save_link


START, END = date(2026, 11, 1), date(2026, 11, 23)
LINKS = [("kering", 1, True), ("luxottica", 3, False)]
UPC, EAN, OTHER = "123456789012", "0123456789012", "123456789029"


@pytest.fixture
def engine():
    db = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with db.begin() as conn:
        for sql in (
            "CREATE TABLE res_partner(id INTEGER, commercial_partner_id INTEGER, name TEXT)",
            "CREATE TABLE purchase_order(id INTEGER, name TEXT, date_order TEXT, state TEXT, partner_id INTEGER)",
            "CREATE TABLE purchase_order_line(id INTEGER, order_id INTEGER, product_id INTEGER, "
            "name TEXT, product_qty INTEGER, display_type TEXT)",
            "CREATE TABLE product_product(id INTEGER, barcode TEXT, default_code TEXT)",
            "INSERT INTO res_partner VALUES (1,1,'Kering'),(2,1,'Sucursal'),(3,3,'Luxottica'),(4,4,'Otro')",
            "INSERT INTO purchase_order VALUES "
            "(25,'PO25','2026-11-22 12:00:00','purchase',2),"
            "(24,'PO24','2026-11-22 12:00:00','cancel',3),"
            "(23,'PO23','2026-11-22 12:00:00','draft',4),"
            "(22,'PO22','2026-11-21 10:00:00','draft',3),"
            "(21,'PO21','2026-10-12 10:00:00','purchase',2)",
            f"INSERT INTO product_product VALUES (40,'{EAN}','M-BLUE'),(41,'{OTHER}','M-RED'),(42,NULL,'M-GREEN')",
            "INSERT INTO purchase_order_line VALUES "
            "(101,25,40,'Azul',2,NULL),(102,25,40,'Azul',1,NULL),(103,25,41,'Rojo',1,NULL),"
            "(104,24,42,'Verde',5,NULL),(105,25,40,'Nota',0,'line_note')",
        ):
            conn.exec_driver_sql(sql)
    yield db
    db.dispose()


def test_order_page_has_stable_cursor_and_keeps_duplicate_lines(engine, monkeypatch):
    monkeypatch.setattr("provider_odoo_orders.ORDER_PAGE_SIZE", 1)
    first, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END)
    assert first[0]["id"] == 25
    assert first[0]["supplier"] == "Sucursal"
    assert first[0]["provider"] == "kering"
    assert [line["ean"] for line in first[0]["lines"]] == [EAN, EAN, OTHER]
    assert [line["quantity"] for line in first[0]["lines"]] == ["2", "1", "1"]
    second, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END, before=cursor)
    assert second[0]["state"] == "cancel"
    assert second[0]["lines"][0]["ean"] == ""
    third, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END, before=cursor)
    assert third[0]["state"] == "draft"
    assert third[0]["line_count"] == 0
    assert cursor is None


def test_association_and_barcode_conflicts_fail_closed(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql(f"INSERT INTO product_product VALUES (43,'{UPC}','CONFLICTING-VARIANT')")
    orders, _ = read_purchase_page(engine, [*LINKS, ("safilo", 2, False)], ("kering",), START, END)
    assert len(orders) == 1
    assert orders[0]["provider"] == ""
    assert [line["barcode_conflict"] for line in orders[0]["lines"]] == [True, True, False]


def test_oversized_order_continues_without_losing_lines(engine, monkeypatch):
    monkeypatch.setattr("provider_odoo_orders.ORDER_LINE_BUDGET", 2)
    first, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END)
    assert first[0]["line_count"] == 3
    assert [line["id"] for line in first[0]["lines"]] == [101, 102]
    assert cursor[2] == 102
    second, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END, before=cursor)
    assert [order["id"] for order in second] == [25, 24]
    assert [line["id"] for line in second[0]["lines"]] == [103]
    assert second[0]["last_line_number"] == 3
    third, cursor = read_purchase_page(engine, LINKS, ("kering", "luxottica"), START, END, before=cursor)
    assert [order["id"] for order in third] == [22]
    assert cursor is None


def test_timeout_redacts_driver_details():
    engine = Mock()
    engine.connect.side_effect = OperationalError("private query", None, TimeoutError("private credential"))
    with pytest.raises(OdooUnavailable, match="^odoo_no_disponible$"):
        read_purchase_page(engine, LINKS, ("kering",), START, END)


def test_postgres_transaction_is_read_only_and_bounded():
    engine, conn = Mock(), Mock()
    engine.dialect.name = "postgresql"
    conn.engine = engine
    engine.connect.return_value = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    conn.execute.return_value.mappings.return_value.all.return_value = []
    assert read_purchase_page(engine, LINKS, ("kering",), START, END) == ([], None)
    first, second, query = conn.execute.call_args_list
    assert str(first.args[0]) == "SET TRANSACTION READ ONLY"
    assert str(second.args[0]) == "SET LOCAL statement_timeout = 20000"
    assert "product_variant_combination" in str(query.args[0])
    assert query.args[1]["take"] == 51 and query.args[1]["line_limit"] == 2001
    conn.begin.return_value.rollback.assert_called_once()


def _image(color):
    content = BytesIO()
    Image.new("RGB", (600, 600), color).save(content, format="PNG")
    return content.getvalue()


def _save(repository, ean, view, color, name=None):
    repository.save(ean, name or f"{view}.png", _image(color), provider="Kering", origin="test", view=view)


def test_catalog_verified_coverage_and_filters(engine, tmp_path, monkeypatch):
    root = tmp_path / "images"
    repo = ImageRepository(root)
    _save(repo, UPC, "frontal", "blue")
    _save(repo, UPC, "frontal", "blue", "duplicate.png")
    _save(repo, UPC, "lateral", "cyan")
    _save(repo, UPC, "perspectiva", "navy")
    _save(repo, OTHER, "frontal", "red")
    _save(repo, OTHER, "lateral", "yellow")
    _save(repo, OTHER, "lifestyle", "purple")
    _save(repo, OTHER, "unknown", "green")
    _save(repo, OTHER, "detalle", "black")
    monkeypatch.setattr(data, "repository_root", lambda: root)
    monkeypatch.setattr(data, "state_root", lambda _: root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    snapshot = data.acquire(lambda: engine, LINKS, ("kering", "luxottica"), START, END,
                            request_root=tmp_path, history_root=tmp_path)
    order, no_barcode, empty = snapshot["orders"]
    assert order["coverage"] == data.PARTIAL
    assert order["counts"] == {data.COMPLETE: 1, data.PARTIAL: 1}
    blue, duplicate_line, red = order["lines"]
    assert blue["image"] is duplicate_line["image"]
    assert blue["image"]["counts"]["frontal"] == 1
    assert red["image"]["counts"]["lifestyle"] == red["image"]["counts"]["detalle"] == 1
    assert red["image"]["counts"]["unknown"] == 1
    assert red["image"]["missing"] == ["perspectiva"]
    assert no_barcode["coverage"] == empty["coverage"] == data.UNKNOWN
    assert no_barcode["lines"][0]["issues"] == ["Sin EAN/UPC"]
    assert snapshot["totals"]["codes"] == 2
    assert snapshot["totals"]["photos"] == 2
    assert snapshot["totals"]["review"] == 1
    assert filter_orders(snapshot["orders"], number="po2", barcode=UPC, provider="kering",
                         coverage=data.PARTIAL, state="purchase", start="2026-11-22") == [order]
    assert filter_orders(snapshot["orders"], errors="Registrados") == []
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_empty_catalog_and_kering_history_do_not_mutate(engine, tmp_path, monkeypatch):
    root, history = tmp_path / "images", tmp_path / "kering"
    ImageRepository(root)
    store = ImageStore(history)
    with store.connect() as conn:
        conn.execute("INSERT INTO runs (id, started, status) VALUES ('run', 1, 'En proceso')")
        conn.execute("""
            INSERT INTO attempts (run_id, order_id, number, supplier, attempt, status, snapshot, results, error)
            VALUES ('run',25,'PO25',1,1,'Error','{"supplier_id":2}','{}','fallo_lectura_o_proceso')
        """)
    monkeypatch.setattr(data, "repository_root", lambda: root)
    monkeypatch.setattr(data, "state_root", lambda _: root)
    snapshot = data.acquire(lambda: engine, LINKS, ("kering", "luxottica"), START, END,
                            request_root=tmp_path, history_root=history)
    order, lux, _ = snapshot["orders"]
    assert order["coverage"] == data.NONE
    assert order["history"][0]["status"] == "Error"
    assert order["has_errors"] and order["state"] == "purchase"
    assert not lux["history"]
    with store.connect() as conn:
        assert conn.execute("SELECT status FROM runs WHERE id='run'").fetchone()[0] == "En proceso"


def test_assisted_history_requires_order_evidence(engine, tmp_path):
    orders, _ = read_purchase_page(engine, [("marcolin", 1, True)], ("marcolin",), START, END)
    order = orders[0]
    order.update(date="2026-11-22", history=[], review_codes=set(), history_limited=False)
    store = ProviderStore(tmp_path)
    with store.connect() as conn:
        conn.executemany("INSERT INTO requests(id, provider, state, created_at, updated_at) VALUES "
                         "(?, 'marcolin', 'failed', 1, 2)", [(1,), (2,), (3,)])
        conn.executemany("INSERT INTO request_odoo_items "
                         "(request_id, ean, sku, supplier, order_name, order_date, status) "
                         "VALUES (?, ?, ?, 'Sucursal', ?, '2026-11-22', 'confirmed')",
                         [(1, UPC, "M-BLUE", "PO25"), (2, UPC, "M-BLUE", "PO99"), (3, UPC, "M-RED", "PO25")])
        conn.executemany("INSERT INTO events(request_id, at, to_state, detail) VALUES (?, 2, 'failed', 'private')",
                         [(1,), (2,), (3,)])
        conn.execute("INSERT INTO review_items(request_id, provider, name, sha256, reason, ean, created_at) "
                     "VALUES (1, 'marcolin', 'test.png', 'test', 'review', ?, 1)", (UPC,))
    data.append_assisted_history(orders, tmp_path)
    assert [entry["source"] for entry in order["history"]] == ["Marcolin · solicitud 1"]
    assert order["history"][0]["error"]
    assert "private" not in repr(order["history"])
    assert order["review_codes"] == {EAN.zfill(14)}


def test_contextual_library_does_not_open_normal_store(tmp_path, monkeypatch):
    from app_pages import image_library_page as library_page
    from app_pages import provider_orders_page as page

    root = tmp_path / "images"
    repo = ImageRepository(root)
    _save(repo, UPC, "frontal", "blue")
    orders = [{"lines": [{"ean": UPC, "barcode_conflict": False}]}]
    evidence, _ = data.image_evidence(orders, repo.database, root)
    photos = evidence[UPC.zfill(14)]["photos"]
    store = Mock(side_effect=AssertionError("Must not initialise the normal library"))
    monkeypatch.setattr(library_page, "service", store)
    show = Mock(wraps=page.st.image)
    monkeypatch.setattr(page.st, "image", show)
    app = AppTest.from_string("""
from app_pages.image_library_page import render_images_page
render_images_page(lambda _: None, lambda: None, lambda: None, return_to_orders=lambda: None)
""")
    app.session_state["auth_role"] = "admin"
    app.session_state[page.CONTEXT] = ((None, None), "PO25", UPC, photos, root)
    app.run()
    assert not app.exception and "solo lectura" in app.title[0].value
    app.selectbox[0].select(0).run()
    assert not app.exception and show.call_count == 1
    assert show.call_args.args[0] == _image("blue")
    assert not store.called


APP = """
import streamlit as st
from app_pages.provider_orders_page import render_order_panel
def unexpected():
    raise AssertionError('No legacy route or engine on render')
render_order_panel(unexpected, unexpected)
"""


def _button(app, label):
    return next(b for b in app.button if b.label == label)


def test_streamlit_explicit_query_and_retained_results(monkeypatch, tmp_path, engine):
    root = tmp_path / "images"
    ImageRepository(root)
    save_link(tmp_path, "safilo", [4], reviewer="Prueba")
    from app_pages import provider_orders_page as page

    monkeypatch.setattr(page, "data_root", lambda: tmp_path)
    monkeypatch.setattr(page, "kering_root", lambda: tmp_path)
    monkeypatch.setattr(data, "repository_root", lambda: root)
    monkeypatch.setattr(data, "state_root", lambda _: root)
    engine_factory = Mock(return_value=engine)
    real_acquire = page.acquire
    acquire = Mock(side_effect=lambda _, *args, **kwargs: real_acquire(engine_factory, *args, **kwargs))
    monkeypatch.setattr(page, "acquire", acquire)
    app = AppTest.from_string(APP)
    app.session_state["auth_role"] = "admin"
    app.run()
    assert not app.exception
    assert "Todavía no" in app.info[0].value
    assert not app.metric and not acquire.called
    app.date_input[0].set_value(START)
    app.date_input[1].set_value(END)
    app.run()
    assert not acquire.called
    _button(app, "Consultar pedidos de Odoo").click().run()
    assert not app.exception
    assert acquire.call_count == engine_factory.call_count == 1
    assert list(app.dataframe[0].value["Pedido"]) == ["PO23"]
    next(widget for widget in app.selectbox if widget.label == "Ver detalle del pedido").select(23).run()
    assert not app.exception
    assert any("Sin historial disponible" in m.value for m in app.info)
    app.text_input[0].set_value("ausente").run()
    assert not app.exception
    assert "Ningún pedido" in app.info[0].value
    acquire.side_effect = OdooUnavailable("detalle reservado")
    _button(app, "Consultar pedidos de Odoo").click().run()
    assert "Se conserva la consulta anterior" in app.error[0].value
    assert "reservado" not in app.error[0].value
    assert engine_factory.call_count == 1
    app.session_state["auth_role"] = "masterdata"
    app.run()
    assert "administradores" in app.error[0].value
    assert not app.metric and acquire.call_count == 2


@pytest.mark.parametrize("provider", ["marcolin", "safilo", "nuorder"])
def test_assisted_operation_plans_once_and_imports_zip(provider, engine, tmp_path, monkeypatch):
    root = tmp_path / "images"
    repository = ImageRepository(root)
    monkeypatch.setattr(actions, "data_root", lambda: tmp_path)
    connector = build_connector(provider, root=tmp_path, repository=repository)
    monkeypatch.setattr(actions, "build_connector", lambda key: connector)
    orders, _ = read_purchase_page(engine, [(provider, 1, True)], (provider,), START, END, order_id=25)
    order = orders[0]
    eligible = {line["id"]: line for line in order["lines"]}
    order["lines"] = [eligible[101]]
    result = actions.prepare_assisted(order, "Prueba")
    rid, = result["created"]
    linked, = actions.linked_requests(order)
    assert linked["id"] == rid and linked["mode"] == "manual" and linked["state"] == "ready"
    assert actions.prepare_assisted(order, "Prueba")["already_live"] == [EAN]
    assert actions.linked_requests({**order, "name": "PO24"}) == []
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("photo.png", _image("blue"))
        archive.writestr("manifest.csv", f"filename,ean,view\nphoto.png,{EAN},frontal\n")
    result = connector.import_zip(rid, package.getvalue(), reviewer="Prueba")
    assert result["imported"] == 1
    with pytest.raises(ValueError, match="ya se importó"):
        connector.import_zip(rid, package.getvalue(), reviewer="Prueba")
    assert actions.prepare_assisted(order, "Prueba")["already_prepared"] == [EAN]
    assert repository.verified_bytes(repository.records(EAN)[0]) == _image("blue")


def test_manual_retry_keeps_order_evidence_and_cannot_repeat(tmp_path):
    connector = build_connector("marcolin", root=tmp_path)
    store, settings = connector.service.store, connector.service.settings
    items = {EAN: {"ean": EAN, "sku": "AZUL", "order": "PO25", "supplier": "Sucursal",
                   "date": "2026-11-22", "status": "confirmed"}}
    rid, = store.plan(settings, [EAN], mode="manual", order_evidence=items, reviewer="Prueba")["created"]
    with store.connect() as db:
        db.execute("UPDATE requests SET state='failed' WHERE id=?", (rid,))
        db.execute("UPDATE request_eans SET live=0 WHERE request_id=?", (rid,))
    new = store.retry(settings, rid, manual_codes={EAN.zfill(14)}, reviewer="Prueba")
    assert store.odoo_items(new) == store.odoo_items(rid)
    with store.connect() as db:
        db.execute("UPDATE request_eans SET live=0 WHERE request_id=?", (new,))
    assert store.retry(settings, rid, manual_codes={EAN.zfill(14)}, reviewer="Prueba") is None


def test_confirmed_action_rereads_odoo_and_stops_if_coverage_is_now_complete(engine, tmp_path, monkeypatch):
    root = tmp_path / "images"
    repo = ImageRepository(root)
    monkeypatch.setattr(actions, "associations", lambda *_: (LINKS, []))
    monkeypatch.setattr(actions, "state_root", lambda _: root)
    order = read_purchase_page(engine, LINKS, ("kering",), START, END)[0][0]
    order["date"] = "2026-11-22"
    selected = {actions.line_identity(order["lines"][0])}
    fresh = actions.revalidate(lambda: engine, order, selected, root)
    assert [line["id"] for line in fresh["lines"]] == [101]
    for view, color in zip(("frontal", "lateral", "perspectiva"), ("blue", "cyan", "navy")):
        _save(repo, UPC, view, color)
    with pytest.raises(ValueError, match="cobertura ha cambiado"):
        actions.revalidate(lambda: engine, order, selected, root)


def test_order_actions_require_confirmation_and_do_not_execute_on_rerender(engine, monkeypatch):
    order = read_purchase_page(engine, LINKS, ("kering",), START, END)[0][0]
    order["provider"] = "marcolin"
    for line in order["lines"]:
        line["image"] = {"status": data.COMPLETE if line["ean"] == OTHER else data.PARTIAL,
                         "missing": ["perspectiva"], "incomplete": False}
    load = Mock(return_value=order)
    prepare = Mock(return_value={"created": [1], "already_live": [], "already_prepared": []})
    monkeypatch.setattr(actions, "linked_requests", lambda _: [])
    monkeypatch.setattr(actions, "revalidate", load)
    monkeypatch.setattr(actions, "prepare_assisted", prepare)
    app = AppTest.from_string("""
import streamlit as st
from app_pages.provider_order_actions import render_order_actions
render_order_actions(st.session_state.order, None, None)
""")
    app.session_state["order"] = order
    app.session_state["auth_role"] = "admin"
    app.session_state["auth_user"] = "Prueba"
    app.run()
    app.checkbox[0].check().run()
    assert app.multiselect[0].options == [EAN]
    app.multiselect[0].select(EAN.zfill(14)).run()
    assert not load.called
    _button(app, "Preparar procesamiento").click().run()
    assert not load.called and app.error
    app.checkbox[1].check()
    _button(app, "Preparar procesamiento").click().run()
    assert not app.exception
    assert prepare.call_count == 1
    assert load.call_args.args[2] == {actions.line_identity(order["lines"][0])}
    app.run()
    assert prepare.call_count == 1
    app.session_state["auth_role"] = "masterdata"
    app.run()
    assert not app.button


def test_kering_uses_existing_batch_with_selected_lines_and_rejects_stale_preview(engine, monkeypatch):
    order = read_purchase_page(engine, LINKS, ("kering",), START, END)[0][0]
    order["date"] = "2026-11-22"
    config = {"test_mode": True, "auto_enabled": False, "test_order_ids": [25],
              "cutoff_date": "2026-10-05", "supplier_id": 2}
    config_store = Mock()
    config_store.load.return_value = config
    monkeypatch.setattr(actions, "ConfigStore", lambda _: config_store)
    selected, *rest = order["lines"]
    revalidate = Mock(return_value={**order, "lines": [selected]})
    monkeypatch.setattr(actions, "revalidate", revalidate)
    portal, batch = object(), Mock()
    monkeypatch.setattr(actions, "create_portal", lambda _: portal)
    monkeypatch.setattr(actions, "kering_service", lambda: batch)
    latest = Mock(return_value=None)
    monkeypatch.setattr(actions, "latest_kering", latest)
    keys = {actions.line_identity(selected)}
    actions.kering_submission(None, order, keys, None, "Prueba", None)
    args, kwargs = batch.submit_for_user.call_args
    assert args[0][0]["lines"] == args[6](25)["lines"] == [selected]
    assert kwargs["portal"] is portal
    kwargs["preflight"](None)
    latest.return_value = {"run_id": "already_started"}
    with pytest.raises(ValueError, match="Otro operador"):
        kwargs["preflight"](None)
    config_store.load.return_value = {**config, "test_order_ids": []}
    with pytest.raises(ValueError, match="no autorizado"):
        actions.kering_submission(None, order, keys, None, "Prueba", None)


def test_luxottica_only_reads_local_receipts(monkeypatch):
    from cloud_dashboard import ProcessStatus

    read = Mock(return_value=ProcessStatus("luxoptica-mail", "Correo", "luxpending"))
    monkeypatch.setattr(actions, "read_journal", read)
    app = AppTest.from_string("""
import streamlit as st
from app_pages.provider_order_actions import render_order_actions
render_order_actions({"id": 25, "provider": "luxottica"}, None, None)
""")
    app.session_state["auth_role"] = "admin"
    app.run()
    assert not read.called
    app.checkbox[0].check().run()
    assert not app.exception and not app.button
    assert [call.args[0] for call in read.call_args_list] == [
        "luxoptica-monitor", "luxoptica-mail", "luxoptica-upload",
    ]
