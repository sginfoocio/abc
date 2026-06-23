from __future__ import annotations

from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text

from db_loader import load_odoo_dataframe
from engine import run_abcd_engine
from db_config import load_db_config

# ==============================================================================
# CONFIG
# ==============================================================================

APP_TITLE = "📦 Control ABCD de Productos"
APP_ICON = "📊"

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

# ==============================================================================
# SIDEBAR
# ==============================================================================

with st.sidebar:
    st.title("⚙️ Configuración")
    
    page = st.radio(
        "Selecciona una opción:",
        ["📊 Inicio", "🔍 Buscar Producto", "📈 Reportes ABCD", "📉 Análisis Detallado"],
        key="page_selector"
    )
    
    st.divider()
    
    st.markdown("### Información")
    st.info("""
    **Control ABCD de Productos**
    
    Clasifica productos según:
    - **A**: Alta demanda, alto valor
    - **B**: Demanda media, valor medio
    - **C**: Baja demanda, 60+ días sin ventas
    - **D**: Sin demanda, 120+ días sin ventas
    """)

# ==============================================================================
# PÁGINA: INICIO
# ==============================================================================

if page == "📊 Inicio":
    st.title(APP_TITLE)
    
    st.markdown("""
    ### Bienvenido al Sistema de Control ABCD
    
    Este sistema clasifica productos según su demanda y valor, considerando:
    - **Historial de ventas** (últimos 180 días)
    - **Períodos de agotamiento** (stock = 0)
    - **Valor unitario** (PVO)
    - **Capital bloqueado** en inventario
    
    #### Cómo usar:
    1. **Buscar Producto**: Encuentra un producto específico por nombre o código
    2. **Reportes ABCD**: Ve estadísticas por categoría
    3. **Análisis Detallado**: Análisis profundo de un producto
    """)
    
    # Cargar datos
    with st.spinner("Cargando datos..."):
        df = load_data_cached()
        df_classified = run_abcd_engine(df.copy())
    
    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Total Productos",
            f"{len(df_classified):,}",
            delta="Con stock actual"
        )
    
    with col2:
        capital_total = df_classified["Capital_Bloqueado (€)"].sum()
        st.metric(
            "Capital Bloqueado",
            f"€{capital_total:,.0f}",
            delta=f"Total en inventario"
        )
    
    with col3:
        productos_a = len(df_classified[df_classified['ABCD'] == 'A'])
        st.metric(
            "Categoría A",
            productos_a,
            delta="Alta prioridad"
        )
    
    with col4:
        productos_d = len(df_classified[df_classified['ABCD'] == 'D'])
        st.metric(
            "Categoría D",
            productos_d,
            delta="Para liquidar"
        )
    
    # Gráfico de distribución
    st.subheader("Distribución por Categoría")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Gráfico de pastel
        abcd_counts = df_classified['ABCD'].value_counts().sort_index()
        fig = px.pie(
            values=abcd_counts.values,
            names=abcd_counts.index,
            title="Productos por Categoría",
            color_discrete_map={'A': '#00cc96', 'B': '#636EFA', 'C': '#FFA15A', 'D': '#EF553B'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # Gráfico de capital
        capital_by_abcd = df_classified.groupby('ABCD')['Capital_Bloqueado (€)'].sum().sort_index()
        fig = px.bar(
            x=capital_by_abcd.index,
            y=capital_by_abcd.values,
            title="Capital Bloqueado por Categoría",
            labels={'x': 'Categoría', 'y': 'Capital (€)'},
            color=capital_by_abcd.index,
            color_discrete_map={'A': '#00cc96', 'B': '#636EFA', 'C': '#FFA15A', 'D': '#EF553B'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Tabla resumen
    st.subheader("Resumen por Categoría")
    
    summary = df_classified.groupby('ABCD').agg({
        'product_id': 'count',
        'Stock': 'sum',
        'Capital_Bloqueado (€)': 'sum',
        'PVO': 'mean'
    }).round(2)
    summary.columns = ['Productos', 'Stock Total', 'Capital (€)', 'PVO Promedio']
    summary['Stock Total'] = summary['Stock Total'].astype(int)
    summary['Capital (€)'] = summary['Capital (€)'].apply(lambda x: f"€{x:,.0f}")
    summary['PVO Promedio'] = summary['PVO Promedio'].apply(lambda x: f"€{x:,.2f}")
    
    st.dataframe(summary, use_container_width=True)

# ==============================================================================
# PÁGINA: BUSCAR PRODUCTO
# ==============================================================================

elif page == "🔍 Buscar Producto":
    st.title("Buscar Producto")
    
    with st.spinner("Cargando datos..."):
        df = load_data_cached()
        df_classified = run_abcd_engine(df.copy())
    
    # Búsqueda
    col1, col2 = st.columns([3, 1])
    
    with col1:
        search_term = st.text_input(
            "Busca por nombre, código o barcode:",
            placeholder="Ej: DIOR, 192337232893"
        )
    
    with col2:
        search_btn = st.button("🔍 Buscar", use_container_width=True)
    
    if search_term and search_btn:
        # Búsqueda en múltiples campos
        mask = (
            df_classified['Marca'].str.contains(search_term, case=False, na=False) |
            df_classified['Cód Barras'].astype(str).str.contains(search_term, case=False, na=False) |
            df_classified['product_id'].astype(str).str.contains(search_term, case=False, na=False)
        )
        
        results = df_classified[mask]
        
        if len(results) == 0:
            st.warning("No se encontraron productos")
        elif len(results) == 1:
            # Mostrar detalle si hay un solo resultado
            product = results.iloc[0]
            
            st.subheader(f"📦 {product['Marca']}")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("ID", product['product_id'])
            with col2:
                st.metric("Clasificación", product['ABCD'], 
                         delta=product['Motivo'][:30])
            with col3:
                st.metric("Stock", f"{product['Stock']:.0f} ud")
            with col4:
                st.metric("Capital", f"€{product['Capital_Bloqueado (€)']:,.0f}")
            
            st.divider()
            
            # Información detallada
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
            
            # Análisis de agotamientos
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
            
            # Tabla de resultados
            display_cols = ['Marca', 'Cód Barras', 'ABCD', 'Stock', 'PVO', 'Capital_Bloqueado (€)']
            st.dataframe(
                results[display_cols].head(10),
                use_container_width=True,
                hide_index=True
            )

# ==============================================================================
# PÁGINA: REPORTES ABCD
# ==============================================================================

elif page == "📈 Reportes ABCD":
    st.title("Reportes ABCD")
    
    with st.spinner("Cargando datos..."):
        df = load_data_cached()
        df_classified = run_abcd_engine(df.copy())
    
    # Filtros
    col1, col2, col3 = st.columns(3)
    
    with col1:
        selected_abcd = st.multiselect(
            "Categoría ABCD:",
            ['A', 'B', 'C', 'D'],
            default=['A', 'B', 'C', 'D']
        )
    
    with col2:
        min_capital = st.number_input(
            "Capital mínimo (€):",
            min_value=0,
            value=0,
            step=100
        )
    
    with col3:
        max_products = st.number_input(
            "Mostrar máximo:",
            min_value=10,
            value=50,
            step=10
        )
    
    # Filtrar
    filtered = df_classified[
        (df_classified['ABCD'].isin(selected_abcd)) &
        (df_classified['Capital_Bloqueado (€)'] >= min_capital)
    ]
    
    st.subheader(f"Resultados: {len(filtered)} productos")
    
    # Tabs
    tab1, tab2, tab3 = st.tabs(["📊 Tabla", "📈 Gráficos", "💾 Descargar"])
    
    with tab1:
        # Ordenar por capital
        display_filtered = filtered.sort_values('Capital_Bloqueado (€)', ascending=False).head(max_products)
        
        st.dataframe(
            display_filtered[[
                'Marca', 'ABCD', 'Stock', 'PVO', 'Capital_Bloqueado (€)',
                'Num_Ventas_180D', 'Última Venta', 'Accion_Recomendada'
            ]],
            use_container_width=True,
            hide_index=True
        )
    
    with tab2:
        col1, col2 = st.columns(2)
        
        with col1:
            # Stock por categoría
            stock_by_abcd = filtered.groupby('ABCD')['Stock'].sum().sort_index()
            fig = px.bar(
                x=stock_by_abcd.index,
                y=stock_by_abcd.values,
                title="Stock por Categoría",
                labels={'x': 'Categoría', 'y': 'Stock (ud)'}
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            # Top 10 por capital
            top10 = filtered.nlargest(10, 'Capital_Bloqueado (€)')
            fig = px.bar(
                top10,
                x='Capital_Bloqueado (€)',
                y='Marca',
                orientation='h',
                title="Top 10 Capital Bloqueado",
                labels={'Capital_Bloqueado (€)': 'Capital (€)'}
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        # Exportar a CSV
        csv = filtered.to_csv(index=False)
        st.download_button(
            label="📥 Descargar CSV",
            data=csv,
            file_name=f"abcd_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )

# ==============================================================================
# PÁGINA: ANÁLISIS DETALLADO
# ==============================================================================

elif page == "📉 Análisis Detallado":
    st.title("Análisis Detallado de Productos")
    
    with st.spinner("Cargando datos..."):
        df = load_data_cached()
        df_classified = run_abcd_engine(df.copy())
    
    # Seleccionar producto
    st.markdown("### Selecciona un Producto")
    
    selected_product = st.selectbox(
        "Busca por nombre:",
        df_classified.sort_values('Marca')['Marca'].values,
        label_visibility="collapsed"
    )
    
    product = df_classified[df_classified['Marca'] == selected_product].iloc[0]
    
    # Mostrar análisis
    st.subheader(selected_product)
    
    # Métricas principales
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
    
    # Detalles
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
    
    # Agotamientos
    st.divider()
    st.subheader("Períodos de Agotamiento")
    
    engine = get_db_engine()
    periods = get_product_stockout_periods(int(product['product_id']), engine)
    
    if periods:
        cols = st.columns(len(periods))
        for i, (col, period) in enumerate(zip(cols, periods)):
            with col:
                st.info(f"""
                **Período {i+1}**
                
                Inicio: {period['inicio'].strftime('%d/%m/%Y')}
                Fin: {period['fin'].strftime('%d/%m/%Y') if period['fin'] else 'Aún agotado'}
                Duración: **{period['dias']} días**
                """)
    else:
        st.success("✅ Sin agotamientos registrados")

# ==============================================================================
# FOOTER
# ==============================================================================

st.divider()
st.markdown(
    """
    <div style='text-align: center; color: #888; margin-top: 2rem;'>
    <small>Sistema de Control ABCD de Productos | Últimas mejoras: Ventanas de agotamiento, umbrales 120/60 días</small>
    </div>
    """,
    unsafe_allow_html=True
)
