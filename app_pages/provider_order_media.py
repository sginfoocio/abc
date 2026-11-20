"""Images and ZIPs scoped to the currently selected purchase order."""
import sqlite3
from pathlib import PureWindowsPath

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from image_exports import prepare_repository_zip
from image_thumbnails import UI_BACKGROUND, UI_DIMENSIONS, generate
from market_publication import MARKETS, ReadOnlyImageRepository
from provider_images import ean_key, ean_valid
from repository_catalog_readonly import verified_original
from repository_storage import check_image_root

EXPORT = "supplier_order_export"
PHOTO = "supplier_order_thumbnail"


def export_candidates(lines):
    groups, excluded = {}, []
    for line in lines:
        ean = line["ean"]
        if not ean_valid(ean):
            excluded.append((line, "Sin EAN/UPC válido"))
        else:
            groups.setdefault(ean_key(ean), []).append(line)
    candidates = {}
    for values in groups.values():
        if any(line["barcode_conflict"] for line in values) or len({line["product_id"] for line in values}) != 1:
            excluded.extend((line, "Identidad de variante ambigua") for line in values)
            continue
        candidates[values[0]["ean"]] = values[0]
        excluded.extend((line, "Referencia repetida o equivalente") for line in values[1:])
    return candidates, excluded


def render_order_export(order, root, engine_factory, owner):
    if st.session_state.get("auth_role") != "admin":
        return
    with st.expander("Exportación del pedido"):
        if order["line_count"] != len(order["lines"]):
            st.warning("Pedido fraccionado en Odoo: no se puede preparar una exportación global.")
            return
        candidates, excluded = export_candidates(order["lines"])
        if excluded:
            st.dataframe([{"Línea": line["id"], "EAN/UPC": line["ean"], "Exclusión": reason}
                          for line, reason in excluded], hide_index=True)
        if not candidates:
            st.info("No hay referencias con identidad suficiente para exportar.")
            return
        option = st.radio("Alcance del ZIP", ["Todas las referencias válidas", "Selección de referencias"],
                          horizontal=True, key=f"zip_scope_{order['id']}")
        codes = list(candidates) if option == "Todas las referencias válidas" else st.multiselect(
            "EAN/UPC para el ZIP", list(candidates), key=f"zip_codes_{order['id']}")
        kind = st.selectbox("Formato de exportación", ("Original", *MARKETS), key=f"zip_format_{order['id']}")
        if not codes or len(codes) > 200:
            st.info("Seleccione entre 1 y 200 referencias por ZIP.")
            return
        scope = (owner, order["id"], order["name"], root, tuple(codes), kind,
                 tuple(candidates[code]["product_id"] for code in codes))
        st.caption(f"Pedido {order['name']} · {order['supplier']} · {len(codes)} referencias seleccionadas. "
                   "Los formatos comerciales consultan sus identificadores Odoo al preparar y generar.")
        expected = {code: candidates[code]["product_id"] for code in codes}
        options = dict(role="admin", export_format=kind, engine_factory=engine_factory,
                       expected_variants=expected)
        if st.button("Preparar exportación de imágenes"):
            st.session_state.pop(EXPORT, None)
            try:
                with prepare_repository_zip(ReadOnlyImageRepository(root), codes, preview_only=True, **options) as plan:
                    st.session_state[EXPORT] = (scope, plan, None)
            except ValueError as error:
                st.error(f"Exportación bloqueada: {error}")
            except (OSError, sqlite3.Error, SQLAlchemyError):
                st.error("Exportación bloqueada: no se pudo verificar el catálogo, los archivos o los ID de Odoo.")
        cached = st.session_state.get(EXPORT)
        if not cached or cached[0] != scope:
            return
        _, plan, payload = cached
        st.text(f"EAN/UPC incluidos: {', '.join(codes)}")
        st.caption(f"{len(plan['files']) + len(plan['excluded'])} imágenes disponibles en la vista previa · "
                   f"{len(plan['files'])} exportables · {len(plan['excluded'])} excluidas")
        if plan["files"]:
            st.dataframe(plan["files"], hide_index=True)
        if plan["excluded"]:
            st.dataframe(plan["excluded"], hide_index=True)
        missing = {code: ", ".join(views) for code, views in plan["missing"].items() if views}
        if missing:
            st.dataframe([{"EAN/UPC": code, "Vistas no incluidas": views} for code, views in missing.items()],
                         hide_index=True)
            if kind != "Original":
                st.error("ZIP comercial bloqueado: faltan vistas acreditadas en la selección.")
                return
            st.warning("El ZIP Original incluirá únicamente las fotografías verificadas disponibles.")
        if st.button("Generar ZIP verificado"):
            st.session_state[EXPORT] = (scope, plan, None)
            payload = None
            try:
                with prepare_repository_zip(ReadOnlyImageRepository(root), codes,
                                            expected_preview=plan, **options) as prepared:
                    payload = (prepared.stream.read(), prepared.plan.filename)
                    st.session_state[EXPORT] = (scope, plan, payload)
            except ValueError as error:
                st.error(f"ZIP bloqueado: {error}")
            except (OSError, sqlite3.Error, SQLAlchemyError):
                st.error("No se pudo verificar el catálogo, los archivos o los identificadores. No se generó un ZIP.")
        if payload:
            st.download_button("Descargar ZIP", payload[0], file_name=payload[1],
                               mime="application/zip", on_click="ignore")


def render_photos(photos, root, labels, owner, key, *, thumbnail=True):
    if not photos:
        st.info("No hay fotografías verificadas en la referencia.")
        return
    st.dataframe([{"Clase": labels[photo["view"]], "Nombre": photo["record"]["name"],
                   "Proveedor": photo["record"]["provider"], "Procedencia": photo["record"]["origin"],
                   "Revisión": "Pendiente" if photo["view"] == "unknown" else "—"}
                  for photo in photos], hide_index=True)
    by_id = {photo["record"]["record_id"]: photo for photo in photos}
    chosen = st.selectbox("Fotografía", list(by_id), index=None, key=f"photo_{key}", format_func=lambda i:
                          f"{labels[by_id[i]['view']]} · {PureWindowsPath(by_id[i]['record']['name']).name}")
    if chosen is None:
        return
    record = by_id[chosen]["record"]
    signature = owner, root, chosen, record["checksum"], key
    if not thumbnail or st.button("Ver fotografía", key=f"open_photo_{key}"):
        cached = st.session_state.get(PHOTO)
        if thumbnail or not cached or cached[0] != signature:
            try:
                check_image_root(root)
                content, _, issue = verified_original(record, root)
                if issue or content is None:
                    st.session_state.pop(PHOTO, None)
                    st.warning("No se pudo verificar el original. Actualice la consulta de imágenes.")
                    return
                preview = generate(content, UI_DIMENSIONS, "JPEG", UI_BACKGROUND) if thumbnail else content
                st.session_state[PHOTO] = (signature, preview)
            except (OSError, ValueError, RuntimeError):
                st.session_state.pop(PHOTO, None)
                st.warning("No se pudo leer o visualizar el original.")
    cached = st.session_state.get(PHOTO)
    if cached and cached[0] == signature:
        st.image(cached[1], width=450)
