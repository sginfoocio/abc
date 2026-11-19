# Administración del comparador: auditoría y simplificación

La navegación actual tiene tres áreas principales: **Comparativa de precios**,
**Administración** y **Diagnóstico técnico**. La selección protegida sigue siendo
de 100 EAN y 14 competidores; no se modifica desde la interfaz sin previsualización
explícita y auditoría.

## Auditoría funcional previa

Se revisaron las pantallas `app_pages/price_comparator.py` y `app_pages/price_watch.py`,
la ruta protegida por `require_admin_access`, el piloto, los servicios de vigilancia,
localización, monitorización, permisos, presupuesto y sincronización Vytria, junto con
sus pruebas de integración y Streamlit.

| Situación anterior | Consecuencia | Tratamiento |
| --- | --- | --- |
| Cinco destinos comerciales y seis subapartados administrativos | Comparación, administración y diagnósticos mezclados | Tres áreas, con productos/ofertas juntos y herramientas técnicas apartadas |
| Panel general centrado exclusivamente en el piloto | No representaba la vigilancia actual | Resumen local de productos, fichas, Vytria, precios, colas, alertas e incidencias |
| Configuración web, métodos, fuentes, presupuesto y planes separados | Difícil identificar la configuración efectiva de un competidor | Ficha única por competidor; el conector anterior queda como herramienta avanzada |
| Preparación y ejecución separadas por cada motor | Exigía conocer dos colas | Lote supervisado con selección, vista previa, preparación local y confirmación |
| Contadores de monitorización anteriores al filtro | Cifras ajenas a la selección | Recuentos posteriores a todos los filtros, con total global identificado |
| Tablas/opciones calculadas antes de guardar | Necesidad de recarga manual | Rerender inmediato con mensaje persistido tras altas, grupos, reglas, fuentes y decisiones |
| Comprobaciones por truthiness de fechas/textos SQL | Mensajes como «bloqueado el nan» | Comprobación explícita de nulos; no se inventan fechas |
| Piloto de 72 horas visible en administración | Podía confundirse con la vigilancia actual | Activación, reanudación, parada y tick retirados; histórico solo de lectura |
| Exportaciones centradas en piloto/alertas | Faltaba trazabilidad detallada del motor | CSV de observaciones e histórico almacenados, sin actualizar por HTTP |

No se han modificado la identidad comercial, comparabilidad, IVA, mercado, stock,
autenticación ni el procedimiento de despliegue. No hay migraciones nuevas.

## Tres áreas

1. **Comparativa de precios**: cifras, posición y gráficas primero; después, tabla de
   productos con precio propio, ofertas por tienda, diferencia, disponibilidad y
   última actualización. Los filtros generales se comparten y los detalles no se
   duplican en una pantalla separada.
2. **Administración**:
   - **Resumen**: productos vigilados, competidores activos, fichas, precios y cola.
   - **Productos**: altas/importación, reglas, grupos/frecuencia y estado de localización.
   - **Tiendas**: una ficha por competidor para métodos, URLs, fuentes, presupuesto,
     frecuencia, incidencias y conectores avanzados.
   - **Por revisar**: confirmar o descartar identidad; la confirmación acreditada
     encola lectura prioritaria en el trabajador existente.
   - **Vigilancia y consumos**: estado/consumos, lote supervisado o continuidad actual.
3. **Diagnóstico técnico**: ajustes, importaciones, históricos/auditoría, colas y
   búsqueda manual avanzada por EAN o modelo/color. Los controles del piloto de 72
   horas ya no se muestran; sus registros permanecen consultables en históricos.

## Flujo diario conservado: 100 EAN y 14 competidores

1. En **Administración → Productos**, gestionar vigilancia sin cambiar silenciosamente
   la selección protegida; importar y retirar conserva el historial.
2. En **Administración → Tiendas**, revisar autorización por método, fuente/URL,
   mercado, moneda, IVA, presupuestos y bloqueos. Guardar una URL no autoriza la
   consulta ni confirma la identidad.
3. En **Administración → Por revisar**, confirmar o descartar cada candidata. Al
   confirmar identidad con lectura, se solicita el precio mediante la cola prioritaria
   existente; se respetan bloqueos, presupuestos, robots y fechas futuras.
4. En **Administración → Vigilancia y consumos**, revisar estado y lote supervisado.
   La preparación no consulta HTTP; ejecutar requiere confirmación explícita y revalida
   su selección.
5. Consultar la comparativa y el histórico. Repetir, refrescar o reiniciar no debe
   duplicar solicitudes; los errores y ofertas no comparables conservan su motivo.
6. La búsqueda manual avanzada conserva la consulta por EAN o modelo/color; no es
   necesaria después de confirmar una ficha y no reemplaza la lectura prioritaria.

Las frecuencias no se deducen de un texto: se muestra el intervalo utilizado por el
motor (grupo, si tiene sobrescritura; después competidor; por defecto 24 horas).
Localización y monitorización mantienen planes independientes. Guardar un plan no
activa el interruptor del servidor.

## Estados y exportaciones

En **Resumen**, los KPI **Igual o más barato** y **Más caro** expresan porcentajes
sobre los productos con comparación válida; sin muestra muestran **Sin datos**.
Las cinco tarjetas de posición usan etiquetas **Productos · …** para identificar
recuentos, no porcentajes: más barato, mismo precio, más caro, sin precio Vytria
y sin oferta comparable. Sus grupos son excluyentes y respetan los filtros generales;
**Ver productos** solo cambia la selección local, sin actualizar precios ni colas.

**Sin localizar**, **ficha localizada pendiente de precio**, **sin precio**, **sin stock**,
**precio caducado**, **sin autorización**, **bloqueado**, **retirada** y **revisión** son estados
distintos. Un precio observado no comparable puede mostrarse como evidencia, pero no
se utiliza en diferencias ni KPIs.

En **Comparativa de precios**, todos los contadores respetan
competidor, marca, estado y búsqueda; se identifica también el total global.
El CSV detallado respeta los productos/competidor seleccionados e incluye sus
observaciones históricas, no solo el último precio:
EAN, marca, modelo, color, competidor, URL, precio, promoción, moneda, disponibilidad,
IVA, fecha original, última comprobación, identidad, método, origen de ficha,
estado de monitorización y caducidad. La última comprobación procede del recibo
`offer_checks` o del trabajo que referencia la oferta; si no existe, se conserva
la fecha original de observación. La vigencia/estado del trabajo solo se atribuye
a su `last_offer_id`; no se inventan una fecha reciente ni una caducidad nueva
para una observación antigua.

Abrir alertas ya no genera alertas nuevas: la detección de caducidad sigue en las
operaciones del monitor. Leer vistas/exportaciones no lanza peticiones.

### Fechas históricas y comprobación de sincronización (solo lectura)

La analítica convierte las fechas ISO 8601 a UTC antes de filtrar, ordenar o calcular
vigencia. Admite precisión variable, offsets y fechas antiguas sin zona (UTC).
Las observaciones sin fecha válida quedan fuera del cálculo; las fechas no vacías
inválidas generan un aviso con campo y cantidad, sin volcar datos. Una comprobación
nula/inválida no renueva un precio: se conserva su fecha de captura válida.
No se reescriben los históricos ni se generan observaciones para rellenar gráficos.
Las lecturas comerciales actuales también filtran y eligen el último precio por
instante UTC (incluida la importación manual), no por `MAX` ni ordenación de textos.
Una fecha futura de captura no entra en los KPIs, y una comprobación anterior,
inválida o futura no sustituye la fecha de captura válida.

Para verificar si Vytria guardó precios antes de una excepción de visualización,
consultar el precio propio desde su resumen local y **Diagnóstico técnico → Históricos y auditoría** sin pulsar
sincronización ni ejecución. Las lecturas
existentes `VytriaPriceSync.last_run()`, `prices()` y `price_history(ean)` no consultan
Odoo ni escriben datos. La escritura de precios/histórico se confirma en la
transacción de sincronización, no en el render del gráfico.

Si la excepción impide acceder a Cloud, ejecutar únicamente estas consultas sobre
una conexión SQLite abierta en modo de solo lectura (no hace falta una copia completa
ni un acceso nuevo). El primer resultado identifica la ejecución del incidente:

```sql
SELECT id, trigger, started_at, finished_at, status, monitored,
       inserted, changed, unchanged, issues
FROM vytria_sync_runs ORDER BY id DESC LIMIT 10;

SELECT h.sync_run_id, COUNT(*) AS observaciones_guardadas,
       COUNT(DISTINCT h.ean) AS productos
FROM vytria_price_history h
WHERE h.sync_run_id IN (SELECT id FROM vytria_sync_runs ORDER BY id DESC LIMIT 10)
GROUP BY h.sync_run_id ORDER BY h.sync_run_id DESC;

SELECT v.ean, v.price, v.currency, v.source, v.validation_status,
       v.synced_at, v.changed_at, v.stale_since
FROM vytria_prices v
ORDER BY julianday(v.synced_at) DESC, v.ean LIMIT 50;
```

`unchanged` también acredita precios reconfirmados: no crea una fila histórica nueva.
Un resultado `ok`/`partial` debe contrastarse con sus contadores, precios y fechas;
una excepción de render por sí sola no acredita el resultado de producción.
No repetir una sincronización para diagnosticarlo.

### Orden de las opciones

Las listas de datos del comparador y sus seis apartados comparten orden natural,
sin distinguir mayúsculas ni acentos: `Óptica 2` precede a `Optica 10`.
Competidores se ordenan por nombre visible; marcas, categorías y grupos por nombre.
El mismo criterio admite colecciones cuando se ofrezcan como lista (actualmente se
configuran como fuentes, no hay un selector de nombres de colección).
Los productos se ordenan por marca, modelo y código de color, con EAN como desempate
e identificador. Los nombres repetidos se desempatan por identificador estable.
No se sustituyen claves de widgets ni valores guardados por índices ordenados.

Se conservan los órdenes funcionales de navegación, pasos, métodos, estados,
intervalos, decisiones y acciones, y los encabezados «Todos» o vacíos.
Reordenar una lista no prepara colas, autoriza accesos ni realiza consultas.

## Dependencia e integración

Este cambio continúa en **PR #144**, rama `fix/comparator-commercial-first`.
No abre otra PR, no modifica la selección de 100 EAN/14 competidores y no fusiona ni
despliega. La validación CI/Docker debe corresponder al HEAD final de esa PR.

El Dockerfile incorpora `price_batch.py`. No cambian workflows, secretos, permisos,
volúmenes ni servicios. Planificación y correo no se activan al actualizar la UI.
Las pruebas usan SQLite temporal y competidores simulados, nunca tiendas reales.

## Capturas

Capturas sintéticas sin consultas comerciales ni datos productivos. «Antes» muestra
la navegación anterior; «Después» se genera desde las rutas reales del comparador
con un catálogo/oferta sintéticos y solicitudes HTTP prohibidas por el harness.
El harness verifica que no haya excepciones ni desbordamiento horizontal.

| Vista | Antes | Después |
| --- | --- | --- |
| Comparativa | [Captura anterior](../../PRICE_COMPARATOR_PREVIEW/resumen-desktop.png) | [Captura nueva](PR144_ADMIN_SIMPLIFICATION/after-light-comparator-desktop.png) |
| Administración | [Captura anterior](../../PRICE_COMPARATOR_PREVIEW/administracion-desktop.png) | [Captura nueva](PR144_ADMIN_SIMPLIFICATION/after-light-comparator-admin-desktop.png) |
| Diagnóstico técnico | [Controles anteriores](../../PRICE_COMPARATOR_PREVIEW/administracion-desktop.png) | [Captura nueva](PR144_ADMIN_SIMPLIFICATION/after-light-comparator-diagnostics-desktop.png) |

Para repetir las capturas «Después»:

```text
python scripts/cloud_identity_visual_harness.py docs/Precios/PR144_ADMIN_SIMPLIFICATION --label after --comparator-only
```
