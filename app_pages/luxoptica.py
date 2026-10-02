from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st


def render_images_page(
    render_sidebar_shell: Callable[[str], None],
    require_admin_access: Callable[[], None],
    render_footer: Callable[[], None],
    load_image_catalog: Callable[[Path], object],
    application_root: Path,
) -> None:
    render_sidebar_shell("Repositorio de imágenes")
    require_admin_access()
    st.title("Imágenes")
    st.caption("Explora las imágenes descargadas por modelo, EAN y mercado.")

    images_root = application_root / "repo" / "images"
    image_catalog = load_image_catalog(images_root)
    st.subheader("Visor de imágenes")
    search_ean = st.text_input(
        "Buscar por EAN",
        placeholder="Introduce el EAN completo o una parte",
        key="luxoptica_search_ean",
    ).strip()
    market_filter = st.selectbox(
        "Mercado",
        options=["Todos", "Original", "Farfetch", "Miinto"],
        key="luxoptica_market_filter",
    )

    filtered_catalog = image_catalog
    if search_ean:
        filtered_catalog = filtered_catalog[
            filtered_catalog["EAN"].str.contains(search_ean, case=False, na=False)
        ]
    if market_filter != "Todos":
        filtered_catalog = filtered_catalog[filtered_catalog["Mercado"] == market_filter]

    if filtered_catalog.empty:
        st.info("No hay imágenes que coincidan con la búsqueda.")
    else:
        st.caption(
            f"{len(filtered_catalog):,} imágenes | "
            f"{filtered_catalog['EAN'].nunique():,} EAN | "
            f"{filtered_catalog['Modelo'].nunique():,} modelos"
        )
        page_size = 24
        total_pages = max(1, (len(filtered_catalog) + page_size - 1) // page_size)
        current_page = min(st.session_state.get("luxoptica_gallery_page", 1), total_pages)
        page_column, size_column = st.columns([3, 1])
        with page_column:
            current_page = st.number_input(
                "Página",
                min_value=1,
                max_value=total_pages,
                value=current_page,
                step=1,
                key="luxoptica_gallery_page_input",
            )
        with size_column:
            st.caption(f"{total_pages} página(s) de {page_size} miniaturas")
        st.session_state["luxoptica_gallery_page"] = int(current_page)

        start = (int(current_page) - 1) * page_size
        page_catalog = filtered_catalog.iloc[start : start + page_size]
        thumbnail_columns = st.columns(4)
        for position, (row_index, image_row) in enumerate(page_catalog.iterrows()):
            with thumbnail_columns[position % 4]:
                st.image(image_row["Ruta"], width="stretch")
                st.caption(
                    f"{image_row['EAN']} · {image_row['Mercado']}\n"
                    f"{image_row['Archivo']} · {image_row['Fecha']}"
                )
                if st.button(
                    "Ver imagen",
                    key=f"luxoptica_view_{row_index}",
                    width="stretch",
                ):
                    st.session_state["luxoptica_selected_image"] = str(image_row["Ruta"])
                    st.rerun()

        selected_path = st.session_state.get("luxoptica_selected_image")
        if selected_path and selected_path in set(filtered_catalog["Ruta"]):
            selected_row = filtered_catalog[filtered_catalog["Ruta"] == selected_path].iloc[0]
            st.markdown("#### Imagen ampliada")
            viewer_column, details_column = st.columns([2, 1])
            with viewer_column:
                st.image(selected_path, caption=selected_row["Archivo"], width="stretch")
            with details_column:
                st.write(f"**Modelo:** {selected_row['Modelo']}")
                st.write(f"**EAN:** {selected_row['EAN']}")
                st.write(f"**Mercado:** {selected_row['Mercado']}")
                st.write(f"**Fecha:** {selected_row['Fecha']}")
                st.write(f"**Archivo:** {selected_row['Archivo']}")

        st.markdown("#### Estructura encontrada")
        st.dataframe(
            filtered_catalog[["Modelo", "EAN", "Mercado", "Archivo", "Fecha"]],
            hide_index=True,
            width="stretch",
        )

    render_footer()


def render_pending_page(
    render_sidebar_shell: Callable[[str], None],
    require_admin_access: Callable[[], None],
    render_footer: Callable[[], None],
    render_pending_panel: Callable[[], None],
) -> None:
    render_sidebar_shell("Repositorio de imágenes")
    require_admin_access()
    st.title("Pendiente Luxoptica")
    st.caption("Revisa y procesa manualmente las copias pendientes para Farfetch y Miinto.")
    render_pending_panel()
    render_footer()