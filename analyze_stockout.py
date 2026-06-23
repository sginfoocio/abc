from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd
from datetime import datetime

def analyze_stockout_periods(product_id):
    """Detecta períodos de agotamiento de stock que explican falta de ventas"""
    
    config = load_db_config()
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    # Obtener movimientos de stock ordenados
    query = text("""
        WITH internal_locations AS (
            SELECT id FROM stock_location WHERE usage = 'internal'
        ),
        stock_moves AS (
            SELECT 
                sm.date,
                sm.product_qty,
                CASE 
                    WHEN sl_src.usage = 'supplier' AND sl_dst.usage = 'internal' THEN 'ENTRADA'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'customer' THEN 'SALIDA'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'internal' THEN 'TRANSFERENCIA'
                    ELSE 'OTRO'
                END as tipo,
                sm.state
            FROM stock_move sm
            JOIN stock_location sl_src ON sm.location_id = sl_src.id
            JOIN stock_location sl_dst ON sm.location_dest_id = sl_dst.id
            WHERE sm.product_id = :product_id
              AND sm.state = 'done'
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
        print(f"No hay movimientos para el producto")
        return
    
    # Calcular stock acumulado y detectar períodos de agotamiento
    stock = 0
    periods = []
    last_date = None
    stockout_start = None
    last_sale_before_stockout = None
    
    for row in rows:
        fecha = row[0]
        tipo = row[1]
        cantidad = row[2]
        
        # Contar solo entradas y salidas que afecten stock interno
        if tipo == 'ENTRADA':
            stock += cantidad
            if stockout_start:
                # Se repuso, termina período de agotamiento
                periods.append({
                    'inicio': stockout_start,
                    'fin': fecha,
                    'dias_sin_stock': (fecha - stockout_start).days,
                    'ultima_venta_antes': last_sale_before_stockout
                })
                stockout_start = None
                last_sale_before_stockout = None
        elif tipo == 'SALIDA':
            stock -= cantidad
            last_date = fecha
            if stock >= 0:
                last_sale_before_stockout = fecha
        
        # Detectar agotamiento
        if stock <= 0 and not stockout_start:
            stockout_start = fecha
    
    # Si termina en agotamiento
    if stockout_start:
        periods.append({
            'inicio': stockout_start,
            'fin': None,
            'dias_sin_stock': (datetime.now() - stockout_start).days if stockout_start else None,
            'ultima_venta_antes': last_sale_before_stockout
        })
    
    return periods, rows

def main():
    print("\n" + "="*100)
    print("ANÁLISIS DE PERÍODOS DE AGOTAMIENTO DE STOCK")
    print("="*100)
    
    product_id = 85762
    periods, moves = analyze_stockout_periods(product_id)
    
    if not periods:
        print("\nNo hay períodos de agotamiento detectados")
        return
    
    print(f"\nProducto ID: {product_id}")
    print(f"Total de movimientos: {len(moves)}")
    print(f"\nPeríodos de AGOTAMIENTO DE STOCK:\n")
    
    for i, period in enumerate(periods, 1):
        inicio = period['inicio']
        fin = period['fin'] if period['fin'] else 'Aún agotado'
        dias = period['dias_sin_stock']
        ultima_venta = period['ultima_venta_antes']
        
        print(f"{i}. PERÍODO DE AGOTAMIENTO")
        print(f"   Inicio: {inicio}")
        print(f"   Fin: {fin}")
        print(f"   Duración: {dias} días")
        print(f"   Última venta ANTES del agotamiento: {ultima_venta}")
        print()
    
    print("="*100)
    print("CONCLUSIÓN")
    print("="*100)
    print("""
El producto ESTUVO SIN STOCK durante períodos específicos.
Las "no ventas" en esos períodos NO indican falta de demanda, sino FALTA DE STOCK.

Para una clasificación ABCD más justa, debemos:
1. Detectar períodos de agotamiento
2. Excluir esos períodos del cálculo de "días sin ventas"
3. Considerar las ventas Y la capacidad de vender
""")

if __name__ == "__main__":
    main()
