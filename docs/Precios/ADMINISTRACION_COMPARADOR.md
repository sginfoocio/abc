# Administración del comparador: auditoría y simplificación

## Auditoría funcional previa

Se revisaron las pantallas `app_pages/price_comparator.py` y `app_pages/price_watch.py`,
la ruta protegida por `require_admin_access`, el piloto, los servicios de vigilancia,
localización, monitorización, permisos, presupuesto y sincronización Vytria, junto con
sus pruebas de integración y Streamlit.

| Situación anterior | Consecuencia | Tratamiento |
| --- | --- | --- |
| Ocho destinos de Administración y diez dentro de Vigilancia | Operaciones fragmentadas | Seis destinos principales, sin administración paralela |
| Panel general centrado exclusivamente en el piloto | No representaba la vigilancia actual | Resumen local de productos, fichas, Vytria, precios, colas, alertas e incidencias |
| Configuración web, métodos, fuentes, presupuesto y planes separados | Difícil identificar la configuración efectiva de un competidor | Ficha única por competidor; el conector anterior queda como herramienta avanzada |
| Preparación y ejecución separadas por cada motor | Exigía conocer dos colas | Lote supervisado con selección, vista previa, preparación local y confirmación |
| Contadores de monitorización anteriores al filtro | Cifras ajenas a la selección | Recuentos posteriores a todos los filtros, con total global identificado |
| Tablas/opciones calculadas antes de guardar | Necesidad de recarga manual | Rerender inmediato con mensaje persistido tras altas, grupos, reglas, fuentes y decisiones |
| Comprobaciones por truthiness de fechas/textos SQL | Mensajes como «bloqueado el nan» | Comprobación explícita de nulos; no se inventan fechas |
| Sin procedencia visible del piloto | Parecía necesario volver a localizar | Ficha y origen visibles; reutilización acreditada por candidato confirmado de la misma URL |
| Exportaciones centradas en piloto/alertas | Faltaba trazabilidad detallada del motor | CSV de observaciones e histórico almacenados, sin actualizar por HTTP |

No se han modificado la identidad comercial, comparabilidad, IVA, mercado, stock,
autenticación ni el procedimiento de despliegue. No hay migraciones nuevas.

## Seis apartados

1. **Panel general**: vigilancia actual, cobertura, última lectura por competidor,
   vigencia/origen de Vytria, alertas y bloqueos.
2. **Productos en vigilancia**: listado con retirada conservando histórico, alta individual,
   importación CSV/Excel o EAN pegados, reglas, grupos/frecuencias y localización por producto.
3. **Competidores**: una ficha con permisos por método, fuentes, presupuesto/pausas,
   frecuencia, incidencias y controles avanzados del conector anterior. El estado
   activo se edita aquí; importaciones técnicas ya no ofrece un segundo editor.
4. **Actualizaciones**: lote supervisado, resultados/observaciones, sincronización Vytria
   y ciclo diario existente, y revisión de colas como herramienta avanzada.
5. **Alertas e incidencias**: alertas persistidas, resumen descargable sin envío y
   resolución humana auditada de bloqueos.
6. **Configuración y diagnóstico**: parámetros, importaciones técnicas, exportaciones,
   auditoría/históricos, piloto anterior y recuperación de búsquedas.

## Operación habitual: 50 EAN y seis competidores

1. Añadir los EAN en **Productos en vigilancia**. Se validan contra el catálogo y su
   identidad marca + modelo + color. El alta ya prepara búsquedas, sin HTTP.
2. Revisar la ficha de cada competidor: autorización de los métodos que se usarán,
   fuentes disponibles, presupuesto y frecuencia. Ninguna autorización es implícita.
3. En **Actualizaciones → Lote supervisado**, seleccionar EAN y competidores.
4. Revisar fichas reutilizadas, trabajos necesarios, estado de acceso, frecuencia efectiva
   y HTTP disponibles. El HTTP mínimo no es una reserva ni un máximo prometido: robots,
   listados, paginación y reintentos hacen variable el consumo. Los límites reales se aplican
   por los motores existentes antes de cada petición.
5. **Preparar trabajos sin HTTP**. Las colas conservan sus identificadores, unicidad,
   leases, reintentos e históricos.
6. Confirmar expresamente y ejecutar. Si se marca sincronización Vytria, se leen primero
   **todos los EAN vigilados** mediante la integración existente; después se localizan
   y monitorizan únicamente los pares seleccionados. Cambiar la selección invalida la
   preparación y la confirmación anteriores.
7. Consultar resultados, errores y observaciones. Una segunda ejecución no repite
   búsquedas para fichas verificadas ni actualiza precios todavía vigentes.

Los pares pendientes incluidos en el piloto anterior se indican como tales y se
gestionan en sus controles avanzados; no se eluden sus restricciones. Una ficha ya
verificada puede monitorizarse sin volver a localizarla. Revisión de identidad y
fallos terminales requieren los controles de revisión/reintento existentes.

Las frecuencias no se deducen de un texto: se muestra el intervalo utilizado por el
motor (grupo, si tiene sobrescritura; después competidor; por defecto 24 horas).
Localización y monitorización mantienen planes independientes. Guardar un plan no
activa el interruptor del servidor.

## Estados y exportaciones

**Sin localizar**, **ficha localizada pendiente de precio**, **sin precio**, **sin stock**,
**precio caducado**, **sin autorización**, **bloqueado**, **retirada** y **revisión** son estados
distintos. Un precio observado no comparable puede mostrarse como evidencia, pero no
se utiliza en diferencias ni KPIs.

En **Actualizaciones → Precios y observaciones**, todos los contadores respetan
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

Para verificar si Vytria guardó precios antes de una excepción de visualización,
consultar **Actualizaciones → Precios y observaciones → Precio Vytria y ciclo diario**
y **Configuración y diagnóstico → Auditoría, históricos y exportaciones** sin pulsar
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

Esta rama se apila sobre **PR #112**, con autorización del solicitante. No modifica
su rama ni el worktree original. Orden: integrar #112 primero y después actualizar
esta PR con `main`, revisar el diff y repetir CI. No fusionar ni desplegar sin aprobación.

El Dockerfile incorpora `price_batch.py`. No cambian workflows, secretos, permisos,
volúmenes ni servicios. Planificación y correo no se activan al actualizar la UI.
Las pruebas usan SQLite temporal y competidores simulados, nunca tiendas reales.

## Capturas

Datos simulados de 50 EAN y seis competidores, modo claro. La validación en navegador
comprueba anchos de 1440 y 390 píxeles, sin desbordamiento horizontal de la página
ni excepciones. Las tablas conservan sus controles nativos de búsqueda y columnas.

| Vista | Escritorio | Móvil |
| --- | --- | --- |
| Panel general | [Captura](admin-panel-desktop.png) | [Captura](admin-panel-mobile.png) |
| Lote supervisado | [Captura](admin-lote-desktop.png) | [Captura](admin-lote-mobile.png) |
