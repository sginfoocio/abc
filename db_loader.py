from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from db_config import DBConfig, load_db_config

REQUIRED_COLUMNS = ["PVO", "Stock", "Primera Compra", "Última Compra", "Última Venta"]

DEFAULT_QUERY = """
WITH supplier_locations AS (
    SELECT id
    FROM stock_location
    WHERE usage = 'supplier'
), internal_locations AS (
    SELECT id
    FROM stock_location
    WHERE usage = 'internal'
), customer_locations AS (
    SELECT id
    FROM stock_location
    WHERE usage = 'customer'
), stock_actual AS (
    SELECT
        product_id,
        SUM(quantity) AS "Stock"
    FROM stock_quant
    WHERE location_id IN (SELECT id FROM internal_locations)
    GROUP BY product_id
), first_purchase AS (
    SELECT
        sm.product_id,
        MIN(sm.date) AS "Primera Compra"
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'supplier'
      AND dst.usage = 'internal'
    GROUP BY sm.product_id
), last_purchase AS (
    SELECT
        sm.product_id,
        MAX(sm.date) AS "Última Compra"
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'supplier'
      AND dst.usage = 'internal'
    GROUP BY sm.product_id
), last_sale AS (
    SELECT
        sm.product_id,
        MAX(sm.date) AS "Última Venta"
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'internal'
      AND dst.usage = 'customer'
      AND sm.sale_line_id IS NOT NULL
    GROUP BY sm.product_id
), preferred_supplier AS (
    SELECT DISTINCT ON (product_tmpl_id)
        product_tmpl_id,
        price,
        price_discount
    FROM product_supplierinfo
    ORDER BY product_tmpl_id, sequence, id
), last_week_sales AS (
    SELECT
        sm.product_id,
        SUM(sm.product_qty) AS "Ventas_7_Dias"
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'internal'
      AND dst.usage = 'customer'
      AND sm.sale_line_id IS NOT NULL
      AND sm.date >= NOW() - INTERVAL '7 days'
    GROUP BY sm.product_id
), last_180_sales AS (
    SELECT
        sm.product_id,
        COUNT(*) AS "Num_Ventas_180D",
        SUM(sm.product_qty) AS "Ventas_180_Dias"
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'internal'
      AND dst.usage = 'customer'
      AND sm.sale_line_id IS NOT NULL
      AND sm.date >= NOW() - INTERVAL '180 days'
    GROUP BY sm.product_id
), last_replenishment AS (
    SELECT
        sm.product_id,
        MAX(sm.date) AS fecha_ultima_reposicion
    FROM stock_move sm
    JOIN stock_location src ON sm.location_id = src.id
    JOIN stock_location dst ON sm.location_dest_id = dst.id
    WHERE sm.state = 'done'
      AND COALESCE(sm.scrapped, FALSE) = FALSE
      AND COALESCE(sm.is_inventory, FALSE) = FALSE
      AND src.usage = 'supplier'
      AND dst.usage = 'internal'
    GROUP BY sm.product_id
)
SELECT
    pp.id AS product_id,
    pt.name AS "Marca",
    pt.default_code AS "Cód Barras",
    pt.categ_id AS categoria,
    COALESCE(sa."Stock", 0) AS "Stock",
    COALESCE(ps.price_discount, ps.price, pt.list_price) AS "PVO",
    COALESCE(ps.price, pt.list_price) AS "PVO sin descuento",
    fp."Primera Compra",
    lp."Última Compra",
    ls."Última Venta",
    COALESCE(ws."Ventas_7_Dias", 0) AS "Ventas_7_Dias",
    COALESCE(l180."Num_Ventas_180D", 0) AS "Num_Ventas_180D",
    COALESCE(l180."Ventas_180_Dias", 0) AS "Ventas_180_Dias",
    lr.fecha_ultima_reposicion AS "Última Reposicion"
FROM product_product pp
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN stock_actual sa ON sa.product_id = pp.id
LEFT JOIN first_purchase fp ON fp.product_id = pp.id
LEFT JOIN last_purchase lp ON lp.product_id = pp.id
LEFT JOIN last_sale ls ON ls.product_id = pp.id
LEFT JOIN preferred_supplier ps ON ps.product_tmpl_id = pt.id
LEFT JOIN last_week_sales ws ON ws.product_id = pp.id
LEFT JOIN last_180_sales l180 ON l180.product_id = pp.id
LEFT JOIN last_replenishment lr ON lr.product_id = pp.id
WHERE pt.active = TRUE
  AND COALESCE(sa."Stock", 0) > 0
"""


def load_odoo_dataframe(query: str = DEFAULT_QUERY, config: DBConfig | None = None) -> pd.DataFrame:
    config = config or load_db_config()
    url = URL.create(
        "postgresql+psycopg2",
        username=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
        database=config.database,
    )
    engine = create_engine(url)
    with engine.connect() as connection:
        df = pd.read_sql_query(query, connection)

    if "Stock" in df.columns:
        df = df[df["Stock"] > 0].reset_index(drop=True)

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"El resultado de la consulta debe incluir las columnas: {', '.join(missing)}"
        )

    return df
