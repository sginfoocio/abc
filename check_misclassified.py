from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd
from engine import run_abcd_engine
from datetime import datetime, timedelta
import sys
import io

# Fix para emojis en Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_misclassified_d_products():
    """Detecta productos clasificados como D que podrían estar mal clasificados"""
    
    config = load_db_config()
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    # Obtener datos de todos los productos con stock
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
        sales_last_180_days AS (
            SELECT
                sm.product_id,
                COUNT(*) as num_ventas_180,
                SUM(sm.product_qty) as unidades_vendidas_180
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
        ),
        sales_last_year AS (
            SELECT
                sm.product_id,
                COUNT(*) as num_ventas_año,
                SUM(sm.product_qty) as unidades_vendidas_año
            FROM stock_move sm
            JOIN stock_location src ON src.id = sm.location_id
            JOIN stock_location dst ON dst.id = sm.location_dest_id
            WHERE sm.state = 'done'
              AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
              AND src.usage = 'internal'
              AND dst.usage = 'customer'
              AND sm.sale_line_id IS NOT NULL
              AND sm.date >= NOW() - INTERVAL '365 days'
            GROUP BY sm.product_id
        ),
        preferred_supplier AS (
            SELECT DISTINCT ON (product_tmpl_id)
                product_tmpl_id,
                price,
                price_discount
            FROM product_supplierinfo
            ORDER BY product_tmpl_id, sequence, id
        )
        SELECT
            pp.id AS product_id,
            pt.name AS "Nombre",
            pp.barcode AS "Barcode",
            COALESCE(sa."Stock", 0) AS "Stock",
            COALESCE(ps.price_discount, ps.price, pt.list_price) AS "PVO",
            fp."Primera Compra",
            lp."Última Compra",
            ls."Última Venta",
            COALESCE(s180.num_ventas_180, 0) as ventas_180_dias,
            COALESCE(s180.unidades_vendidas_180, 0) as unidades_180_dias,
            COALESCE(sy.num_ventas_año, 0) as ventas_año,
            COALESCE(sy.unidades_vendidas_año, 0) as unidades_año
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_actual sa ON sa.product_id = pp.id
        LEFT JOIN first_purchase fp ON fp.product_id = pp.id
        LEFT JOIN last_purchase lp ON lp.product_id = pp.id
        LEFT JOIN last_sale ls ON ls.product_id = pp.id
        LEFT JOIN preferred_supplier ps ON ps.product_tmpl_id = pt.id
        LEFT JOIN sales_last_180_days s180 ON s180.product_id = pp.id
        LEFT JOIN sales_last_year sy ON sy.product_id = pp.id
        WHERE pt.active = TRUE
          AND COALESCE(sa."Stock", 0) > 0
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
    
    df_all = pd.DataFrame(rows)
    
    # Ejecutar análisis ABCD
    df_all_copy = df_all.copy()
    df_abcd = run_abcd_engine(df_all_copy)
    
    # Identificar productos clasificados como D
    df_d_products = df_abcd[df_abcd['ABCD'] == 'D'].copy()
    
    print("\n" + "="*120)
    print("🔍 DETECCIÓN DE PRODUCTOS CLASIFICADOS INCORRECTAMENTE COMO D")
    print("="*120)
    
    if len(df_d_products) == 0:
        print("\n✅ No hay productos clasificados como D")
        return
    
    print(f"\n⚠️  Total de productos clasificados como D: {len(df_d_products)}\n")
    
    # Analizar cada producto D para detectar mal clasificaciones
    candidates_for_reclassification = []
    
    for idx, row in df_d_products.iterrows():
        product_id = row['product_id']
        nombre = row['Nombre']
        stock = float(row['Stock'])
        pvo = float(row['PVO'])
        ultima_venta = row['Última Venta']
        ventas_180 = int(row['ventas_180_dias']) if row['ventas_180_dias'] else 0
        unidades_180 = float(row['unidades_180_dias']) if row['unidades_180_dias'] else 0
        ventas_año = int(row['ventas_año']) if row['ventas_año'] else 0
        unidades_año = float(row['unidades_año']) if row['unidades_año'] else 0
        
        # Criterios para detectar mal clasificación
        tiene_demanda_reciente = ventas_180 > 0  # Vendió en últimos 180 días
        tiene_demanda_historica = ventas_año > 3  # Más de 3 ventas en el año
        capital_bloqueado = stock * pvo
        tiene_capital_significativo = capital_bloqueado > 500  # Más de €500
        
        if tiene_demanda_reciente or (tiene_demanda_historica and tiene_capital_significativo):
            candidates_for_reclassification.append({
                'product_id': product_id,
                'Nombre': nombre,
                'Stock': stock,
                'PVO': pvo,
                'Capital_Bloqueado': capital_bloqueado,
                'Última_Venta': ultima_venta,
                'Ventas_180D': ventas_180,
                'Unidades_180D': unidades_180,
                'Ventas_Año': ventas_año,
                'Unidades_Año': unidades_año,
                'Motivo': f"Demanda histórica: {ventas_180} ventas en 180 días, {ventas_año} en el año"
            })
    
    if candidates_for_reclassification:
        print(f"\n🚨 CANDIDATOS PARA RECLASIFICACIÓN: {len(candidates_for_reclassification)} productos\n")
        print("Estos productos están clasificados como D pero tienen demanda histórica:\n")
        
        df_candidates = pd.DataFrame(candidates_for_reclassification)
        
        # Mostrar tabla formateada
        for idx, row in df_candidates.iterrows():
            print(f"\n{idx+1}. 📦 {row['Nombre']}")
            print(f"   ├─ Product ID: {row['product_id']}")
            print(f"   ├─ Stock: {row['Stock']:.0f} ud | Precio: €{row['PVO']:.2f}")
            print(f"   ├─ Capital Bloqueado: €{row['Capital_Bloqueado']:.2f}")
            print(f"   ├─ Última Venta: {row['Última_Venta']}")
            print(f"   ├─ Ventas Últimos 180 días: {row['Ventas_180D']} ({row['Unidades_180D']:.0f} ud)")
            print(f"   ├─ Ventas Último Año: {row['Ventas_Año']} ({row['Unidades_Año']:.0f} ud)")
            print(f"   └─ 💡 {row['Motivo']}")
        
        print("\n" + "="*120)
        print("📋 RESUMEN DE PROBLEMAS")
        print("="*120)
        total_capital = df_candidates['Capital_Bloqueado'].sum()
        print(f"  • Capital total bloqueado en productos mal clasificados: €{total_capital:.2f}")
        print(f"  • Número de productos afectados: {len(df_candidates)}")
        print(f"\n⚠️  RECOMENDACIÓN:")
        print(f"  Estos productos deberían ser reclasificados considerando:")
        print(f"  1. Actualizar 'Última Venta' con la fecha más reciente disponible")
        print(f"  2. O revisar los parámetros del motor ABCD (umbral de 90 días)")
        print(f"  3. Considerar si deberían estar en categoría B o C")
        
    else:
        print("\n✅ No hay productos mal clasificados como D")
        print("   Los productos clasificados como D no tienen demanda reciente")

if __name__ == "__main__":
    find_misclassified_d_products()
