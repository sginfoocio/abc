from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd

def fix_product_classification():
    """Actualiza la clasificación de productos mal clasificados como D"""
    
    config = load_db_config()
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    print("\n" + "="*100)
    print("CORRECCIÓN DE CLASIFICACIÓN ABCD - PRODUCTOS CLASIFICADOS INCORRECTAMENTE")
    print("="*100)
    
    # Identificar productos D con demanda reciente
    query = text("""
        WITH internal_locations AS (
            SELECT id FROM stock_location WHERE usage = 'internal'
        ),
        stock_actual AS (
            SELECT product_id, SUM(quantity) AS stock_qty
            FROM stock_quant
            WHERE location_id IN (SELECT id FROM internal_locations)
            GROUP BY product_id
        ),
        first_purchase AS (
            SELECT sm.product_id, MIN(sm.date) AS primera_compra
            FROM stock_move sm
            JOIN stock_location src ON sm.location_id = src.id
            JOIN stock_location dst ON sm.location_dest_id = dst.id
            WHERE sm.state = 'done' AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
              AND src.usage = 'supplier' AND dst.usage = 'internal'
            GROUP BY sm.product_id
        ),
        last_sale AS (
            SELECT sm.product_id, MAX(sm.date) AS ultima_venta
            FROM stock_move sm
            JOIN stock_location src ON sm.location_id = src.id
            JOIN stock_location dst ON sm.location_dest_id = dst.id
            WHERE sm.state = 'done' AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
              AND src.usage = 'internal' AND dst.usage = 'customer' AND sm.sale_line_id IS NOT NULL
            GROUP BY sm.product_id
        ),
        sales_180 AS (
            SELECT sm.product_id, COUNT(*) as ventas_180
            FROM stock_move sm
            JOIN stock_location src ON sm.location_id = src.id
            JOIN stock_location dst ON sm.location_dest_id = dst.id
            WHERE sm.state = 'done' AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
              AND src.usage = 'internal' AND dst.usage = 'customer' AND sm.sale_line_id IS NOT NULL
              AND sm.date >= NOW() - INTERVAL '180 days'
            GROUP BY sm.product_id
        )
        SELECT
            pp.id,
            pt.name,
            COALESCE(sa.stock_qty, 0) as stock,
            fp.primera_compra,
            ls.ultima_venta,
            COALESCE(s180.ventas_180, 0) as ventas_180,
            CASE
                WHEN s180.ventas_180 > 0 THEN 'B'  -- Tiene ventas recientes
                WHEN COALESCE(sa.stock_qty, 0) > 0 THEN 'C'  -- Solo tiene stock
                ELSE 'D'  -- Sin movimiento
            END as clasificacion_correcta
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_actual sa ON sa.product_id = pp.id
        LEFT JOIN first_purchase fp ON fp.product_id = pp.id
        LEFT JOIN last_sale ls ON ls.product_id = pp.id
        LEFT JOIN sales_180 s180 ON s180.product_id = pp.id
        WHERE pt.active = TRUE AND COALESCE(sa.stock_qty, 0) > 0
        ORDER BY pp.id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
    
    df = pd.DataFrame(rows, columns=['product_id', 'name', 'stock', 'primera_compra', 
                                      'ultima_venta', 'ventas_180', 'clasificacion_correcta'])
    
    # Productos que necesitan actualización
    df_to_fix = df[df['clasificacion_correcta'].isin(['B', 'C'])].copy()
    
    print(f"\nProductos que necesitan reclasificación: {len(df_to_fix)}")
    print(f"  - Deberían estar en B: {len(df_to_fix[df_to_fix['clasificacion_correcta'] == 'B'])}")
    print(f"  - Deberían estar en C: {len(df_to_fix[df_to_fix['clasificacion_correcta'] == 'C'])}")
    
    # Soluciones propuestas
    print("\n" + "="*100)
    print("OPCIONES PARA CORREGIR EL PROBLEMA")
    print("="*100)
    
    print("\nOPCIÓN 1: Aumentar el umbral de 'Días sin ventas para D' (Recomendado)")
    print("-" * 100)
    print("  Cambiar el parámetro DEFAULT_DAYS_WITHOUT_SALES_FOR_D de 90 a 180+ días")
    print("  Ubicación: engine.py línea 8")
    print("  Beneficio: Los productos con ventas recientes no caerán en D tan rápido")
    print("  Riesgo: Algunos productos verdaderamente obsoletos se quedarán más tiempo en stock")
    
    print("\nOPCIÓN 2: Actualizar las fechas de 'Última Venta' en la base de datos")
    print("-" * 100)
    print("  Para productos con demanda histórica, establecer 'Última Venta' a la fecha real más reciente")
    print("  Esto requiere una limpieza de datos")
    
    print("\nOPCIÓN 3: Crear una regla adicional basada en 'Ventas en los últimos 180 días'")
    print("-" * 100)
    print("  Si el producto tiene al menos 1 venta en últimos 180 días → No debe ser D")
    print("  Esta es la más precisa para tu negocio")
    
    # Mostrar algunos ejemplos
    print("\n" + "="*100)
    print("EJEMPLOS DE PRODUCTOS MAL CLASIFICADOS")
    print("="*100)
    
    sample = df_to_fix.head(5)
    for idx, row in sample.iterrows():
        print(f"\n{idx+1}. {row['name']}")
        print(f"   Stock: {row['stock']:.0f} ud")
        print(f"   Ultima Venta: {row['ultima_venta']}")
        print(f"   Ventas ultimos 180 dias: {row['ventas_180']}")
        print(f"   -> Deberia ser: {row['clasificacion_correcta']}")

if __name__ == "__main__":
    fix_product_classification()
