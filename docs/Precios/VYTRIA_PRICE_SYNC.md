# Sincronización del PVP Vytria desde Odoo

## Precio final público de PrestaShop (preparación, deshabilitada por defecto)

La tarifa 59 sigue siendo la referencia efectiva de Odoo. PrestaShop puede aplicar
ofertas posteriores a esa tarifa: son diferencias comerciales, no errores de Odoo.
Cuando se habilita el lector, solo para la intersección del manifiesto existente de
100 EAN con los vigilados activos y autorizados por la ejecución, se compara el
resultado vigente del motor nativo. Una lectura fallida o caducada conserva el
último resultado y su fecha real, pero no se compara ni se sustituye por Odoo.
Sin habilitación y sin lectura anterior, la operativa existente no cambia.

**Contraste público (2026-11-20):** un GET sin sesión autenticada a la
[ficha Prada PR 17WS 2AU8C1](https://www.vytria.com/es/pr-17ws-2au8c1.html)
mostró EAN `8056597435581`, calibre 49, país `ES` (6), moneda `EUR` (1),
tienda `Vytria` (1) y `is_logged=false`. La ficha publicó **232,05 €**,
también en `product:price:amount`, y mostró **273,00 €** como precio anterior.
El producto público indicado es `498`; el `68` del selector es un valor de
atributo, no el ID de la combinación. La página no identifica `427094`, ni
confirma el grupo fiscal, cómo se aplicó el IVA o qué representan los 225,62 €.
El importe público no se usa como semilla, ni esta URL se usa como lector automático.

**Antes de habilitar:** no se facilitó la conexión MySQL ni se confirmó una
salida existente del motor para esta instalación. El lector está preparado para
una relación MySQL de solo lectura (vista o instantánea existente, que deberá
conectar el responsable de PrestaShop con el cálculo nativo). No hay cálculo
SQL aproximado, nuevo endpoint ni cambios en PrestaShop en este trabajo.

1. Con el usuario existente, revisar `SELECT VERSION()` (servidor MySQL),
   `INFORMATION_SCHEMA.COLUMNS` (prefijo/esquema) y la versión de la aplicación
   (administración o configuración de PrestaShop). Verificar `product[_shop]`,
   `product_attribute[_shop]`, `shop`, `country`, `currency`, `configuration`,
   `specific_price`, `specific_price_priority`, `specific_price_rule`,
   `tax_rules_group`/`tax_rule`/`tax` y módulos/hook/overrides de precios. Estas
   son tablas candidatas, no un inventario confirmado. Comprobar `427094` por
   separado en `id_product`, `id_product_attribute` e `id_specific_price`, y
   cruzar con EAN, tienda y calibre; el mismo número puede existir en varias tablas.
2. Confirmar con el administrador el grupo anónimo, el contexto fiscal ES
   (incluidos criterio y ámbito geográfico de dirección, regla y tratamiento de
   IVA), la tienda y moneda, y contrastar el resultado del motor con la ficha
   anónima para el ejemplo antes de extenderlo a los restantes EAN. Verificar base
   e impacto de combinación, precio específico y regla de catálogo seleccionados,
   prioridades, fechas, cantidad y personalizaciones activas. No usar datos de
   cupones, transporte o descuentos condicionados al carrito.
3. Inyectar por el gestor de secretos aprobado en el entorno del proceso
   `PRESTASHOP_MYSQL_HOST`, `_PORT` (3306 si se omite), `_DATABASE`, `_USER`,
   `_PASSWORD`, `_SSL_CA` (ruta al certificado CA), `_PREFIX` y
   `_NATIVE_QUOTES` (nombre de la relación existente; sin esquema ni credenciales).
   Aquí los nombres abreviados comparten el prefijo `PRESTASHOP_MYSQL`.
   Usar TLS con verificación de identidad, permisos exclusivamente `SELECT` sobre
   los objetos necesarios y transacciones `READ ONLY`. Configurar los IDs
   confirmados `PRESTASHOP_PRICE_SHOP_ID`, `_GROUP_ID`, `_COUNTRY_ID`,
   `_CURRENCY_ID` (prefijo `PRESTASHOP_PRICE`). Solo después de validar la
   instalación y obtener aprobación, `PRESTASHOP_PRICE_ENABLED=1`.

**Contrato de la relación nativa:** columnas `ean` (GTIN-13 de texto, incluye
ceros), `shop_id`, `country_id`, `currency_id`, `group_id` y `payload` (JSON).
Una sola fila por EAN y contexto. La salida representa el precio unitario final
que realmente publica el motor activo (incluidos módulos y reglas de catálogo)
y contiene:

- `product_id`, `combination_id` (0 solo si no hay combinación), IDs del
  contexto como en las columnas, `market: "ES"`, `currency: "EUR"`,
  `customer_id: 0`, `quantity: 1`, `tax_included: true`, `shipping_included: false`
  y `cart_discount: false`.
- `price`: importe final decimal en texto (p. ej. `"232.05"`); `engine`:
  identificación/versionado del productor nativo; `fiscal_context`: descripción
  del grupo de reglas/ámbito fiscal y tratamiento aplicado, sin datos personales.
- `observed_at` y `valid_until` con zona horaria. El productor debe acotar
  `valid_until` al siguiente momento de cambio conocido (incluido el inicio de
  una promoción aún inactiva). ABC nunca usa una lectura más de 24 horas.
- `offer`: `null` si no aplica; en otro caso `reference` (regla/oferta inequívoca),
  `starts_at`/`ends_at` (ISO con zona, o `null` si abierto) y `conditions`
  (`min_quantity` entero; además tienda, grupo, país, moneda, combinación,
  prioridad, tipo de reducción y tratamiento fiscal según corresponda).
- `other_calibres`: mapa de IDs de otras combinaciones del mismo modelo/color
  a importes finales del mismo contexto, o `{}` si no hay otros. Las diferencias
  se señalan, sin elegir otra combinación ni intercambiar sus precios. Enumerar
  solo los calibres del producto; no incorporar EAN adicionales al manifiesto.

La relación es un contrato de integración **aún no validado**. Una vista que
solo reste un porcentaje o elija una fila de `specific_price` no cumple el
contrato. Las pruebas offline verifican la aceptación/rechazo de resultados
nativos simulados y la conservación del precio; no certifican la precedencia,
el IVA ni los módulos de la tienda real.

En SQLite, `prestashop_prices` guarda la última lectura, vencimiento, último
intento e incidencia; `prestashop_price_history` guarda cambios de precio,
oferta o contexto. Las reconfirmaciones solo actualizan las fechas del estado
vigente. Ambas tablas se vinculan por EAN a `vytria_prices` y al histórico
existente de Odoo; no alteran dicha tarifa ni la identidad/disponibilidad del
comparador. La sección de administración distingue las dos fuentes.

## Referencia Odoo y operativa anterior

La integración Odoo sustituye la importación manual de precios propios. Es **solo de lectura**:
no se modifica nada en Odoo ni en PrestaShop.

## Fuente del precio

Auditoría de solo lectura sobre `DiagonalDBProd` (PostgreSQL 12.8, sesión `READ ONLY`):

- Tarifa real de Vytria España: **ID 59** («Vytria TARIFA_ESPAÑA», EUR, `website_id=10`, canal
  `prestashop`). La 17 es la versión anterior (`website_id=4`).
- Precio final: tabla `prestashop_pricelist_item_pricelist` (`pricelist_id=59`), unida a
  `prestashop_pricelist_item` (`product_id` → `product_product`). Se guarda **tal cual**: ya incluye
  los descuentos de la tarifa y el IVA. No se vuelve a descontar ni se suma IVA.
- Descuento de la tarifa: `list_price × (1 − descuento %)`, con el descuento del ítem
  `1_pricelist_map` de la tarifa 59 asociado a los mapas del producto
  (`product_pricelist_map_product_template_rel`), redondeado a 1,00. El campo
  `product_template.pricelist_map_id` **no** es este mapa.
- IVA: un único impuesto de venta por producto con `price_include = True` (10 % o 21 %). El
  porcentaje se guarda solo como información.
- Los 12 EAN auditados coinciden exactamente entre `list_price`/mapa, precio de integración y
  precio sincronizado (probado también contra Odoo real, con SQLite temporal).
- No se usan `diagonal_vytria_int_catalog_item*` (0 filas) ni el canal 5.

**Pendiente: contraste directo con los precios publicados en PrestaShop. No está validado.**

### Auditoría del ciclo diario de vigilancia

Se ha revisado el lector `SqlOdooPriceReader`, las consultas `PRODUCTS_SQL`/`PRICES_SQL`,
la validación de impuestos/mapas, los scripts de auditoría y sus pruebas, y las
integraciones Odoo existentes. El código disponible no contiene un cliente directo
del catálogo/checkout de PrestaShop. No se han consultado servicios reales en esta revisión.

**Decisión aprobada:** reutilizar la tarifa 59 como fuente operativa de Vytria,
identificada siempre como **Odoo tarifa 59**, sin afirmar que se haya contrastado
el precio publicado en la tienda. La validación de la fórmula de Odoo no acredita
por sí sola promociones o reglas particulares del checkout de PrestaShop.

Alternativas no utilizadas:

- `product_template.list_price`: precio base; no representa por sí solo el PVP
  descontado de la tarifa España. Solo sirve como comprobación, no como sustituto.
- Tarifa 17, canal 5 y tablas de integración vacías: no son la fuente auditada
  actual. No se crea una nueva integración ni se recalculan descuentos/IVA.
- API o scraping de PrestaShop: no existe un lector reutilizable ni evidencia
  de configuración/autorización para validarlo aquí. Contraste pendiente.
- Excel: contingencia supervisada existente, no fuente automática diaria.

## Datos guardados (SQLite del comparador, volumen persistente)

| Tabla | Contenido |
|---|---|
| `vytria_prices` | Precio vigente por EAN: precio, moneda, IVA incluido, % IVA, origen, tarifa, IDs de origen (producto, plantilla, catálogo, fila de precio), fecha de origen, precio recalculado, estado de validación, fechas de primera/última variación y de sincronización. |
| `vytria_price_history` | Una fila por cambio (precio, IVA, moneda, estado). Nunca se borra. |
| `vytria_sync_runs` / `vytria_sync_issues` | Registro de cada ejecución y de las incidencias por EAN. |
| `vytria_sync_settings` | `auto_enabled` (por defecto `0`). |
| `vytria_price_alerts` | Alertas de cambios relevantes de precios validados vigilados, enlazadas de forma única al cambio de histórico. Se muestran junto a las alertas de competencia; sin envío de correo. |

Solo se sincronizan los EAN del catálogo del comparador (consulta acotada por código de barras;
los UPC-12 se buscan con y sin cero inicial). La escritura es incremental: si el origen no cambia,
solo se actualiza `synced_at` y no se crea histórico.

## Validación cruzada y estados

El precio de origen nunca se modifica; la discrepancia se registra. Solo `validated` se compara
con la competencia (`comparable = 1`).

| Estado | Significado |
|---|---|
| `validated` | El precio coincide con la fórmula de la tarifa (tolerancia 0,01 €). |
| `price_mismatch` | Difiere del recálculo. Se guarda el precio recibido y el esperado. |
| `no_map` | El producto no tiene mapa con regla en la tarifa 59. |
| `multiple_maps` | Mapas con resultados distintos. |
| `exceptional_rule` | Descuento negativo (p. ej. `Vytria CT0649S`, −300 %), nombre del mapa distinto del % aplicado (p. ej. «65 %» que en la 59 aplica 45 %), recargo, cantidad mínima, fechas, regla por producto o cálculo no `formula`. |
| `tax_issue` | Sin IVA, más de un IVA, impuesto no porcentual o no incluido en el precio. |
| `currency_issue` | Moneda distinta de EUR. |
| `discontinued` | Variante/plantilla inactiva o no vendible. |

Incidencias que **no sobrescriben** el último precio confirmado: `not_in_odoo`, `no_price`
(sin fila, inactiva o ≤ 0), `multiple_prices`, `ambiguous_product`, `unexpected_catalog`.
En el comparador, un EAN sincronizado sin precio `validated` queda sin precio propio (no se usa el
Excel); un EAN nunca sincronizado conserva el precio importado manualmente.

## Ejecución

- **Manual**: botón «Actualizar precios Vytria» (Análisis comercial → Comparador de precios).
- **Diaria**: interruptor «Sincronización diaria automática», **desactivado por defecto**. Solo
  actúa `python vytria_price_sync.py --scheduled` (se omite si está desactivado o si hubo una
  sincronización correcta en las últimas 24 h, salvo intervalo configurado). Esta PR **no** crea ningún disparador ni servicio;
  activarlo en producción requiere un job aprobado que invoque ese comando.
- **Concurrencia**: `FileLock` junto a la base; un segundo intento recibe «sincronización en curso».
- **Errores y reintentos**: 3 intentos con espera 2 s / 4 s ante fallos de conexión; si falla, se
  conservan los precios y se registra solo el tipo de error (sin host ni credenciales).
- **Conexión**: `load_db_config()` (`DB_*`), `default_transaction_read_only=on`,
  `SET TRANSACTION READ ONLY`, `statement_timeout=30000`; solo `SELECT`.

## Contingencia

La importación Excel de precios queda en un desplegable «Contingencia administrativa». Un precio
sincronizado desde Odoo tiene prioridad sobre el manual para el mismo EAN.

## Limitaciones

- Sin precio promocional ni disponibilidad: la tarifa 59 ya incorpora los descuentos y la
  disponibilidad no procede de esta fuente.
- Mercado fijo `ES` y moneda EUR.
- Los productos con mapas especiales quedan marcados para revisión y no entran en la comparación
  hasta confirmarse.
- La muestra auditada (12 EAN) no contenía mapas 65 % ni CT0649S: se cubren con datos de prueba.
- El Dockerfile copia `vytria_price_sync.py`; sin él la página del comparador no arranca.

## Revisión previa al merge (PR #88)

- **Vigencia**: un precio sincronizado solo participa en comparaciones si es alidated, comparable, sin incidencia abierta y con sincronización de menos de 7 días (SYNCED_PRICE_MAX_AGE_DAYS). Si no, se muestra como stale (incidencia) o xpired (antiguo) y no se compara.
- **Incidencias**: ante un fallo de un EAN con precio previo se conserva precio e histórico, se marca `stale_since`/`stale_reason` y no se actualiza `synced_at`. Se limpia al volver a sincronizar correctamente.
- **Interfaz**: el botón lanza la sincronización en un hilo en segundo plano (bloqueo de fichero junto a la SQLite); la página se refresca cada 2 s mientras corre. Las ejecuciones huérfanas en `running` se cierran como `error` en la siguiente.
- **Migración**: solo `CREATE TABLE IF NOT EXISTS` de tablas `vytria_*`; no se tocan tablas existentes. Ruta SQLite (PR #87) intacta.
- **Pendiente**: contraste con PrestaShop y disparador diario (desactivado).

## Operativa diaria de productos vigilados

- `python vytria_price_sync.py --watched`: actualización **manual supervisada**
  exclusivamente de `watch_products.active=1`. También disponible, con confirmación,
  en Vigilancia automática → Precios y monitorización → Precio Vytria y ciclo diario.
- `python vytria_price_sync.py --scheduled --watched`: entrada diaria de los
  vigilados; no hace nada sin `auto_enabled=1`. Reutiliza el lector, bloqueo,
  ejecuciones, incidencias, precios e históricos del sincronizador anterior.
- `PriceMonitor.tick()` llama a esta sincronización **antes** de procesar los
  competidores habilitados. Requiere tanto el interruptor de servidor
  `PRICE_MONITOR_SCHEDULE_ENABLED` como `auto_enabled=1` de Vytria. Ninguno se
  activa al importar módulos, abrir la pantalla o desplegar.
- Frecuencia inicial: **24 horas**. Se aprovecha la fecha persistente por EAN,
  no solo la última ejecución global: añadir un vigilado sin precio provoca
  su sincronización en la siguiente pasada aunque el resto ya esté actualizado.
  Los intervalos configurados menores se respetan; uno mayor no amplía la
  vigencia diaria de los productos vigilados.
- Lectura acotada al conjunto vigilado, sin límite estructural de 50 productos.
  Las ejecuciones legacy/manuales de todo el catálogo siguen disponibles.
- Ante fallos de conexión hay tres intentos 2/4 s; una ejecución diaria parcial
  o fallida tiene además una pausa persistente de **30 min** antes de reintentar.
  Una ejecución interrumpida se recupera mediante el bloqueo existente y se
  marca como interrumpida; el fingerprint evita duplicar históricos/alertas.
- Los precios **vigilados** caducan a las 24 h exactas (o antes si la vigencia
  global del comparador es menor); se rechazan fechas futuras o ilegibles.
  Diagnósticos, inventario y dashboard reutilizan `vytria_price_states`.
  La vigencia legacy de productos no vigilados no cambia.
- Si falla la lectura, se marca como desactualizado el último precio vigilado:
  no se cambia su valor, `synced_at` ni histórico, pero no se usa como vigente.
  Un valor vacío, no positivo, infinito/NaN, ambiguo o inconsistente nunca
  reemplaza un precio vigilado previamente validado. La incidencia conserva
  el motivo y, ante discrepancia, el precio recibido. Una lectura válida
  posterior limpia el estado, incluso si el precio no ha cambiado.
- Una incidencia de Vytria no impide conservar observaciones de competencia:
  las diferencias y nuevas alertas de «más barato que Vytria» no se calculan
  con el precio propio desactualizado. No hay fallback silencioso a Excel
  cuando ya existe un precio sincronizado del EAN.
- El histórico de Vytria y competencia y sus gráficos ya existentes se
  reutilizan. Los cambios válidos vigilados que superan el umbral vigente
  generan una alerta persistente única por fila histórica; una simple
  reconfirmación del mismo precio no genera otra.

Migración: se añade únicamente `vytria_price_alerts` con
`CREATE TABLE IF NOT EXISTS`, sin modificar/borrar datos anteriores ni exigir
PostgreSQL para el comparador. PostgreSQL solo es el origen Odoo, como antes.
