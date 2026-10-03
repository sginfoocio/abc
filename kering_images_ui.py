from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from kering_images import (
    BatchService, ConfigStore, ImageStore, VIEWS, STATES, data_root, read_orders, read_suppliers,
)


@st.cache_resource
def service() -> BatchService:
    return BatchService(ImageStore(data_root()))


def admin_only() -> None:
    if st.session_state.get("auth_role") != "admin":
        st.error("Acceso reservado a administradores.")
        st.stop()


def resolve_engine(engine):
    return engine() if callable(engine) else engine


def render_kering_settings(engine) -> None:
    admin_only()
    st.subheader("Kering")
    store = ConfigStore(data_root())
    try:
        config = store.load()
        suppliers = read_suppliers(resolve_engine(engine))
    except Exception:
        st.error("No se puede leer la configuracion cifrada o los proveedores Odoo. Revise la clave y la conexion del servidor.")
        return
    options = [0] + [row["id"] for row in suppliers]
    names = {row["id"]: row["name"] for row in suppliers}
    names[0] = "Seleccionar proveedor"
    with st.form("kering_settings"):
        url = st.text_input("URL del portal Kering", config["url"])
        username = st.text_input("Usuario Kering", config["username"])
        password = st.text_input("Contrase\u00f1a Kering", type="password")
        supplier = st.selectbox("Proveedor Odoo Kering", options,
                                index=options.index(config["supplier_id"]) if config["supplier_id"] in options else 0,
                                format_func=lambda value: names[value])
        clear = st.checkbox("Eliminar credenciales guardadas")
        save = st.form_submit_button("Guardar Kering", icon=":material/save:")
    if save:
        save_settings(store, {"url": url.strip(), "username": "" if clear else username.strip(),
                              "password": "" if clear else password or config["password"], "supplier_id": supplier})
    if st.button("Comprobar configuracion Kering", icon=":material/check_circle:"):
        try:
            current = store.load()
            store.cipher()
            if not current["username"] or not current["password"] or current["supplier_id"] not in names or not current["supplier_id"]:
                st.warning("Faltan credenciales o proveedor Odoo.")
            else:
                st.success("Cifrado y proveedor disponibles. No se ha comprobado el acceso autenticado a Kering.")
        except Exception:
            st.error("No se pudo comprobar la configuracion del servidor.")
    st.warning("Descarga real bloqueada: falta verificar busqueda por EAN, correspondencia exacta y vistas en una sesion autenticada de Kering.")


def save_settings(store, values):
    try:
        store.save(values)
        st.success("Configuracion Kering guardada cifrada.")
    except Exception:
        st.error("No se pudo guardar. Compruebe URL, proveedor y clave de cifrado del servidor.")


def local_order_time(value):
    instant = datetime.fromisoformat(str(value))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo("Europe/Madrid"))


def pending_summary(store, order):
    total = len(order["lines"])
    complete = sum(bool(line["ean"]) and len(store.valid_views(line["ean"])) == 3 for line in order["lines"])
    missing = sum(not line["ean"] for line in order["lines"])
    return f"{complete}/{total} completos; {total - complete} pendientes; {missing} sin EAN"


def submit_orders(engine, config, selected, start, end):
    supplier = int(config["supplier_id"])

    def loader(order_id):
        orders = read_orders(resolve_engine(engine), supplier, start, end, [order_id])
        if not orders:
            raise ValueError("pedido_fuera_de_seleccion")
        return orders[0]

    try:
        return service().submit(selected, st.session_state.get("auth_user", "admin"), supplier, start, end, loader)
    except Exception:
        st.error("No se pudo iniciar el lote. Compruebe el almacenamiento o espere a que terminen los lotes activos.")
        return None


@st.fragment(run_every="3s")
def render_progress(run_id):
    rows = [row for row in service().store.history() if row["run_id"] == run_id]
    if not rows:
        return
    finished = sum(row["status"] not in {"Pendiente", "En proceso"} for row in rows)
    st.progress(finished / len(rows), text=f"{finished}/{len(rows)} pedidos")
    st.dataframe(pd.DataFrame(rows)[["number", "status", "error"]], hide_index=True, width="stretch")
    for row in rows:
        results = json.loads(row["results"])
        if results:
            with st.expander(row["number"]):
                st.dataframe([dict(EAN=ean, **result["views"], Motivo=result["reason"], Intentos=result["tries"])
                              for ean, result in results.items()], hide_index=True)


def render_kering_page(engine) -> None:
    admin_only()
    st.title("Im\u00e1genes Kering")
    st.warning("Portal autenticado no verificado. No se realizan descargas reales. Los archivos existentes pueden reutilizarse y registrarse en el historial.")
    try:
        config = ConfigStore(data_root()).load()
        batch = service()
    except Exception:
        st.error("No se pudo abrir el almacenamiento Kering. Revise los permisos y la clave del servidor.")
        return
    if not config["supplier_id"]:
        st.info("Seleccione el proveedor Kering en Configuracion.")
        return
    orders_tab, history_tab = st.tabs(["Pedidos", "Historial"])
    with orders_tab:
        with st.form("kering_order_dates"):
            left, right = st.columns(2)
            start = left.date_input("Fecha desde", date.today() - timedelta(days=30))
            end = right.date_input("Fecha hasta", date.today())
            search = st.form_submit_button("Consultar pedidos", icon=":material/search:")
        if search:
            try:
                st.session_state["kering_orders"] = read_orders(resolve_engine(engine), config["supplier_id"], start, end)
                st.session_state["kering_interval"] = (start, end, config["supplier_id"])
            except Exception:
                st.error("No se pudieron leer los pedidos. Compruebe fechas, esquema Odoo y permisos de lectura.")
                st.session_state.pop("kering_orders", None)
        orders = st.session_state.get("kering_orders", [])
        interval = st.session_state.get("kering_interval")
        if interval and interval[2] != config["supplier_id"]:
            orders = []
        if orders:
            frame = pd.DataFrame([{"Seleccionar": False, "ID": order["id"], "Pedido": order["name"],
                                   "Fecha": local_order_time(order["date_order"]).strftime("%d/%m/%Y %H:%M"),
                                   "Estado": order["state"], "Productos": pending_summary(batch.store, order)} for order in orders])
            edited = st.data_editor(frame, hide_index=True, width="stretch",
                                    disabled=["ID", "Pedido", "Fecha", "Estado", "Productos"],
                                    key=f"kering_selection_{interval}")
            selected_ids = set(edited.loc[edited["Seleccionar"], "ID"])
            if st.button("Descargar imagenes", icon=":material/download:", disabled=not selected_ids):
                selected = [order for order in orders if order["id"] in selected_ids]
                st.session_state["kering_run"] = submit_orders(engine, config, selected, interval[0], interval[1])
        elif interval:
            st.info("No hay pedidos para esta seleccion.")
        if st.session_state.get("kering_run"):
            render_progress(st.session_state["kering_run"])
    with history_tab:
        render_history(engine, config, batch.store)


def render_history(engine, config, store):
    st.button("Actualizar historial", icon=":material/refresh:")
    rows = store.history()
    number = st.text_input("Numero de pedido")
    states = st.multiselect("Estado del intento", STATES)
    filter_dates = st.checkbox("Filtrar fechas del historial")
    if filter_dates:
        first, second = st.columns(2)
        order_from = first.date_input("Pedido desde", date.today() - timedelta(days=30))
        order_until = second.date_input("Pedido hasta", date.today())
        execution_from = first.date_input("Ejecucion desde", date.today() - timedelta(days=30))
        execution_until = second.date_input("Ejecucion hasta", date.today())
    dates = (order_from, order_until, execution_from, execution_until) if filter_dates else None
    filtered = filter_history(rows, number, states, dates)
    if not filtered:
        st.info("Sin intentos registrados para estos filtros.")
        return
    display = pd.DataFrame(filtered)[["number", "order_date", "status", "attempt", "user", "started", "ended", "error", "run_id"]]
    st.dataframe(display, hide_index=True, width="stretch")
    selected = st.selectbox("Detalle de intento", range(len(filtered)),
                            format_func=lambda index: f"{filtered[index]['number']} | intento {filtered[index]['attempt']} | {filtered[index]['status']}")
    render_attempt(engine, config, store, filtered[selected])


def filter_history(rows, number, states, dates):
    filtered = []
    for row in rows:
        if number.casefold() not in row["number"].casefold() or (states and row["status"] not in states):
            continue
        if dates:
            order_from, order_until, execution_from, execution_until = dates
            order_day = local_order_time(row["order_date"]).date()
            execution_day = datetime.fromtimestamp(row["started"], ZoneInfo("Europe/Madrid")).date()
            if not order_from <= order_day <= order_until or not execution_from <= execution_day <= execution_until:
                continue
        filtered.append(row)
    return filtered


def render_attempt(engine, config, store, row):
    order = json.loads(row["snapshot"])
    results = json.loads(row["results"])
    st.write(f"Intervalo: {row['start_date']} a {row['end_date']} | Proveedor Odoo: {row['supplier']}")
    for line in order["lines"]:
        if not line["ean"]:
            st.warning(f"Linea {line['id']}: producto sin EAN")
    outcomes = [value for result in results.values() for value in result["views"].values()]
    st.write(f"Descargadas: {outcomes.count('Descargada')} | Reutilizadas: {outcomes.count('Reutilizada')} | Pendientes: {outcomes.count('Pendiente')}")
    for ean in dict.fromkeys(line["ean"] for line in order["lines"] if line["ean"]):
        render_product(store, ean, results.get(ean, {}))
    st.download_button("ZIP del pedido", store.zip_order(order), file_name=f"kering-{order['id']}.zip",
                       mime="application/zip", icon=":material/folder_zip:")
    if row["status"] not in {"Pendiente", "En proceso"}:
        if st.button("Reintentar pendientes y errores", icon=":material/replay:",
                     disabled=row["supplier"] != config["supplier_id"]):
            start = date.fromisoformat(row["start_date"])
            end = date.fromisoformat(row["end_date"])
            st.session_state["kering_run"] = submit_orders(engine, config, [order], start, end)
            if st.session_state["kering_run"]:
                st.success("Nuevo intento en segundo plano. Progreso disponible en Pedidos.")


def render_product(store, ean, result):
    st.subheader(ean)
    valid = store.valid_views(ean)
    st.write(result.get("reason", ""))
    for column, view in zip(st.columns(3), VIEWS):
        with column:
            if view in valid:
                st.image(str(valid[view]), caption=view, width="stretch")
            else:
                st.warning(f"{view}: pendiente")
    if result:
        st.write(result["views"])


def gallery_rows() -> list[dict]:
    root = data_root()
    if not (root / "history.sqlite3").exists():
        return []
    store = ImageStore(root)
    with store.connect() as connection:
        eans = [row[0] for row in connection.execute("SELECT DISTINCT ean FROM images")]
    rows = []
    for ean in eans:
        for view, path in store.valid_views(ean).items():
            timestamp = path.stat().st_mtime
            rows.append({"Modelo": "Kering", "EAN": ean, "Mercado": "Kering", "Archivo": view,
                         "Ruta": str(path), "Descargada": timestamp,
                         "Fecha": datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")})
    return rows