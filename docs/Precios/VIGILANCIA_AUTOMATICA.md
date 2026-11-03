# Vigilancia automática de precios — Fase 1

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
- Un producto bloqueado o sin autorización **no** se marca como inexistente. La fase 2 lo registrará como «localización no disponible por restricciones de acceso».

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

## Próximas fases

### Fase 2: motor de localización propio

- Cola persistente que reutiliza `scheduler_jobs` y `FileLock`.
- Un conector común por competidor que elige el mejor método autorizado y, si falla, pasa a la siguiente alternativa:
  1. URL ya verificada.
  2. Feed o API del competidor.
  3. Sitemap.
  4. Buscador interno.
  5. Navegación por catálogo.
- Autoconfirmación por marca, modelo y color.
- Todo el tráfico pasa por `GovernedFetcher`.

### Fase 3: descubrimiento incremental mediante catálogos, marcas y categorías

- Índice persistente de listados y fichas descubiertos, con su fecha de comprobación.
- Recorrido **dirigido** solo a las marcas y categorías de los productos vigilados.
- Sin volver a visitar los listados que siguen vigentes.
- Siempre dentro del presupuesto de cada competidor.
- No es un crawler masivo.

### Fase 4: relocalización y monitorización

- Relocalización solo de las fichas desaparecidas.
- Monitorización periódica de precio y disponibilidad, separada de la localización.
- Caducidad de precios.
- Alertas cuando el precio cambie un 5 % o más, o cambie la disponibilidad, sin duplicados.

### Integración externa futura

La búsqueda externa queda como extensión opcional (`external_search` en `WebPriceSearch` y el método `external`). Está desactivada y nada del motor depende de ella.
