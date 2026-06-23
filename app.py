from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from db_loader import load_odoo_dataframe
from engine import load_input_file, run_abcd_engine


APP_TITLE = "📦 Control ABCD de Productos"
APP_ICON = "📊"
LOCAL_INPUT_NAME = "input.xlsx"
DATA_SOURCE_OPTIONS = ["Excel", "Base de Datos"]


def configure_page() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        layout="wide",
    )


def load_excel_dataframe(uploaded_file_bytes: bytes | None, local_path: str) -> pd.DataFrame:
    if uploaded_file_bytes is not None:
        return load_input_file(BytesIO(uploaded_file_bytes))

    path = Path(local_path)
    if path.exists():
        return load_input_file(path)

    raise FileNotFoundError(
        "No se encontró input.xlsx y no se ha subido ningún archivo."
    )


def load_data(uploaded_file, local_path: Path, data_source: str) -> pd.DataFrame:
    if data_source == "Excel":
        uploaded_file_bytes = uploaded_file.read() if uploaded_file is not None else None
        return load_excel_dataframe(uploaded_file_bytes, str(local_path))

    if data_source == "Base de Datos":
        return load_odoo_dataframe()

    raise ValueError(f"Origen de datos desconocido: {data_source}")


def prepare_dataframe(
    raw_df: pd.DataFrame,
    days_without_sales_for_d: int,
    reference_date: pd.Timestamp,
) -> pd.DataFrame:
    df = run_abcd_engine(raw_df, days_without_sales_for_d, reference_date)

    if "PVO sin descuento" not in df.columns:
        df["PVO sin descuento"] = df["PVO"]

    return add_weekly_rotation_index(df, reference_date)


def add_weekly_rotation_index(
    df: pd.DataFrame,
    reference_date: pd.Timestamp,
) -> pd.DataFrame:
    df = df.copy()
    if "Indice_de_Rotacion_Semanal" in df.columns:
        return df

    today = (
        pd.Timestamp(reference_date).normalize()
        if reference_date is not None
        else pd.Timestamp.today().normalize()
    )

    if "Ventas_7_Dias" in df.columns:
        df["Indice_de_Rotacion_Semanal"] = (
            df["Ventas_7_Dias"] / df["Stock"].replace({0: np.nan})
        )
    else:
        df["Última Venta"] = pd.to_datetime(df["Última Venta"], errors="coerce")
        days_since_last_sale = (today - df["Última Venta"].dt.normalize()).dt.days
        df["Indice_de_Rotacion_Semanal"] = np.where(
            df["Stock"] > 0,
            np.where(
                df["Última Venta"].notna(),
                np.where(days_since_last_sale > 0, 7 / days_since_last_sale, 7.0),
                0.0,
            ),
            np.nan,
        )

    return df


def format_currency(value: float) -> str:
    if pd.isna(value):
        return ""

    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)

    formatted = f"{amount:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def dataframe_to_csv(dataframe: pd.DataFrame) -> bytes:
    csv_text = dataframe.to_csv(sep=";", decimal=",", index=False)
    return csv_text.encode("utf-8")


def save_snapshot(dataframe: pd.DataFrame, analysis_date: pd.Timestamp) -> Path:
    snapshot_dir = Path(__file__).resolve().parent / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_df = dataframe.copy()
    snapshot_df["Fecha_Analisis"] = analysis_date.strftime("%Y-%m-%d")
    snapshot_path = snapshot_dir / f"abc_snapshot_{analysis_date.strftime('%Y%m%d')}.csv"
    snapshot_df.to_csv(snapshot_path, sep=";", decimal=",", index=False, encoding="utf-8")
    return snapshot_path


def style_table(df: pd.DataFrame) -> pd.DataFrame.style:
    pd.set_option("styler.render.max_elements", len(df) * len(df.columns))
    
    abcd_palette = {
        "A": "background-color: #e63946; color: white",
        "B": "background-color: #fca311; color: black",
        "C": "background-color: #ffdd57; color: black",
        "D": "background-color: #212121; color: white",
    }
    alert_palette = {
        "🔴 LIQUIDAR": "background-color: #d62828; color: white",
        "🟠 REVISAR": "background-color: #f77f00; color: white",
        "🟢 OK": "background-color: #2a9d8f; color: white",
        "⚪ BAJO IMPACTO": "background-color: #8d99ae; color: white",
    }

    def abcd_style(value):
        return abcd_palette.get(value, "")

    def alert_style(value):
        return alert_palette.get(value, "")

    styled = df.style
    styled = styled.map(abcd_style, subset=["ABCD"])
    styled = styled.map(alert_style, subset=["Alerta"])
    styled = styled.format(
        {
            "PVO": lambda v: format_currency(v),
            "PVO sin descuento": lambda v: format_currency(v),
            "Capital_Bloqueado (€)": lambda v: format_currency(v),
            "Capital sin dto": lambda v: format_currency(v),
            "Indice_de_Rotacion_Semanal": lambda v: (
                ""
                if pd.isna(v)
                else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            ),
        }
    )
    styled = styled.set_properties(**{"text-align": "center"})
    return styled


def build_sidebar_filters(df: pd.DataFrame) -> tuple[list[str], list[str], bool, bool]:
    st.sidebar.header("🔎 Filtros")

    selected_abcd = st.sidebar.multiselect(
        "ABCD",
        options=sorted(df["ABCD"].dropna().unique()),
        default=sorted(df["ABCD"].dropna().unique()),
    )

    selected_alerts = st.sidebar.multiselect(
        "Alerta",
        options=sorted(df["Alerta"].dropna().unique()),
        default=sorted(df["Alerta"].dropna().unique()),
    )

    only_d = st.sidebar.checkbox("Solo productos D")
    only_review = st.sidebar.checkbox("Solo productos a revisar (≤ 15 días)")

    return selected_abcd, selected_alerts, only_d, only_review


def filter_dataframe(
    df: pd.DataFrame,
    selected_abcd: list[str],
    selected_alerts: list[str],
    only_d: bool,
    only_review: bool,
) -> pd.DataFrame:
    df_filtered = df.copy()
    df_filtered = df_filtered[df_filtered["ABCD"].isin(selected_abcd)]
    df_filtered = df_filtered[df_filtered["Alerta"].isin(selected_alerts)]

    if only_d:
        df_filtered = df_filtered[df_filtered["ABCD"] == "D"]

    if only_review:
        df_filtered = df_filtered[df_filtered["Dias_para_D"] <= 15]

    return df_filtered


def render_kpis(df: pd.DataFrame) -> None:
    total_products = len(df)
    products_with_stock = df[df["Stock"] > 0]
    total_blocked = products_with_stock["Capital_Bloqueado (€)"].sum()
    blocked_d = products_with_stock.loc[products_with_stock["ABCD"] == "D", "Capital_Bloqueado (€)"].sum()
    red_alerts = len(df[df["Alerta"] == "🔴 LIQUIDAR"])
    orange_alerts = len(df[df["Alerta"] == "🟠 REVISAR"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total productos", total_products)
    c2.metric("Capital bloqueado total", format_currency(total_blocked))
    c3.metric("Capital bloqueado en D", format_currency(blocked_d))
    c4.metric("Alertas 🔴 / 🟠", f"{red_alerts} / {orange_alerts}")


def render_main_table(df: pd.DataFrame) -> None:
    st.subheader("📋 Tabla principal de productos")
    st.caption("Ordena las columnas y usa los filtros para explorar la clasificación.")

    styled = style_table(df)
    st.dataframe(styled, use_container_width=True)


def render_secondary_views(df: pd.DataFrame) -> None:
    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("🔴 Productos D (Liquidación)")
        d_products = df[df["ABCD"] == "D"].sort_values(
            by="Capital_Bloqueado (€)", ascending=False
        )
        d_view = d_products[
            [
                "ABCD",
                "Marca",
                "Cód Barras",
                "Stock",
                "Motivo",
                "Capital_Bloqueado (€)",
                "Primera Compra",
                "Última Compra",
                "Última Venta",
                "Fecha_Revision",
                "Dias_para_D",
                "Alerta",
                "Accion_Recomendada",
            ]
        ]
        st.dataframe(d_view, use_container_width=True)
        st.download_button(
            "Descargar productos D",
            dataframe_to_csv(d_view),
            file_name="productos_d.csv",
            mime="text/csv",
        )

    with right:
        st.subheader("🟠 Productos B que pasan a D pronto")
        soon_to_d = df[
            (df["ABCD"] == "B") & (df["Dias_para_D"] <= 15)
        ].sort_values(by="Dias_para_D")
        st.dataframe(
            soon_to_d[
                [
                    "ABCD",
                    "Marca",
                    "Cód Barras",
                    "Stock",
                    "Motivo",
                    "Dias_para_D",
                    "Capital_Bloqueado (€)",
                    "Primera Compra",
                    "Última Compra",
                    "Última Venta",
                    "Alerta",
                    "Accion_Recomendada",
                ]
            ],
            use_container_width=True,
        )


def render_top_50_d(df: pd.DataFrame) -> None:
    st.divider()
    st.subheader("🏷️ Top 50 referencias D por capital bloqueado")
    d_products = df[df["ABCD"] == "D"].sort_values(
        by="Capital_Bloqueado (€)", ascending=False
    ).head(50)

    if d_products.empty:
        st.info("No hay productos D para mostrar en el top 50.")
        return

    total_capital_top_50 = d_products["Capital_Bloqueado (€)"].sum()
    total_capital_top_50_sin_dto = (d_products["PVO sin descuento"] * d_products["Stock"]).sum()

    c1, c2 = st.columns(2)
    c1.metric("Capital bloqueado top 50", format_currency(total_capital_top_50))
    c2.metric("Capital top 50 sin dto", format_currency(total_capital_top_50_sin_dto))

    top_50_view = d_products.copy()
    top_50_view["PVO sin descuento"] = top_50_view["PVO sin descuento"]
    top_50_view["Capital sin dto"] = top_50_view["PVO sin descuento"] * top_50_view["Stock"]
    top_50_view = top_50_view[
        [
            "Cód Barras",
            "Stock",
            "PVO",
            "PVO sin descuento",
            "Capital sin dto",
            "Primera Compra",
            "Última Venta",
            "Capital_Bloqueado (€)",
            "Indice_de_Rotacion_Semanal",
        ]
    ].rename(
        columns={
            "Cód Barras": "Referencia",
            "Indice_de_Rotacion_Semanal": "Indice de Rotación Semanal",
        }
    )

    st.dataframe(top_50_view, use_container_width=True)
    st.download_button(
        "Descargar top 50 D",
        dataframe_to_csv(top_50_view),
        file_name="top_50_d.csv",
        mime="text/csv",
    )


def main() -> None:
    configure_page()
    st.title(APP_TITLE)
    st.caption("Visualiza el motor ABCD sin duplicar la lógica de negocio en la interfaz.")

    project_dir = Path(__file__).resolve().parent
    local_input = project_dir / LOCAL_INPUT_NAME

    st.sidebar.header("⚙️ Origen de datos")
    days_without_sales_for_d = st.sidebar.number_input(
        "Días sin ventas para marcar como D",
        min_value=1,
        value=90,
        step=1,
        help="Si un producto no tiene ventas durante este número de días, pasa a D.",
    )
    analysis_date = st.sidebar.date_input(
        "Fecha del análisis",
        value=pd.Timestamp.today().date(),
        help="Selecciona la fecha para calcular la clasificación ABCD.",
    )
    data_source = st.sidebar.radio(
        "Selecciona el origen de los datos",
        DATA_SOURCE_OPTIONS,
        index=0,
    )

    if data_source == "Excel":
        if local_input.exists():
            st.sidebar.warning("🟡 Usando archivo local de Excel")
        else:
            st.sidebar.error("🔴 No hay archivo local disponible; sube un Excel")
        uploaded_file = st.sidebar.file_uploader(
            "Sube un Excel de productos (input.xlsx)", type=["xlsx"]
        )
        sidebar_status_placeholder = None
    else:
        sidebar_status_placeholder = st.sidebar.empty()
        sidebar_status_placeholder.info("🟡 Conectando a la base de datos Odoo de producción...")
        uploaded_file = None

    try:
        raw_df = load_data(uploaded_file, local_input, data_source)
        if data_source == "Base de Datos" and sidebar_status_placeholder is not None:
            sidebar_status_placeholder.success(
                "🟢 Conectado a la base de datos Odoo de producción"
            )
    except FileNotFoundError as error:
        st.info(str(error))
        st.stop()
    except Exception as error:
        if data_source == "Base de Datos" and sidebar_status_placeholder is not None:
            sidebar_status_placeholder.error(f"🔴 Error de conexión: {error}")
        st.error(f"No se pudo cargar la base de datos: {error}")
        st.stop()

    try:
        with st.spinner("Ejecutando motor ABCD..."):
            df = prepare_dataframe(
                raw_df,
                days_without_sales_for_d,
                pd.Timestamp(analysis_date),
            )
    except ValueError as error:
        st.error(str(error))
        st.stop()

    st.sidebar.markdown("---")
    if st.sidebar.button("Guardar snapshot del análisis"):
        snapshot_path = save_snapshot(df, pd.Timestamp(analysis_date))
        st.sidebar.success(f"Snapshot guardado: {snapshot_path.name}")

    render_kpis(df)

    selected_abcd, selected_alerts, only_d, only_review = build_sidebar_filters(df)
    filtered_df = filter_dataframe(df, selected_abcd, selected_alerts, only_d, only_review)

    if filtered_df.empty:
        st.warning("No hay productos que coincidan con los filtros seleccionados.")
    else:
        render_main_table(filtered_df)

    render_secondary_views(df)
    render_top_50_d(df)


if __name__ == "__main__":
    main()
