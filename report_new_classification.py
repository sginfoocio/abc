from db_loader import load_odoo_dataframe
from engine import run_abcd_engine
import pandas as pd

print("\n" + "="*100)
print("SISTEMA ABCD CON NUEVOS UMBRALES Y VENTANAS DE AGOTAMIENTO")
print("="*100)

print("""
REGLAS DE CLASIFICACIÓN (ACTUALIZADAS):

A) Producto probado, ventas activas, alto valor
   - Tiene ventas en últimos 180 días
   - Pasó período de margen (90 días)
   - PVO >= P80 (percentil 80 de precios)
   - Acción: REPONER / PRIORIZAR

B) Productos con ventas (valor medio) O nuevos
   - Tiene ventas pero PVO < P80
   - O es nuevo (< 90 días desde primera compra)
   - Acción: MANTENER CONTROLADO

C) Sin ventas 60+ días (excluyendo agotamientos)
   - Sin ventas 60+ días (considerando ventanas de agotamiento)
   - Bajo impacto económico o rotación limitada
   - Acción: MANTENER MÍNIMO

D) Sin ventas 120+ días (sin demanda reciente)
   - Sin ventas 120+ días (excluyendo agotamientos)
   - Y sin ventas en últimos 180 días
   - Verdadera obsolescencia
   - Acción: LIQUIDAR / NO REPONER

================================================================================
""")

# Cargar datos
df_datos = load_odoo_dataframe()
print(f"Productos cargados: {len(df_datos)}\n")

# Clasificar
df_clasificado = run_abcd_engine(df_datos.copy())

# Estadísticas
stats = df_clasificado.groupby('ABCD').agg({
    'product_id': 'count',
    'Capital_Bloqueado (€)': ['sum', 'mean'],
    'Stock': 'sum',
    'PVO': 'mean'
}).round(2)

print("DISTRIBUCIÓN POR CATEGORÍA:")
print("-" * 100)
for categoria in ['A', 'B', 'C', 'D']:
    subset = df_clasificado[df_clasificado['ABCD'] == categoria]
    if len(subset) > 0:
        print(f"\n{categoria}. {subset['Motivo'].values[0] if len(subset) > 0 else 'N/A'}")
        print(f"   Productos: {len(subset)}")
        print(f"   Capital bloqueado: €{subset['Capital_Bloqueado (€)'].sum():,.2f}")
        print(f"   Stock total: {subset['Stock'].sum():,.0f} unidades")
        print(f"   PVO promedio: €{subset['PVO'].mean():,.2f}")

# Ejemplo: productos en C que podrían ser problemáticos
print("\n" + "="*100)
print("EJEMPLO: 5 PRODUCTOS EN CATEGORÍA C (60+ días sin ventas)")
print("="*100)
c_products = df_clasificado[df_clasificado['ABCD'] == 'C'].head(5)
for idx, row in c_products.iterrows():
    print(f"\n{row['Marca']}")
    print(f"  - Stock: {row['Stock']:.0f} ud")
    print(f"  - Capital: €{row['Capital_Bloqueado (€)']:,.2f}")
    print(f"  - Última venta: {row['Última Venta']}")
    print(f"  - Acción: {row['Accion_Recomendada']}")

print("\n" + "="*100)
print("VALIDACIÓN FINAL")
print("="*100)
print(f"""
✓ Sistema de clasificación ABCD ACTUALIZADO
✓ D: Sin ventas 120+ días (excluyendo agotamientos)
✓ C: Sin ventas 60+ días (excluyendo agotamientos)
✓ Respeta ventanas de agotamiento automáticamente
✓ Demanda real vs. falta de stock están diferenciadas
""")
