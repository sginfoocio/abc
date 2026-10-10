from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy.exc import OperationalError

from kering_images import (
    BatchService, ConfigStore, ImageStore, VIEWS, STATES, data_root, configured_orders, read_suppliers,
    read_supplier_contacts, displayed_views, purchase_status, latest_attempts, trial_validated, batch_busy,
)
from kering_portal import KeringPortal
from kering_jobs import prepare_selection, make_loader, schedule_status


SAVE_ICON = ":material/save:"
DATE_FORMAT = "DD/MM/YYYY"
TIME_FORMAT = "%d/%m/%Y %H:%M"
MADRID = ZoneInfo("Europe/Madrid")


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
        supplier = st.selectbox(
            "Proveedor Odoo Kering", options,
            index=options.index(config["supplier_id"]) if config["supplier_id"] in options else 0,
            format_func=lambda value: names[value],
        )
        include_contacts = st.checkbox(
            "Incluir contactos de la misma entidad comercial Odoo",
            value=config.get("include_commercial_contacts", False),
        )
        clear = st.checkbox("Eliminar credenciales guardadas")
        save = st.form_submit_button("Guardar Kering", icon=SAVE_ICON)
    if save:
        save_settings(store, {**store.load(), "url": url.strip(), "username": "" if clear else username.strip(),
                              "password": "" if clear else password or config["password"], "supplier_id": supplier,
                              "include_commercial_contacts": include_contacts})
    if st.button("Ver contactos asociados al proveedor guardado", disabled=not config["supplier_id"]):
        show_supplier_contacts(engine, config["supplier_id"])
    if st.button("Comprobar configuracion Kering", icon=":material/check_circle:"):
        check_local_configuration(store, names)
    running = st.session_state.get("kering_access_probe")
    if st.button("Probar acceso a Kering", icon=":material/login:", disabled=running is not None and not running.done()):
        try:
            st.session_state["kering_access_probe"] = service().executor.submit(probe_access, store.load())
        except Exception:
            st.error("No se pudo iniciar la prueba de acceso.")
    if st.session_state.get("kering_access_probe"):
        render_access_probe()
    render_job_settings(store)


def render_job_settings(config_store):
    config = config_store.load()
    store = service().store
    validated = trial_validated(store, config)
    st.subheader("Procesamiento Kering")
    with st.form("kering_job_settings"):
        cutoff = st.date_input("Fecha de corte", date.fromisoformat(config["cutoff_date"]), format=DATE_FORMAT)
        automatic = st.toggle("Procesamiento autom\u00e1tico", value=config["auto_enabled"])
        hours = st.number_input("Intervalo (horas)", min_value=0.0, value=float(config["auto_interval_hours"]), step=1.0)
        test_mode = st.toggle("Modo de prueba", value=config["test_mode"] if validated else True, disabled=not validated)
        saved = st.form_submit_button("Guardar procesamiento", icon=SAVE_ICON)
    if saved:
        save_settings(config_store, {**config_store.load(), "cutoff_date": cutoff.isoformat(),
                                   "auto_enabled": automatic, "auto_interval_hours": hours,
                                   "test_mode": test_mode, "full_lot_validated": validated and not test_mode})
    if not validated:
        st.info("Validaci\u00f3n inicial de pedidos pendiente")
    render_scheduler_status()


def format_timestamp(value):
    return datetime.fromtimestamp(value, MADRID).strftime(TIME_FORMAT) if value else "-"


@st.fragment(run_every="5s")
def render_scheduler_status():
    try:
        config = ConfigStore(data_root()).load()
        status = schedule_status(service().store, config)
        previous, upcoming = st.columns(2)
        previous.metric("\u00daltima ejecuci\u00f3n autom\u00e1tica", format_timestamp(status["last_finished"] or status["last_started"]))
        upcoming.metric("Pr\u00f3xima ejecuci\u00f3n", format_timestamp(status["next_run"]))
        st.write(f"Resultado: {status['result']}")
    except Exception:
        st.error("No se pudo consultar el estado del programador.")


def show_supplier_contacts(engine, supplier_id):
    try:
        st.dataframe(read_supplier_contacts(resolve_engine(engine), supplier_id), hide_index=True)
    except Exception:
        st.error("No se pudieron consultar los contactos del proveedor.")


def check_local_configuration(store, names):
    try:
        current = store.load()
        store.cipher()
        if not current["username"] or not current["password"] or current["supplier_id"] not in names or not current["supplier_id"]:
            st.warning("Faltan credenciales o proveedor Odoo.")
        else:
            st.success("Cifrado y proveedor disponibles. No se ha comprobado el acceso autenticado a Kering.")
    except Exception:
        st.error("No se pudo comprobar la configuracion del servidor.")


def create_portal(config):
    return KeringPortal(config)


def probe_access(config):
    return create_portal(config).check_access()


@st.fragment(run_every="2s")
def render_access_probe():
    future = st.session_state["kering_access_probe"]
    if not future.done():
        st.info("Comprobando acceso a Kering...")
        return
    try:
        result = future.result()
        if result["ok"]:
            st.success("Acceso autenticado a Kering confirmado. Esta prueba no descarga productos.")
        else:
            st.warning(f"Acceso no confirmado: {result['code']}")
        if result.get("phases"):
            st.dataframe(result["phases"], hide_index=True)
    except Exception:
        st.error("No se pudo comprobar el acceso a Kering.")


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
    return instant.astimezone(MADRID)


def pending_summary(store, order):
    total = len(order["lines"])
    complete = sum(bool(line["ean"]) and len(store.valid_views(line["ean"])) >= 3 for line in order["lines"])
    missing = sum(not line["ean"] for line in order["lines"])
    return f"{complete}/{total} completos; {total - complete} pendientes; {missing} sin EAN"


def submit_orders(engine, config, selected, start, end):
    try:
        store = service().store
        config_store = ConfigStore(data_root())
        current_config = config_store.load()
        if current_config["supplier_id"] != config["supplier_id"]:
            raise ValueError("El proveedor ha cambiado")
        start = prepare_selection(store, current_config, selected, start, end)
        supplier = int(current_config["supplier_id"])
        loader = make_loader(store, config_store, lambda: resolve_engine(engine), start, end, supplier,
                             reader=configured_orders)
        return service().submit(selected, st.session_state.get("auth_user", "admin"), supplier, start, end, loader,
                                portal=create_portal(current_config), origin="Manual")
    except Exception:
        st.error("No se pudo iniciar: revise corte, pedidos autorizados o lotes activos.")
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
        cutoff = date.fromisoformat(config["cutoff_date"])
        with st.form("kering_order_dates"):
            left, right = st.columns(2)
            start = left.date_input("Fecha desde", cutoff, min_value=cutoff, format=DATE_FORMAT)
            end = right.date_input("Fecha hasta", max(cutoff, date.today()), min_value=cutoff, format=DATE_FORMAT)
            st.form_submit_button("Consultar pedidos", icon=":material/search:")
        render_order_list(engine, start, end)
        if st.session_state.get("kering_run"):
            render_progress(st.session_state["kering_run"])
    with history_tab:
        render_history(engine, config, batch.store)


@st.fragment(run_every="5s")
def render_order_list(engine, start, end):
    stage = "configuracion"
    try:
        config = ConfigStore(data_root()).load()
        stage = "almacenamiento"
        store = service().store
        stage = "odoo"
        orders = configured_orders(resolve_engine(engine), config, start, end)
        stage = "historial"
        latest = latest_attempts(store)
        stage = "estado"
        cache = {}
        rows = [purchase_row(store, order, latest.get(order["id"]), cache) for order in orders]
    except Exception as error:
        st.error(order_list_error(error, stage))
        return
    if not orders:
        st.info("Sin pedidos para este corte e intervalo.")
        return
    render_pilot_selection(config, orders)
    edited = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch",
                            disabled=[key for key in rows[0] if key != "Seleccionar"],
                            key=f"kering_selection_{config['supplier_id']}_{config['cutoff_date']}_{start}_{end}")
    selected_ids = set(edited.loc[edited["Seleccionar"], "ID"])
    selected = [order for order in orders if order["id"] in selected_ids]
    allowed = set(config["test_order_ids"])
    busy = batch_busy(store)
    permitted = bool(selected) and (not config["test_mode"] or len(selected) <= 2 and selected_ids <= allowed)
    if st.button("Procesar seleccionados", icon=":material/download:", disabled=not permitted or busy):
        st.session_state["kering_run"] = submit_orders(engine, config, selected, start, end)
        if st.session_state["kering_run"]:
            st.rerun()
    render_order_actions(engine, config, orders, start, end, busy)
    detail_id = st.session_state.get("kering_detail_id")
    current_order = next((order for order in orders if order["id"] == detail_id), None)
    if current_order is not None:
        render_current_order(store, current_order, latest.get(detail_id))


def order_list_error(error, stage):
    if stage == "odoo" and isinstance(error, OperationalError):
        return (
            "No se pudo acceder a PostgreSQL de Odoo. Compruebe que el servidor acepta conexiones "
            "y que el acceso de red y la configuracion de base de datos son correctos. "
            "No se han iniciado nuevas descargas. Codigo: ODOO_CONEXION."
        )
    messages = {
        "configuracion": "No se pudo leer la configuracion cifrada de Kering. Codigo: KERING_CONFIG.",
        "almacenamiento": "No se pudo abrir el almacenamiento Kering. Codigo: KERING_ALMACENAMIENTO.",
        "odoo": "No se pudieron leer los pedidos de Odoo. Codigo: ODOO_CONSULTA.",
        "historial": "No se pudo leer el historial Kering. Codigo: KERING_HISTORIAL.",
        "estado": "No se pudo calcular el estado actual de los pedidos. Codigo: KERING_ESTADO.",
    }
    return messages.get(stage, "No se pudo cargar el listado Kering. Codigo: KERING_LISTADO.")


def render_order_actions(engine, config, orders, start, end, busy):
    allowed = set(config["test_order_ids"])
    page_size = 20
    pages = (len(orders) - 1) // page_size + 1
    current = st.number_input("P\u00e1gina de acciones", min_value=1, max_value=pages, value=1, key="kering_actions_page")
    for order in orders[(current - 1) * page_size:current * page_size]:
        title, process, detail = st.columns([4, 1, 1])
        title.write(order["name"])
        if process.button("Procesar", key=f"kering_process_{order['id']}", icon=":material/download:",
                          disabled=busy or config["test_mode"] and order["id"] not in allowed):
            st.session_state["kering_run"] = submit_orders(engine, config, [order], start, end)
            if st.session_state["kering_run"]:
                st.rerun()
        if detail.button("Detalle", key=f"kering_detail_{order['id']}", icon=":material/image:"):
            st.session_state["kering_detail_id"] = order["id"]


def purchase_row(store, order, latest, cache):
    status = purchase_status(store, order, latest, cache)
    return {"Seleccionar": False, "ID": order["id"], "Pedido": order["name"],
            "Fecha": local_order_time(order["date_order"]).strftime(TIME_FORMAT),
            "Odoo": order["state"], "Procesamiento": status["status"],
            "Completos": status["complete"], "Pendientes": status["pending"],
            "\u00daltimo procesamiento": format_timestamp(status["last_processed"]),
            "Cambios": status["changed"]}


def render_pilot_selection(config, orders):
    if not config["test_mode"]:
        return
    names = {order["id"]: order["name"] for order in orders}
    options = list(dict.fromkeys(list(names) + config["test_order_ids"]))
    ids = st.multiselect("Pedidos autorizados para prueba", options, default=config["test_order_ids"],
                         format_func=lambda value: names.get(value, f"ID {value}"), max_selections=2,
                         key=f"kering_pilot_{config['supplier_id']}_{config['cutoff_date']}")
    if st.button("Guardar selecci\u00f3n de prueba", icon=SAVE_ICON):
        config_store = ConfigStore(data_root())
        save_settings(config_store, {**config_store.load(), "test_order_ids": ids})
        st.rerun()


def render_current_order(store, order, latest):
    st.subheader(order["name"])
    status = purchase_status(store, order, latest)
    st.write(f"{status['status']} | {status['complete']} completos | {status['pending']} pendientes")
    results = json.loads(latest["results"]) if latest else {}
    for line in order["lines"]:
        st.write(line.get("product_name") or f"Producto {line['product_id']}")
        if line["ean"]:
            render_product(store, line["ean"], results.get(line["ean"], {}))
        else:
            st.warning(f"Linea {line['id']}: sin EAN")
    if latest and latest["error"]:
        st.warning(latest["error"])
    st.download_button("ZIP del pedido", store.zip_order(order), file_name=f"kering-{order['id']}.zip",
                       mime="application/zip", icon=":material/folder_zip:", key=f"kering_current_zip_{order['id']}")


def render_history(engine, config, store):
    st.button("Actualizar historial", icon=":material/refresh:")
    with st.expander("Ejecuciones"):
        st.dataframe(store.executions(), hide_index=True, width="stretch")
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
    display = pd.DataFrame(filtered)[["number", "order_date", "status", "origin", "attempt", "user", "started", "ended", "error", "run_id"]]
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
            execution_day = datetime.fromtimestamp(row["started"], MADRID).date()
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
    identity = result.get("identity")
    if identity:
        st.write(identity)
    for column, view in zip(st.columns(3), displayed_views(valid)):
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
                         "Fecha": datetime.fromtimestamp(timestamp).strftime(TIME_FORMAT)})
    return rows