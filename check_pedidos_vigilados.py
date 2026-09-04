"""Consulta de pedidos (sale.order) de Odoo para clientes en la lista de vigilancia."""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from db_config import DBConfig, load_db_config

# 'Pendientes': pedido confirmado, no facturado del todo o con lineas sin entregar por completo.
PENDING_CONDITION = """
    AND so.state = 'sale'
    AND (
        so.invoice_status IN ('to invoice', 'no')
        OR EXISTS (
            SELECT 1
            FROM sale_order_line sol
            WHERE sol.order_id = so.id
              AND sol.qty_delivered < sol.product_uom_qty
        )
    )
"""

BASE_QUERY = """
SELECT
    so.id AS pedido_id,
    so.name AS pedido,
    rp.name AS cliente,
    COALESCE(rp_shipping.name, '') AS direccion_entrega,
    so.date_order,
    so.state,
    so.invoice_status,
    so.amount_total
FROM sale_order so
JOIN res_partner rp ON rp.id = so.partner_id
LEFT JOIN res_partner rp_shipping ON rp_shipping.id = so.partner_shipping_id
WHERE (rp.name = ANY(:clientes) OR COALESCE(rp_shipping.name, '') = ANY(:clientes))
  AND so.date_order >= :fecha_desde
  AND so.date_order < :fecha_hasta_exclusiva
{estado_condicion}
ORDER BY so.date_order DESC
"""


def find_matching_orders(
    clientes: list[str],
    fecha_desde: date,
    fecha_hasta: date,
    solo_pendientes: bool = True,
    config: DBConfig | None = None,
) -> pd.DataFrame:
    """Busca pedidos de sale_order cuyo cliente o dirección de entrega coincide con la lista de vigilancia."""
    columns = ["pedido_id", "pedido", "cliente", "direccion_entrega", "date_order", "state", "invoice_status", "amount_total"]
    if not clientes:
        return pd.DataFrame(columns=columns)

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
    query = BASE_QUERY.format(estado_condicion=PENDING_CONDITION if solo_pendientes else "")

    # date_order es fecha+hora; la fecha "hasta" se compara como limite exclusivo del dia siguiente.
    params = {
        "clientes": clientes,
        "fecha_desde": fecha_desde,
        "fecha_hasta_exclusiva": fecha_hasta,
    }
    with engine.connect() as connection:
        df = pd.read_sql_query(text(query), connection, params=params)

    return df
