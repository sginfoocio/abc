"""P0.1: explicit, read-only Odoo order and image-coverage overview.

The dashboard never starts downloads, creates requests or contacts portals. All
Odoo/catalog reads are requested by an administrator pressing the query button.
"""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from provider_images import ean_valid, ean_key
from provider_odoo_orders import (
    AssociationNotConfigured, OdooUnavailable, confirmed_partner_ids,
    read_order_lines, IMAGE_LABELS, BASIS_TEXT,
)
from repository_catalog_readonly import catalog_coverage

PROVIDERS = ("luxottica", "kering", "marcolin", "safilo", "nuorder")
STATE = {
    "completas": "Vistas completas (catálogo)",
    "parciales": "Vistas parciales (catálogo)",
    "sin_registros": "Sin registros en catálogo",
    "sin_vistas_validas": "Sin vistas técnicas acreditadas",
    "desconocida": "Cobertura desconocida",
}


def aggregate(lines, coverage):
    """Preserve distinct orders and lines, including repeated barcodes.

    Coverage and order processing status are different concepts. This read-only
    overview deliberately does not claim that any order has been processed.
    """
    orders = {}
    for line in lines:
        order = orders.setdefault(line.order_id, {
            "id": line.order_id, "name": line.order, "date": line.date,
            "supplier": line.supplier, "lines": [],
        })
        cov = coverage.get(line.ean) or {
            "label": "desconocida", "basis": "catalogo_no_consultado",
            "views": [], "missing_views": [], "records": 0,
        }
        order["lines"].append({
            "ean": line.ean, "reference": line.reference,
            "product": line.product, "coverage": cov,
            "valid_ean": ean_valid(line.ean),
        })
    rows = []
    for order in orders.values():
        labels = [item["coverage"]["label"] for item in order["lines"]]
        status = ("Desconocida" if not labels or "desconocida" in labels else
                  "Completa (catálogo)" if all(x == "completas" for x in labels) else
                  "Parcial / pendiente")
        rows.append({
            "Pedido": order["name"], "Fecha": order["date"],
            "Proveedor Odoo": order["supplier"],
            "Referencias": len(order["lines"]),
            "Vistas completas": labels.count("completas"),
            "Cobertura": status,
            "Procesamiento": "No consultado",
            "_order_id": order["id"],
        })
    return orders, rows


def query_orders(engine, links_root, provider, start, end, limit=1000):
    """One bounded read-only Odoo query and one read-only catalog query."""
    partners = confirmed_partner_ids(links_root, provider)
    lines, truncated = read_order_lines(
        engine, partners, start, end, max_lines=limit)
    codes = list(dict.fromkeys(
        line.ean for line in lines if ean_valid(line.ean)))
    report = catalog_coverage(codes)
    orders, rows = aggregate(lines, report["coverage"])
    return {
        "orders": orders, "rows": rows, "truncated": truncated,
        "catalog": report["catalog"], "lines": len(lines),
        "codes": len({ean_key(code) for code in codes}),
    }


def render(engine_factory, links_root):
    """Show the overview without I/O to Odoo or provider portals on render."""
    if st.session_state.get("auth_role") != "admin":
        st.error("Acceso reservado a administradores.")
        st.stop()

    st.subheader("Pedidos y cobertura de imágenes")
    st.caption(
        "Consulta administrativa de solo lectura. La cobertura procede del "
        "catálogo existente y no acredita el procesamiento del pedido, "
        "la integridad física de los archivos ni la política comercial del proveedor."
    )
    provider = st.selectbox("Proveedor", PROVIDERS, key="provider_orders_provider")
    today = date.today()
    a, b, c = st.columns(3)
    with a:
        start = st.date_input(
            "Pedidos desde", today - timedelta(days=90),
            key="provider_orders_start")
    with b:
        end = st.date_input("Pedidos hasta", today, key="provider_orders_end")
    with c:
        limit = st.number_input(
            "Máximo de líneas", min_value=1, max_value=2000,
            value=500, key="provider_orders_limit")
    signature = (provider, str(start), str(end), int(limit))
    state_key = "provider_orders_readonly_result"
    if st.session_state.get("provider_orders_signature") != signature:
        st.session_state.pop(state_key, None)
    st.session_state["provider_orders_signature"] = signature

    if st.button("Consultar pedidos en Odoo", key="provider_orders_fetch"):
        if start > end:
            st.warning("La fecha inicial debe ser anterior o igual a la final.")
        else:
            try:
                engine = engine_factory() if callable(engine_factory) else engine_factory
                st.session_state[state_key] = query_orders(
                    engine, links_root, provider, start, end, int(limit))
            except AssociationNotConfigured:
                st.session_state.pop(state_key, None)
                st.warning(
                    "No hay una asociación Odoo-proveedor confirmada para esta "
                    "ruta. Revísala antes de consultar; no se infiere por marca.")
            except OdooUnavailable as exc:
                st.session_state.pop(state_key, None)
                st.error(f"No fue posible consultar Odoo: {exc.code}.")
            except (OSError, ValueError) as exc:
                st.session_state.pop(state_key, None)
                st.error(f"Consulta no disponible: {exc.__class__.__name__}.")

    result = st.session_state.get(state_key)
    if result is None:
        st.info("Selecciona el proveedor y pulsa Consultar pedidos en Odoo.")
        return

    if result["truncated"]:
        st.warning("Resultado limitado: reduce el período para revisar todos los pedidos.")
    catalog = result["catalog"]
    if catalog["status"] != "consultado":
        st.warning("Catálogo no disponible: cobertura desconocida, no equivale a ausencia de imágenes.")

    x, y, z = st.columns(3)
    x.metric("Pedidos", len(result["orders"]))
    y.metric("Líneas leídas", result["lines"])
    z.metric("EAN/UPC distintos", result["codes"])

    rows = result["rows"]
    if not rows:
        st.info("No se encontraron pedidos confirmados para el período.")
        return
    status = st.selectbox(
        "Filtrar cobertura", ["Todas", "Completa (catálogo)", "Parcial / pendiente", "Desconocida"],
        key="provider_orders_status")
    needle = st.text_input("Buscar número de pedido", key="provider_orders_text").strip().casefold()
    visible = [row for row in rows if
               (status == "Todas" or row["Cobertura"] == status)
               and (not needle or needle in row["Pedido"].casefold())]
    st.dataframe(
        [{k: v for k, v in row.items() if not k.startswith("_")} for row in visible],
        hide_index=True, use_container_width=True)
    if not visible:
        return
    choices = {f'{row["Pedido"]} · {row["_order_id"]}': row["_order_id"] for row in visible}
    selected = st.selectbox("Detalle del pedido", list(choices), key="provider_orders_detail")
    order = result["orders"][choices[selected]]
    details = []
    for item in order["lines"]:
        cov = item["coverage"]
        details.append({
            "EAN/UPC": item["ean"], "Referencia": item["reference"],
            "Producto": item["product"],
            "Cobertura": STATE.get(cov["label"], "Desconocida"),
            "Vistas": ", ".join(cov["views"]) or "—",
            "Faltantes": ", ".join(cov["missing_views"]) or "—",
            "Registros": cov["records"],
            "Evidencia": BASIS_TEXT.get(cov["basis"], ""),
        })
    st.dataframe(details, hide_index=True, use_container_width=True)
    st.caption(
        "El historial de ejecución por pedido no se agrega todavía: "
        "sus fuentes Kering, correo Luxottica y solicitudes manuales tienen "
        "contratos distintos. No se interpreta ausencia de historial como 'no procesado'.")
