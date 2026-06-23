from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd
from engine import run_abcd_engine
from datetime import datetime, timedelta

def analyze_product_details(product_id):
    """Analiza en detalle por qué un producto está en D"""
    
    config = load_db_config()
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    # Consulta detallada
    query = text("""
        WITH internal_locations AS (
            SELECT id FROM stock_location WHERE usage = 'internal'
        ),
        stock_actual AS (
            SELECT
                product_id,
                SUM(quantity) AS "Stock"
            FROM stock_quant
            WHERE location_id IN (SELECT id FROM internal_locations)
            GROUP BY product_id
        ),
        first_purchase AS (
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
        ),
        last_purchase AS (
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
        ),
        last_sale AS (
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
        ),
        preferred_supplier AS (
            SELECT DISTINCT ON (product_tmpl_id)
                product_tmpl_id,
                price,
                price_discount
            FROM product_supplierinfo
            ORDER BY product_tmpl_id, sequence, id
        ),
        recent_sales AS (
            SELECT
                sm.product_id,
                COUNT(*) AS num_sales,
                MAX(sm.date) AS last_sale,
                SUM(sm.product_qty) as total_qty
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
        )
        SELECT
            pp.id AS product_id,
            pt.name AS "Marca",
            COALESCE(sa."Stock", 0) AS "Stock",
            COALESCE(ps.price_discount, ps.price, pt.list_price) AS "PVO",
            fp."Primera Compra",
            lp."Última Compra",
            ls."Última Venta",
            COALESCE(rs.num_sales, 0) as ventas_ultimos_180_dias,
            COALESCE(rs.total_qty, 0) as unidades_ultimos_180_dias
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_actual sa ON sa.product_id = pp.id
        LEFT JOIN first_purchase fp ON fp.product_id = pp.id
        LEFT JOIN last_purchase lp ON lp.product_id = pp.id
        LEFT JOIN last_sale ls ON ls.product_id = pp.id
        LEFT JOIN preferred_supplier ps ON ps.product_tmpl_id = pt.id
        LEFT JOIN recent_sales rs ON rs.product_id = pp.id
        WHERE pp.id = :product_id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"product_id": int(product_id)})
        rows = result.fetchall()
        
        if not rows:
            print(f"❌ No se encontró producto")
            return
        
        row = rows[0]
        
        print("\n" + "="*80)
        print(f"📦 ANÁLISIS DETALLADO - {row[1]}")
        print("="*80)
        
        # Datos básicos
        print(f"\n📊 DATOS ACTUALES:")
        print(f"  • Product ID: {row[0]}")
        stock = float(row[2])
        pvo = float(row[3])
        print(f"  • Stock actual: {stock} unidades")
        print(f"  • PVO (Precio): €{pvo}")
        print(f"  • Capital bloqueado: €{stock * pvo:.2f}")
        
        # Fechas clave
        today = pd.Timestamp.today().normalize()
        primera_compra = pd.Timestamp(row[4]).normalize() if row[4] else None
        ultima_venta = pd.Timestamp(row[6]).normalize() if row[6] else None
        
        print(f"\n📅 FECHAS IMPORTANTES:")
        print(f"  • Primera Compra: {row[4]}")
        if primera_compra:
            dias_desde = (today - primera_compra).days
            print(f"    └─ Hace {dias_desde} días (umbral: 90 días)")
        
        print(f"  • Última Venta: {row[6]}")
        if ultima_venta:
            dias_sin_venta = (today - ultima_venta).days
            print(f"    └─ Hace {dias_sin_venta} días (umbral para D: 90 días)")
        
        print(f"  • Última Compra: {row[5]}")
        
        # Ventas recientes
        ventas_ultimos_180 = int(row[8]) if row[8] else 0
        unidades_ultimos_180 = float(row[9]) if len(row) > 9 and row[9] else 0
        print(f"\n💰 VENTAS ÚLTIMOS 180 DÍAS:")
        print(f"  • Número de transacciones: {ventas_ultimos_180}")
        print(f"  • Unidades vendidas: {unidades_ultimos_180}")
        
        # Crear DataFrame para el análisis ABCD
        df = pd.DataFrame({
            'Marca': [row[1]],
            'Stock': [stock],
            'PVO': [pvo],
            'Primera Compra': [row[4]],
            'Última Venta': [row[6]],
        })
        
        print(f"\n🔍 CLASIFICACIÓN ACTUAL:")
        df_abcd = run_abcd_engine(df.copy())
        
        print(f"  • Clasificación: {df_abcd['ABCD'].iloc[0]}")
        print(f"  • Motivo: {df_abcd['Motivo'].iloc[0]}")
        print(f"  • Alerta: {df_abcd['Alerta'].iloc[0]}")
        print(f"  • Acción: {df_abcd['Accion_Recomendada'].iloc[0]}")
        
        # Análisis del problema
        print(f"\n⚠️  ANÁLISIS DEL PROBLEMA:")
        if ultima_venta:
            dias_sin_venta = (today - ultima_venta).days
            print(f"  ✗ Última venta hace {dias_sin_venta} días (> 90 días sin ventas)")
        print(f"  ✓ Tiene stock actual: {stock} unidades")
        print(f"  ✓ Pasó período de margen: {dias_desde} días desde primera compra")
        
        if ventas_ultimos_180 > 0:
            print(f"  ✓ Tuvo {ventas_ultimos_180} ventas en últimos 180 días")
            print(f"\n💡 POSIBLE SOLUCIÓN:")
            print(f"  El producto fue clasificado como D porque no tiene ventas hace >90 días.")
            print(f"  Sin embargo, sigue siendo un producto con demanda histórica.")
            print(f"  Se recomienda reclasificarlo considerando:")
            print(f"  - Registrar una nueva venta para actualizar 'Última Venta'")
            print(f"  - O revisar si el producto necesita reabastecimiento")
        else:
            print(f"\n⚠️  El producto no tuvo ventas en los últimos 180 días")
            print(f"  Podría ser realmente un producto obsoleto o de baja demanda")

if __name__ == "__main__":
    analyze_product_details(85762)
