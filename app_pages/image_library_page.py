"""Biblioteca de imágenes: product-centred browsing of the common image repository.

Browsing reads only the local catalog and thumbnail cache. It never calls Odoo or
providers, never builds a ZIP during a render and never reads more than the visible
covers (grid) or the selected product (detail) from storage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
import streamlit as st

from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

from bulk_image_search import MAX_INPUT_FILE_BYTES, parse_ean_upload
import cloud_design as design
from image_exports import prepare_repository_zip
import image_library as library
from image_thumbnails import UI_BACKGROUND, UI_DIMENSIONS, UI_VERSION, ThumbnailCache
from kering_images_ui import original_for_display, service, visible_access
from provider_images import parse_ean_text

TITLE = "Biblioteca de imágenes"
FILTER_KEYS = ("lib_query", "lib_provider", "lib_market", "lib_view", "lib_availability")
MIME = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _visible_previews(repository, records, access) -> dict:
    """High-quality derived previews for the visible records; errors become text per record."""
    previews: dict = {}
    try:
        with ThumbnailCache(repository).batch(records) as cache:
            for record in records:
                try:
                    previews[record.id] = cache.get(
                        record, access[record.id], dimensions=UI_DIMENSIONS,
                        version=UI_VERSION, background=UI_BACKGROUND)
                except (OSError, ValueError, RuntimeError, SyntaxError, Image.DecompressionBombError) as error:
                    previews[record.id] = str(error)
    except (OSError, ValueError, RuntimeError) as error:
        previews = {record.id: str(error) for record in records}
    return previews


def _keep_widget_state() -> None:
    # Streamlit drops widget state when a page is left; re-assigning keeps filters across navigation.
    for key in (*FILTER_KEYS, "lib_size", "lib_page", "lib_multi_text", "lib_multi_format",
                "lib_multi_all", "lib_multi_selected"):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _clear_filters() -> None:
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)
    st.session_state["lib_page"] = 1


def _goto(page: int) -> None:
    st.session_state["lib_page"] = page


def _open(ean: str) -> None:
    st.session_state["lib_ean"] = ean
    st.session_state.pop("lib_photo", None)


def _close() -> None:
    st.session_state.pop("lib_ean", None)
    st.session_state.pop("lib_photo", None)


def _load(repository, refresh: bool):
    if refresh or "image_gallery_records" not in st.session_state:
        st.session_state["image_gallery_records"] = repository.catalog_records()
        st.session_state.pop("image_gallery_groups", None)
        st.session_state.pop("lib_zip", None)
        st.session_state.pop("lib_multi_zip", None)
        st.session_state.pop("lib_original", None)
    if "image_gallery_groups" not in st.session_state:
        st.session_state["image_gallery_groups"] = library.group_records(
            st.session_state["image_gallery_records"]
        )
    return st.session_state["image_gallery_groups"]


def _stats(groups) -> tuple[tuple[str, str], ...]:
    counts = library.summary(groups)
    return (
        (f"{counts['images']:,}".replace(",", "."), "fotografías únicas"),
        (f"{counts['representations']:,}".replace(",", "."), "representaciones"),
        (f"{counts['eans']:,}".replace(",", "."), "EAN"),
        (f"{counts['models']:,}".replace(",", "."), "modelos"),
    )


def _header(groups, subtitle: str) -> bool:
    left, right = st.columns([4, 1.5], vertical_alignment="bottom")
    with left:
        design.render(design.page_header_html(TITLE, subtitle, _stats(groups)))
    with right:
        return st.button("Actualizar", icon=":material/refresh:", key="image_gallery_refresh",
                         help="Vuelve a leer el catálogo local de imágenes.")


def _read_filters(groups) -> library.LibraryFilters:
    options = library.filter_options(groups)
    for key, valid in (("lib_provider", options["providers"]), ("lib_market", options["markets"]),
                       ("lib_view", list(library.VIEW_FILTERS)),
                       ("lib_availability", [library.ALL, *library.AVAILABILITY_FILTERS[1:]])):
        if st.session_state.get(key) not in (None, *valid):
            st.session_state.pop(key)
    query = st.text_input(
        "Buscar", key="lib_query", icon=":material/search:",
        placeholder="EAN (completo o parcial), modelo, referencia, marca o proveedor",
    ).strip()
    with st.container(horizontal=True, wrap=True, key="lib_filters"):
        provider = st.selectbox("Proveedor", options["providers"], key="lib_provider")
        market = st.selectbox("Mercado", options["markets"], key="lib_market")
        view = st.selectbox("Vista", list(library.VIEW_FILTERS), key="lib_view")
        availability = st.selectbox(
            "Fotografías", [library.ALL, *library.AVAILABILITY_FILTERS[1:]], key="lib_availability")
        st.session_state.setdefault("lib_size", library.PAGE_SIZES[1])
        st.selectbox("Por página", library.PAGE_SIZES, key="lib_size")
    return library.LibraryFilters(query, provider, market, view, availability)


def _preview(previews, record) -> bytes | None:
    value = previews.get(record.id)
    return value if isinstance(value, bytes) else None


def _card_views(group) -> tuple[tuple[str, bool], ...]:
    return tuple((label, view in group.present_views) for view, label in library.CORE_VIEWS)


def _render_grid(repository, page, refresh: bool, signature: tuple) -> None:
    covers = [group.cover for group in page.items]
    access = visible_access(repository, covers, "image_gallery_access", refresh=refresh,
                            selection=(signature, page.number))
    previews = _visible_previews(repository, covers, access)
    with st.container(horizontal=True, wrap=True, key="lib_grid"):
        for group in page.items:
            with st.container(border=True, key=f"lib_card_{group.ean}"):
                design.render(design.product_card_html(
                    title=group.title, provider=", ".join(group.providers), brand=group.brand,
                    reference=group.reference, ean=group.ean, named=group.has_commercial_title,
                    count=len(group.photos), views=_card_views(group),
                    preview=_preview(previews, group.cover),
                ))
                st.button("Ver fotografías", key=f"lib_open_{group.ean}", width="stretch",
                          icon=":material/photo_library:", on_click=_open, args=(group.ean,))


def _render_pager(page) -> None:
    previous, status, following = st.columns([1, 4, 1], vertical_alignment="center")
    with previous:
        st.button("Anterior", icon=":material/chevron_left:", key="lib_prev", width="stretch",
                  disabled=page.number <= 1, on_click=_goto, args=(page.number - 1,))
    with status:
        design.render(f'<p class="cd-pager" aria-live="polite">'
                      f'{design.pager_text(page.first, page.last, page.total, page.number, page.pages, "productos")}</p>')
    with following:
        st.button("Siguiente", icon=":material/chevron_right:", key="lib_next", width="stretch",
                  disabled=page.number >= page.pages, on_click=_goto, args=(page.number + 1,))


def _multi_input_changed() -> None:
    st.session_state["lib_multi_dirty"] = True
    st.session_state.pop("lib_multi_zip", None)


def _render_multi_search(repository, groups, engine_factory):
    with st.expander("Buscar varios EAN y descargar ZIP", expanded="lib_multi_values" in st.session_state):
        pasted = st.text_area("EAN/UPC del listado", key="lib_multi_text",
                              placeholder="Un EAN o varios, separados por comas, espacios o saltos de línea.",
                              on_change=_multi_input_changed)
        uploaded = st.file_uploader("Importar listado EAN", type=["xlsx", "csv", "txt"], key="lib_multi_file",
                                    max_upload_size=1,
                                    on_change=_multi_input_changed,
                                    help="Hasta 1 MiB / 500 entradas. Excel: columna EAN, Barcode o GTIN, "
                                         "o una lista sin cabecera. Use texto o formato de ceros para conservarlos.")
        if st.button("Buscar EAN en el repositorio", key="lib_multi_search"):
            for key in ("lib_multi_values", "lib_multi_zip", "lib_multi_selected", "lib_multi_source"):
                st.session_state.pop(key, None)
            try:
                if len(pasted.encode("utf-8")) > MAX_INPUT_FILE_BYTES:
                    raise ValueError("El texto supera 1 MiB.")
                values = parse_ean_text(pasted)
                if uploaded is not None:
                    if uploaded.size > MAX_INPUT_FILE_BYTES:
                        raise ValueError("El archivo supera 1 MiB.")
                    values.extend(parse_ean_upload(uploaded.getvalue(), uploaded.name))
                result = library.search_eans(groups, values)
                if not result["rows"] and not result["invalid"]:
                    raise ValueError("Introduzca al menos un EAN/UPC.")
                st.session_state["lib_multi_values"] = values
                st.session_state["lib_multi_dirty"] = False
                st.session_state["lib_multi_source"] = uploaded.name if uploaded is not None else None
            except (OSError, ValueError, UnicodeError) as error:
                st.error(f"Listado no válido: {error}")
        values = st.session_state.get("lib_multi_values")
        if values is None:
            return None
        result = library.search_eans(groups, values)
        if st.button("Mostrar todo el catálogo", key="lib_multi_clear"):
            for key in ("lib_multi_values", "lib_multi_zip", "lib_multi_selected", "lib_multi_source", "lib_multi_dirty"):
                st.session_state.pop(key, None)
            st.rerun()
        if result["invalid"]:
            st.warning("Códigos inválidos: " + ", ".join(result["invalid"]))
        if st.session_state.get("lib_multi_source"):
            st.caption(f"Listado activo importado de {st.session_state['lib_multi_source']}. "
                       "El resultado se conserva al consultar el detalle, aunque el selector de archivo se vacíe.")
        found = [row["code"] for row in result["rows"] if row["groups"]]
        missing = [row["code"] for row in result["rows"] if not row["groups"]]
        st.caption(f"Entradas: {result['total']} · Repetidos/equivalentes: {result['duplicates']} · "
                   f"Con imágenes: {len(found)} · Sin imágenes en catálogo: {len(missing)}")
        rows = []
        scoped = []
        for row in result["rows"]:
            for group in row["groups"]:
                scoped.append(group)
                rows.append({
                    "EAN solicitado": row["code"], "EAN catálogo": group.ean, "Variante/modelo": group.title,
                    "Color": ", ".join(library.variant_identity(list(group.records))["colors"]),
                    "Fotos únicas": len(group.photos), "Representaciones": len(group.records),
                    "Vistas": ", ".join(library.view_name(view) for view in sorted(group.present_views)),
                    "Procedencia": ", ".join(group.providers),
                    "Revisión": ", ".join(group.related_eans),
                })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if missing:
            st.warning("Sin imágenes en el catálogo local (no acredita ausencia en Odoo): " + ", ".join(missing))
        if not found:
            return scoped
        if st.session_state.get("lib_multi_dirty"):
            st.warning("El listado ha cambiado. Pulse Buscar EAN en el repositorio para actualizar los resultados y el ZIP.")
            return scoped
        st.caption("La galería inferior mantiene sus filtros. El ZIP utiliza los EAN seleccionados del listado, "
                   "no los filtros de la galería. No solicita imágenes ni contacta portales.")
        all_found = st.checkbox("Usar todos los EAN encontrados", value=True, key="lib_multi_all")
        if all_found:
            chosen = found
        else:
            if "lib_multi_selected" in st.session_state:
                st.session_state["lib_multi_selected"] = [
                    code for code in st.session_state["lib_multi_selected"] if code in found
                ]
            chosen = st.multiselect("EAN para el ZIP", found, default=found, key="lib_multi_selected")
        export_format = st.selectbox("Formato de exportación", ["Original", "Farfetch", "Miinto"], key="lib_multi_format")
        if export_format != "Original":
            st.info("Se consultarán los IDs externos en Odoo solo en lectura al preparar el ZIP. "
                    "Se aplican las reglas existentes V1/V2/V3 acreditadas; se detallarán las fotos excluidas. "
                    "Para conservar todas las vistas, detalles y lifestyle, elija Original.")
        st.caption("Límites: 500 entradas, 2000 fotos únicas, 64 MiB por ZIP, 30 MiB / 25 MP por foto, "
                   "60 segundos de generación. Las asociaciones sospechosas bloquean el ZIP.")
        key = (tuple(values), tuple(chosen), export_format)
        prepared = st.session_state.get("lib_multi_zip")
        if prepared and prepared["key"] != key:
            st.session_state.pop("lib_multi_zip")
            prepared = None
        if st.button("Preparar ZIP conjunto", key="lib_multi_prepare", disabled=not chosen):
            st.session_state.pop("lib_multi_zip", None)
            st.session_state.pop("lib_zip", None)
            prepared = None
            try:
                with prepare_repository_zip(
                    repository, chosen, role=st.session_state.get("auth_role"),
                    export_format=export_format, engine_factory=engine_factory,
                ) as exported:
                    prepared = {"key": key, "bytes": exported.stream.read(), "name": exported.plan.filename,
                                "excluded": exported.plan.manifest["excluded"]}
            except SQLAlchemyError as error:
                st.error(f"No se pudieron verificar los IDs en Odoo ({type(error).__name__}). No se ha generado ZIP.")
            except (OSError, ValueError, RuntimeError, PermissionError, SyntaxError,
                    Image.DecompressionBombError) as error:
                st.error(f"No se puede preparar el ZIP: {error}")
            else:
                st.session_state["lib_multi_zip"] = prepared
        if prepared:
            if prepared["excluded"]:
                st.warning(f"{len(prepared['excluded'])} fotografías fuera de las reglas del formato; "
                           "consulte las exclusiones antes de descargar.")
                st.dataframe(pd.DataFrame(prepared["excluded"]), hide_index=True, width="stretch")
            st.download_button("Descargar ZIP conjunto", prepared["bytes"], file_name=prepared["name"],
                               mime="application/zip", key="lib_multi_download", icon=":material/download:",
                               on_click="ignore")
            st.caption("ZIP preparado con archivos verificados. Vuelva a prepararlo para obtener una nueva instantánea.")
        return scoped


def _render_browser(repository, groups, refresh: bool, engine_factory=None) -> None:
    scoped = _render_multi_search(repository, groups, engine_factory)
    filters = _read_filters(groups)
    size = int(st.session_state.get("lib_size", library.PAGE_SIZES[1]))
    signature = (*filters.signature(), size, None if scoped is None else tuple(g.ean for g in scoped))
    if st.session_state.get("lib_signature") != signature:
        st.session_state["lib_signature"] = signature
        st.session_state["lib_page"] = 1
    results = library.filter_groups(groups if scoped is None else scoped, filters)
    page = library.paginate(results, st.session_state.get("lib_page", 1), size)
    st.session_state["lib_page"] = page.number
    if not groups:
        design.render(design.empty_state_html(
            "La biblioteca aún no tiene imágenes",
            "Las fotografías aparecerán aquí cuando se descarguen o se importen desde los procesos de proveedor."))
        return
    if not results:
        design.render(design.empty_state_html(
            "Sin resultados", "Ningún producto coincide con la búsqueda y los filtros aplicados."))
        st.button("Quitar filtros", key="lib_clear", icon=":material/filter_alt_off:", on_click=_clear_filters)
        return
    if filters != library.LibraryFilters():
        counts = library.summary(results)
        st.caption(f"{page.total:,} productos · {counts['images']:,} fotografías únicas · "
                   f"{counts['representations']:,} representaciones con los filtros actuales".replace(",", "."))
    _render_grid(repository, page, refresh, signature)
    _render_pager(page)


def _photo_caption(record) -> str:
    parts = [library.view_name(record.view), library.market_label(record.market), record.provider]
    return " · ".join(part for part in parts if part)


def _original(repository, record) -> bytes:
    cached = st.session_state.get("lib_original")
    if cached and cached["id"] == record.id and cached["checksum"] == record.checksum:
        return cached["bytes"]
    content = original_for_display(repository, record)
    st.session_state["lib_original"] = {"id": record.id, "checksum": record.checksum, "bytes": content}
    return content


def _step(delta: int, records, current: int) -> None:
    st.session_state["lib_photo"] = records[(current + delta) % len(records)].id


def _select(record_id: int) -> None:
    st.session_state["lib_photo"] = record_id


def _render_viewer(repository, group, records, access, previews, *, readonly=False) -> None:
    ids = [record.id for record in records]
    selected_id = st.session_state.get("lib_photo")
    index = ids.index(selected_id) if selected_id in ids else 0
    record = records[index]
    image_column, info_column = st.columns([1, 1])
    with image_column:
        if access.get(record.id) is None or not isinstance(previews.get(record.id), bytes):
            st.warning(f"No se puede mostrar esta fotografía: {previews.get(record.id, 'original no accesible')}")
        else:
            try:
                st.image(previews[record.id] if readonly else _original(repository, record), width="stretch")
            except (OSError, ValueError) as error:
                st.error(f"No se puede abrir la imagen: {error}")
    with info_column:
        st.subheader(library.view_name(record.view))
        st.write(f"**Origen:** {record.provider or 'Sin dato'}")
        st.write(f"**Mercado:** {library.market_label(record.market)}")
        st.caption(f"Fotografía {index + 1} de {len(records)}")
        previous, following = st.columns(2)
        previous.button("Anterior", icon=":material/chevron_left:", key="lib_photo_prev", width="stretch",
                        disabled=len(records) < 2, on_click=_step, args=(-1, records, index))
        following.button("Siguiente", icon=":material/chevron_right:", key="lib_photo_next", width="stretch",
                         disabled=len(records) < 2, on_click=_step, args=(1, records, index))
        cached = st.session_state.get("lib_original")
        if not readonly and cached and cached["id"] == record.id:
            st.download_button("Descargar imagen", cached["bytes"], file_name=record.name,
                               mime=MIME.get(Path(record.name).suffix.lower(), "application/octet-stream"),
                               icon=":material/download:", key="lib_download_one", width="stretch")


def _render_zip(repository, group) -> None:
    key = tuple((r.id, r.checksum) for r in group.records)
    prepared = st.session_state.get("lib_zip")
    if prepared and prepared["key"] != key:
        st.session_state.pop("lib_zip")
        prepared = None
    if st.button("Preparar ZIP del producto", icon=":material/folder_zip:", key="lib_prepare_zip",
                 help="Reúne todas las imágenes disponibles de este EAN. Solo se genera al pulsar."):
        st.session_state.pop("lib_multi_zip", None)
        try:
            content = repository.zip_eans_for_user([group.ean], role=st.session_state.get("auth_role"))
        except (OSError, ValueError, PermissionError) as error:
            st.error(f"No se puede preparar el ZIP: {error}")
        else:
            prepared = {"key": key, "bytes": content}
            st.session_state["lib_zip"] = prepared
    if prepared:
        st.download_button("Descargar ZIP", prepared["bytes"], file_name=f"{group.ean}.zip",
                           mime="application/zip", icon=":material/download:", key="lib_zip_download")


def _render_details(group, records) -> None:
    if st.session_state.get("auth_role") != "admin":
        return
    with st.expander("Detalles técnicos y trazabilidad"):
        rows = []
        for record in records:
            identity = record.metadata.get("identity")
            identity = identity if isinstance(identity, dict) else {}
            rows.append({
                "Representación": record.id, "EAN/UPC": record.ean,
                "Modelo": record.metadata.get("modelo") or identity.get("model") or group.model,
                "Color": record.metadata.get("color_proveedor") or record.metadata.get("color")
                or identity.get("color") or "",
                "Vista": library.view_name(record.view), "Archivo original": record.name,
                "Checksum": record.checksum, "Huella visual": record.pixels,
                "Ruta": str(record.path), "Incorporada": record.date,
                "Origen": record.origin, "Proveedor": record.provider,
                "Mercado": library.market_label(record.market),
            })
        frame = pd.DataFrame(rows)
        st.dataframe(frame, hide_index=True, width="stretch")


def _render_product(repository, group) -> None:
    st.button("Volver a la biblioteca", icon=":material/arrow_back:", key="lib_back", on_click=_close)
    subtitle = " · ".join(part for part in (", ".join(group.providers), group.brand, f"EAN {group.ean}") if part)
    design.render(design.page_header_html(group.title, subtitle, (
        (str(len(group.photos)), "fotografías únicas"),
        (str(len(group.records)), "representaciones"),
        (str(len(group.providers)), "origen" if len(group.providers) == 1 else "orígenes"),
    )))
    if group.missing_core:
        design.render(design.notice_html(
            "Vistas sin fotografía: " + ", ".join(group.missing_core)
            + ". Las vistas no se deducen del aspecto de la imagen.", "warning"))
    else:
        design.render(design.notice_html("Disponibles las vistas V1, V2 y V3.", "success"))
    if group.related_eans:
        st.warning("Archivos o huellas coincidentes con otros EAN: " + ", ".join(group.related_eans)
                   + ". Revisar modelo y color; no se han fusionado productos ni eliminado fotografías.")
    if group.visual_equivalents:
        st.info(f"{len(group.visual_equivalents)} grupos de archivos distintos comparten huella visual. "
                "Se conservan todos; la coincidencia no demuestra una asociación correcta.")
    records = list(group.photos)
    access = visible_access(repository, records, "image_gallery_detail_access", selection=group.ean)
    previews = _visible_previews(repository, records, access)
    _render_viewer(repository, group, records, access, previews)
    st.subheader("Fotografías únicas")
    with st.container(horizontal=True, wrap=True, key="lib_thumbs"):
        for record in records:
            with st.container(key=f"lib_thumb_{record.id}"):
                caption = _photo_caption(record)
                design.render(design.photo_html(_preview(previews, record), f"Fotografía {caption}"))
                design.render(design.caption_html(caption))
                st.button("Ver", key=f"lib_photo_{record.id}", width="stretch",
                          on_click=_select, args=(record.id,))
    _render_zip(repository, group)
    _render_details(group, group.records)


def render_images_page(
    render_sidebar_shell: Callable[[str], None],
    require_admin_access: Callable[[], None],
    render_footer: Callable[[], None],
    engine_factory=None,
    return_to_orders=None,
) -> None:
    render_sidebar_shell("Repositorio de imágenes")
    require_admin_access()
    if return_to_orders is not None:
        from app_pages.provider_orders_page import render_contextual_photos

        if render_contextual_photos(return_to_orders):
            render_footer()
            return
    design.inject_styles()
    _keep_widget_state()
    try:
        repository = service().store.repository
        refresh = bool(st.session_state.get("image_gallery_refresh"))
        groups = _load(repository, refresh)
    except (OSError, ValueError, RuntimeError) as error:
        design.render(design.page_header_html(TITLE))
        design.render(design.notice_html(f"Repositorio no disponible: {error}", "error"))
        return
    selected = next((g for g in groups if g.ean == st.session_state.get("lib_ean")), None)
    if st.session_state.get("lib_ean") and selected is None:
        _close()
    if selected is not None:
        _render_product(repository, selected)
    else:
        _header(groups, "Fotografías de producto por EAN, vistas y origen.")
        _render_browser(repository, groups, refresh, engine_factory)
    render_footer()
