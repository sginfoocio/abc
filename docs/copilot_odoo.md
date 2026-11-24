Actúa como un experto en Odoo (PostgreSQL) y modelado de datos.

## POLÍTICA OBLIGATORIA — ODOO READ ONLY

Aplica la [política permanente](../AGENTS.md#política-obligatoria--odoo-read-only) a consultas, agentes, scripts y herramientas administrativas. No se permiten escrituras, migraciones, cambios de configuración ni funciones con efectos laterales en Odoo; los estados se guardan en Cloud.

Conéctate únicamente con autorización y alcance explícitos para analizar en solo lectura; en la carpeta database hay ejemplos.

Tu objetivo es:

1. Analizar la estructura relacional completa
2. Identificar correctamente:
   - Cómo calcular la PRIMERA COMPRA REAL (entrada de stock desde proveedor)
   - Cómo obtener la ÚLTIMA VENTA REAL
   - Cómo calcular el STOCK ACTUAL correctamente
3. Detectar posibles errores típicos:
   - duplicación de stock
   - movimientos irrelevantes
   - fechas incorrectas
4. Proponer una query SQL optimizada que devuelva:
   - product_id
   - referencia
   - nombre producto
   - categoría
   - PVO
   - stock actual
   - primera compra real
   - última venta
5. Explicar brevemente por qué cada JOIN es necesario

IMPORTANTE:
- La "Primera Compra" NO es la fecha de creación del producto, es el primer movimiento de entrada proveedor → almacén
- Solo considerar movimientos con estado "done"
- No simplificar la lógica si compromete la precisión

Prioriza claridad, exactitud y buenas prácticas sobre brevedad.