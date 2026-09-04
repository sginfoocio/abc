"""Script standalone (sin Streamlit) para comprobar pedidos pendientes de clientes
vigilados y enviar una alerta por email. Pensado para ejecutarse via cron/Task Scheduler.

Uso: python run_alerta_pedidos.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from check_pedidos_vigilados import find_matching_orders
from db_config import load_env_file
from graph_mail_downloader import send_alert_email
from watchlist_config import load_watchlist

NOTIFICADOS_FILE = Path(__file__).resolve().parent / "alerta_pedidos_notificados.json"
ALERT_RECIPIENT_EMAIL_ENV = "ALERT_RECIPIENT_EMAIL"
ALERT_RECIPIENT_EMAIL_DEFAULT = "roberto@diagonaleyewear.com"
ALERT_RECIPIENT_EMAILS_EXTRA = ["virginia.nunez@diagonaleyewear.com"]
DIAS_ATRAS_ENV = "ALERTA_PEDIDOS_DIAS_ATRAS"
DIAS_ATRAS_DEFAULT = 180


def _log(mensaje: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}", flush=True)


def _load_notificados() -> set[int]:
    if not NOTIFICADOS_FILE.exists():
        return set()
    data = json.loads(NOTIFICADOS_FILE.read_text(encoding="utf-8") or "[]")
    return set(data)


def _save_notificados(pedido_ids: set[int]) -> None:
    NOTIFICADOS_FILE.write_text(
        json.dumps(sorted(pedido_ids), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_alert_email_html(resultados: pd.DataFrame) -> str:
    filas = "".join(
        f"<tr><td>{row.pedido}</td><td>{row.cliente}</td><td>{row.direccion_entrega}</td>"
        f"<td>{row.date_order}</td><td>{row.state}</td><td>{row.invoice_status}</td>"
        f"<td>{row.amount_total}</td></tr>"
        for row in resultados.itertuples(index=False)
    )
    return (
        "<p>Se han detectado los siguientes pedidos NUEVOS de clientes vigilados en Odoo:</p>"
        "<table border='1' cellpadding='4' cellspacing='0'>"
        "<tr><th>Pedido</th><th>Cliente</th><th>Dirección entrega</th><th>Fecha</th><th>Estado</th>"
        "<th>Estado factura</th><th>Total</th></tr>"
        f"{filas}"
        "</table>"
    )


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

    notificados = _load_notificados()
    nuevos = resultados[~resultados["pedido_id"].isin(notificados)]

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
            html_body=_build_alert_email_html(nuevos),
            to_address=destinatarios,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR al enviar el email de alerta: {exc}")
        return 1

    notificados.update(int(pid) for pid in nuevos["pedido_id"].tolist())
    _save_notificados(notificados)
    _log("Alerta enviada y registro de notificados actualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
