from sqlalchemy import create_engine, text
from db_config import load_db_config
import pandas as pd
from datetime import datetime

def get_stock_history(product_id):
    """Obtiene el historial completo de movimientos de stock"""
    
    config = load_db_config()
    engine = create_engine(
        f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
    )
    
    query = text("""
        WITH RECURSIVE stock_history AS (
            -- Primer movimiento
            SELECT 
                sm.id,
                sm.date,
                sm.product_qty,
                sl_src.name as ubicacion_origen,
                sl_dst.name as ubicacion_destino,
                sl_src.usage as uso_origen,
                sl_dst.usage as uso_destino,
                sm.state,
                sm.product_qty as cantidad_neta,
                CASE 
                    WHEN sl_src.usage = 'supplier' AND sl_dst.usage = 'internal' THEN 'ENTRADA (Compra)'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'customer' THEN 'SALIDA (Venta)'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'internal' THEN 'TRANSFERENCIA'
                    WHEN sl_src.usage = 'internal' AND sl_dst.usage = 'supplier' THEN 'DEVOLUCIÓN'
                    ELSE 'OTRO'
                END as tipo_movimiento,
                ROW_NUMBER() OVER (ORDER BY sm.date) as seq
            FROM stock_move sm
            JOIN stock_location sl_src ON sm.location_id = sl_src.id
            JOIN stock_location sl_dst ON sm.location_dest_id = sl_dst.id
            WHERE sm.product_id = :product_id
              AND sm.state = 'done'
              AND COALESCE(sm.scrapped, FALSE) = FALSE
              AND COALESCE(sm.is_inventory, FALSE) = FALSE
        )
        SELECT 
            date,
            tipo_movimiento,
            ubicacion_origen,
            ubicacion_destino,
            product_qty as cantidad_movimiento,
            state,
            seq
        FROM stock_history
        ORDER BY date ASC, seq ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"product_id": int(product_id)})
        rows = result.fetchall()
        
        if not rows:
            print(f"No hay movimientos de stock para el producto")
            return
        
        df = pd.DataFrame(rows, columns=['Fecha', 'Tipo', 'Origen', 'Destino', 'Cantidad', 'Estado', 'Seq'])
        
        # Calcular stock solo desde movimientos internos
        stock_interno = 0
        movements = []
        
        for idx, row in df.iterrows():
            fecha = row['Fecha']
            tipo = row['Tipo']
            cantidad = row['Cantidad']
            origen = row['Origen']
            destino = row['Destino']
            
            # Contar solo movimientos que afecten el stock interno
            if 'Entrada' in tipo or 'Compra' in tipo:
                stock_interno += cantidad
                movements.append({
                    'Fecha': fecha,
                    'Tipo': tipo,
                    'Cantidad_Movimiento': cantidad,
                    'Stock_Acumulado': stock_interno,
                    'Detalles': f"{origen} → {destino}"
                })
            elif 'Salida' in tipo or 'Venta' in tipo:
                stock_interno -= cantidad
                movements.append({
                    'Fecha': fecha,
                    'Tipo': tipo,
                    'Cantidad_Movimiento': -cantidad,
                    'Stock_Acumulado': stock_interno,
                    'Detalles': f"{origen} → {destino}"
                })
            else:
                movements.append({
                    'Fecha': fecha,
                    'Tipo': tipo,
                    'Cantidad_Movimiento': cantidad if 'Entrada' in tipo else -cantidad,
                    'Stock_Acumulado': stock_interno,
                    'Detalles': f"{origen} → {destino}"
                })
        
        df_movimientos = pd.DataFrame(movements)
        
        print("\n" + "="*100)
        print(f"📊 HISTORIAL DE STOCK - PRODUCTO {product_id}")
        print("="*100)
        print(df_movimientos.to_string(index=False))
        
        # Análisis de eventos clave
        print("\n" + "="*100)
        print("🔑 EVENTOS CLAVE")
        print("="*100)
        
        # Momentos donde pasó a 0 o cercano
        if len(df_movimientos) > 0:
            min_stock = df_movimientos['Stock_Acumulado'].min()
            min_date = df_movimientos[df_movimientos['Stock_Acumulado'] == min_stock]['Fecha'].values[0]
            print(f"\n📉 STOCK MÍNIMO: {min_stock} unidades")
            print(f"   📅 Fecha: {min_date}")
            
            # Encontrar cuando se quedó en 0 o cerca
            cero_eventos = df_movimientos[df_movimientos['Stock_Acumulado'] <= 1]
            if len(cero_eventos) > 0:
                print(f"\n⚠️  STOCK CRÍTICO (≤ 1 unidad):")
                print(cero_eventos[['Fecha', 'Tipo', 'Stock_Acumulado']].to_string(index=False))
            
            # Reposiciones (entradas después de nivel bajo)
            entradas = df_movimientos[df_movimientos['Tipo'].str.contains('Entrada|Compra', na=False)]
            if len(entradas) > 0:
                print(f"\n✅ REPOSICIONES (Entradas/Compras):")
                print(entradas[['Fecha', 'Cantidad_Movimiento', 'Stock_Acumulado']].to_string(index=False))
            
            # Stock actual
            print(f"\n💾 STOCK ACTUAL: {df_movimientos['Stock_Acumulado'].iloc[-1]} unidades")
        
        # Estadísticas
        print("\n" + "="*100)
        print("📈 ESTADÍSTICAS")
        print("="*100)
        entradas = df_movimientos[df_movimientos['Cantidad_Movimiento'] > 0]
        salidas = df_movimientos[df_movimientos['Cantidad_Movimiento'] < 0]
        
        print(f"  • Total de movimientos: {len(df_movimientos)}")
        print(f"  • Entradas/Compras: {len(entradas)}")
        print(f"  • Salidas/Ventas: {len(salidas)}")
        print(f"  • Unidades recibidas: {entradas['Cantidad_Movimiento'].sum():.0f}")
        print(f"  • Unidades vendidas: {abs(salidas['Cantidad_Movimiento'].sum()):.0f}")
        print(f"  • Primer movimiento: {df_movimientos['Fecha'].min()}")
        print(f"  • Último movimiento: {df_movimientos['Fecha'].max()}")

if __name__ == "__main__":
    get_stock_history(85762)
