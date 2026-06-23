from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd
from engine import run_abcd_engine

def analyze_product_for_reclassification(product_id):
    """Analiza la clasificación ABCD de un producto específico"""
    
    config = load_db_config()
    
    # Crear conexión
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    # Consulta para obtener todos los datos necesarios del producto
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
        )
        SELECT
            pp.id AS product_id,
            pt.name AS "Marca",
            pt.default_code AS "Cód Barras",
            COALESCE(sa."Stock", 0) AS "Stock",
            COALESCE(ps.price_discount, ps.price, pt.list_price) AS "PVO",
            fp."Primera Compra",
            lp."Última Compra",
            ls."Última Venta"
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_actual sa ON sa.product_id = pp.id
        LEFT JOIN first_purchase fp ON fp.product_id = pp.id
        LEFT JOIN last_purchase lp ON lp.product_id = pp.id
        LEFT JOIN last_sale ls ON ls.product_id = pp.id
        LEFT JOIN preferred_supplier ps ON ps.product_tmpl_id = pt.id
        WHERE pp.id = :product_id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"product_id": int(product_id)})
        rows = result.fetchall()
        
        if not rows:
            print(f"❌ No se encontró producto con ID: {product_id}")
            return
        
        # Convertir a DataFrame
        df = pd.DataFrame(rows)
        
        print("\n" + "="*70)
        print(f"📋 DATOS DEL PRODUCTO (ID: {product_id})")
        print("="*70)
        print(df.to_string(index=False))
        
        # Analizar con el motor ABCD
        print("\n" + "="*70)
        print("🔍 ANÁLISIS ABCD")
        print("="*70)
        
        try:
            df_abcd = run_abcd_engine(df.copy())
            
            # Mostrar las columnas relevantes
            cols_to_show = ["Marca", "Stock", "PVO", "Capital_Bloqueado (€)", 
                           "Dias_Sin_Venta", "Clasificación", "Motivo_Clasificación"]
            cols_available = [col for col in cols_to_show if col in df_abcd.columns]
            
            print("\nClasificación ABCD:")
            print(df_abcd[cols_available].to_string(index=False))
            
            # Mostrar el motivo específico
            if "Motivo_Clasificación" in df_abcd.columns:
                motivo = df_abcd["Motivo_Clasificación"].iloc[0]
                clasificacion = df_abcd["Clasificación"].iloc[0]
                print(f"\n📍 Clasificación: {clasificacion}")
                print(f"📝 Motivo: {motivo}")
                
        except Exception as e:
            print(f"❌ Error al ejecutar análisis ABCD: {e}")

if __name__ == "__main__":
    # Analizar el producto DIOR DIORPACIFIC
    analyze_product_for_reclassification(85762)
