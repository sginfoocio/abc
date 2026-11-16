"""Administrator-only bulk search; all external reads require an explicit analysis."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from PIL import Image

import bulk_image_search as bulk
import cloud_design as design
from image_repository import repository_root
from provider_images import parse_ean_file, parse_ean_text
from provider_images_ui import data_root
from provider_odoo_orders import BASIS_TEXT, IMAGE_LABELS
from repository_catalog_readonly import resolve_catalog


def _views(values):
    labels = {"frontal": "V1 frontal", "perspectiva": "V2 perspectiva", "lateral": "V3 lateral"}
    return ", ".join(labels[v] for v in values) or "—"


def _table_row(row):
    product, proposal, report = row["product"] or {}, row["proposal"], row["coverage"]
    return {
        "EAN/UPC": " / ".join(row["equivalents"]),
        "Producto/variante": " · ".join(str(x) for x in (product.get("product"), product.get("default_code")) if x),
        "Marca": str(product.get("brand") or ""),
        "Proveedor Odoo": proposal["supplier"],
        "Portal propuesto": proposal["portal"],
        "Registros en catálogo": report["records"],
        "Vistas disponibles": _views(report["views"]),
        "Vistas faltantes": _views(report["missing_views"]) if report["label"] != "desconocida" else "Desconocidas",
        "Cobertura": IMAGE_LABELS[report["label"]],
        "Base": BASIS_TEXT[report["basis"]],
        "Situación": proposal["status"],
        "Motivo": proposal["reason"],
    }


def _visible(row, selection):
    report, status = row["coverage"], row["proposal"]["status"]
    if selection == "Disponibles":
        return bool(report["records"])
    if selection == "Faltantes":
        return report["label"] in {"parciales", "sin_registros", "sin_vistas_validas"}
    if selection == "Revisión":
        return status != "Ruta confirmada" or report["label"] == "desconocida" or bool(report["issues"])
    return True


def _detail(row, root):
    from app_pages.image_library_page import _render_viewer  # avoid circular import at module load

    proposal, report = row["proposal"], row["coverage"]
    with st.expander("Por qué se propone este proveedor", expanded=True):
        st.write(proposal["reason"])
        for item in proposal["evidence"]:
            st.write(f"• {item}")
        st.caption("Las fotografías existentes no autorizan nuevas adquisiciones.")
    st.caption(f"Base de cobertura: {BASIS_TEXT[report['basis']]}.")
    if report["issues"]:
        st.write("Incidencias de archivos:", report["issues"])
    if not row["records"]:
        return
    try:
        records = bulk.photo_records(row["records"], root)
    except (OSError, ValueError, RuntimeError):
        st.warning("Almacenamiento de imágenes no disponible.")
        return
    previews, access = {}, {}
    for record in records:
        try:
            previews[record.id] = bulk.verified_photo(record)
            access[record.id] = True
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            previews[record.id], access[record.id] = "original no verificable", None
    if records:
        st.subheader("Fotografías existentes")
        _render_viewer(None, None, records, access, previews, readonly=True)


def render(engine_factory):
    st.header("Búsqueda masiva por EAN")
    st.caption("Análisis en solo lectura. No se crean solicitudes ni se contactan portales.")
    pasted = st.text_area("EAN/UPC", placeholder="Un código por línea, o separados por comas o punto y coma")
    uploaded = st.file_uploader("Importar listado", type=["csv", "txt"], help="Máximo 1 MiB, en UTF-8.")
    if st.button("Analizar listado", type="primary"):
        st.session_state.pop("bulk_image_result", None)
        try:
            values = parse_ean_text(pasted)
            if uploaded is not None:
                content = uploaded.getvalue()
                if len(content) > 1024 * 1024:
                    raise ValueError("El archivo supera 1 MiB.")
                content.decode("utf-8-sig")
                values.extend(parse_ean_file(content))
            root = repository_root()
            catalog = resolve_catalog() or root / ".catalog.sqlite3"
            result = bulk.analyze(values, catalog=catalog, root=root, engine_factory=engine_factory,
                                  links_root=data_root())
        except (ValueError, UnicodeDecodeError) as error:
            st.error(f"Listado no válido: {error}")
        else:
            st.session_state["bulk_image_result"] = (result, root)
    saved = st.session_state.get("bulk_image_result")
    if not saved:
        return
    result, root = saved
    st.write(f"Entradas: {result['total']} · Productos únicos: {len(result['groups'])} · "
             f"Repetidos: {result['duplicates']} · Inválidos: {len(result['invalid'])}")
    if result["invalid"]:
        st.warning("Códigos inválidos: " + ", ".join(result["invalid"]))
    if result["catalog_error"]:
        st.warning(f"Catálogo o almacenamiento no disponible: {result['catalog_error']}. Cobertura desconocida.")
    if result["odoo_error"]:
        st.warning(f"Odoo no disponible ({result['odoo_error']}); imágenes locales siguen consultables.")
    if result["links_error"]:
        st.warning("No se pudieron leer las asociaciones de proveedor; propuestas pendientes de revisión.")
    selection = st.selectbox("Filtrar", ["Todos", "Disponibles", "Faltantes", "Revisión"])
    st.caption("Disponibles: con registros en catálogo; la cobertura de vistas solo cuenta archivos verificados.")
    rows = [row for row in result["rows"] if _visible(row, selection)]
    if not rows:
        st.info("No hay productos para este filtro.")
        return
    st.dataframe(pd.DataFrame([_table_row(row) for row in rows]), hide_index=True, width="stretch")
    labels = {row["code"]: row for row in rows}
    chosen = st.selectbox("Detalle del producto", list(labels), format_func=lambda code: " / ".join(labels[code]["equivalents"]))
    _detail(labels[chosen], root)


def render_page(render_sidebar_shell, require_admin_access, render_footer, engine_factory):
    render_sidebar_shell("Repositorio de imágenes")
    require_admin_access()
    design.inject_styles()
    render(engine_factory)
    render_footer()
