"""Administrative purchase-order panel. Network acquisition only on explicit submit."""
from datetime import datetime, timedelta
from math import ceil

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from app_pages.provider_order_snapshot import (
    COMPLETE, MADRID, NONE, PARTIAL, PROVIDERS, UNKNOWN, VIEWS, acquire, associations,
)
from kering_images import data_root as kering_root
from provider_images import ean_key, ean_valid
from provider_images_ui import data_root
from provider_odoo_orders import MAX_ORDER_DAYS, OdooUnavailable
from repository_catalog_readonly import verified_original
from repository_storage import check_image_root

SNAPSHOT = "supplier_order_snapshot"
CONTEXT = "supplier_order_photos"
LEGACY = "supplier_order_legacy"
PAGE_SIZE = 15
LABELS = dict(zip(VIEWS, ("Frontal", "Lateral", "Perspectiva", "Detalle", "Lifestyle", "Otras imágenes")))


def owner():
    return st.session_state.get("auth_user"), st.session_state.get("auth_session_id")


def _guard():
    if st.session_state.get("auth_role") != "admin":
        st.error("Acceso reservado a administradores.")
        st.stop()


def _provider(order):
    return PROVIDERS.get(order["provider"], "Asociación ambigua")


def filter_orders(orders, *, number="", barcode="", state="", coverage="", errors="",
                  provider=None, start="", end=""):
    needle, code = number.strip().casefold(), barcode.strip()
    return [
        o for o in orders if (not needle or needle in (o["name"] or "").casefold())
        and (not code or (ean_valid(code) and ean_key(code) in o["codes"])
             or any(code == line["ean"] for line in o["lines"]))
        and (not state or state == o["state"])
        and (provider is None or provider == o["provider"])
        and (not start or o["date"] >= start) and (not end or o["date"] <= end)
        and (not coverage or coverage == o["coverage"])
        and (not errors or o["has_errors"] == (errors == "Registrados"))
    ]


def _paginate(items, key, label):
    pages = ceil(len(items) / PAGE_SIZE)
    if pages < 2:
        return items
    page = st.selectbox(label, range(1, pages + 1), key=key)
    return items[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]


def _request(engine_factory, links, providers, start, end, *, before=None, page=1):
    if not start or not end or not 0 <= (end - start).days < MAX_ORDER_DAYS:
        st.error(f"Seleccione un intervalo válido de hasta {MAX_ORDER_DAYS} días.")
        return
    try:
        snapshot = acquire(engine_factory, links, providers, start, end, before=before)
    except ValueError:
        st.error("No se pudo consultar con esta configuración de proveedores y fechas.")
        return
    except (OdooUnavailable, SQLAlchemyError, TimeoutError, OSError):
        st.error("Odoo no disponible o tiempo de espera agotado. Se conserva la consulta anterior.")
        return
    st.session_state[SNAPSHOT] = (owner(), snapshot, page)
    for key in ("sup_order", "sup_page", "sup_lines", f"sup_dates_{page}"):
        st.session_state.pop(key, None)


def _summary(snapshot):
    totals = snapshot["totals"]
    st.caption("Indicadores de la página consultada, antes de aplicar los filtros de tabla.")
    for row in (
        (("Pedidos", len(snapshot["orders"])), ("Cobertura completa", totals.get(COMPLETE, 0)),
         ("Cobertura parcial", totals.get(PARTIAL, 0)), ("Sin imágenes válidas", totals.get(NONE, 0)),
         ("Cobertura no determinada", totals.get(UNKNOWN, 0))),
        (("Pedidos con errores", totals.get("errors", 0)), ("Referencias consultadas", totals.get("lines", 0)),
         ("EAN/UPC distintos", totals["codes"]), ("EAN con fotos", totals["photos"]),
         ("EAN para revisión", totals["review"])),
    ):
        for column, (label, amount) in zip(st.columns(len(row)), row):
            column.metric(label, amount)
    st.caption("Cobertura técnica: frontal, lateral y perspectiva acreditadas; no es una política de exportación.")


def _last(order):
    if not order["history"]:
        return "Sin historial disponible"
    attempt = order["history"][0]
    return f"{attempt['date']} · {attempt['status']}"


def _photo_counts(line):
    report = line["image"]
    return {label: str(report["counts"][view]) if report and not report["incomplete"] else
            f"≥ {report['counts'][view]}" if report else "—" for view, label in LABELS.items()}


def _detail(order, root, library_page, engine_factory):
    st.subheader(f"{_provider(order)} · {order['name']}")
    st.text(f"{order['supplier']} · {order['date']} · Estado Odoo: {order['state']}")
    st.caption(f"{order['line_count']} referencias · {len(order['codes'])} EAN consultados · "
               f"Cobertura: {order['coverage']} · Último proceso: {_last(order)}")
    if not order["provider"]:
        st.warning("La relación comercial corresponde a más de un proveedor configurado; no se atribuye historial.")
    if order["line_count"] != len(order["lines"]):
        first = order["last_line_number"] - len(order["lines"]) + 1
        st.warning(f"Solo se muestran las líneas {first}–{order['last_line_number']} de este pedido. "
                   "La cobertura global no es concluyente. Use la paginación de Odoo para continuar.")
    visible = _paginate(order["lines"], "sup_lines", "Página de referencias")
    if not visible:
        st.info("Este pedido no tiene líneas de producto.")
    else:
        st.dataframe([{
            "Línea": line["id"], "Variante": line["product_id"], "EAN/UPC": line["ean"],
            "Referencia": line["sku"], "Producto": line["product"], "Modelo": line["model"],
            "Color": line["color"], "Calibre": line["caliber"], "Cantidad": line["quantity"],
            **_photo_counts(line), "Cobertura": line["image"]["status"] if line["image"] else UNKNOWN,
            "Vistas no verificadas": ", ".join(LABELS[v] for v in line["image"]["missing"]) if line["image"] else "—",
            "Incidencias": ", ".join(issue for issue in line["issues"] if issue),
        } for line in visible], hide_index=True)
    variants = {line["id"]: line for line in order["lines"] if line["image"] and line["image"]["photos"]}
    if variants and library_page is not None:
        line_id = st.selectbox("Producto para abrir en el repositorio", list(variants), format_func=lambda i: (
            f"{variants[i]['ean']} · {variants[i]['sku']} · línea {i}"), key=f"sup_product_{order['id']}")
        if st.button("Abrir imágenes del producto"):
            line = variants[line_id]
            st.session_state[CONTEXT] = (owner(), order["name"], line["ean"], line["image"]["photos"], root)
            st.switch_page(library_page)
    from app_pages.provider_order_actions import render_order_actions

    render_order_actions(order, root, engine_factory)
    st.markdown("#### Historial de procesamiento")
    if order["history"]:
        st.dataframe([{"Fecha": item["date"], "Resultado": item["status"], "Fuente": item["source"],
                       "Referencias afectadas": ", ".join(item["eans"]), "Incidencia": item["detail"]}
                      for item in order["history"]], hide_index=True)
        if order["history_limited"]:
            st.caption("Historial acotado a los eventos más recientes.")
    else:
        st.info("Sin historial disponible")


def render_order_panel(engine_factory, legacy_renderer, library_page=None):
    _guard()
    if st.session_state.get(LEGACY) == owner():
        if st.button("Volver a pedidos de solo lectura"):
            st.session_state.pop(LEGACY)
            st.rerun()
        legacy_renderer()
        return
    with st.expander("Herramientas existentes de los proveedores"):
        st.caption("Fuera de la consulta de solo lectura. Conservan sus permisos y acciones actuales.")
        if st.button("Abrir herramientas existentes"):
            st.session_state[LEGACY] = owner()
            st.rerun()
    links, association_notices = associations(data_root(), kering_root())
    configured = set(p for p, _, _ in links)
    st.caption(f"Proveedores con asociación Odoo: {len(configured)} de {len(PROVIDERS)} · " +
               " · ".join(f"{label}: {'configurado' if key in configured else 'sin asociación confirmada'}"
                          for key, label in PROVIDERS.items()))
    for notice in association_notices:
        st.warning(notice)
    today = datetime.now(MADRID).date()
    with st.form("supplier_orders_odoo"):
        provider_col, start_col, end_col = st.columns(3)
        provider = provider_col.selectbox("Proveedor", ["", *PROVIDERS],
                                          format_func=lambda key: PROVIDERS.get(key, "Todos configurados"))
        start = start_col.date_input("Desde", today - timedelta(days=30))
        end = end_col.date_input("Hasta", today)
        submitted = st.form_submit_button("Consultar pedidos de Odoo")
    if submitted:
        _request(engine_factory, links, (provider,) if provider else tuple(PROVIDERS), start, end)

    cached = st.session_state.get(SNAPSHOT)
    if not cached or cached[0] != owner():
        st.info("Todavía no se han consultado pedidos de Odoo. Seleccione el proveedor y el intervalo.")
        return
    _, snapshot, acquired_page = cached
    previous_config = {p for p, _, _ in snapshot["links"]}
    scope = ", ".join(PROVIDERS[p] for p in snapshot["providers"] if p in previous_config)
    st.caption(f"Página Odoo {acquired_page} · {scope} · {snapshot['start']} a {snapshot['end']} · "
               f"Consultada el {snapshot['queried']} (Madrid).")
    if snapshot["next"] and tuple(links) != snapshot["links"]:
        st.warning("Las asociaciones han cambiado. Inicie una nueva consulta para continuar.")
    elif snapshot["next"] and st.button("Consultar siguiente página de Odoo"):
        _request(engine_factory, links, snapshot["providers"], snapshot["start"], snapshot["end"],
                 before=snapshot["next"], page=acquired_page + 1)
        if st.session_state[SNAPSHOT] is not cached:
            st.rerun()
    for notice in snapshot["notices"]:
        st.warning(notice)
    _summary(snapshot)
    if not snapshot["orders"]:
        st.info("No hay pedidos para el alcance consultado.")
        return
    a, b, c = st.columns(3)
    number = a.text_input("Número de pedido")
    barcode = b.text_input("EAN/UPC")
    state = c.selectbox("Estado Odoo", ["", *sorted({o["state"] for o in snapshot["orders"] if o["state"]})])
    d, e, f = st.columns(3)
    coverage = d.selectbox("Estado de cobertura", ["", COMPLETE, PARTIAL, NONE, UNKNOWN])
    errors = e.selectbox("Errores en el historial", ["", "Registrados", "Sin errores registrados"])
    provider_filter = f.selectbox("Proveedor en tabla", [None, *sorted({o["provider"] for o in snapshot["orders"]})],
                                  format_func=lambda key: "Todos" if key is None else
                                  PROVIDERS.get(key, "Asociación ambigua"))
    interval = st.date_input("Fechas en tabla", (snapshot["start"], snapshot["end"]),
                             min_value=snapshot["start"], max_value=snapshot["end"], key=f"sup_dates_{acquired_page}")
    filtered = filter_orders(snapshot["orders"], number=number, barcode=barcode, state=state,
                             coverage=coverage, errors=errors, provider=provider_filter,
                             start=interval[0].isoformat() if interval else "",
                             end=interval[1].isoformat() if len(interval) == 2 else "")
    if not filtered:
        st.info("Ningún pedido de la página consultada coincide con los filtros.")
        return
    visible = _paginate(filtered, "sup_page", "Página de tabla")
    st.dataframe([{
        "Proveedor": _provider(o), "Pedido": o["name"], "Fecha": o["date"], "Estado Odoo": o["state"],
        "Referencias": o["line_count"], "EAN": len(o["codes"]), "Completos": o["counts"][COMPLETE],
        "Parciales": o["counts"][PARTIAL], "Sin imágenes": o["counts"][NONE], "No determinados": o["counts"][UNKNOWN],
        "Cobertura": o["coverage"], "Errores": "Registrados" if o["has_errors"] else "No registrados",
        "Último proceso": _last(o),
    } for o in visible], hide_index=True)
    by_id = {o["id"]: o for o in visible}
    selected = st.selectbox("Ver detalle del pedido", list(by_id), index=None, key="sup_order",
                            format_func=lambda i: f"{by_id[i]['name']} · {_provider(by_id[i])} · ID {i}")
    if selected in by_id:
        _detail(by_id[selected], snapshot["root"], library_page, engine_factory)


def render_contextual_photos(return_to_orders):
    """Render the contextual library without initialising its normal store or thumbnail cache."""
    _guard()
    context = st.session_state.get(CONTEXT)
    if not context or context[0] != owner():
        return False
    _, order, ean, photos, root = context
    st.title("Biblioteca de imágenes · solo lectura")
    st.text(f"{order} · EAN/UPC {ean}")
    if st.button("Salir del contexto del pedido"):
        st.session_state.pop(CONTEXT)
        return_to_orders()
        return True
    visible = _paginate(photos, "sup_photo_page", "Página de fotografías")
    if not visible:
        return True
    photo = st.selectbox("Fotografía", range(len(visible)), index=None, format_func=lambda i: (
        f"{LABELS[visible[i]['view']]} · {visible[i]['record']['name']}"))
    if photo is not None:
        record = visible[photo]["record"]
        try:
            check_image_root(root)
            content, _, issue = verified_original(record, root)
        except (OSError, ValueError, RuntimeError):
            content, issue = None, "archivo_no_accesible"
        if issue or content is None:
            st.warning("No se pudo verificar el archivo. Actualice la consulta de pedidos.")
        else:
            st.image(content, width=450)
    return True
