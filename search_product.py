from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd

def search_product(product_id):
    """Busca un producto por ID, barcode o default_code"""
    
    config = load_db_config()
    
    # Crear conexión
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    with engine.connect() as conn:
        # Buscar en product_product
        query = text("""
            SELECT 
                pp.id,
                pp.default_code,
                pp.barcode,
                pt.name,
                pt.list_price,
                pp.active,
                pp.create_date,
                pp.write_date
            FROM product_product pp
            LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
            WHERE pp.id::text = :product_id
               OR pp.barcode = :product_id
               OR pp.default_code = :product_id
               OR pt.id::text = :product_id
            LIMIT 20
        """)
        
        result = conn.execute(query, {"product_id": product_id})
        rows = result.fetchall()
        
        if rows:
            df = pd.DataFrame(rows)
            print(f"\n✅ Se encontraron {len(df)} registros para: {product_id}\n")
            print(df.to_string())
            
            # Obtener stock disponible
            for row in rows:
                pp_id = row[0]
                print(f"\n--- Stock para Producto ID {pp_id} ---")
                stock_query = text("""
                    SELECT 
                        sl.name,
                        SUM(sq.quantity) as quantity
                    FROM stock_quant sq
                    JOIN stock_location sl ON sq.location_id = sl.id
                    WHERE sq.product_id = :pp_id
                    GROUP BY sl.name
                """)
                stock_result = conn.execute(stock_query, {"pp_id": pp_id})
                stock_rows = stock_result.fetchall()
                if stock_rows:
                    stock_df = pd.DataFrame(stock_rows)
                    print(stock_df.to_string())
        else:
            print(f"\n❌ No se encontró producto con ID: {product_id}")

if __name__ == "__main__":
    search_product("192337232893")
