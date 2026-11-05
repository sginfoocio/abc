# Diagnóstico de cobertura de localización (100 × 5)

## Alcance y fuentes

Se contrastaron el [informe de auditoría](./Diagonal_evidencia_Copilot/Informe_y_Copilot.txt), la [matriz de competidores](./Diagonal_evidencia_Copilot/Matriz_14.csv), las [47 evidencias](./Diagonal_evidencia_Copilot/Evidencias_47.csv) y el [catálogo de 100 productos](./Diagonal_evidencia_Copilot/Productos_100.csv) (commit fuente `92243628b4e925ba9d549b076580726c383a43ac) con el código de la rama y las exportaciones [de resultados](./Diagonal_evidencia_Copilot/2026-11-05T22-30_export.csv) y [de aplazamientos](./Diagonal_evidencia_Copilot/2026-11-05T22-31_export.csv). Los ejemplos acreditan patrones y candidatos, no consultas HTTP actuales, fichas verificadas, ofertas comparables ni autorización de automatización.

El CSV de resultados tiene 500 pares: 89 procesados y 411 no ejecutados. Suma 126 peticiones. El campo `outcomes` llegó como `[object Object]`; por tanto, no permite reconstruir candidatos solicitados, redirecciones, hechos extraídos ni decisiones de identidad. Los 60 estados `available` abarcan 27 EAN, pero `available` por sí solo no acredita comparabilidad comercial.

## Cobertura exportada

| Tienda | Procesados / 100 | Peticiones | Resultado exportado | No ejecutados y motivo principal |
|---|---:|---:|---|---|
| Óptica Óptima | 43 | 69 | 24 `available`; 19 `unavailable` por métodos de descubrimiento no autorizados | 46 fuera del alcance documentado; 11 en revisión |
| Amevista | 17 | 18 | 17 `available` | 83 sin alias/handle acreditado |
| Edel-Optics | 12 | 13 | 11 `available`; 1 `unknown` | 88 sin Product ID de `selectedSize` |
| Visio Factory | 13 | 15 | 8 `available`; 4 `unavailable`; 1 conflicto enviado a revisión | 85 fuera del alcance; 2 sin medidas vinculadas a variante |
| Domanci | 4 | 11 | 3 sin ficha localizada en el catálogo consultado; 1 con presupuesto agotado | 93 por presupuesto agotado; 3 en revisión |
| **Total** | **89** | **126** | **60 `available`, no equivalentes automáticamente a comparables** | **411** |

El CSV de aplazamientos separa los motivos por par. No se hicieron consultas comerciales durante este análisis.

## Amevista: cobertura de los 100 EAN

Para `0889652494821` (Gucci GG1660S / 001), el plan previo no generaba candidata: Gucci no coincidía con E017–E021 y solo constaba en la regla añadida en esta rama. Preview lo marcaba no listo y prepare no encolaba una localización. La URL aportada (`/es/gucci-gg1660s-001`) sigue siendo una candidata, no prueba de que se solicitara o de que la ficha exista.

La matriz de auditoría describe el patrón parcial `/{locale}/{marca_alias}-{modelo_alias}-{color}` y limita Amevista a Prada, Tom Ford y Tiffany de sol. E017/E018 documentan las variantes Prada `0PR` y `PR`; E019 muestra Tom Ford; E020/E021 acreditan el alias Tiffany `tiffany` en los locales `es` y `us`. Se genera en `/es/` para el catálogo español: no se traslada el locale `om` de E019 ni se emite la URL `us` como segunda candidata. El prefijo `0PR` se conserva únicamente para la candidata exacta de E017; las demás referencias Prada usan el prefijo de modelo `PR` observado en E018. Tiffany usa el alias acreditado `tiffany`.

La regla Gucci ya presente en esta rama se mantiene como scope explícito de sol, construyendo marca-modelo-color y atribuyendo solo la URL aportada como procedencia. Esa URL es evidencia de forma, no identidad; los demás candidatos Gucci no se presentan como fichas verificadas. No se amplía el patrón a otras categorías, marcas o tiendas.

Simulación offline sobre `Productos_100.csv`: **49 de 100 EAN** generan una candidata Amevista:

| Marca / categoría del catálogo | EAN con candidata | Base documental |
|---|---:|---|
| Prada / sol | 13 | E017–E018; prefijo `0PR` solo en la variante exacta documentada y `PR` para el patrón |
| Tom Ford / sol | 29 | E019; ruta marca-modelo-color |
| Tiffany & Co. / sol | 1 | E020–E021; alias `tiffany`, locale español |
| Gucci / sol | 6 | URL aportada y scope Gucci solar de esta rama; candidatas hipotéticas |
| **Total** | **49** | **49 URLs; ninguna verificada por esta simulación** |

Los **51 EAN restantes** quedan excluidos con motivo concreto:

| Motivo | EAN |
|---|---:|
| Marca sin patrón Amevista documentado: Bottega Veneta (1), Cartier (1), Chloé (2), Miu Miu de sol (24), Ray-Ban (6) y Saint Laurent (6); requieren URL/alias acreditado | 40 |
| Categoría fuera de la evidencia Amevista, que solo cubre sol: Miu Miu montura (6) y Tom Ford montura (5) | 11 |
| **Total excluido** | **51** |

El patrón de Gucci se conserva por la petición previa, pero la única URL aportada corresponde a GG1660S / 001: los otros cinco Gucci son hipótesis de ruta de menor evidencia, no una extrapolación confirmada por páginas. La simulación solo cuenta URLs generadas; no acredita localización, identidad, disponibilidad ni oferta comparable. Los tres Gucci que ya tenían resultado en la exportación histórica no se reconsultan ni se reclasifican. Los datos Vytria/Odoo y su hora de sincronización tampoco verifican fichas de Amevista.

## Óptica Óptima: 19 intentos sin localización

El CSV confirma 19 resultados finales `unavailable` y 39 peticiones sumadas en esos pares (18 con 2 peticiones y FT5737B con 3). La razón final incluye el fallback: feed/API, sitemap/índices, catálogo y búsqueda interna sin autorización. El CSV no retiene las etapas anteriores.

Reproducción offline de `candidate_plan()` con los 19 productos del catálogo local: **cada entrada genera una candidata runnable** usando el patrón de categoría/modelo/color documentado en E002 o E003. Esto prueba que el plan del código local no las descarta por falta de atributos o categoría; **no prueba** que el Cloud desplegado generase o solicitase esas URLs. La lista calculada es:

| EAN | Modelo / color | Candidata generada por el plan local | Peticiones del CSV |
|---|---|---|---:|
| 0889214095985 | FT0751N / 01A | `/gafas-de-sol/tom-ford-ft0751n-01a.html` | 2 |
| 0889214319371 | FT0836 / 001 | `/gafas-graduadas/tom-ford-ft0836-001.html` | 2 |
| 0889214281791 | FT0884 / 01E | `/gafas-de-sol/tom-ford-ft0884-01e.html` | 2 |
| 0889214403315 | FT1022 / 20E | `/gafas-de-sol/tom-ford-ft1022-20e.html` | 2 |
| 0889214403407 | FT1026 / 05E | `/gafas-de-sol/tom-ford-ft1026-05e.html` | 2 |
| 0889214402530 | FT1044 / 01E | `/gafas-de-sol/tom-ford-ft1044-01e.html` | 2 |
| 0889214602718 | FT1044 / 52N | `/gafas-de-sol/tom-ford-ft1044-52n.html` | 2 |
| 0889214520418 | FT1153 / 01E | `/gafas-de-sol/tom-ford-ft1153-01e.html` | 2 |
| 0889214685209 | FT1177 / 01V | `/gafas-de-sol/tom-ford-ft1177-01v.html` | 2 |
| 0889214685216 | FT1177 / 93E | `/gafas-de-sol/tom-ford-ft1177-93e.html` | 2 |
| 0889214558619 | FT1211 / 52E | `/gafas-de-sol/tom-ford-ft1211-52e.html` | 2 |
| 0889214558633 | FT1211 / 53E | `/gafas-de-sol/tom-ford-ft1211-53e.html` | 2 |
| 0889214625595 | FT1301 / 01E | `/gafas-de-sol/tom-ford-ft1301-01e.html` | 2 |
| 0889214625601 | FT1301 / 01V | `/gafas-de-sol/tom-ford-ft1301-01v.html` | 2 |
| 0889214625984 | FT1310 / 01A | `/gafas-de-sol/tom-ford-ft1310-01a.html` | 2 |
| 0889214686374 | FT1400 / 56A | `/gafas-de-sol/tom-ford-ft1400-56a.html` | 2 |
| 0889214257314 | FT5737B / 045 | `/gafas-graduadas/tom-ford-ft5737b-045.html` | 3 |
| 0889214586650 | FT6058B / 001 | `/gafas-graduadas/tom-ford-ft6058b-001.html` | 2 |
| 0889214599834 | FT6082B / 051 | `/gafas-graduadas/tom-ford-ft6082b-051.html` | 2 |

Estas son **candidatas hipotéticas**, no direcciones verificadas. No se puede atribuir individualmente un resultado a candidata ausente, rechazo de identidad, categoría, respuesta HTTP o redirección; faltan los eventos y `outcomes` estructurados de esos trabajos. En particular, los 39 requests no se pueden repartir entre `robots.txt`, ficha y otras etapas a partir de este CSV.

El código actual sigue rechazando una respuesta `CollectionPage`, `ItemList` sin producto vinculado y las rutas de categoría de Óptima; esa protección no se ha relajado. Los 11 pares Óptima aplazados corresponden a trabajos en revisión: no se reintentan ni aceptan automáticamente.
Sus EAN son `0889214602701`, `0889214519788`, `0889214402547`, `0889214684080`, `0889214558626`, `0889214635310`, `0889214669742`, `0889214642387`, `0889214643322`, `0889214686305` y `0889214042422`.

## Matriz de evidencia y potencial

| Tienda | Evidencia auditada ya aprovechada por el código | Cobertura adicional que podría intentar el código | Límite real / requisito pendiente |
|---|---|---|---|
| Óptica Óptima | E001–E003: rutas por categoría, modelo/color y alias acotado FT5634B → FT5634-B. Los 19 intentos anteriores tienen plan local runnable. | 11 pares concretos aplazados aún tienen plan candidato local; están en revisión, por lo que la cobertura adicional ejecutable ahora es 0. | No hay evidencia exportada de la respuesta de las 19 candidatas. La búsqueda interna permanece prohibida; cualquier ficha debe superar robots, permisos y comprobación de identidad. |
| Amevista | E017/E018: Prada `0PR`/`PR`; E019: Tom Ford; E020/E021: alias `tiffany` y locales distintos; URL aportada para Gucci. | 49 candidatas entre los 100 EAN (13 Prada, 29 Tom Ford, 1 Tiffany, 6 Gucci); 43 nuevas frente al scope Gucci anterior. | Solo sol. `0PR` no es universal; E021 no genera un duplicado US para el catálogo español. 40 productos de marcas sin patrón y 11 de montura siguen excluidos. Toda candidata necesita permiso, robots y validación ordinaria de identidad. |
| Edel-Optics | E014 mapea el EAN Prada 8056597875349 a `selectedSize=1907958`; ese par ya fue procesado. E015/E016 siguen siendo ejemplos documentales separados. | 0 mappings adicionales runnable entre los 88 aplazamientos. | `selectedSize` es un ID de producto, no calibre ni EAN. El 403 previo y la falta de una fuente de IDs autorizada impiden resolver los 88 restantes. |
| Visio Factory | E041 respalda el par Prada/GTIN con medidas vinculadas; E042 respalda la URL documentada de Cartier; E043 conserva como revisión la ambigüedad de las dos referencias RB2140 901; E044 dejó un conflicto de color y sigue en revisión. | 0 nuevas candidatas seguras para los 85 fuera de alcance. Las 2 referencias RB2140 siguen sin medidas asociadas a sus variantes. | No inferir medidas ni colapsar colores. El resultado E044 no convierte el conflicto en oferta aceptada. |
| Domanci | E032/E033 permanecen como URLs históricas no verificadas y no runnable; no se interpreta un negativo como inexistencia. | 0 candidatas adicionales respaldadas por la auditoría entre los pares pendientes. | 93 no se ejecutaron por presupuesto y 3 esperan revisión. No ampliar el presupuesto ni usar búsqueda interna prohibida. |

La regla Amevista se amplió únicamente a las marcas y categoría descritas arriba; otras reglas no se ampliaron. Los ejemplos no justifican extrapolar handles, IDs opacos, medidas o aliases fuera de ese scope. La distinción se mantiene: **candidata generada ≠ ficha verificada ≠ oferta comparable**.

## Cambios de esta PR

- Cada outcome del localizador conserva en JSON el plan de candidatos, las candidatas realmente examinadas, su estado de facts (incluido `no_product`), URL candidata/solicitada/final cuando existe, redirecciones registradas, rechazo de ficha, decisión y motivos de identidad, y los métodos de descubrimiento ejecutados o no autorizados.
- La vista de lote serializa `outcomes` como JSON en una descarga CSV explícita; la descarga JSON existente conserva su estructura. Los dos CSV antiguos siguen sin poder recuperar los objetos perdidos.
- Preview y claims comparten `job_deferral()` y el límite de intentos; `execute()` prepara desde la misma instantánea que seleccionó, y un par no reclamado informa el estado y aplazamiento actuales de su cola.
- Amevista genera candidatas `/es/{marca_alias}-{modelo_alias}-{color}` para Prada, Tom Ford y Tiffany de sol según E017–E021, y mantiene el scope solar Gucci ya añadido. Una URL candidata no verifica una ficha; un bloqueo de acceso sigue impidiendo preparar/ejecutar el trabajo.
- No se modificaron permisos, robots, presupuesto, históricos ni reglas de identidad. Las regresiones utilizan exclusivamente transportes y páginas sintéticas offline; no representan HTML capturado de tiendas.

## Prueba supervisada posterior propuesta (no ejecutada)

Se propone únicamente el par solicitado. No se incluye ningún otro EAN o tienda:

| Par exacto | Objetivo | Precondición y estimación |
|---|---|---|
| Amevista — EAN `0889652494821` (Gucci GG1660S / 001) | Ejecutar una sola preparación y comprobación supervisada del candidato; verificar identidad por los datos reales de la ficha y detenerse ante contradicción/revisión. No aceptar una revisión ni asumir comparabilidad. | Solo si `ficha` está autorizada, robots permite la URL, el presupuesto permite ambas etapas y el trabajo es elegible según cola. Localización: 1 lectura de ficha + 0–1 robots; si la ficha queda verificada y el flujo autorizado continúa a monitorización, 1 lectura adicional de precio + 0–1 robots. Estimación total 2–4 requests; los controles vigentes son el límite, no esta estimación. Si falla cualquier gate, 0 consultas comerciales y se informa el motivo. |

No ejecutar ahora, sincronizar Vytria ni reintentar otros pares. Una localización exitosa verificaría la ficha, pero la oferta solo sería utilizable después de la lectura de monitorización y sus comprobaciones independientes.
