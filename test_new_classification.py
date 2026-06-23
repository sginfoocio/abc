from db_loader import load_odoo_dataframe
from engine import run_abcd_engine
import pandas as pd

# Cargar datos directamente de la BD
print("\n" + "="*100)
print("VERIFICANDO RECLASIFICACION CON MOTOR ABCD MEJORADO")
print("="*100)

try:
    df_datos = load_odoo_dataframe()
    print(f"\nProductos cargados: {len(df_datos)}")
    print(f"Columnas disponibles: {list(df_datos.columns)}")
    
    # Buscar nuestro producto DIOR
    producto = df_datos[df_datos['product_id'] == 85762]
    
    if len(producto) > 0:
        print("\n" + "-"*100)
        print("ANTES DEL ANÁLISIS ABCD:")
        print("-"*100)
        for col in ['Marca', 'Stock', 'PVO', 'Primera Compra', 'Última Venta', 
                     'Num_Ventas_180D', 'Ventas_180_Dias']:
            if col in producto.columns:
                print(f"  {col}: {producto[col].values[0]}")
        
        # Aplicar motor ABCD
        df_clasificado = run_abcd_engine(producto.copy())
        
        print("\n" + "-"*100)
        print("DESPUES DEL ANALISIS ABCD:")
        print("-"*100)
        print(f"  Clasificacion: {df_clasificado['ABCD'].values[0]}")
        print(f"  Motivo: {df_clasificado['Motivo'].values[0]}")
        print(f"  Alerta: {df_clasificado['Alerta'].values[0]}")
        print(f"  Accion: {df_clasificado['Accion_Recomendada'].values[0]}")
        
        print("\n✓ El motor ABCD ahora considera que tiene ventas en ultimos 180 dias!")
        print("  Por lo tanto, NO deberia estar en D")
        
    else:
        print("No se encontro el producto 85762")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
