# Vigilancia automática de precios — Fases 1 a 4

Cloud tiene su propio motor de localización y vigilancia de precios. Se ejecuta en nuestra infraestructura (`s2026`), lo mantenemos nosotros y **no depende de ninguna API externa de búsqueda**. Reutiliza `price_web.py`, `price_discovery.py`, `price_pilot.py`, `price_scheduler.py` y los extractores actuales.

La fase 1 crea las bases para gestionar miles de EAN y muchos competidores sin registrar URLs a mano. El piloto de 20 EAN (`price_pilot.py`, tablas `pilot_*`) **no cambia**: sigue funcionando hasta que se valide el nuevo motor.

Ubicación en Cloud: **Comparador de precios → Administración → Vigilancia automática**. Al abrir la pantalla no se realiza ninguna petición HTTP.

## Contenido de la fase 1

| Bloque | Módulo | Tablas |
| --- | --- | --- |
| Lista de vigilancia | `price_watch.py` | `watch_products`, `watch_groups`, `watch_rules` |
| Autorizaciones por competidor y método | `price_access.py` | `access_methods` |
| Presupuestos por competidor | `price_access.py` | `access_budgets`, `access_requests` |
| Bloqueos (CAPTCHA, 401/403/407/429) | `price_access.py` | `access_incidents` |
| Auditoría de decisiones | `price_access.py` | `access_audit` |
| Interfaz de administración | `app_pages/price_watch.py` | — |

La migración solo añade tablas y es idempotente (`CREATE TABLE IF NOT EXISTS`). No modifica ninguna tabla existente.

## Selección de productos

Hay tres modos de selección. Los tres aplican la misma validación:

1. **Individual:** se eligen productos del catálogo del comparador.
2. **Importación masiva:** se pegan EAN o se sube un CSV o Excel con una columna `EAN`, `GTIN` o `EAN13`. También se puede incorporar a la vez el fichero completo al catálogo; para ello se reutiliza `import_products`.
3. **Reglas:** filtran por marca, categoría, prefijo de modelo o texto contenido en el nombre o el modelo. El catálogo no tiene un campo «colección»; el prefijo de modelo o el texto cumplen esa función. Las reglas se aplican de forma explícita y **solo añaden** productos.

Un EAN solo se acepta si cumple tres condiciones:

- Es un GTIN-12 o GTIN-13 válido.
- Está en el catálogo.
- Tiene **marca, modelo y color**, que forman la identidad comercial que usará la localización. El calibre no interviene.

Al retirar un producto, este se desactiva y su histórico se conserva.

## Frecuencia de monitorización

El intervalo se resuelve en este orden:

1. El del **grupo** del producto.
2. Si el grupo no tiene uno, el del competidor (`competitor_schedule.interval_hours`).
3. Si no hay ninguno, **24 h**.

## Gobierno de acceso

### Métodos (por competidor)

| Método | Uso previsto |
| --- | --- |
| Fichas de producto | Consultar y monitorizar fichas concretas |
| Buscador interno | Usar el buscador público de la tienda cuando esté permitido |
| Sitemap e índices | Leer los sitemaps declarados |
| Catálogo, marcas y categorías | Navegar de forma dirigida por los listados públicos y sus enlaces internos |
| Feed / API del competidor | Usar un feed o una API propia del competidor, con autorización |
| Búsqueda externa (opcional) | Solo como punto de extensión: **desactivada** salvo que se defina `PRICE_EXTERNAL_SEARCH_ENABLED=1` |

Reglas de autorización:

- **Ningún método está autorizado por defecto.** Que una página sea pública o que el robots.txt sea permisivo no equivale a una autorización.
- Autorizar o prohibir un método exige indicar el responsable y una justificación. Cada cambio queda auditado.
- `robots.txt` se sigue comprobando en cada URL, aunque el método esté autorizado.
- Si un requisito conocido indica que «robots.txt prohíbe la búsqueda», el buscador interno queda marcado automáticamente como **prohibido**.

### Presupuestos

Los presupuestos se editan desde la interfaz, sin tocar el código, y se calculan sobre una ventana móvil de 24 h.

- Valores iniciales por competidor: 200 consultas al día y 10 por minuto.
- La pausa aplicada es la mayor entre la configurada y `competitor_web_config.pause_seconds`.

Cada petición, incluida la de `robots.txt`, se **reserva antes de lanzarla** dentro de una transacción `BEGIN IMMEDIATE`. Por eso varios procesos a la vez no pueden superar un límite.

### Bloqueos

`GovernedFetcher` envuelve el `WebFetcher` existente:

- **HTTP 429:** se abre una incidencia que espera el plazo de `Retry-After`. Si no viene esa cabecera, espera 1 h, con un máximo de 24 h. Pasado el plazo, se reanuda sola.
- **HTTP 401, 403 y 407, y CAPTCHA:** se abre una incidencia que **requiere una decisión humana**, con responsable y nota. Hasta entonces, se suspenden todos los trabajos de ese competidor.
- Las incidencias abiertas no se duplican.
- No se intenta evadir ninguna protección.
- Un producto bloqueado o sin autorización **no** se marca como inexistente. La fase 2 lo registra como «localización no disponible por restricciones de acceso».

## Caso Domanci (revisado el 3-11-2026)

### robots.txt (PrestaShop, sin línea `Sitemap:`)

| Ruta | Estado |
| --- | --- |
| `/es/busqueda`, `?q=`, `?search_query=`, `?order=`, `?n=`, `?tag=` | Prohibido |
| Cuentas, carrito y pedidos | Prohibido |
| Fichas (`/es/gafas-de-sol/<marca>-<modelo>-<color>-<calibre>.html`) | No prohibido |
| Categorías (`/es/gafas-de-sol`, `/es/gafas-de-sol/mujer`…) | No prohibido |
| Marcas (`/es/marca/prada`…) | No prohibido |
| Paginación (`?page=N`) | No prohibida |

El slug de la URL ya contiene marca, modelo y color. Así, un listado de marca permite preseleccionar candidatos **sin abrir sus fichas**: solo se consultan las fichas cuyo slug coincide con un producto vigilado.

### Condiciones generales (`/es/condiciones-generales`)

- Permiten usar la web «únicamente para consultar información, realizar pedidos y gestionar sus compras».
- Prohíben reproducir contenidos sin autorización.
- No mencionan expresamente los robots ni la extracción automatizada.

### Conclusión

- **Existe una ruta técnica** permitida por robots.txt: listado de marca o categoría → paginación `?page=N` → ficha.
- **No hay una autorización expresa** para consultarla de forma automatizada.
- Por eso, los métodos *Catálogo, marcas y categorías* y *Fichas de producto* de Domanci quedan **pendientes de una decisión humana**: aceptar el riesgo con un volumen bajo y documentado, o pedir confirmación a `info@domanci.com`.
- El buscador interno sigue **prohibido**.
- Sin esa decisión, el motor registrará «localización no disponible por restricciones de acceso» y no intentará nada más.

## Fase 2: motor de localización propio (`price_locator.py`)

Ubicación: **Vigilancia automática → Localización automática**. Al añadir productos a la vigilancia se crean sus búsquedas en todos los competidores activos (sin HTTP). Las búsquedas se procesan por lotes con «Procesar lote» o con la línea de órdenes:

```bash
python price_locator.py enqueue               # prepara la cola (idempotente)
python price_locator.py run --max-jobs 200 --deadline 900 [--competitor ID]
python price_locator.py status
```

La ejecución periódica (cron o planificador de `s2026`) es una decisión de despliegue: esta fase no la activa.

### Tablas nuevas (solo `CREATE TABLE IF NOT EXISTS`)

| Tabla | Uso |
| --- | --- |
| `locate_jobs` | Cola persistente: un trabajo por producto × competidor (`UNIQUE`), con estado, prioridad, intentos, reintento y *lease*. |
| `locate_events` | Histórico auditable de cada resultado: estado, método, URL, evidencia y peticiones consumidas. |
| `discovery_sources` | Plantillas de páginas de marca o catálogo (`https://tienda/marca/{brand}`) o feeds del propio competidor. |
| `discovery_pages` | Listados ya consultados, con fecha: no se vuelven a pedir hasta que caducan (168 h por defecto). |
| `discovery_index` | Índice local incremental de fichas descubiertas, con firma (ruta + texto del enlace) y datos extraídos. |

Las URLs confirmadas se guardan en la tabla existente `competitor_product_urls` (origen `auto_watch`), igual que las del piloto, para que la monitorización las reutilice.

### Funcionamiento por trabajo

1. **URL verificada existente** → hecho, sin HTTP. Una URL marcada `needs_relocation` crea un trabajo de **relocalización**.
2. **Índice local**: se buscan enlaces cuya firma contenga el modelo y el código de color como tramos separados (`pr-27zs-16k08z`, `0PR 27ZS`, `FT1044S` ≈ `FT1044`). Primero se reutiliza lo ya indexado.
3. **Descubrimiento**, en este orden y **solo con métodos autorizados**: feed → sitemap → páginas de marca/catálogo (`?page=N`) → buscador interno → (externo, desactivado). Tras cada método se vuelve al índice.
4. **Verificación**: se consulta la ficha (o se reutilizan sus datos durante 30 días) y se clasifica con la regla comercial **marca + modelo + código de color**. No se exige EAN publicado ni coincidencia de calibre; un EAN publicado contradictorio, variantes ambiguas o la falta de color en la ficha van a **revisión manual**. Color o modelo distintos se rechazan.
5. Las URLs descartadas antes (rechazadas por una persona o por el piloto) nunca se reconfirman.

### Estados

| Estado | Significado |
| --- | --- |
| `done` | Ficha verificada y registrada. |
| `review` | Coincidencia dudosa: decidir en la pantalla (confirmar o descartar con motivo, auditado). |
| `unavailable` | «Localización no disponible por restricciones de acceso». **No** significa que el producto no exista. Se reabre al cambiar autorizaciones. |
| `not_found` | Se consultaron métodos autorizados sin éxito; reintento a los 7 días. |
| `waiting` | Presupuesto agotado, pausa o HTTP 429: se reanuda tras el plazo. |
| `blocked` | CAPTCHA o 401/403/407: el competidor queda suspendido hasta una decisión humana. |
| `failed` | Cinco intentos interrumpidos o con error. |
| `pilot` | Par del piloto supervisado de 20 EAN: lo gestiona el piloto, no este motor. |

### Garantías

- Todas las peticiones pasan por `GovernedFetcher`: autorización por método, robots.txt, presupuesto diario, límite por minuto y pausas de la fase 1.
- Un bloqueo o un límite **detiene todo el competidor** en esa ejecución y deja sus trabajos en espera o bloqueados, sin insistir. Esa suspensión no cuenta como intento fallido.
- Recuperación: si el proceso se interrumpe, el trabajo en curso se recupera al caducar su *lease* (10 min); los listados y fichas ya guardados no se vuelven a pedir.
- Un único proceso por competidor (`FileLock` compartido con el piloto).
- Sin servicios externos de búsqueda.

### Prueba funcional (`tests/test_price_locator.py`)

Competidor simulado con 5 marcas, 100 modelos, 6 colores por modelo (uno no vigilado), calibres distintos y EAN publicado solo en un tercio de las fichas. Se añaden 500 EAN sin URL:

- Se crean los 500 trabajos al añadirlos a la vigilancia.
- El proceso se interrumpe a mitad; tras caducar el *lease*, una segunda ejecución completa los 500 (`done`), con 500 URLs verificadas de origen `auto_watch`.
- Cada página de listado se consulta una sola vez en total; los colores no vigilados nunca se consultan; una nueva ejecución no hace ninguna petición.

Domanci se valida con un sitio simulado con sus reglas de robots.txt: sin autorización todo queda `unavailable` sin peticiones; con una autorización **simulada** de catálogo y fichas, se localiza por `/es/marca/{brand}` sin tocar `/es/busqueda`. En producción **no** se habilita nada para Domanci: la fuente `/es/marca/{brand}` se registra, pero no se usa mientras los métodos sigan pendientes.

## Fase 3: descubrimiento ampliado y planificación

Sin crawler masivo ni APIs externas. Todo sigue pasando por `GovernedFetcher`: robots.txt, autorizaciones por método, presupuestos, pausas y gestión de CAPTCHA, 403 y 429.

### Fuentes de listado (`discovery_sources`, columnas nuevas mediante `_ADDED_COLUMNS`)

| Columna | Uso |
|---|---|
| `kind` | `brand`, `collection`, `category` (método `catalog`) o `feed`. |
| `follow_pattern` | Expresión regular de la ruta de los sublistados que se pueden seguir (por ejemplo `^/gafas-de-sol/[a-z-]+$`). |
| `max_depth` | Profundidad de sublistados, de 0 a 3. Por defecto 0, es decir, sin seguir enlaces. |
| `adapter` | `generic` (enlaces más paginación `?page=`/`?p=`, `rel="next"` o `data-next-url`) o `itemlist` (JSON-LD `ItemList`, para tarjetas cuyo enlace no identifica el producto, como `/p/1234` con el texto «Ver»). Para un competidor con otra estructura basta con registrar un adaptador nuevo (`register_adapter`) y elegirlo en la fuente; no hace falta modificar el motor. |

- **De lo concreto a lo amplio:** para cada producto se leen primero las páginas de marca, después las colecciones y por último las categorías. Solo se pasa al nivel siguiente si el anterior no dejó ningún candidato. Una página de marca fija solo se lee para productos de esa marca.
- **Seguimiento controlado:** un sublistado se sigue solo si cumple `follow_pattern` **y** nombra una marca vigilada, sin superar `max_depth`, como máximo 30 sublistados por página y `max_pages` páginas por fuente y ejecución. Los listados de marcas no vigiladas nunca se piden.
- **Enlaces duplicados:** las URLs se normalizan (sin fragmento ni parámetros `utm_*`, `gclid`, `fbclid`…). Un mismo producto enlazado varias veces se indexa una sola vez y su ficha se consulta una sola vez.
- **Caducidad y cambios** (`discovery_pages.content_hash`, `changed_at`, `unchanged_runs`, `children`):
  - Mientras una página no caduca, no se vuelve a pedir; sus enlaces de paginación y sublistados se reutilizan desde la copia guardada.
  - Al caducar, se vuelve a leer y se compara el hash de los enlaces extraídos. Si no ha cambiado, la siguiente caducidad se duplica (×2, después ×4 como máximo). Si ha cambiado, vuelve al valor configurado y se actualiza `changed_at`.
  - Todas las páginas de un listado paginado se comprueban, porque los productos nuevos pueden aparecer en cualquiera de ellas.
- **Fichas retiradas:** una ficha que responde 404 o 410 queda como `gone` en el índice (`discovery_index.gone_at`).
  - `PriceLocator.retire(competitor, url, motivo)` marca sus filas verificadas como `needs_relocation`; el siguiente `enqueue()` reabre el trabajo como `relocate`, que se resuelve con el índice y los listados vigentes.
  - La ficha retirada no se vuelve a consultar ni se puede reasignar.
  - El histórico de ofertas no se toca.
- **Varios colores de una referencia:** cada color se asocia a su propia ficha. Las variantes de calibre no deciden la identidad; solo ordenan las fichas equivalentes, de modo que se prueba primero el mismo calibre.

### Planificación periódica (`locate_schedule`, `locate_runs`)

- `PriceLocator.tick()` ejecuta los competidores habilitados cuyo intervalo ha vencido:
  - Encola los trabajos (de forma idempotente) y procesa un lote acotado, con un máximo de trabajos y un plazo en minutos.
  - Programa la siguiente ejecución.
  - Registra la ejecución en `locate_runs`.
- **Doble interruptor, apagado por defecto:**
  - Variable de entorno del servidor `PRICE_LOCATOR_SCHEDULE_ENABLED=1`.
  - Y, además, el competidor habilitado en la pantalla (cambio auditado en `access_audit`).
  - Sin ambos, `tick()` no hace nada. **En `s2026` no se activa en esta fase.**
- **Recuperación:**
  - Un `FileLock` global impide dos pasadas simultáneas.
  - Una ejecución que quedó en `running` por una caída se marca como `interrupted` en la pasada siguiente.
  - Sus trabajos se recuperan al vencer el *lease*, y los listados y fichas ya guardados no se repiten.
  - Una ejecución interrumpida no reprograma su competidor, así que se vuelve a intentar en la pasada siguiente.
- **CLI:**
  - `python price_locator.py schedule` muestra la planificación.
  - `python price_locator.py tick` hace una pasada.
  - `python price_locator.py loop --every 300` funciona como servicio; solo actúa con el interruptor activo.
- Es independiente de la planificación de precios (`competitor_schedule`, `price_scheduler.py`).

### Pruebas (`tests/test_price_locator_discovery.py`, 22)

- **Paginación:** `?page=`, `rel="next"` y `data-next-url`.
- **Niveles de fuente:**
  - Marca, colección y categoría.
  - Sublistados de marcas no vigiladas que nunca se piden.
  - Profundidad acotada.
  - Validación de la fuente.
- **Enlaces duplicados:** fragmentos y `utm_*`.
- **Páginas modificadas y sin cambios:** caducidad adaptativa de 24 h a 48 h y después a 96 h.
- **Fichas retiradas:** con relocalización y en un listado desactualizado.
- **Referencias con varios colores:** con distintos calibres.
- **Bloqueos en un sublistado:** CAPTCHA, 403 y 429, con reanudación tras el `Retry-After` y reutilización de la copia de la categoría.
- **Interrupción:** recuperación tras una caída a mitad del recorrido, en la que cada listado y cada ficha se piden una sola vez.
- **Competidor sin método de listado autorizado:** cero peticiones y estado `unavailable`.
- **Adaptador `itemlist`:** comparado con `generic`.
- **Prueba de 500 EAN** (5 marcas vigiladas y 2 no vigiladas, 6 colores por modelo, listados paginados):
  - **Primera ejecución**, interrumpida y recuperada: 500 de 500 verificadas. Se leen 16 listados (la categoría y 5 marcas × 3 páginas), cada uno una sola vez, y 500 fichas.
  - **Segunda ejecución:** ninguna petición.
  - **100 EAN nuevos ya presentes en el índice:** 100 fichas y `robots.txt`, sin ningún listado. Con el índice vacío, los mismos 100 EAN cuestan 117 peticiones.
- **Planificación:**
  - Desactivada por defecto.
  - Ejecución, reprogramación y auditoría.
  - Recuperación de una ejecución interrumpida sin duplicados.
  - Domanci inerte sin autorización documentada.

## Fase 4: beta de monitorización de las fichas localizadas

Se reutilizan los componentes existentes; no se crea otro extractor, histórico ni comparativa.

1. **Qué se monitoriza:** las filas `competitor_product_urls` con `status='verified'` de productos vigilados activos (`watch_products.active=1`). Es exactamente lo que deja la localización (`source='auto_watch'` o `user`), más las URLs del piloto.
2. **Cuándo:**
   - La frecuencia se obtiene con `PriceWatch.monitor_interval_hours`: la del grupo si está definida, o la del competidor en `competitor_schedule` (24 h por defecto).
   - `monitor_jobs` es una cola independiente, con una fila única por producto/competidor. Se prepara únicamente para productos vigilados activos y fichas verificadas.
   - `not_before` y `sampled_at` separan el siguiente intento de la última lectura correcta de precio (`last_ok_at`). Una página sin precio no rejuvenece el precio anterior.
   - `monitor_schedule` habilita los competidores por separado. `PRICE_MONITOR_SCHEDULE_ENABLED` está apagado por defecto; `tick` no hace nada sin ese interruptor. No se modifica la habilitación del planificador legacy del piloto.
3. **Cómo se consulta:**
   - Con `GovernedFetcher(method='ficha', purpose='monitor')`, compartiendo presupuestos, pausas, robots.txt y bloqueos con la localización.
   - Un bloqueo suspende el competidor exactamente igual que en la localización.
4. **Extracción y registro:**
   - `extract_product` para leer precio, disponibilidad e IVA, con la configuración de `competitor_web_config`.
   - `Outcome` y `WebPriceSearch.record` usan `import_offers`: `offers`, `search_results` cuando no hay precio y `offer_checks` para las comprobaciones sin cambios. No se crea otro histórico.
   - La URL conserva `last_price`, `last_availability` y `last_ok_at`, sin cambiar el origen ni la auditoría de su confirmación.
   - Las comparativas y KPIs existentes excluyen, para pares monitorizados, una oferta antigua si su URL deja de estar verificada, falta precio comparable, cambia el stock o caduca el precio. Las ofertas del piloto no incluidas en vigilancia conservan su comportamiento.
   - Se exige identidad exacta, stock disponible, mercado ES, EUR e IVA incluido confirmado. Un precio observado no comparable se conserva con su estado y sin diferencias comerciales.
5. **Identidad en cada lectura:**
   - La página leída se vuelve a clasificar con `classify`.
   - Si deja de coincidir (otro modelo/color, EAN contradictorio, ambigüedad), la ficha y su trabajo de localización pasan a revisión; no se registra ese precio. La revisión usa la pantalla ya existente.
6. **Fichas desaparecidas:**
   - Un 404 o 410 llama a `PriceLocator.retire` y prepara la relocalización sin realizarla desde el monitor.
   - Los errores temporales conservan el trabajo y se reintentan tras 30 minutos, hasta cinco intentos. Los límites y bloqueos no consumen esos intentos.
7. **Caducidad de precios:** se mostrará como caducado un precio con `last_ok_at` anterior a 2× el intervalo del producto.
8. **Alertas:**
   - `recent_changes` de `price_analytics.py` ya calcula las variaciones respecto a `alert_threshold_pct` (5 % configurable).
   - `price_alerts` persiste subidas/bajadas (umbral inicial 5 %, configurable), paso a más barato que Vytria, cambios de disponibilidad, fichas desaparecidas y precios caducados.
   - Una clave única por trabajo, tipo y episodio evita duplicados tras reintentos o reinicios; conserva fecha, producto, competidor y valores anterior/nuevo.
   - `email_summary` prepara un resumen descargable. No hay envío automático ni llamadas a Graph/SMTP.

### Recuperación y límites de la beta

- Se reutiliza el bloqueo por competidor del piloto/localizador; dos procesos no consultan al mismo tiempo al mismo competidor.
- Cada trabajo se reserva durante diez minutos. Una caída se recupera al vencer esa reserva.
- La muestra extraída y su fecha se guardan antes del importador. Si la ejecución cae después, se reutiliza sin HTTP ni búsquedas; el importador y las alertas son idempotentes.
- Una URL compartida por varios EAN/calibres reutiliza una muestra todavía vigente y vuelve a comprobar la identidad de cada EAN.
- Existe una ventana inevitable entre recibir HTTP y guardar la muestra: si el proceso muere justo ahí, esa petición puede repetirse. Su presupuesto ya queda consumido. No se promete entrega exactamente una vez por HTTP.
- Las alertas se crean al ejecutar un lote, preparar la cola o abrir «Alertas». Sin proceso periódico activo no hay detección de caducidad en segundo plano.
- Los trabajos terminados y las transiciones quedan en `monitor_jobs`/`monitor_events`; no se borra el histórico.

### Lectura de resultados y comprobación de observaciones sin repetir el lote

`inventory()` interpreta los nulos de SQLite/pandas (`None`, `NaN`, `NA`, `NaT`) como
ausencia de fecha, no como una fecha para `fromisoformat`. Admite texto ISO (incluidos
`Z`, offsets y el separador de SQLite), `datetime`, `date`, `Timestamp` y `datetime64`.
La comparación de vigencia se realiza en UTC; las fechas antiguas sin zona se
interpretan como UTC, nunca como la zona local del servidor.
Una observación sin vigencia o con fecha inválida se conserva, no se considera
comparable y muestra un estado y aviso explícitos; una fecha inválida también
genera un aviso en el log con EAN/competidor/tipo, sin volcar el contenido recibido.
La visualización no corrige la base ni prepara trabajos ni consume HTTP.

Los otros usos de `fromisoformat` del monitor leen directamente campos SQLite
`TEXT` de la cola o la fecha del recibo JSON, escritos con `_iso()` (UTC con zona).
No pasan por pandas. Se conserva su validación estricta y no se cambia el
comportamiento de ejecución, recuperación ni planificación.

Para comprobar un lote ya terminado **no pulsar Preparar ni Actualizar precios**:

- **Comparador → Productos → detalle → Histórico completo de precios** reutiliza
  `offer_history(ean)` y muestra precio, moneda, stock, mercado, identidad, fecha y URL
  ya guardados. La tabla permite descargar sus filas desde el menú de Streamlit.
  Su visibilidad depende de los filtros/ofertas recientes del detalle.
- Tras aplicar la corrección, **Vigilancia automática → Precios y monitorización →
  Trabajos de monitorización** muestra `jobs()` y la tabla comercial sin ejecutar
  el motor; también se pueden descargar las tablas desde su menú.
- El CSV de **ejecuciones del piloto** no acredita este lote del motor nuevo.
  El resumen de correo solo contiene alertas, no todas las observaciones.
- En la versión afectada el fallo de `inventory()` impide llegar a la tabla de
  trabajos. Si el detalle de producto tampoco permite recuperar el lote completo,
  ejecutar la siguiente consulta sobre una conexión SQLite **de solo lectura**
  (por ejemplo, `sqlite3 -readonly <ruta-SQLite-del-comparador>`).
  Solo selecciona los cinco trabajos terminados más recientes de Visio Factory,
  no identifica inequívocamente un lote si ya hubo otros posteriores: comprobar
  EAN/fechas contra el lote supervisado antes de considerarlos los cinco afectados.

```sql
WITH selected AS (
    SELECT j.*
    FROM monitor_jobs j
    JOIN competitors c ON c.id = j.competitor_id
    WHERE lower(c.name) LIKE '%visio%factory%' AND j.state = 'done'
    ORDER BY julianday(j.last_ok_at) DESC, j.id DESC
    LIMIT 5
)
SELECT j.id AS job_id, j.ean, c.name AS competitor,
       p.brand, p.model, p.color, j.state, j.url AS verified_url,
       j.sampled_at, j.last_ok_at, j.valid_until, j.eligible,
       j.last_offer_id, o.id AS stored_offer_id,
       o.price, o.promotional_price, o.currency, o.market,
       o.availability, o.match_status, o.method, o.captured_at,
       o.url AS observed_url, o.evidence,
       k.last_ok_at AS last_offer_check_at, k.checks,
       (SELECT COUNT(*) FROM monitor_events e
        WHERE e.job_id = j.id AND e.state = 'done') AS completed_events,
       j.sample AS saved_receipt
FROM selected j
JOIN competitors c ON c.id = j.competitor_id
JOIN products p ON p.ean = j.ean
LEFT JOIN offers o ON o.id = j.last_offer_id
LEFT JOIN offer_checks k ON k.last_offer_id = o.id
    AND k.ean = j.ean AND k.competitor_id = j.competitor_id AND k.market = o.market
ORDER BY j.id;
```

Para exportar únicamente el resultado, en la consola SQLite usar `.headers on`,
`.mode csv` y `.once visio-monitor.csv` antes del `SELECT`. El archivo se crea
localmente; no se modifica SQLite. Deben aparecer cinco trabajos, sus recibos y
sus ofertas asociadas (o una ausencia explícita, sin ocultarla con un `INNER JOIN`).
Las comprobaciones de precio sin cambios pueden reutilizar `stored_offer_id`:
`last_offer_check_at` y `saved_receipt` acreditan la nueva lectura sin exigir cinco
ofertas nuevas. No ejecutar el monitor para verificar estos datos.

### Uso y prueba supervisada

En **Comparador → Administración → Vigilancia automática**:

1. Añadir productos de Vytria (selección, importación de EAN o reglas), con marca/modelo/color completos.
2. Revisar «Autorizaciones por método» y «Presupuestos». Las autorizaciones del piloto no se transfieren implícitamente al motor escalable.
3. En «Localización automática», ejecutar un lote dirigido al competidor autorizado.
4. En «Precios y monitorización», elegir **un competidor**, revisar el presupuesto y marcar la confirmación del lote. El botón procesa como máximo diez fichas por defecto y solo trabajos vencidos.
5. Consultar precio Vytria/competidor, mejor competidor, diferencias (Vytria menos competidor, porcentaje sobre Vytria), stock, moneda, IVA, fecha y estado. Hay filtros de marca/estado/búsqueda y evolución histórica reutilizada.
6. «Alertas» permite cambiar el umbral y descargar el resumen de correo, sin enviar nada.
7. El dashboard general recibe las ofertas del mismo histórico; no hace falta volver a importarlas.

CLI (desde el directorio de la aplicación; no ejecutar en producción sin aprobación):

```text
python price_monitor.py --db <ruta-SQLite> enqueue --competitor <id>
python price_monitor.py --db <ruta-SQLite> run --competitor <id> --max-jobs 10
python price_monitor.py --db <ruta-SQLite> status
python price_monitor.py --db <ruta-SQLite> email-summary
python price_monitor.py --db <ruta-SQLite> tick
```

`loop --every 300` está preparado, pero **no se instala ni se activa en la beta**.

Prueba real preparada para **Visio Factory, Edel-Optics, Amevista y Óptica Óptima**:

- Validar por escrito cada método y las rutas concretas; comprobar configuración de mercado/moneda/IVA y selectores del piloto.
- Empezar con 2-5 EAN por competidor y fuentes de descubrimiento ya comprobadas. No es necesario introducir URLs de producto si hay método de localización autorizado.
- Guardar el consumo inicial/final; ejecutar primero localización, después precios. Verificar manualmente identidad, precio, stock, IVA y mercado en la ficha.
- Repetir el lote antes de su vencimiento: cero consultas. Repetir tras el intervalo configurado para comprobar actualización sin búsquedas.
- Ante CAPTCHA/403/429, detenerse y revisar la incidencia; no reautorizar ni resolver automáticamente.
- **ConGafasDeSol y Domanci siguen restringidos** hasta contar con autorización documentada de los métodos y rutas. No se han realizado consultas reales desde esta entrega.

Pruebas automatizadas: `tests/test_price_monitor.py` incluye **500 EAN × dos competidores simulados** (1.000 combinaciones), localización/confirmación, precios, cambios, alertas, caída y recuperación, ausencia de nuevas búsquedas, presupuestos y protecciones. Incluye además migración desde esquema de fase 3, recibos tras caída entre importación/cierre, URL compartida por calibres, caducidad, umbral exacto e interfaz sin HTTP.

## Orden de integración de las PR

Estado comprobado el 3-11-2026 con `git merge-tree` sobre `origin`:

| PR | Rama | Base | Contenido |
|---|---|---|---|
| #106 | `fix/session-security-issue-18` | `main` | Sesión persistente, dashboard y autoconfirmación de fichas. |
| #107 | `feat/price-watch-phase1` | `main` | Fase 1: vigilancia, autorizaciones y presupuestos. |
| #108 | `feat/price-watch-phase2` | `feat/price-watch-phase1` | Fase 2: localizador, cola e índice. |
| #109 | `feat/price-watch-phase3` | `feat/price-watch-phase2` | Fase 3: descubrimiento ampliado y planificación. |
| (fase 4) | `feat/price-watch-phase4` | `feat/price-watch-phase3` | Beta: monitorización, comparativas y alertas. |

- #106 y #107 parten del mismo commit de `main` (`9e9b625`). `main` no ha avanzado desde entonces.
- **Único conflicto:** `Dockerfile`, en la línea `COPY price_pilot.py price_discovery.py …`. #106 añade `price_auto_confirm.py` y #107/#108 añaden `price_access.py price_watch.py price_locator.py`. La resolución consiste en conservar todos los módulos:
  `COPY price_pilot.py price_discovery.py price_auto_confirm.py price_access.py price_watch.py price_locator.py price_monitor.py .`
- `price_comparator.py` y `app_pages/price_comparator.py` se combinan automáticamente: los cambios de esquema de #106 y de las fases son independientes.

**Orden:**

1. **#106** en `main`. Es independiente y no necesita las fases.
2. **#107:**
   - Actualizarla con `main`, resolver el `Dockerfile` como se indica y esperar el CI.
   - Después, fusionarla.
3. **#108:**
   - Cambiar su base a `main` (GitHub lo hace al fusionar #107 si se borra la rama), actualizarla y volver a resolver la misma línea del `Dockerfile`.
   - Esperar el CI y fusionarla.
4. **Fase 3:** igual que #108, con base `main` tras fusionar #108.
5. **Fase 4:** actualizar base a `main` después de #109, conservar también `price_monitor.py` en el Dockerfile y repetir CI y validación integrada.

- **No fusionar una fase sin la anterior:** cada una depende de las tablas y clases de la previa.
- **No confundir integración con despliegue:** un push a `main` publica imagen; el despliegue productivo depende del workflow `deploy-cloud-production.yml` (manual o `CLOUD_AUTO_DEPLOY=true`). Antes de integrar, verificar que esta variable no activa despliegues no autorizados. No se modifica aquí.
- Ninguna fase activa por sí misma consultas reales:
  - Las autorizaciones nacen pendientes.
  - Localización y monitorización necesitan sus interruptores independientes y habilitar cada competidor.
- Para esta beta, revisar e integrar la pila completa antes de autorizar un único despliegue.

## Próximas fases

### Despliegue de la beta en `s2026` (preparado, NO ejecutado)

Requiere autorización humana; no se ha accedido al servidor ni validado sus montajes desde este entorno.

1. **Inventario y parada de escrituras:** registrar digest/commit actual y configuración Compose efectiva sin imprimir secretos. Verificar el controlador de despliegue y pausar escritores del comparador durante la ventana autorizada. No detener servicios desconocidos ni el puerto 8508.
2. **Backup:** en almacenamiento privado fuera de Git, guardar la configuración/secretos con permisos restringidos y generar snapshots coherentes de `price_comparator.sqlite3` y `auth_state.sqlite3`. Reutilizar [snapshot_sqlite_copy.py](../../scripts/snapshot_sqlite_copy.py) con `--source` y un `--target` nuevo si el almacenamiento es local y todos los escritores están parados: conserva DB+WAL, usa `Connection.backup` en una copia y verifica integridad/recuentos sin abrir el original. Si la ruta no es local, no eludir su guarda; revisar el procedimiento de almacenamiento aprobado. No copiar solo el `.sqlite3` con WAL activo. Verificar existencia/fecha de las copias. No rotar `AUTH_COOKIE_SECRET`.
3. **Ensayo de migración:** duplicar el backup del comparador; abrir esa copia con `PriceComparatorStore` de la imagen candidata. Comprobar `integrity_check`, `foreign_key_check`, recuentos intactos de productos/ofertas/URLs/vigilancia/piloto y creación de `monitor_jobs`, `monitor_events`, `monitor_schedule`, `price_alerts`. Repetir el arranque en la misma copia para verificar idempotencia. No ensayar sobre la base productiva.
4. **Persistencia:** confirmar en la definición efectiva de producción que `/app/data` está montado en el volumen persistente con permisos para el usuario del contenedor. `PRICE_COMPARATOR_DB_PATH=/app/data/price_comparator.sqlite3`, `AUTH_STATE_PATH=/app/data/auth_state.sqlite3` y el mismo `AUTH_COOKIE_SECRET` deben sobrevivir al cambio de imagen. El despliegue usa su snapshot aprobado, no necesariamente el Compose del repositorio. SQLite con una única instancia sigue siendo suficiente; no se añade PostgreSQL ni otro servicio.
5. **Interruptores:** mantener `PRICE_LOCATOR_SCHEDULE_ENABLED` y `PRICE_MONITOR_SCHEDULE_ENABLED` ausentes o a `0`, sin instalar `loop`. Verificar también los planificadores legacy/piloto y la ausencia de nuevos servicios/tareas del host. El correo no tiene envío automático.
6. **Imagen y arranque:** desplegar únicamente el digest aprobado del commit integrado con CI verde; no `latest` mutable. Usar el procedimiento existente del controlador para sustituir la imagen, sin recrear los volúmenes. Verificar salud de Streamlit, imports de todos los módulos y logs sin credenciales/tokens.
7. **Autenticación:** probar login y acceso según permisos, cookie recordada antes/después de recrear la aplicación con el mismo secreto y almacén. Mantener el backup de autenticación; no invalidar sesiones por rotación accidental.
8. **Comparador:** abrir dashboard, piloto y vigilancia sin que se reserve ninguna petición HTTP. Verificar datos Vytria ya sincronizados, contadores y alertas. Ejecutar solo el lote real acotado descrito arriba tras autorización. Anotar resultados y peticiones consumidas.
9. **Rollback:** apagar cualquier tarea de la beta; volver al digest anterior y su Compose/configuración aprobados. Las tablas nuevas son aditivas y la versión anterior las ignora. Si la migración/datos no superan validación, restaurar el backup coherente con todos los escritores detenidos, sin mezclar archivos WAL de distintas versiones; volver a comprobar integridad, login y piloto. Restaurar el almacén de autenticación solo si es necesario y manteniendo el mismo secreto; nunca publicar backups.

La habilitación periódica y el envío de correo quedan fuera de esta autorización de beta.

### Integración externa futura

La búsqueda externa queda como extensión opcional (`external_search` en `WebPriceSearch` y el método `external`). Está desactivada y nada del motor depende de ella.
