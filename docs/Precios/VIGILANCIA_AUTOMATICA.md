# Vigilancia automática de precios — Fases 1 a 3

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

## Fase 4 (diseño): monitorización de las fichas localizadas

Se reutilizan los componentes existentes; no se crea otro extractor, histórico ni comparativa.

1. **Qué se monitoriza:** las filas `competitor_product_urls` con `status='verified'` de productos vigilados activos (`watch_products.active=1`). Es exactamente lo que deja la localización (`source='auto_watch'` o `user`), más las URLs del piloto.
2. **Cuándo:**
   - La frecuencia de cada producto y competidor se obtiene con `PriceWatch.monitor_interval_hours` (grupos, por defecto 24 h), comparada con `last_ok_at` de la fila.
   - Un planificador gemelo de `locate_schedule` (o una ampliación de `competitor_schedule` en `price_scheduler.py`) decide qué competidores toca procesar.
3. **Cómo se consulta:**
   - Con `GovernedFetcher(method='ficha', purpose='monitor')`, compartiendo presupuestos, pausas, robots.txt y bloqueos con la localización.
   - Un bloqueo suspende el competidor exactamente igual que en la localización.
4. **Extracción y registro:**
   - `extract_product` para leer precio, disponibilidad e IVA, con la configuración de `competitor_web_config`.
   - `Outcome` y `WebPriceSearch.record` para el registro: `import_offers` en `offers` y `observations`.
   - `last_price`, `last_availability` y `last_ok_at` se actualizan como hoy en `_upsert_index`.
   - Las comparativas, la evolución y los KPIs del dashboard ya leen de `offers` y `observations`.
5. **Identidad en cada lectura:**
   - La página leída se vuelve a clasificar con `classify`.
   - Si deja de coincidir (otro modelo o color), la ficha pasa a revisión y no se registra el precio.
6. **Fichas desaparecidas:**
   - Un 404 o 410 llama a `PriceLocator.retire`, y el siguiente `enqueue()` crea la relocalización.
   - Los errores temporales solo incrementan `failures`, como hoy en `_mark_relocation`.
7. **Caducidad de precios:** se mostrará como caducado un precio con `last_ok_at` anterior a 2× el intervalo del producto.
8. **Alertas:**
   - `recent_changes` de `price_analytics.py` ya calcula las variaciones respecto a `alert_threshold_pct` (5 % configurable).
   - La fase 4 añade los cambios de disponibilidad y una tabla de alertas emitidas con clave única (EAN, competidor, observación), para no notificar dos veces la misma variación.

## Orden de integración de las PR

Estado comprobado el 3-11-2026 con `git merge-tree` sobre `origin`:

| PR | Rama | Base | Contenido |
|---|---|---|---|
| #106 | `fix/session-security-issue-18` | `main` | Sesión persistente, dashboard y autoconfirmación de fichas. |
| #107 | `feat/price-watch-phase1` | `main` | Fase 1: vigilancia, autorizaciones y presupuestos. |
| #108 | `feat/price-watch-phase2` | `feat/price-watch-phase1` | Fase 2: localizador, cola e índice. |
| (fase 3) | `feat/price-watch-phase3` | `feat/price-watch-phase2` | Fase 3: descubrimiento ampliado y planificación. |

- #106 y #107 parten del mismo commit de `main` (`9e9b625`). `main` no ha avanzado desde entonces.
- **Único conflicto:** `Dockerfile`, en la línea `COPY price_pilot.py price_discovery.py …`. #106 añade `price_auto_confirm.py` y #107/#108 añaden `price_access.py price_watch.py price_locator.py`. La resolución consiste en conservar todos los módulos:
  `COPY price_pilot.py price_discovery.py price_auto_confirm.py price_access.py price_watch.py price_locator.py .`
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

- **No fusionar una fase sin la anterior:** cada una depende de las tablas y clases de la previa.
- **El despliegue solo ocurre con un push a `main`.** Ninguna fase activa por sí misma consultas reales:
  - Las autorizaciones nacen pendientes.
  - La planificación necesita `PRICE_LOCATOR_SCHEDULE_ENABLED=1` y habilitar cada competidor.
- Se puede desplegar tras cualquier paso del orden anterior sin dejar una fase a medias.

## Próximas fases

### Fase 4: monitorización

Implementar el diseño anterior: monitorización periódica de las fichas verificadas, caducidad de precios y alertas de precio y disponibilidad sin duplicados.

### Activación en `s2026`

Para activarlo en `s2026`:

1. Documentar las autorizaciones de cada competidor y método.
2. Configurar las fuentes de listado.
3. Activar `PRICE_LOCATOR_SCHEDULE_ENABLED` y el servicio `price_locator.py loop`.
4. Habilitar los competidores uno a uno, empezando por los de bajo volumen.

### Integración externa futura

La búsqueda externa queda como extensión opcional (`external_search` en `WebPriceSearch` y el método `external`). Está desactivada y nada del motor depende de ella.
