"""Script standalone (sin Streamlit) para comprobar pedidos pendientes de clientes
vigilados y enviar una alerta por email. Pensado para ejecutarse via cron/Task Scheduler.

Uso: python run_alerta_pedidos.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from check_pedidos_vigilados import find_matching_orders
from db_config import load_env_file
from graph_mail_downloader import send_alert_email
from order_alerts import (
    OrderAlertStore,
    build_alert_email_html,
    import_legacy_notification_file,
)
from watchlist_config import load_watchlist

NOTIFICADOS_FILE = Path(__file__).resolve().parent / "alerta_pedidos_notificados.json"
ALERT_RECIPIENT_EMAIL_ENV = "ALERT_RECIPIENT_EMAIL"
ALERT_RECIPIENT_EMAIL_DEFAULT = "roberto@diagonaleyewear.com"
ALERT_RECIPIENT_EMAILS_EXTRA = ["virginia.nunez@diagonaleyewear.com"]
DIAS_ATRAS_ENV = "ALERTA_PEDIDOS_DIAS_ATRAS"
DIAS_ATRAS_DEFAULT = 180


def _log(mensaje: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}", flush=True)


def main() -> int:
    load_env_file()

    watchlist = load_watchlist()
    if not watchlist:
        _log("Lista de vigilancia vacía. Nada que comprobar.")
        return 0

    dias_atras = int(os.getenv(DIAS_ATRAS_ENV, DIAS_ATRAS_DEFAULT))
    hoy = date.today()
    fecha_desde = hoy - timedelta(days=dias_atras)
    fecha_hasta_exclusiva = hoy + timedelta(days=1)

    _log(f"Comprobando pedidos pendientes para {len(watchlist)} cliente(s) vigilado(s)...")
    try:
        resultados = find_matching_orders(
            clientes=watchlist,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta_exclusiva,
            solo_pendientes=True,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR al consultar Odoo: {exc}")
        return 1

    if resultados.empty:
        _log("No hay pedidos pendientes de clientes vigilados.")
        return 0

    alert_store = OrderAlertStore()
    import_legacy_notification_file(alert_store, NOTIFICADOS_FILE)
    batch = alert_store.claim_orders(resultados["pedido_id"].tolist())
    nuevos = resultados[resultados["pedido_id"].isin(batch.order_ids)]

    if nuevos.empty:
        _log(
            f"Se encontraron {len(resultados)} pedido(s) pendiente(s), "
            "pero ya fueron notificados anteriormente."
        )
        return 0

    destinatario = os.getenv(ALERT_RECIPIENT_EMAIL_ENV, "").strip() or ALERT_RECIPIENT_EMAIL_DEFAULT
    destinatarios = list(dict.fromkeys([destinatario, *ALERT_RECIPIENT_EMAILS_EXTRA]))

    _log(f"Se encontraron {len(nuevos)} pedido(s) nuevo(s). Enviando alerta a {', '.join(destinatarios)}...")
    try:
        send_alert_email(
            subject=f"Alerta de pedidos vigilados ({len(nuevos)})",
            html_body=build_alert_email_html(nuevos.to_dict(orient="records")),
            to_address=destinatarios,
        )
    except Exception as exc:  # noqa: BLE001
        alert_store.mark_failed(batch.batch_id, str(exc))
        _log(f"ERROR al enviar el email de alerta: {exc}")
        return 1

    alert_store.mark_sent(batch.batch_id, ", ".join(destinatarios))
    _log("Alerta enviada y registro compartido de notificaciones actualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
