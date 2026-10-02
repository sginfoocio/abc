from datetime import date, datetime, timezone

import pytest
from cryptography.fernet import Fernet

from kering_images import ConfigStore, PORTAL_URL, utc_interval
from kering_images import ImageStore, VIEWS, execute_run, process_ean
from kering_images import read_orders, InterventionRequired, UnverifiedPortal, BatchService
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import create_engine, text
import json
from zipfile import ZipFile
import socket


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Las pruebas Kering son offline")
    monkeypatch.setattr(socket, "create_connection", reject)
from io import BytesIO
from PIL import Image


def photo(color):
    buffer = BytesIO()
    Image.new("RGB", (600, 600), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakePortal:
    def __init__(self, views=VIEWS):
        self.calls = []
        self.views = views

    def fetch(self, ean, pending):
        self.calls.append((ean, pending))
        return {view: photo((index * 80, 10, 20)) for index, view in enumerate(VIEWS)
                if view in pending and view in self.views}


def order(order_id=1, eans=("0012345678901",)):
    return dict(id=order_id, name=f"PO{order_id}", supplier_id=7, date_order="2026-03-29 12:00:00",
                state="purchase", lines=[dict(id=index, product_id=index + 1, ean=ean)
                                         for index, ean in enumerate(eans)])


def test_full_madrid_day_and_dst():
    assert utc_interval(date(2026, 3, 29), date(2026, 3, 29)) == (
        datetime(2026, 3, 28, 23), datetime(2026, 3, 29, 22)
    )
    with pytest.raises(ValueError):
        utc_interval(date(2026, 2, 2), date(2026, 2, 1))


def test_encrypted_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("KERING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = ConfigStore(tmp_path)
    assert store.load()["password"] == ""
    config = dict(url=PORTAL_URL, username="offline-user", password="offline-secret", supplier_id=7)
    store.save(config)
    assert b"offline-secret" not in (tmp_path / "config.enc").read_bytes()
    assert store.load() == config
    with pytest.raises(ValueError):
        store.save(dict(config, url="https://example.org"))


def test_reuse_and_repair_only_pending(tmp_path):
    store = ImageStore(tmp_path)
    portal = FakePortal()
    ean = "0012345678901"
    assert set(process_ean(store, portal, ean)["views"].values()) == {"Descargada"}
    assert set(process_ean(store, portal, ean)["views"].values()) == {"Reutilizada"}
    assert len(portal.calls) == 1
    store.path(ean, "frontal").unlink()
    store.path(ean, "lateral").write_bytes(b"corrupt")
    process_ean(store, portal, ean)
    assert portal.calls[-1] == (ean, ("frontal", "lateral"))
    assert len(store.valid_views(ean)) == 3


def test_history_reuse_missing_ean_and_changed_lines(tmp_path):
    store = ImageStore(tmp_path)
    portal = FakePortal()
    orders = [order(), order(2, ("0012345678901", ""))]
    run = store.create_run(orders, "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda order_id: orders[order_id - 1], portal)
    history = store.history()
    assert [row["status"] for row in history] == ["Completo", "Parcial"]
    assert len(portal.calls) == 1
    changed = order(1, ("0012345678901", "0000000000002"))
    run = store.create_run([changed], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda _: changed, portal)
    assert portal.calls[-1][0] == "0000000000002"
    history = ImageStore(tmp_path).history()
    assert len(history) == 3
    assert history[0]["attempt"] == 2


def test_odoo_read_only_date_supplier_cancel_and_display_lines():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE purchase_order(id INTEGER, name TEXT, partner_id INTEGER, date_order TEXT, state TEXT)"))
        connection.execute(text("CREATE TABLE purchase_order_line(id INTEGER, order_id INTEGER, product_id INTEGER, display_type TEXT)"))
        connection.execute(text("CREATE TABLE product_product(id INTEGER, barcode TEXT)"))
        for order_id, supplier, when, state in [
            (1, 7, "2026-03-28 23:00:00", "purchase"),
            (2, 7, "2026-03-29 21:59:59", "draft"),
            (3, 7, "2026-03-29 22:00:00", "purchase"),
            (4, 8, "2026-03-29 12:00:00", "purchase"),
            (5, 7, "2026-03-29 12:00:00", "cancel"),
            (6, 7, "2026-03-28 22:59:59", "purchase"),
        ]:
            connection.execute(text("INSERT INTO purchase_order VALUES(:id, :name, :supplier, :when, :state)"),
                               dict(id=order_id, name=f"PO{order_id}", supplier=supplier, when=when, state=state))
        connection.execute(text("INSERT INTO product_product VALUES(1, '0012345678901')"))
        connection.execute(text("INSERT INTO purchase_order_line VALUES(1,1,1,NULL),(2,1,NULL,'line_note'),(3,1,NULL,'line_section'),(4,2,2,NULL)"))
    orders = read_orders(engine, 7, date(2026, 3, 29), date(2026, 3, 29))
    assert [item["id"] for item in orders] == [1, 2]
    assert orders[0]["lines"] == [dict(id=1, product_id=1, ean="0012345678901")]
    assert orders[1]["lines"][0]["ean"] == ""
    assert [item["id"] for item in read_orders(engine, 7, date(2026, 3, 29), date(2026, 3, 29), [2])] == [2]


def test_partial_duplicate_and_html_are_not_complete(tmp_path):
    store = ImageStore(tmp_path)
    partial = FakePortal(("frontal",))
    result = process_ean(store, partial, "0001")
    assert list(result["views"].values()).count("Pendiente") == 2
    class InvalidPortal:
        def fetch(self, ean, pending):
            return {"frontal": photo((0, 10, 20)), "lateral": photo((0, 10, 20)), "perspectiva": b"<html>login</html>"}
    result = process_ean(store, InvalidPortal(), "0002")
    assert len(store.valid_views("0002")) == 1
    assert result["reason"] == "imagen_invalida"


def test_concurrent_ean_no_duplicate_download(tmp_path):
    store = ImageStore(tmp_path)
    entered, release = Event(), Event()
    class SlowPortal(FakePortal):
        def fetch(self, ean, pending):
            entered.set()
            assert release.wait(10)
            return super().fetch(ean, pending)
    portal = SlowPortal()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(process_ean, store, portal, "0001")
        assert entered.wait(10)
        result = process_ean(ImageStore(tmp_path), portal, "0001")
        assert result["reason"] == "ean_en_proceso"
        release.set()
        assert future.result(timeout=10)["reason"] == ""
    assert process_ean(store, portal, "0001")["tries"] == 0
    assert len(portal.calls) == 1


def test_interrupt_retry_and_zip(tmp_path):
    store = ImageStore(tmp_path)
    run = store.create_run([order()], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    store.recover(now=10**12)
    assert store.history()[0]["status"] == "Interrumpido"
    run = store.create_run([order()], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda _: order(), FakePortal())
    assert store.history()[0]["attempt"] == 2
    with ZipFile(BytesIO(store.zip_order(order()))) as archive:
        assert len(archive.namelist()) == 3
        assert all(name.startswith("kering/0012345678901/") for name in archive.namelist())


def test_bounded_retries_and_intervention_no_secrets(tmp_path):
    store = ImageStore(tmp_path)
    class FailingPortal:
        def __init__(self, exception):
            self.count = 0
            self.exception = exception
        def fetch(self, ean, pending):
            self.count += 1
            raise self.exception("secret-must-not-appear")
    failing = FailingPortal(TimeoutError)
    result = process_ean(store, failing, "0001")
    assert failing.count == 3
    assert "secret" not in json.dumps(result)
    intervention = FailingPortal(InterventionRequired)
    assert process_ean(store, intervention, "0001")["reason"] == "intervencion_captcha_o_mfa"
    assert intervention.count == 1
    assert process_ean(store, UnverifiedPortal(), "0001")["reason"] == "portal_no_verificado"


def test_background_batch_keeps_processing_after_product_error(tmp_path):
    service = BatchService(ImageStore(tmp_path))
    class OneBadPortal(FakePortal):
        def fetch(self, ean, pending):
            if ean == "0001":
                raise TimeoutError("secret")
            return super().fetch(ean, pending)
    orders = [order(1, ("0001",)), order(2, ("0002",))]
    service.submit(orders, "offline", 7, date(2026, 3, 29), date(2026, 3, 29), lambda order_id: orders[order_id-1], OneBadPortal())
    service.executor.shutdown(wait=True)
    assert [row["status"] for row in service.store.history()] == ["Error", "Completo"]


def test_admin_settings_ui_password_not_prefilled(tmp_path, monkeypatch):
    import kering_images_ui as ui
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("KERING_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("KERING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ConfigStore(tmp_path).save(dict(url=PORTAL_URL, username="offline", password="secret-hidden", supplier_id=7))
    monkeypatch.setattr(ui, "read_suppliers", lambda _: [{"id": 7, "name": "Offline supplier"}])
    app = AppTest.from_string("import streamlit as st\nfrom kering_images_ui import render_kering_settings\nst.session_state['auth_role']='admin'\nrender_kering_settings(None)")
    app.run()
    assert not app.exception
    assert app.text_input[2].value == ""
    assert "secret-hidden" not in str(app)
    restricted = AppTest.from_string("import streamlit as st\nfrom kering_images_ui import render_kering_settings\nst.session_state['auth_role']='masterdata'\nrender_kering_settings(None)")
    restricted.run()
    assert restricted.error
    assert not restricted.text_input


def test_history_filters_and_local_dates():
    from kering_images_ui import filter_history, local_order_time
    row = dict(number="PO001", status="Parcial", order_date="2026-03-28 23:00:00",
               started=datetime(2026, 3, 29, 10, tzinfo=timezone.utc).timestamp())
    assert local_order_time(row["order_date"]).date() == date(2026, 3, 29)
    dates = (date(2026, 3, 29),) * 4
    assert filter_history([row], "po001", ["Parcial"], dates) == [row]
    assert filter_history([row], "PO002", [], dates) == []
    assert filter_history([row], "", ["Completo"], dates) == []


def test_secret_file_configuration_and_fail_closed(tmp_path, monkeypatch):
    key = tmp_path / "server.key"
    key.write_bytes(Fernet.generate_key())
    monkeypatch.delenv("KERING_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("KERING_ENCRYPTION_KEY_FILE", str(key))
    config = ConfigStore(tmp_path / "data")
    config.save(dict(url=PORTAL_URL, username="offline", password="offline-secret", supplier_id=7))
    assert config.load()["password"] == "offline-secret"
    monkeypatch.setenv("KERING_ENCRYPTION_KEY_FILE", str(tmp_path / "absent.key"))
    with pytest.raises(OSError):
        config.load()


def test_duplicates_missing_ean_and_complete_from_existing_are_audited(tmp_path):
    store = ImageStore(tmp_path)
    portal = FakePortal()
    current = order(1, ("0001", "0001", ""))
    run = store.create_run([current], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda _: current, portal)
    assert len(portal.calls) == 1
    assert store.history()[0]["status"] == "Parcial"
    current = order(1, ("0001", "0001"))
    run = store.create_run([current], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda _: current, UnverifiedPortal())
    latest = store.history()[0]
    assert latest["status"] == "Completo"
    assert latest["attempt"] == 2
    assert set(json.loads(latest["results"])["0001"]["views"].values()) == {"Reutilizada"}


def test_thumbnail_invalid_ean_and_corrupt_history_remain_pending(tmp_path):
    store = ImageStore(tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, "PNG")
    class ThumbnailPortal:
        def fetch(self, ean, pending):
            return {view: buffer.getvalue() for view in pending}
    assert process_ean(store, ThumbnailPortal(), "0001")["reason"] == "imagen_invalida"
    assert not store.valid_views("0001")
    portal = FakePortal()
    assert process_ean(store, portal, "../secret")["reason"] == "ean_invalido"
    assert not portal.calls
    process_ean(store, portal, "0001")
    store.path("0001", "frontal").write_bytes(b"corrupt")
    assert len(ImageStore(tmp_path).valid_views("0001")) == 2


def test_settings_database_failure_does_not_expose_details(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("KERING_DATA_ROOT", str(tmp_path))
    app = AppTest.from_string("import streamlit as st\nfrom kering_images_ui import render_kering_settings\nst.session_state['auth_role']='admin'\ndef failed_engine():\n    raise ValueError('sensitive-connection-value')\nrender_kering_settings(failed_engine)")
    app.run()
    assert not app.exception
    assert app.error
    assert "sensitive-connection-value" not in str(app.error)


def test_file_disappears_between_orders_in_same_batch(tmp_path):
    store = ImageStore(tmp_path)
    portal = FakePortal()
    orders = [order(1, ("0001",)), order(2, ("0001",))]
    def loader(order_id):
        if order_id == 2:
            store.path("0001", "frontal").unlink()
        return orders[order_id - 1]
    run = store.create_run(orders, "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, loader, portal)
    assert portal.calls == [("0001", VIEWS), ("0001", ("frontal",))]
    assert all(row["status"] == "Completo" for row in store.history())


def test_atomic_write_failure_recovers_without_orphan(tmp_path, monkeypatch):
    import kering_images
    store = ImageStore(tmp_path)
    writer = kering_images.atomic_write
    def interrupted(path, content):
        raise OSError("offline interrupted write")
    monkeypatch.setattr(kering_images, "atomic_write", interrupted)
    assert process_ean(store, FakePortal(), "0001")["reason"] == "imagen_invalida"
    assert not store.valid_views("0001")
    monkeypatch.setattr(kering_images, "atomic_write", writer)
    assert process_ean(store, FakePortal(), "0001")["reason"] == ""
    assert len(store.valid_views("0001")) == 3


def test_orders_and_history_ui_are_offline(tmp_path, monkeypatch):
    import kering_images_ui as ui
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("KERING_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("KERING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ConfigStore(tmp_path).save(dict(url=PORTAL_URL, username="offline", password="offline", supplier_id=7))
    store = ImageStore(tmp_path)
    run = store.create_run([order()], "offline", 7, date(2026, 3, 29), date(2026, 3, 29))
    execute_run(store, run, lambda _: order(), FakePortal())
    monkeypatch.setattr(ui, "read_orders", lambda *args, **kwargs: [order()])
    ui.service.clear()
    app = AppTest.from_string("import streamlit as st\nfrom kering_images_ui import render_kering_page\nst.session_state['auth_role']='admin'\nrender_kering_page(None)")
    app.run()
    assert not app.exception
    assert app.title[0].value == "Im\u00e1genes Kering"
    assert app.get("download_button")
    next(button for button in app.button if button.label == "Consultar pedidos").click().run()
    assert not app.exception
    assert any("Seleccionar" in frame.value.columns for frame in app.dataframe)
    ui.service.clear()