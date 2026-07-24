from __future__ import annotations

from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
import hmac
import json
import os

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text

from db_loader import load_odoo_dataframe
from engine import run_abcd_engine
from db_config import load_db_config, load_env_file
from transform_luxottica_masterdata import (
    build_brand_dictionary_from_db,
    build_color_dictionary_from_db,
    build_executive_summary_markdown,
    transform_masterdata,
)
from validate_masterdata_odoo_dryrun import (
    analyze_masterdata_against_odoo,
    build_summary_markdown,
    load_odoo_snapshot,
)

# ==============================================================================
# CONFIG
# ==============================================================================

APP_TITLE = "Diagonal Eyewear"
APP_ICON = "📊"
AUTH_USERNAME_ENV = "APP_USERNAME"
AUTH_PASSWORD_ENV = "APP_PASSWORD"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==============================================================================
# FUNCIONES AUXILIARES
# ==============================================================================

@st.cache_resource
def get_db_engine():
    """Obtiene conexión a BD (cacheada)"""
    config = load_db_config()
    return create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )

@st.cache_data(ttl=300)  # Cache por 5 minutos
def load_data_cached():
    """Carga datos con cache"""
    return load_odoo_dataframe()


@st.cache_data(ttl=300)
def load_odoo_snapshot_cached():
    """Carga snapshot de Odoo para dry-run de importacion."""
    return load_odoo_snapshot()


@st.cache_data(ttl=300)
def load_masterdata_reference_maps():
    """Carga diccionarios de apoyo para transformacion MASTERDATA."""
    return build_color_dictionary_from_db(), build_brand_dictionary_from_db()


def load_masterdata_file(file_source) -> pd.DataFrame:
    """Carga un Excel MASTERDATA preservando texto."""
    df = pd.read_excel(file_source, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Serializa un DataFrame a Excel en memoria."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buffer.getvalue()


def split_report_warnings(report) -> tuple[list[str], list[str], list[str]]:
    """Separa incidencias bloqueantes, de riesgo medio e informativas para la UI."""
    blocking: list[str] = []
    medium: list[str] = []
    info: list[str] = []

    if report.discarded > 0:
        medium.append(f"Se descartaron {report.discarded} filas durante la transformacion.")
    if report.discarded_accessories > 0:
        info.append(f"{report.discarded_accessories} filas fueron marcadas como accesorios.")
    if report.discarded_invalid_product > 0:
        medium.append(f"{report.discarded_invalid_product} filas no cumplian criterio de producto valido.")
    if report.leading_zero_real_loss_columns > 0:
        blocking.append("Hay perdida real de ceros iniciales en al menos una columna critica.")
    if report.invalid_yes_no_rows > 0:
        blocking.append(f"Hay {report.invalid_yes_no_rows} filas con valores invalidos SI/NO.")
    if report.unmatched_brand_names:
        medium.append(f"Marcas con incidencia abierta: {', '.join(report.unmatched_brand_names)}")
    for key, values in report.anomalous_values.items():
        blocking.append(f"Valores anómalos en {key}: {', '.join(values)}")

    return blocking, medium, info

def _get_auth_credentials() -> tuple[str, str]:
    """Obtiene credenciales desde variables de entorno."""
    load_env_file()
    username = os.getenv(AUTH_USERNAME_ENV, "")
    password = os.getenv(AUTH_PASSWORD_ENV, "")
    return username, password

def _render_login() -> None:
    st.title(APP_TITLE)
    st.subheader("Acceso restringido")

    expected_user, expected_password = _get_auth_credentials()
    if not expected_user or not expected_password:
        st.error(
            f"Autenticación no configurada. Define {AUTH_USERNAME_ENV} y {AUTH_PASSWORD_ENV}."
        )
        st.stop()

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar")

    if submitted:
        valid_user = hmac.compare_digest(username, expected_user)
        valid_password = hmac.compare_digest(password, expected_password)
        if valid_user and valid_password:
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = username
            st.rerun()
        st.error("Usuario o contraseña incorrectos")

    st.stop()

def require_authentication() -> None:
    """Bloquea la app hasta que el usuario se autentique."""
    if not st.session_state.get("authenticated", False):
        _render_login()

def get_product_stockout_periods(product_id: int, engine) -> list:
    """Obtiene períodos de agotamiento de un producto"""
    query = text("""
        WITH stock_moves AS (
            SELECT 
                sm.date,
                sm.product_qty,
                CASE 
                    WHEN sl_src.usage = 'supplier' AND sl_dst.usage = 'internal' THEN 'ENTRADA'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'customer' THEN 'SALIDA'
                    ELSE 'OTRO'
                END as tipo
            FROM stock_move sm
            JOIN stock_location sl_src ON sm.location_id = sl_src.id
            JOIN stock_location sl_dst ON sm.location_dest_id = sl_dst.id
            WHERE sm.product_id = :product_id AND sm.state = 'done'
              AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
            ORDER BY sm.date ASC
        )
        SELECT date, tipo, product_qty FROM stock_moves
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"product_id": int(product_id)})
        rows = result.fetchall()
    
    if not rows:
        return []
    
    # Calcular períodos de agotamiento
    stock = 0
    periods = []
    stockout_start = None
    
    for row in rows:
        fecha, tipo, cantidad = row
        
        if tipo == 'ENTRADA':
            stock += cantidad
            if stockout_start:
                periods.append({
                    'inicio': stockout_start,
                    'fin': fecha,
                    'dias': (fecha - stockout_start).days
                })
                stockout_start = None
        elif tipo == 'SALIDA':
            stock -= cantidad
        
        if stock <= 0 and not stockout_start:
            stockout_start = fecha
    
    if stockout_start:
        periods.append({
            'inicio': stockout_start,
            'fin': None,
            'dias': (datetime.now() - stockout_start.replace(tzinfo=None)).days
        })
    
    return periods

def render_sidebar_shell(section_name: str) -> None:
    with st.sidebar:
        st.title("⚙️ Configuración")
        st.caption(f"Sesión: {st.session_state.get('auth_user', 'usuario')}")
        st.caption(f"Área: {section_name}")
        if st.button("Cerrar sesión"):
            st.session_state["authenticated"] = False
            st.session_state.pop("auth_user", None)
            st.rerun()
        st.divider()
        st.markdown("### Información")
        st.info(
            """
            **Diagonal Eyewear**

            - Área ABC para análisis comercial.
            - Área Masterdata para transformación y validación previa a Odoo.
            """
        )


def render_footer() -> None:
    st.divider()
    st.markdown(
        """
        <div style='text-align: center; color: #888; margin-top: 2rem;'>
        <small>Diagonal Eyewear | Plataforma ABC y Masterdata</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_home_page() -> None:
    render_sidebar_shell("Inicio")
    st.title(APP_TITLE)
    st.subheader("Portal interno")
    st.write("Selecciona una de las dos áreas principales.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Área ABC")
        st.write("Análisis ABCD, búsqueda, reportes y detalle de producto.")
        st.page_link(ABC_PAGE, label="Entrar en ABC", use_container_width=True)
    with col2:
        st.markdown("### Área Masterdata")
        st.write("Transformación de Luxottica, advertencias, descargas y dry-run Odoo.")
        st.page_link(MASTER_PAGE, label="Entrar en Masterdata", use_container_width=True)

    render_footer()


def render_abc_page() -> None:
    render_sidebar_shell("ABC")
    with st.sidebar:
        abc_page = st.radio(
            "Sección ABC:",
            ["📊 Inicio", "🔍 Buscar Producto", "📈 Reportes ABCD", "📉 Análisis Detallado"],
            key="abc_page_selector",
        )

    if abc_page == "📊 Inicio":
        st.title("ABC")
        st.markdown(
            """
            ### Bienvenido al sistema ABCD

            Este sistema clasifica productos según su demanda y valor, considerando:
            - **Historial de ventas** (últimos 180 días)
            - **Períodos de agotamiento** (stock = 0)
            - **Valor unitario** (PVO)
            - **Capital bloqueado** en inventario
            """
        )

        with st.spinner("Cargando datos..."):
            df = load_data_cached()
            df_classified = run_abcd_engine(df.copy())

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Productos", f"{len(df_classified):,}", delta="Con stock actual")
        with col2:
            capital_total = df_classified["Capital_Bloqueado (€)"].sum()
            st.metric("Capital Bloqueado", f"€{capital_total:,.0f}", delta="Total en inventario")
        with col3:
            productos_a = len(df_classified[df_classified['ABCD'] == 'A'])
            st.metric("Categoría A", productos_a, delta="Alta prioridad")
        with col4:
            productos_d = len(df_classified[df_classified['ABCD'] == 'D'])
            st.metric("Categoría D", productos_d, delta="Para liquidar")

        st.subheader("Distribución por Categoría")
        col1, col2 = st.columns(2)
        with col1:
            abcd_counts = df_classified['ABCD'].value_counts().sort_index()
            fig = px.pie(
                values=abcd_counts.values,
                names=abcd_counts.index,
                title="Productos por Categoría",
                color_discrete_map={'A': '#00cc96', 'B': '#636EFA', 'C': '#FFA15A', 'D': '#EF553B'},
            )
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            capital_by_abcd = df_classified.groupby('ABCD')['Capital_Bloqueado (€)'].sum().sort_index()
            fig = px.bar(
                x=capital_by_abcd.index,
                y=capital_by_abcd.values,
                title="Capital Bloqueado por Categoría",
                labels={'x': 'Categoría', 'y': 'Capital (€)'},
                color=capital_by_abcd.index,
                color_discrete_map={'A': '#00cc96', 'B': '#636EFA', 'C': '#FFA15A', 'D': '#EF553B'},
            )
            st.plotly_chart(fig, use_container_width=True)

        summary = df_classified.groupby('ABCD').agg({
            'product_id': 'count',
            'Stock': 'sum',
            'Capital_Bloqueado (€)': 'sum',
            'PVO': 'mean',
        }).round(2)
        summary.columns = ['Productos', 'Stock Total', 'Capital (€)', 'PVO Promedio']
        summary['Stock Total'] = summary['Stock Total'].astype(int)
        summary['Capital (€)'] = summary['Capital (€)'].apply(lambda x: f"€{x:,.0f}")
        summary['PVO Promedio'] = summary['PVO Promedio'].apply(lambda x: f"€{x:,.2f}")
        st.subheader("Resumen por Categoría")
        st.dataframe(summary, use_container_width=True)

    elif abc_page == "🔍 Buscar Producto":
        st.title("Buscar Producto")
        with st.spinner("Cargando datos..."):
            df = load_data_cached()
            df_classified = run_abcd_engine(df.copy())

        col1, col2 = st.columns([3, 1])
        with col1:
            search_term = st.text_input("Busca por nombre, código o barcode:", placeholder="Ej: DIOR, 192337232893")
        with col2:
            search_btn = st.button("🔍 Buscar", use_container_width=True)

        if search_term and search_btn:
            mask = (
                df_classified['Marca'].str.contains(search_term, case=False, na=False)
                | df_classified['Cód Barras'].astype(str).str.contains(search_term, case=False, na=False)
                | df_classified['product_id'].astype(str).str.contains(search_term, case=False, na=False)
            )
            results = df_classified[mask]
            if len(results) == 0:
                st.warning("No se encontraron productos")
            elif len(results) == 1:
                product = results.iloc[0]
                st.subheader(f"📦 {product['Marca']}")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("ID", product['product_id'])
                with col2:
                    st.metric("Clasificación", product['ABCD'], delta=product['Motivo'][:30])
                with col3:
                    st.metric("Stock", f"{product['Stock']:.0f} ud")
                with col4:
                    st.metric("Capital", f"€{product['Capital_Bloqueado (€)']:,.0f}")
                st.divider()
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Información Comercial**")
                    st.write(f"- **Código**: {product['Cód Barras']}")
                    st.write(f"- **PVO**: €{product['PVO']:.2f}")
                    st.write(f"- **Primera Compra**: {product['Primera Compra']}")
                    st.write(f"- **Última Compra**: {product['Última Compra']}")
                with col2:
                    st.markdown("**Actividad Reciente**")
                    st.write(f"- **Última Venta**: {product['Última Venta']}")
                    st.write(f"- **Ventas 180 días**: {product['Num_Ventas_180D']:.0f}")
                    st.write(f"- **Unidades (180d)**: {product['Ventas_180_Dias']:.0f}")
                    st.write(f"- **Última Reposición**: {product.get('Última Reposicion', 'N/A')}")
                st.divider()
                st.subheader("Períodos de Agotamiento")
                engine = get_db_engine()
                periods = get_product_stockout_periods(int(product['product_id']), engine)
                if periods:
                    for i, period in enumerate(periods, 1):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.write(f"**Período {i}**")
                            st.write(f"Inicio: {period['inicio']}")
                        with col2:
                            st.write(f"Fin: {period['fin'] if period['fin'] else 'Aún agotado'}")
                        with col3:
                            st.write(f"**Duración: {period['dias']} días**")
                else:
                    st.info("No hay períodos de agotamiento registrados")
            else:
                st.info(f"Se encontraron {len(results)} productos. Mostrando los primeros 10:")
                display_cols = ['Marca', 'Cód Barras', 'ABCD', 'Stock', 'PVO', 'Capital_Bloqueado (€)']
                st.dataframe(results[display_cols].head(10), use_container_width=True, hide_index=True)

    elif abc_page == "📈 Reportes ABCD":
        st.title("Reportes ABCD")
        with st.spinner("Cargando datos..."):
            df = load_data_cached()
            df_classified = run_abcd_engine(df.copy())

        col1, col2, col3 = st.columns(3)
        with col1:
            selected_abcd = st.multiselect("Categoría ABCD:", ['A', 'B', 'C', 'D'], default=['A', 'B', 'C', 'D'])
        with col2:
            min_capital = st.number_input("Capital mínimo (€):", min_value=0, value=0, step=100)
        with col3:
            max_products = st.number_input("Mostrar máximo:", min_value=10, value=50, step=10)

        filtered = df_classified[
            (df_classified['ABCD'].isin(selected_abcd))
            & (df_classified['Capital_Bloqueado (€)'] >= min_capital)
        ]
        filtered_report = filtered.copy()
        if ('EAN' not in filtered_report.columns or filtered_report['EAN'].isna().all()) and 'Cód Barras' in filtered_report.columns:
            filtered_report['EAN'] = filtered_report['Cód Barras']
        st.subheader(f"Resultados: {len(filtered)} productos")
        tab1, tab2, tab3 = st.tabs(["📊 Tabla", "📈 Gráficos", "💾 Descargar"])
        with tab1:
            display_filtered = filtered_report.sort_values('Capital_Bloqueado (€)', ascending=False).head(max_products)
            st.dataframe(
                display_filtered[[
                    'Marca', 'Modelo', 'EAN', 'ABCD', 'Stock', 'PVO', 'Capital_Bloqueado (€)',
                    'Num_Ventas_180D', 'Última Venta', 'Accion_Recomendada',
                ]],
                use_container_width=True,
                hide_index=True,
            )
        with tab2:
            col1, col2 = st.columns(2)
            with col1:
                stock_by_abcd = filtered.groupby('ABCD')['Stock'].sum().sort_index()
                fig = px.bar(x=stock_by_abcd.index, y=stock_by_abcd.values, title="Stock por Categoría", labels={'x': 'Categoría', 'y': 'Stock (ud)'})
                st.plotly_chart(fig, use_container_width=True)
            with col2:
                top10 = filtered.nlargest(10, 'Capital_Bloqueado (€)')
                fig = px.bar(top10, x='Capital_Bloqueado (€)', y='Marca', orientation='h', title="Top 10 Capital Bloqueado", labels={'Capital_Bloqueado (€)': 'Capital (€)'})
                st.plotly_chart(fig, use_container_width=True)
        with tab3:
            csv = filtered_report.to_csv(index=False)
            st.download_button("📥 Descargar CSV", data=csv, file_name=f"abcd_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv")

    else:
        st.title("Análisis Detallado de Productos")
        with st.spinner("Cargando datos..."):
            df = load_data_cached()
            df_classified = run_abcd_engine(df.copy())
        st.markdown("### Selecciona un Producto")
        selected_product = st.selectbox("Busca por nombre:", df_classified.sort_values('Marca')['Marca'].values, label_visibility="collapsed")
        product = df_classified[df_classified['Marca'] == selected_product].iloc[0]
        st.subheader(selected_product)
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("ABCD", product['ABCD'])
        with col2:
            st.metric("Stock", f"{product['Stock']:.0f}")
        with col3:
            st.metric("PVO", f"€{product['PVO']:.2f}")
        with col4:
            st.metric("Capital", f"€{product['Capital_Bloqueado (€)']:,.0f}")
        with col5:
            st.metric("Ventas 180d", f"{product['Num_Ventas_180D']:.0f}")
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Histórico**")
            st.write(f"- Primera Compra: {product['Primera Compra']}")
            st.write(f"- Última Compra: {product['Última Compra']}")
            st.write(f"- Última Venta: {product['Última Venta']}")
            st.write(f"- Última Reposición: {product.get('Última Reposicion', 'N/A')}")
        with col2:
            st.markdown("**Clasificación**")
            st.write(f"- Motivo: {product['Motivo']}")
            st.write(f"- Alerta: {product['Alerta']}")
            st.write(f"- Acción: {product['Accion_Recomendada']}")
        st.divider()
        st.subheader("Períodos de Agotamiento")
        engine = get_db_engine()
        periods = get_product_stockout_periods(int(product['product_id']), engine)
        if periods:
            cols = st.columns(len(periods))
            for i, (col, period) in enumerate(zip(cols, periods)):
                with col:
                    st.info(
                        f"""
                        **Período {i+1}**

                        Inicio: {period['inicio'].strftime('%d/%m/%Y')}
                        Fin: {period['fin'].strftime('%d/%m/%Y') if period['fin'] else 'Aún agotado'}
                        Duración: **{period['dias']} días**
                        """
                    )
        else:
            st.success("✅ Sin agotamientos registrados")

    render_footer()


def render_master_page() -> None:
    render_sidebar_shell("Masterdata")
    with st.sidebar:
        master_page = st.radio(
            "Sección Masterdata:",
            ["📥 Importador Masterdata", "🧪 Dry-run Odoo"],
            key="master_page_selector",
        )

    masterdata_dir = Path(__file__).resolve().parent / "docs" / "MasterData"
    available_files = sorted(masterdata_dir.glob("*transformado.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)

    if master_page == "📥 Importador Masterdata":
        st.title("Importador Masterdata")
        st.caption("Flujo web: cargar Excel origen, transformar, revisar advertencias y descargar el MASTERDATA.")
        st.subheader("1. Cargar Excel origen Luxottica")
        uploaded_source = st.file_uploader("Sube el Excel origen", type=["xlsx"], key="masterdata_source_upload")
        use_whitelist = st.checkbox("Aplicar whitelist de accesorios local", value=True)

        if uploaded_source is not None and st.button("Transformar fichero", type="primary"):
            with st.spinner("Transformando MASTERDATA..."):
                source_df = load_masterdata_file(uploaded_source)
                color_map, brand_map = load_masterdata_reference_maps()
                whitelist_codes: set[str] = set()
                whitelist_path = masterdata_dir / "accessory_whitelist_codes.txt"
                if use_whitelist and whitelist_path.exists():
                    whitelist_codes = {
                        line.strip().upper()
                        for line in whitelist_path.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.strip().startswith("#")
                    }
                output_df, transform_report, discarded_audit, zero_audit_df, brand_audit_df = transform_masterdata(
                    source_df,
                    color_map,
                    accessory_whitelist=whitelist_codes,
                    brand_map=brand_map,
                )
                st.session_state["masterdata_transform_output_df"] = output_df
                st.session_state["masterdata_transform_report"] = transform_report.to_dict()
                st.session_state["masterdata_transform_discarded_df"] = discarded_audit
                st.session_state["masterdata_transform_zero_df"] = zero_audit_df
                st.session_state["masterdata_transform_brand_df"] = brand_audit_df
                st.session_state["masterdata_transform_source_name"] = uploaded_source.name

        output_df = st.session_state.get("masterdata_transform_output_df")
        report_dict = st.session_state.get("masterdata_transform_report")
        discarded_audit = st.session_state.get("masterdata_transform_discarded_df")
        zero_audit_df = st.session_state.get("masterdata_transform_zero_df")
        brand_audit_df = st.session_state.get("masterdata_transform_brand_df")
        source_name = st.session_state.get("masterdata_transform_source_name", "origen.xlsx")

        if output_df is not None and report_dict is not None:
            st.subheader("2. Advertencias y resultado")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Procesados", report_dict["total_input"])
            with col2:
                st.metric("Exportados", report_dict["total_output"])
            with col3:
                st.metric("Descartados", report_dict["discarded"])
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Marcas no resueltas", len(report_dict["unmatched_brand_names"]))
            with col2:
                st.metric("Filas inválidas SI/NO", report_dict["invalid_yes_no_rows"])
            with col3:
                st.metric("Pérdida real de ceros", report_dict["leading_zero_real_loss_columns"])

            blocking_warnings, medium_warnings, info_warnings = split_report_warnings(type("ReportProxy", (), report_dict)())
            if blocking_warnings:
                st.error("Avisos bloqueantes")
                for line in blocking_warnings:
                    st.error(line)
            if medium_warnings:
                st.warning("Avisos de riesgo medio")
                for line in medium_warnings:
                    st.warning(line)
            if info_warnings:
                st.info("Avisos informativos")
                for line in info_warnings:
                    st.info(line)
            if not blocking_warnings and not medium_warnings and not info_warnings:
                st.success("Transformación completada sin incidencias críticas ni advertencias abiertas.")
            elif not blocking_warnings:
                st.success("Transformación completada sin incidencias bloqueantes.")

            st.subheader("Campos de lentes")
            nonempty_lens_material = int((output_df["Material de las lentes"].astype(str).str.strip() != "").sum())
            nonempty_lens_color = int((output_df["Color de las lentes"].astype(str).str.strip() != "").sum())
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Material de las lentes informado", nonempty_lens_material)
            with col2:
                st.metric("Color de las lentes informado", nonempty_lens_color)
            if nonempty_lens_material == 0:
                st.info("El Excel origen no trae una columna de material de lente. Por eso `Material de las lentes` queda vacío salvo que se defina una regla de negocio adicional.")
            st.dataframe(
                output_df[["Código del modelo", "Categoría", "Material de las lentes", "Color de las lentes"]].head(50),
                use_container_width=True,
                hide_index=True,
            )

            st.subheader("Vista previa del MASTERDATA")
            st.dataframe(output_df.head(50), use_container_width=True, hide_index=True)

            executive_summary = build_executive_summary_markdown(
                type("ReportProxy", (), {**report_dict, "to_dict": lambda self=None: report_dict})(),
                Path(source_name),
                Path(source_name).with_name(f"{Path(source_name).stem}_transformado.xlsx"),
                None,
            )
            transformed_excel = dataframe_to_excel_bytes(output_df)
            discarded_csv = discarded_audit.to_csv(index=False) if discarded_audit is not None else ""
            zero_csv = zero_audit_df.to_csv(index=False) if zero_audit_df is not None else ""
            brand_csv = brand_audit_df.to_csv(index=False) if brand_audit_df is not None else ""
            report_json = json.dumps(report_dict, ensure_ascii=False, indent=2)

            st.subheader("3. Descargas")
            col1, col2 = st.columns(2)
            with col1:
                st.download_button("📥 Descargar fichero transformado (.xlsx)", data=transformed_excel, file_name=f"{Path(source_name).stem}_transformado.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.download_button("📥 Descargar reporte JSON", data=report_json, file_name=f"{Path(source_name).stem}_validacion.json", mime="application/json")
                st.download_button("📥 Descargar resumen ejecutivo (.md)", data=executive_summary, file_name=f"{Path(source_name).stem}_resumen_ejecutivo.md", mime="text/markdown")
            with col2:
                st.download_button("📥 Descargar auditoría descartes (.csv)", data=discarded_csv, file_name=f"{Path(source_name).stem}_descartes_auditoria.csv", mime="text/csv")
                st.download_button("📥 Descargar auditoría ceros (.csv)", data=zero_csv, file_name=f"{Path(source_name).stem}_ceros_iniciales_resumen.csv", mime="text/csv")
                st.download_button("📥 Descargar auditoría marcas (.csv)", data=brand_csv, file_name=f"{Path(source_name).stem}_marcas_auditoria.csv", mime="text/csv")

    else:
        st.title("Dry-run Odoo")
        st.caption("Usa un MASTERDATA ya transformado para clasificar altas y actualizaciones sin grabar nada.")
        output_df = st.session_state.get("masterdata_transform_output_df")
        source_name = st.session_state.get("masterdata_transform_source_name", "origen.xlsx")
        source_mode = st.radio(
            "Origen del MASTERDATA:",
            ["Usar fichero generado", "Usar último transformado en esta sesión", "Subir Excel"],
            horizontal=True,
        )
        selected_label = ""
        masterdata_df = None
        if source_mode == "Usar fichero generado":
            if not available_files:
                st.warning("No hay ficheros MASTERDATA transformados disponibles en docs/MasterData.")
            else:
                selected_path = st.selectbox("Selecciona un fichero:", available_files, format_func=lambda p: p.name)
                selected_label = str(selected_path)
                if st.button("Ejecutar dry-run", type="primary", key="dryrun_generated"):
                    with st.spinner("Leyendo MASTERDATA y snapshot de Odoo..."):
                        masterdata_df = load_masterdata_file(selected_path)
        elif source_mode == "Usar último transformado en esta sesión":
            if output_df is None:
                st.info("Todavía no has transformado ningún fichero en esta sesión.")
            else:
                selected_label = f"sesion::{source_name}"
                if st.button("Ejecutar dry-run", type="primary", key="dryrun_session"):
                    masterdata_df = output_df.copy()
        else:
            uploaded_file = st.file_uploader("Sube un Excel MASTERDATA transformado", type=["xlsx"], key="dryrun_upload")
            if uploaded_file is not None and st.button("Ejecutar dry-run", type="primary", key="dryrun_upload_btn"):
                selected_label = uploaded_file.name
                with st.spinner("Leyendo MASTERDATA subido y snapshot de Odoo..."):
                    masterdata_df = load_masterdata_file(uploaded_file)

        if masterdata_df is not None:
            required_cols = {"UPC", "Nombre de la marca", "Código del modelo"}
            missing_cols = sorted(required_cols - set(masterdata_df.columns))
            if missing_cols:
                st.error(f"El fichero no parece un MASTERDATA válido. Faltan columnas: {', '.join(missing_cols)}")
            else:
                with st.spinner("Comparando contra Odoo..."):
                    odoo_barcodes_df, brand_map = load_odoo_snapshot_cached()
                    detail_df, report = analyze_masterdata_against_odoo(masterdata_df, odoo_barcodes_df, brand_map)
                    summary_md = build_summary_markdown(report, Path(selected_label), None)
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Altas potenciales", report.creates)
                with col2:
                    st.metric("Actualizaciones potenciales", report.updates)
                with col3:
                    st.metric("Conflictos", report.conflicts)
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Marcas no resueltas", report.unresolved_brands)
                with col2:
                    st.metric("Duplicados en fichero", report.duplicate_barcodes_in_file)
                with col3:
                    st.metric("Registros evaluados", report.total_rows)
                if report.conflicts == 0 and report.duplicate_barcodes_in_file == 0:
                    st.success("Dry-run técnico correcto: no hay conflictos de barcode ni duplicados internos.")
                else:
                    st.warning("El lote requiere revisión antes de importar en Odoo.")
                st.subheader("Resumen")
                st.markdown(summary_md)
                st.subheader("Detalle")
                action_filter = st.multiselect("Filtrar acciones:", ["create", "update", "conflict"], default=["create", "update", "conflict"], key="dryrun_action_filter")
                filtered_detail = detail_df[detail_df["dry_run_action"].isin(action_filter)].copy()
                st.dataframe(filtered_detail, use_container_width=True, hide_index=True)
                st.subheader("Descargas")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button("📥 Descargar detalle CSV", data=filtered_detail.to_csv(index=False), file_name=f"dryrun_odoo_detalle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv")
                with col2:
                    st.download_button("📥 Descargar reporte JSON", data=json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file_name=f"dryrun_odoo_reporte_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", mime="application/json")
                with col3:
                    st.download_button("📥 Descargar resumen MD", data=summary_md, file_name=f"dryrun_odoo_resumen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md", mime="text/markdown")

    render_footer()


require_authentication()

HOME_PAGE = st.Page(render_home_page, title="Inicio", icon="🏠", url_path="", default=True)
ABC_PAGE = st.Page(render_abc_page, title="ABC", icon="📊", url_path="abc")
MASTER_PAGE = st.Page(render_master_page, title="Masterdata", icon="📥", url_path="master")

navigation = st.navigation([HOME_PAGE, ABC_PAGE, MASTER_PAGE], position="sidebar")

navigation.run()
