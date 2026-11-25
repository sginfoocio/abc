from dataclasses import replace
from pathlib import Path
import sys
from threading import Event
from unittest.mock import MagicMock, PropertyMock

from cryptography.fernet import Fernet
import pytest

import graph_mail_downloader as mail
import luxoptica_auto_upload as upload
from luxoptica_auto_upload import CONFIRMATION, ViewTypeUnavailable, _choose_all_views
from luxottica_config import LOGIN_HOST, PORTAL_HOST, LuxotticaConfig, LuxotticaSettings, PORTAL_URL
import luxottica_order_scheduler as scheduler
from provider_images import NEEDS_REVIEW, SUBMITTED, ProviderStore, ean_key


EAN = "8056262484005"


def test_confirmation_requires_explicit_portal_acknowledgement():
    assert CONFIRMATION.search("Tu solicitud se ha enviado.")
    for text in ("Success", "Éxito", "Enviar solicitud", "Your request has been sent?"):
        assert not CONFIRMATION.search(text)


def test_cookie_consent_clicks_accept_only_inside_identified_cookie_notice():
    page = MagicMock()
    notice = MagicMock()
    notice.is_visible.return_value = True
    accept = MagicMock()
    accept.is_visible.return_value = True
    notice.locator.return_value.get_by_role.return_value = accept
    page.get_by_text.return_value.first = notice

    upload._accept_cookie_consent(page, TimeoutError)

    notice_text = page.get_by_text.call_args.args[0]
    assert notice_text.search(
        "Utilizamos cookies necesarias y tecnologías similares. También cookies opcionales."
    )
    assert not notice_text.search("Acepto los términos y condiciones.")
    notice.locator.assert_called_once_with("xpath=..")
    accept.click.assert_called_once_with()
    notice.wait_for.assert_called_once_with(state="hidden", timeout=10000)


def test_cookie_consent_does_not_click_without_cookie_notice():
    page = MagicMock()
    notice = MagicMock()
    notice.is_visible.return_value = False
    page.get_by_text.return_value.first = notice

    upload._accept_cookie_consent(page, TimeoutError)

    notice.locator.assert_not_called()


def test_cookie_consent_fails_closed_when_accept_button_is_missing():
    page = MagicMock()
    notice = MagicMock()
    notice.is_visible.return_value = True
    accept = MagicMock()
    accept.is_visible.return_value = False
    notice.locator.return_value.get_by_role.return_value = accept
    page.get_by_text.return_value.first = notice

    with pytest.raises(upload.CookieConsentUnavailable):
        upload._accept_cookie_consent(page, TimeoutError)

    accept.click.assert_not_called()


def test_pending_documents_are_not_dismissed(monkeypatch):
    page = MagicMock()
    sign = page.get_by_role.return_value.first
    sign.is_visible.return_value = True
    legal = MagicMock()
    monkeypatch.setattr(upload, "_legal_notice", legal)

    assert upload._pending_conditions(page)
    sign.click.assert_not_called()
    page.get_by_role.assert_called_once()
    legal.assert_not_called()


def test_missing_views_stops_without_switching_to_manual_entry(tmp_path):
    page = MagicMock()
    views = page.get_by_text.return_value
    views.wait_for.side_effect = TimeoutError
    request = tmp_path / "request.txt"
    request.write_text(EAN, encoding="ascii")

    with pytest.raises(ViewTypeUnavailable):
        _choose_all_views(page, request, TimeoutError)

    page.locator.assert_called_once_with("input[type='file']")
    page.get_by_role.assert_called_once()
    page.get_by_text.assert_called_once()
    views.click.assert_not_called()


def test_missing_views_reports_step_from_upload(tmp_path, monkeypatch):
    import playwright.sync_api

    page = MagicMock()
    type(page).url = PropertyMock(side_effect=[
        f"https://{LOGIN_HOST}", f"https://{LOGIN_HOST}", f"https://{PORTAL_HOST}",
    ])
    page.locator.return_value.count.return_value = 1
    page.get_by_text.return_value.first.count.return_value = 1
    page.get_by_role.return_value.count.return_value = 1
    driver = MagicMock()
    driver.chromium.launch.return_value.new_page.return_value = page
    context = MagicMock()
    context.__enter__.return_value = driver
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: context)
    monkeypatch.setattr(upload.time, "sleep", lambda _: None)
    monkeypatch.setattr(upload, "_pending_conditions", lambda _: False)
    monkeypatch.setattr(upload, "_accept_cookie_consent", lambda *_: None)
    monkeypatch.setattr(upload, "_choose_all_views", MagicMock(side_effect=ViewTypeUnavailable))
    request = tmp_path / "request.txt"
    request.write_text(EAN, encoding="ascii")

    assert upload._upload_to_luxoptica(PORTAL_URL, "user", "secret", request, "images@example.com",
                                       True, False, preview_only=True) == (
        False, "ver_tipo_sin_todas_las_vistas")


def test_preflight_uses_historical_file_unchanged(monkeypatch):
    request = (Path(__file__).resolve().parents[1] / "docs" / "Luxoptica"
               / "upc-products-images-request-20260819_102506-50-eans.txt")
    original = request.read_bytes()
    settings = LuxotticaSettings(PORTAL_URL, "user", "secret", "images@example.com")
    monkeypatch.setattr(scheduler, "LuxotticaConfig", lambda root: MagicMock(load=lambda: settings))
    monkeypatch.setattr(sys, "argv", ["scheduler", "--preflight-file", str(request)])
    submit = MagicMock(return_value=(True, "Vista previa lista sin enviar"))
    monkeypatch.setattr(scheduler, "_upload_to_luxoptica", submit)

    assert scheduler.main() == 0
    assert submit.call_args.args[3] == request
    assert submit.call_args.kwargs["preview_only"] is True
    assert request.read_bytes() == original


def test_credentials_change_persists_encrypted_and_keeps_secret_when_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("LUXOPTICA_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    settings = LuxotticaConfig(tmp_path)
    values = dict(url=PORTAL_URL, username="test-user", email="images@example.com", enabled=False,
                  interval_minutes=30)
    settings.save(**values, password="test-secret")
    assert b"test-secret" not in settings.path.read_bytes()
    settings.save(**values, password="")
    assert settings.load().password == "test-secret"
    with pytest.raises(ValueError, match="nuevo usuario"):
        settings.save(**{**values, "username": "another-user"}, password="")


@pytest.mark.parametrize("confirmed", [True, False])
def test_scheduler_sends_once_and_keeps_ambiguous_requests_for_review(tmp_path, monkeypatch, confirmed):
    root = tmp_path / "requests"
    images = tmp_path / "images"
    settings = LuxotticaSettings(PORTAL_URL, "example", "secret", "images@example.com", True)

    class Config:
        def load(self):
            return settings

    order = {
        "id": 15, "name": "PO15", "date_order": "2026-11-04 10:00:00", "partner_id": 570,
        "supplier": "Luxottica", "provider": "luxottica", "state": "purchase", "line_count": 1,
        "order_identity_conflict": False,
        "lines": [{"id": 11, "product_id": 45, "ean": EAN, "sku": "RB354700153", "product": "Ray-Ban",
                   "quantity": "2", "model": "RB3547", "color": "00153", "caliber": "51",
                   "barcode_conflict": False}],
    }

    def read(*args, **kwargs):
        return [{**order, "lines": list(order["lines"])}], None

    monkeypatch.setattr(scheduler, "read_purchase_page", read)
    monkeypatch.setattr(scheduler, "associations", lambda *args: ([("luxottica", 570, True)], []))
    monkeypatch.setattr(scheduler, "_legacy_luxottica_keys", lambda path: set())
    monkeypatch.setattr(scheduler, "image_evidence", lambda *args: (
        {ean_key(EAN): {"status": scheduler.NONE, "incomplete": False, "issues": []}}, None))
    monkeypatch.setattr(mail, "provider_data_root", lambda: root)
    submissions = []

    def submit(url, username, password, path, email, headless, keep_open, *, before_send):
        assert before_send()
        submissions.append(path.read_bytes())
        if confirmed:
            return True, "Confirmación de Luxottica: Tu solicitud se ha enviado."
        return True, "Proceso completado. Verifica la confirmación en el portal."

    result = scheduler.run_cycle(object(), root, images, Event(), config=Config(), submit=submit)
    assert result == ("enviado_confirmado" if confirmed else "envio_sin_confirmacion")
    request = ProviderStore(root).list_requests("luxottica")[0]
    assert request["state"] == (SUBMITTED if confirmed else NEEDS_REVIEW)
    assert list(mail._confirmed_order_products()) == (
        [{"modelo": "RB3547", "color": "00153", "ean": EAN}] if confirmed else [])
    scheduler.run_cycle(object(), root, images, Event(), config=Config(), submit=submit)
    assert submissions == [f"{EAN}\r\n".encode("ascii")]

    settings = replace(settings, enabled=False)
    assert scheduler.run_cycle(object(), root, images, Event(), config=Config(), submit=submit) == "desactivado"
