from dataclasses import replace
from threading import Event

from cryptography.fernet import Fernet
import pytest

import graph_mail_downloader as mail
from luxoptica_auto_upload import CONFIRMATION
from luxottica_config import LuxotticaConfig, LuxotticaSettings, PORTAL_URL
import luxottica_order_scheduler as scheduler
from provider_images import NEEDS_REVIEW, SUBMITTED, ProviderStore, ean_key


EAN = "8056262484005"


def test_confirmation_requires_explicit_portal_acknowledgement():
    assert CONFIRMATION.search("Tu solicitud se ha enviado.")
    for text in ("Success", "Éxito", "Enviar solicitud", "Your request has been sent?"):
        assert not CONFIRMATION.search(text)


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
