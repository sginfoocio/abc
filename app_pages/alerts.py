from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
from typing import Callable

import pandas as pd
import streamlit as st

from check_pedidos_vigilados import find_matching_orders
from db_config import load_env_file
from graph_mail_downloader import send_alert_email
from order_alerts import OrderAlertStore, build_alert_email_html, import_legacy_notification_file
from watchlist_config import add_customer, load_watchlist, remove_customer, save_watchlist


ALERT_RECIPIENT_EMAIL_ENV = "ALERT_RECIPIENT_EMAIL"
ALERT_RECIPIENT_EMAIL_DEFAULT = "roberto@diagonaleyewear.com"
ALERT_RECIPIENT_EMAILS_EXTRA = ["virginia.nunez@diagonaleyewear.com"]


def _alert_recipient() -> str:
    load_env_file()
    return os.getenv(ALERT_RECIPIENT_EMAIL_ENV, "").strip() or ALERT_RECIPIENT_EMAIL_DEFAULT


def _render_watchlist_editor(watchlist: list[str]) -> None:
    with st.form("watchlist_add_form", clear_on_submit=True):
        st.subheader("Añadir cliente a vigilar")
        new_name = st.text_input(
            "Nombre exacto del cliente (tal cual figura en Odoo)",
            placeholder="Ej: Óptica Ejemplo S.L.",
        )
        added = st.form_submit_button("Añadir", type="primary")

    if added:
        if new_name.strip():
            watchlist = add_customer(new_name)
            st.success(f"Cliente '{new_name.strip()}' añadido a la lista de vigilancia.")
        else:
            st.warning("Introduce un nombre antes de añadir.")

    st.divider()
    st.subheader("Clientes vigilados")
    if not watchlist:
        st.info("No hay clientes en la lista de vigilancia todavía.")
    else:
        edited_customers = st.data_editor(
            pd.DataFrame({"Cliente": watchlist}),
            column_config={"Cliente": st.column_config.TextColumn("Cliente vigilado", required=True)},
            hide_index=True,
            num_rows="dynamic",
            key="watchlist_editor",
        )
        if st.button("Guardar lista de clientes", type="secondary"):
            customers = [
                str(name).strip()
                for name in edited_customers["Cliente"].dropna().tolist()
                if str(name).strip()
            ]
            customers = list(dict.fromkeys(customers))
            save_watchlist(customers)
            st.success(f"Lista guardada: {len(customers)} cliente(s) vigilado(s).")
            st.rerun()

            customer_to_remove = st.selectbox("Cliente a quitar", options=watchlist)
            if st.button("Quitar cliente", type="secondary"):
                remove_customer(customer_to_remove)
                st.success(f"Cliente '{customer_to_remove}' eliminado de la lista.")
                st.rerun()


def _send_order_alert(results: pd.DataFrame) -> None:
    alert_store = OrderAlertStore()
    legacy_notifications = Path(__file__).resolve().parents[1] / "alerta_pedidos_notificados.json"
    import_legacy_notification_file(alert_store, legacy_notifications)
    batch = alert_store.claim_orders(results["pedido_id"].tolist())
    new_orders = results[results["pedido_id"].isin(batch.order_ids)]
    if new_orders.empty:
        st.info("Los pedidos ya están notificados o hay otra ejecución procesándolos.")
        return

    recipient = _alert_recipient()
    recipients = list(dict.fromkeys([recipient, *ALERT_RECIPIENT_EMAILS_EXTRA]))
    with st.spinner(f"Enviando alerta por email a {', '.join(recipients)}..."):
        try:
            send_alert_email(
                subject=f"Alerta de pedidos vigilados ({len(new_orders)})",
                html_body=build_alert_email_html(new_orders.to_dict(orient="records")),
                to_address=recipients,
            )
        except Exception as exc:  # noqa: BLE001
            alert_store.mark_failed(batch.batch_id, str(exc))
            st.error(f"No se pudo enviar el email de alerta: {exc}")
        else:
            alert_store.mark_sent(batch.batch_id, ", ".join(recipients))
            st.success(f"Email de alerta enviado a {', '.join(recipients)}.")


def _render_order_check(watchlist: list[str]) -> None:
    st.divider()
    st.subheader("Comprobar pedidos")
    start_column, end_column, state_column = st.columns([1, 1, 1])
    today = date.today()
    date_from = start_column.date_input("Desde", value=today - timedelta(days=30))
    date_to = end_column.date_input("Hasta", value=today)
    status_filter = state_column.radio("Estado", options=["Pendientes", "Todos"], horizontal=True)

    if not st.button("Comprobar ahora", type="primary"):
        return
    if not watchlist:
        st.warning("Añade al menos un cliente a la lista de vigilancia antes de comprobar.")
        return
    if date_from > date_to:
        st.warning("La fecha 'Desde' no puede ser posterior a la fecha 'Hasta'.")
        return

    with st.spinner("Consultando pedidos en Odoo..."):
        try:
            results = find_matching_orders(
                clientes=watchlist,
                fecha_desde=date_from,
                fecha_hasta=date_to + timedelta(days=1),
                solo_pendientes=(status_filter == "Pendientes"),
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Error al consultar Odoo: {exc}")
            return

    if results.empty:
        st.info("No se han encontrado pedidos para los clientes vigilados en el rango indicado.")
        return
    st.success(f"Se han encontrado {len(results)} pedido(s).")
    st.dataframe(results, use_container_width=True, hide_index=True)
    _send_order_alert(results)


def render_alerts_page(
    render_sidebar_shell: Callable[[str], None],
    require_admin_access: Callable[[], None],
    render_footer: Callable[[], None],
) -> None:
    render_sidebar_shell("Alerta Pedidos")
    require_admin_access()
    st.title("Alerta de Pedidos de Clientes Vigilados")
    st.caption(
        "Mantén aquí la lista de clientes a vigilar. Se busca coincidencia en el nombre del cliente "
        "o en la dirección de entrega. Cuando se detecte un pedido, se enviará un email de alerta."
    )
    watchlist = load_watchlist()
    _render_watchlist_editor(watchlist)
    _render_order_check(watchlist)

    render_footer()