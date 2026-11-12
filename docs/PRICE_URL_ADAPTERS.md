# Candidatas documentales del localizador (04/11/2026)

## Evidencia y base

Base de la auditoria documental original:
`92243628b4e925ba9d549b076580726c383a43ac`; no confundir con la base actual
de esta ampliacion, que incluye la PR #125 integrada.
Esta rama parte de main y no incluye la auditoria aparcada de las 51 confirmaciones.

Se leyeron los siete documentos de `Precios/Diagonal_evidencia_Copilot`:
`Especificacion_Copilot.json`, `Matriz_14.csv`, `Evidencias_47.csv`,
`Productos_100.csv`, `Enriquecimiento_referencias.csv`,
`Cobertura_marca_categoria.csv` e `Informe_y_Copilot.txt`.
Se cotejaron 47 ejemplos, 100 productos, 100 enriquecimientos y 280 grupos
marca/categoria contra el JSON. La muestra representa 29 grupos documentados,
dos solo historicos y 249 sin muestra; esto no demuestra ausencia comercial.
`Productos_100.csv` no contiene calibre ni puente para ninguno de sus 100 EAN.
`Enriquecimiento_referencias.csv` registra 12 medidas: una asociada a un GTIN
exacto y otras 11 solo a modelo/color. Los CSV de exportacion 2026-11-05
registran resultado/acceso, no medidas de producto. Las referencias E032-E044
incluyen medidas de paginas indexadas o candidatas historicas, pero la mayoria
no publica un GTIN que las vincule a una variante.

Se incorporan nueve registros documentales de medidas en
[PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv](PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv):
E041 (GTIN exacto); tres ejemplos Visio aportados con EAN (uno sin vinculo
evidenciado de medidas al GTIN); una variante RB2140
corroborada por la referencia 5418; el calibre 48 de RB4472, sin puente
acreditado; y los calibres seleccionados para tres EAN Saint Laurent mediante
UPC indexado normalizado y controles `selectedSize`. El registro RB2140
0805289126577 se conserva como `model_color_only`: E043 no vincula sus medidas
al UPC, por lo que el runtime lo registra como limitacion pero no rellena
calibre ni puente. En cambio, la asociacion externa EAN/54-18 de
0805289126584 permite generar la candidata E043 para ese tamaño; sigue siendo
una candidata, no una ficha verificada. Estos datos no se escriben en el
catalogo maestro ni se derivan por el orden de listas. El RB2140 901S31 conserva
pendiente la asociacion estructurada EAN/medida. E031 tambien contiene una
contradiccion de titulo/contenido para FT1044 01E; su candidata sigue sin
identidad verificada.

El registro [EXAMPLES](../price_url_candidates.py) conserva los 47 IDs E001-E047,
URLs originales (incluidos locale y query), marca, modelo, color, categoria,
GTIN documentados y cautelas necesarias. Son datos documentales, **no HTML
capturado, respuestas HTTP actuales, ofertas ni permisos**. La investigacion
no acredita automatizacion para ninguna tienda.

## Arquitectura conservada y ampliacion

- Primero se reutiliza `competitor_product_urls.status=verified`, sin HTTP.
  Otro vinculo pendiente se mantiene en revision; no se sustituye por una
  hipotesis de menor confianza.
- El indice incremental existente se consulta antes de generar candidatas.
  `candidate_plan()` es puro: no accede a SQLite, red, colas ni permisos.
- Las reglas generativas se acotan a marca/categoria representadas; Prada
  en Visio se limita ademas a PR27ZS. No se generan combinaciones de tallas,
  IDs, locales ni nombres comerciales.
- Para coincidencias documentadas se conserva la URL completa, sin convertir
  sus medidas en atributos del producto. El alias FT5634B -> FT5634-B se limita
  a Tom Ford 001 en Optima/Otticait. Los aliases 0PR, Tiffany, regard, dune y
  Fausto se resuelven mediante los ejemplos concretos, no con reglas universales.
- La medida 54-21 asociada al GTIN 8056597875349 en E041 puede sustentar una
  **hipotesis de URL** Bassol. No se escribe en el catalogo ni acredita stock.
  Un calibre explicitamente aportado en `products.caliber` sirve para construir
  otras rutas acotadas; puente se lee del formato existente `54-21`.
- Al ejecutar posteriormente un lote aprobado, las candidatas comprobables
  entran en `discovery_index`, no como fichas verificadas. Se conserva
  `candidate_evidence` JSON por EAN: procedencia, fecha/base, regla, atributos
  usados y referencias E###. Es la unica columna nueva, con valor por defecto
  `{}` y migracion aditiva para SQLite existente.
- El plan completo, incluidas fichas padre, conflictos e historicas no
  ejecutables, se conserva en `locate_events.detail` como JSON, metodo
  `local_candidates`, cero peticiones. El resumen humano mantiene sus limites.
- Cada ficha pasa por el mismo `GovernedFetcher` y clasificador. Las notas del
  vinculo resultante citan la regla y referencias; la evidencia completa queda
  en el indice y los eventos. No se crean ofertas durante la localizacion.
- Los candidatos rechazados/descartados siguen excluidos. La cache y la clave
  `(competitor_id,url)` deduplican lecturas. El limite de seis fichas por
  producto se comparte entre indice, nuevas hipotesis y descubrimiento.
- Si faltan atributos o IDs, continuan las fuentes ya configuradas y
  autorizadas: feed, sitemap, catalogo, busqueda GET verificada y extension
  externa preexistente (si estaba autorizada y habilitada). No se instala
  ningun buscador, fuente, endpoint ni navegador nuevo.

## Corte historico de auditoria de 14 competidores

La tabla siguiente conserva la investigacion original de 14 competidores; no
representa la matriz actual de seis tiendas. Los resultados por par y el alcance
vigente de seis tiendas constan en la seccion final de este documento.

La columna de cobertura cuenta **pares con al menos una candidata comprobable
en un futuro lote autorizado**, evaluando las 100 referencias sin medidas
adicionales ni datos de Cloud. No cuenta URLs verificadas, acceso autorizado,
extraccion de ofertas ni precios comparables.

| Competidor | Investigacion | Generacion / reutilizacion documental | Pares / 100 | Solo revision / 100 | Requisitos y limites |
|---|---|---|---:|---:|---|
| Optica Optima | Confirmado en muestra | Categoria + marca/modelo/color; E001-E003; alias FT5634-B | 47 | 0 | Prada sol y Tom Ford sol/montura; fuera de esos grupos requiere fuente autorizada. 46 son nuevas hipotesis, una URL documental. |
| Optica Bassol | Confirmado en muestra | `/products/`, E004-E007; calibre explicito o asociado al GTIN | 2 | 0 | Prada sol, Miu Miu montura y Gucci sol. Una URL documental y una hipotesis apoyada en E041; no completar los otros calibres. |
| Blickers | Parcial | Mapping exacto por color, E008-E010 | 1 | 0 | ID opaco y calibre por descubrir. 16K08Z usa 5649966192263168 en `/en-gb/`, nunca 5313688741478400 (11L08Z). No se cambia locale para aparentar mercado ES. |
| Visual-Click | Confirmado en muestra | `/products/`, E011-E013; calibre requerido para nuevas rutas | 2 | 0 | Prada sol y Miu Miu montura. Dos URLs documentadas; `/en-eu` no prueba mercado ES. |
| Edel-Optics | Parcial | Mapping de URL completa, E014-E016 | 1 | 0 | `selectedSize=1907958` es Product Id, no 54 ni EAN. Para otros productos falta ID/handle autorizado. |
| Amevista | Parcial | Aliases concretos, E017-E021 | 2 | 0 | PR27ZS 16K08Z lleva 0PR, 19J09Z no; TF3104D usa `tiffany`. No extrapolar a otras referencias. Dos URLs de Tiffany son dos locales, no dos productos. |
| Mia Burton | Parcial | Handles completos, E022-E027 | 2 | 1 | Se conservan `regard` y `dune`. Chloe 003 tiene UPC/EAN no equivalentes y queda en revision; Bottega 002 no se asigna a 005. |
| Optical H | Parcial | Handles completos, E028-E031 | 2 | 0 | E030 no tiene ID final; E031 contradictorio queda excluido aunque coexista con E030. Nuevos handles requieren fuente autorizada, nunca validacion por slug. |
| Domanci | No verificable | E032-E033 visibles solo como historicas pendientes | 0 | 2 | No hay generador acreditado ni verificacion automatica de estas historicas. La fuente de marca anterior y sus permisos no cambian. Sin resultados no implica inexistencia. |
| Optica Universitaria | Parcial | URLs por variante E034-E035; padre E036 pendiente | 2 | 1 | REF/handles descubiertos, numericos o alfanumericos. Un padre sin color no es la variante. |
| ConGafasDeSol | Parcial | Handles concretos E037-E038 | 1 | 0 | Fausto antes/despues de FT0711 segun producto; otras marcas/categorias requieren evidencia. |
| Celine Opticien Lunetier | Parcial | Padres E039-E040, sin atribuirlos a colores | 0 | 3 | Falta URL/ID de variante actual. Fuente autorizada debe vincular URL completa, identidad, precio y stock. No inventar `variant`. |
| Visio Factory | Parcial | Subregla Prada PR27ZS; mappings E041-E044 | 3 | 2 | Nuevas rutas Prada requieren calibre y puente. Cartier tiene handle propio. E043 queda pendiente para el UPC 0805289126577; el UPC 0805289126584 usa su asociación 54-18 para una candidata aún no verificada. 901/58 conserva su identidad. |
| Otticait Eyewear | Confirmado en muestra | Marca/modelo/color y alias FT5634-B, E045-E047 | 18 | 0 | Prada sol y Tom Ford montura. 16 hipotesis y dos mappings; seleccion de talla y oferta requieren comprobacion posterior. |
| **Total (100 x 14)** | | **63 hipotesis generadas + 20 pares con URL documental comprobable** | **83** | **9** | **92 pares tienen alguna candidata; no son 92 verificaciones.** |

Puede haber una candidata pendiente adicional junto a otra comprobable
(Optical H E031 o Universitaria E036); por eso los motivos pendientes y
el numero de URLs no equivalen a los pares de la tabla.

## Identidad, ofertas y acceso: tres validaciones pendientes independientes

1. **Identidad:** se preserva [PRICE_IDENTITY.md](PRICE_IDENTITY.md). Marca,
   modelo y color deben comprobarse en la ficha; calibre no es una barrera.
   GTIN equivalente no oculta contradicciones de modelo/color ni varios GTIN
   no equivalentes. No se fusionan los UPC 805289126577 y 805289126584.
   FT1044/S y DUNE OPT mantienen sus regresiones existentes.
2. **Oferta:** no se cambia el extractor. Variante activa, Product/Offer y
   query completa deben coincidir. Precio, stock, IVA y mercado se evalua
   despues por los componentes existentes; idioma, locale o EUR no prueban ES.
   Sin HTML crudo no se certifica extraccion por tienda ni se inventan selectores.
3. **Acceso:** una URL construida no habilita metodos. Se mantiene la puerta
   `ficha`, los incidentes persistentes (incluso originados por otro metodo),
   pausas, presupuesto y robots del agente real para cada ruta. Tambien se
   respetan los estados web heredados `blocked` y `robots_restricted` en el
   lote/localizador: la generacion no es una via alternativa para levantarlos.
   No se cambian permisos, condiciones, mercados, credenciales ni planificaciones.

## Administracion y estados

En **Actualizaciones > Lote supervisado**, la nueva seccion
**Candidatas locales y requisitos pendientes (sin HTTP)** muestra EAN,
competidor, URL, estado, regla, procedencia, atributos y referencias.
La vista previa no guarda candidatas ni prepara trabajos. Las fichas ya
verificadas no generan alternativas.

- `pending_verification`: hipotesis comprobable, **no URL verificada**.
- `probable`: evidencia historica, contradictoria o ficha padre; no se
  intenta verificar automaticamente por la nueva via.
- `missing_attribute` y `needs_authorized_discovery` son motivos del plan,
  no estados nuevos de colas. Se explica el atributo/handle/variante pendiente.
- Los estados existentes `review`, `unavailable`, `not_found`, `waiting`,
  `blocked` y `pilot` mantienen su significado. El alcance del piloto no cambia.

Solo preparar y aprobar un lote permite consultas futuras. Este desarrollo
no ejecuta lotes comerciales, activa colas reales ni habilita diarios.
Para una reversion de codigo, la columna aditiva puede permanecer; no hay
que borrar historicos ni candidatas. Si se revirtiera a una version que no
conoce los bloqueos heredados, revisar esa limitacion antes de reanudar.

## Ampliacion de cobertura Amevista y Edel-Optics

Se incorporan las observaciones manuales aportadas para esta correccion.
No son capturas HTTP ni verifican identidad, oferta, mercado, IVA, stock,
autorizacion o vigencia.

- **Amevista:** Miu Miu genera candidatos por marca/modelo/color en sol y
  monturas, con evidencia separada por categoria. Bottega Veneta usa la misma
  forma de ruta solo para sol. No se reutiliza el patron de monturas para
  Bottega ni se añade ninguna excepcion por EAN. Para Ray-Ban se conservan
  unicamente los formatos documentados: RB2140 901 y 901S31 mantienen el
  sufijo `wayfarer`; RB4472 1359/2 publica `1359-2` en la URL. No se aplica
  esta conversion de color ni el sufijo a otros modelos/colores.
- En las fichas Amevista compartidas, cada Product se vincula por GTIN
  normalizado y dimensiones publicadas en el mismo nodo estructurado. La
  seleccion no depende del orden de listas paralelas. Un ProductGroup puede
  aportar un precio comun sin stock; la disponibilidad procede de la variante.
  El calibre no cambia el precio, pero una contradiccion publicada entre EAN
  y dimensiones deja el par ambiguo. UPC-A de 12 digitos se normaliza con cero
  inicial para comparar con EAN-13, sin perder el EAN original del manifiesto.
- **Edel-Optics:** para Prada, Tom Ford, Miu Miu y Bottega Veneta dentro de las
  categorias demostradas, se genera la ruta modelo-color-marca sin inventar
  `selectedSize`. El parser resuelve el ID solo si la URL ya lo publica o si
  hay exactamente un radio `prd<id>` marcado y los datos de producto vinculan
  ese mismo ID. El EAN del microdato Edel se toma del bloque
  `eoDetailListTileSize<id>` coincidente. Una seleccion ausente, multiple o
  contradictoria queda ambigua y no aporta oferta aceptable. Se amplian rutas
  para Saint Laurent SL 596 DUNE, SL 872 ERIN y SL 908, preservando los nombres
  comerciales; los IDs de control se leen de la ficha y no se fijan en reglas.
  En esta evidencia el navegador reviso control y oferta, mientras que los UPC
  se recuperaron de paginas indexadas y no estaban visibles en la lectura
  actual. La identidad no se acepta por ese UPC indexado sin que el motor la
  vincule a datos estructurados de la variante.
- La disponibilidad se conserva desde los datos de la variante seleccionada
  (JSON-LD o microdatos ya publicados). El ID se guarda como `variant_id` y
  `selectedSize` sigue siendo Product Id, nunca calibre ni EAN. No se importan
  disponibilidad, precio ni identificadores de los ejemplos manuales.
- **Reevaluacion de continuidad:** la vista previa lee inventario, candidatas y
  permisos locales, sin HTTP. Aplicar requiere responsable y motivo, y mueve
  unicamente pares `restricted` con candidata ejecutable y permiso vigente a
  `pending`, con proxima comprobacion persistida y una entrada en
  `access_audit`. No cambia pares `review`, presupuestos, permisos, recibos ni
  filas historicas; la consulta posterior sigue pasando por el flujo normal.

La [matriz de 100 EAN × seis tiendas](PRICE_COVERAGE_100_EAN_6_STORES.csv)
contiene una fila por par, la candidata local o URL historica, la categoria
del ultimo ciclo y el motivo pendiente. Columnas separadas indican si se
verifico una ficha u oferta comparable en esta revision. `historical_link`
significa solo que la evidencia del piloto guardo una ficha; `local_candidate`
es una ruta propuesta; ninguno significa verificacion actual ni acceso
autorizado. No se ejecutaron consultas al crear la matriz. El corte contiene
138 vinculos historicos, 153 rutas candidatas, 3 pares solo en revision y 306
pares sin ruta local documentada. Los 100 EAN tienen ahora al menos una ruta
historica o candidata, no 100 fichas verificadas. No se verifico ninguna ficha
ni oferta comparable en esta revision. En ConGafasDeSol se descartaron los slugs genericos de otras
marcas: las rutas aportadas solo justifican Prada/sol y las fichas exactas
E037-E038 siguen limitadas a Tom Ford FT0711.
Las siete nuevas rutas cubren cuatro EAN en Amevista (los dos RB2140 901
comparten pagina) y tres en Edel-Optics. Para RB2140 901S31 la entrega
publicada para el 9 de diciembre, y para RB4472 la del 11 de noviembre, se
conservan como plazos; una fecha futura no se convierte en stock inmediato.
El precio comun visible entre calibres RB2140 es evidencia de pagina, no un
precio validado por el comparador. Los UPC indexados de Edel siguen
diferenciados de la evidencia actual de navegador. Los pares con ruta candidata
permanecen pendientes de comprobacion y las restricciones/permisos no cambian.

La restriccion de nuevas candidatas no borra fichas verificadas ni recibos.
Los 138 vinculos historicos del corte coinciden con URLs conservadas en los
recibos del ciclo representado; los recibos de ciclos anteriores permanecen
en el JSON fuente del piloto y no se reescriben.

## Validacion

[Pruebas nuevas](../tests/test_price_url_candidates.py): URLs documentales,
alias, marcas/categorias, atributos ausentes, IDs opacos, locale/query,
dos RB2140, padres, GTIN/color contradictorios, reutilizacion, deduplicacion,
trazabilidad persistida, migracion SQLite y vista previa sin escrituras.
Todas las respuestas de tiendas son **sinteticas** y el transporte real
`requests.Session.send` esta prohibido en estas pruebas.

Las rutas Amevista/Edel y el flujo de reevaluacion tienen regresiones
adicionales para categoria, variante seleccionada, EAN/stock de esa variante,
conflictos de IDs y auditoria sin peticiones reales. En descubrimiento, un EAN
coincidente no confirma una ficha Edel si no se resolvio la variante; la
candidata queda pendiente y no se indexa como verificada.
La URL declarada en `Product` y la URL de `Offer` tambien deben concordar con
la variante `selectedSize` solicitada; una discrepancia deja la ficha ambigua
y sin EAN, precio o disponibilidad aceptables.

Las suites existentes del comparador preservan variantes, FT1044/S,
DUNE OPT, IVA, mercado, stock, ofertas condicionadas, historicos y acceso.
El nuevo modulo se incluye en el COPY explicito de Docker y en su comprobacion
de dependencias transitivas. No se dispone de Docker local; el build sin push
de la PR debe validar la imagen. Resultados finales de pruebas y CI se
registran en la descripcion de la PR, distinguiendo local y remoto.

## Ampliacion conjunta de los seis competidores

Se conserva el manifiesto sin cambios: 100 EAN, seis competidores y 600 pares
unicos. La fuente de atributos es la matriz versionada, `Productos_100.csv`,
las asociaciones auditadas y las nueve entradas trazables de
`PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv`. La exportacion de productos contiene
100 EAN distintos, pero no publica medidas para ninguno. Los atributos
recuperados se limitan a asociaciones exactas por GTIN/EAN o selector de
variante; las medidas solo de modelo/color, las listas sin vinculo estructurado
y las URL historicas no se propagan a los EAN. El resto queda vacio, sin valores
por defecto. Los EAN se mantienen como cadenas, incluidos sus ceros iniciales.

La regla comercial sigue siendo que el calibre no cambia el precio. Si una
ficha publica un precio comun para el modelo/color, puede servir a calibres
distintos; la variante, EAN y atributos publicados se usan para vincular
disponibilidad e identidad. Una contradiccion real de identidad o stock conserva
el estado de revision. Generar una URL no acepta una oferta.

### Alcance de las reglas y fuentes

| Competidor | Normalizacion y regla implementada | Datos de variante y categoria | Descubrimiento alternativo y limite observado |
|---|---|---|---|
| Optica Optima | Categoria en la ruta y slug marca-modelo-color; `FT5634B -> FT5634-B` solo para la referencia documentada. | Distingue `/gafas-de-sol/` y `/gafas-graduadas/`; no usa calibre ni puente. | Se reutilizan fichas/sitemap o fuentes existentes autorizadas. `robots.txt` restringe los endpoints de busqueda interna; no se generan consultas de busqueda. |
| Amevista | `/es/{marca}-{modelo}-{color}` dentro de las familias documentadas. Mantiene alias Prada, Tiffany, Miu Miu, Bottega y Ray-Ban acotados; RB2140 conserva `wayfarer`, RB4472 1359/2 conserva el separador `1359-2`. | Sol y montura se tratan por separado. Los Product/Offer compartidos se vinculan por GTIN normalizado y variante estructurada, no por el orden de listas. UPC de 12 digitos se normaliza con cero inicial. | Se conserva el locale ES y las fuentes de catalogo/sitemap ya existentes; se respetan restricciones de filtros y endpoints de variante/personalizacion. No se inventan aliases para otras marcas. |
| Visio Factory | Se conserva Prada PR27ZS y se agregan subreglas limitadas por familia para FT1044, MU52YS y RB2140; no se inserta el puente universalmente ni se separan todos los colores igual. | Prada, FT1044 y MU52YS requieren calibre/puente; RB2140 usa calibre sin puente para color simple y ambos para el color slash. Solo gafas de sol en las subreglas nuevas. | Las tres fichas aportadas y sus atributos EAN-especificos permiten dos nuevas candidatas (FT1044 01E y MU52YS ZVN5D1). RB2140 901S31 queda pendiente de asociacion estructurada; E043 conserva dos filas en revision. La busqueda interna esta restringida; no se asignan medidas por orden. |
| Edel-Optics | `/{modelo}-{color}-de-{marca}.html` solo en familias/categorias documentadas; conserva `DUNE` y `ERIN`. No fija `selectedSize`. | El identificador se resuelve desde el control seleccionado de la ficha; EAN, precio y disponibilidad tienen que pertenecer a la misma variante. Sol y montura se mantienen distintas. | Se usa el sitemap y los resolutores ya existentes. IDs opacos, handles y UPC indexados no se convierten en hechos actuales ni se calculan. |
| Domanci | `/es/{gafas-de-sol|monturas}/{marca}-{modelo}-{color}-{calibre}.html`, con calibre del EAN objetivo. | La categoria determina la seccion; la talla de la URL no determina un precio diferente. | E032/E033 y el patron aportado sustentan hipotesis de URL, no fichas verificadas. Nueve EAN tienen ahora calibre con procedencia documentada y producen candidatas; 89 siguen sin calibre asociado y dos pares historicos se mantienen en revision. Se evita busqueda interna y no se alteran historicos/revisiones. |
| ConGafasDeSol | Para Prada/sol se conservan unicamente las dos grafias acreditadas del prefijo de modelo. FT0711 Fausto sigue limitado a las fichas historicas exactas; su posicion en el slug varia. | No requiere calibre en el patron Prada; no se generaliza a monturas, otras marcas ni nombres comerciales desconocidos. | Se priorizan ficha/sitemap y fuentes ya configuradas; `robots.txt` restringe `/catalogsearch/`. No se intenta la busqueda bloqueada ni se aceptan tarjetas/categorias como fichas. |

Donde no se puede generar una URL fiable, el flujo puede recurrir solo a las
fuentes autorizadas que ya esten configuradas (fichas enlazadas, feeds,
catalogos o sitemaps sujetos al control existente). No se incorpora un motor de
busqueda, endpoint, navegador, permiso ni metodo de acceso nuevo. Las
restricciones de robots no se omiten para ahorrar presupuesto.

### Reglas nuevas de formato para Visio

El adaptador agrega reglas generales por familia, no por EAN:

- FT1044 se emite como `ft1044-s-{color}-{calibre}-{puente}`; el alias `/S`
  esta respaldado para FT1044 por el ejemplo aportado, no para cualquier Tom
  Ford.
- MU52YS se emite como `mu-52ys-{ZVN}-{resto}-{calibre}-{puente}`. El separador
  del color solo se aplica al formato `ZVN...` documentado; otros colores
  requieren evidencia propia.
- RB2140 conserva el color publicado. El ejemplo de color simple usa
  `{modelo}-{color}-{calibre}`; el color con slash usa ambos componentes y las
  dos medidas. Los RB2140 901 con lecturas/medidas contradictorias de E043 no
  se resuelven por esta regla: siguen en revision.
- Prada PR27ZS conserva su regla previa, que requiere calibre y puente. Ninguna
  subregla permite fabricar medidas ausentes.

Los formatos nuevos se contrastaron con ejemplos de URLs publicas aportados
para la investigacion. Son reglas de construccion, no prueba de que cada URL
resultante exista, de que su variante sea la correcta, ni de precio, mercado,
IVA o stock. La matriz marca como vacia la URL final observada de esta revision:
el generador es local y no realizo HTTP.

### Matriz y cambio de cobertura

La [matriz de 600 pares](PRICE_COVERAGE_100_EAN_6_STORES.csv) se regenera sin
red con `python scripts/update_price_coverage_matrix.py`; el
[resumen por EAN](PRICE_COVERAGE_EAN_SUMMARY_100.csv) tiene exactamente 100
filas. La matriz conserva las columnas historicas y agrega atributos
disponibles, metodo/fuente, evidencia, alcance del patron, estado de elegibilidad,
URLs del plan local, URL final observada y motivo/datos pendientes.
`url_historica` no es una ficha actual; `url_candidata` y `urls_en_plan_local`
no son consultas HTTP; solo los recibos del flujo gobernado acreditan URL final,
ficha, oferta y resultado.

| Competidor | Historicas antes | Candidatas antes | Solo revision antes | Sin ruta guardada antes | Pares con ruta/metodo despues | Pares con URL en plan local | Elegibles localmente |
|---|---:|---:|---:|---:|---:|---:|---:|
| Optica Optima | 24 | 30 | 0 | 46 | 54 | 47 | 47 |
| Amevista | 33 | 52 | 0 | 15 | 85 | 84 | 84 |
| Visio Factory | 12 | 1 | 2 | 85 | 29 | 7 | 5 |
| Edel-Optics | 12 | 70 | 0 | 18 | 82 | 77 | 77 |
| Domanci | 21 | 0 | 1 | 78 | 100 | 11 | 9 |
| ConGafasDeSol | 36 | 0 | 0 | 64 | 36 | 14 | 14 |
| **Total** | **138** | **153** | **3** | **306** | **386** | **240** | **236** |

Los cuatro primeros estados son la clasificacion previa de las 600 filas y no
se alteraron. Los pares con metodo despues incluyen URLs historicas conservadas
y patrones condicionales; no significan que haya 386 candidatas, fichas o
permisos. En particular, Domanci tiene nueve candidatas construidas con
calibres trazables, 89 filas sin calibre y dos pares historicos/en revision.
Visio conserva dos pares en revision, dos candidatas nuevas con medidas
asociadas al EAN y 13 pares parciales sin medidas suficientes. El plan local
contiene 240 pares con URL candidata, ejecutable o no; 236 pares son
elegibles localmente para una comprobacion gobernada, no hay una
consulta aprobada por el mero hecho de ser elegible.

En Visio siguen sin calibre/puente seis FT1044, seis MU52YS y el RB2140 901S31
`8056266001833`; para este ultimo la lista de EAN y tallas de la evidencia #124
no ofrece una asociacion estructurada. En Domanci, los 89 pares restantes
carecen de calibre enlazado al EAN. E032/E033 siguen en revision: sus URL y
medidas historicas no identifican un GTIN actual. El indice autorizado del
localizador puede usar feeds/sitemaps ya configurados, pero los archivos
auditados no contienen una fuente sin medidas vinculada a estos pares; no se
ejecuto ni se altero ninguna fuente.

Por producto, los 100 EAN tenian al menos un enlace historico/candidato antes
de esta ampliacion, y los 100 mantienen al menos un enlace o metodo de
descubrimiento documentado despues; ninguno queda clasificado como producto
inexistente. La distribucion del numero de tiendas con enlace previo es
15/21/29/27/6/2 EAN con 1/2/3/4/5/6 tiendas, respectivamente. Despues, contando
enlaces o metodos condicionales, es 4/14/21/23/29/9 EAN con 1/2/3/4/5/6 tiendas.
Hay 95 EAN con al menos una candidata local elegible, igual que antes: los once
nuevos pares elegibles corresponden a EAN que ya tenian otra ruta elegible.
Cinco aun no tienen
ninguna candidata local elegible:

- `0889652543574` Chloe CH0289S 003
- `0889652630922` Chloe CH0347S 001
- `8056262268902` Ray Ban RB4441D 667787
- `0889652651774` Saint Laurent SL M160 001
- `0889652359700` Saint Laurent SL M94 001

Los motivos concretos por tienda y par, incluidos los atributos pendientes, se
encuentran en la matriz; la falta de una candidata ejecutable no equivale a
falta de producto. Las 600 filas no elevaron ningun estado a ficha verificada u
oferta comparable vigente. No se cambio el manifiesto, la vigilancia, los
presupuestos, permisos, recibos ni revisiones; la regeneracion de la matriz y
las pruebas automatizadas no consultaron tiendas ni ejecutaron Vytria/Odoo.

## Evidencia conectada al runtime y corte local (2026-11-12)

La ampliacion parte de `d0b08ea5366ae6fede8884bff44f8d5f4bce2b20`, que ya
incluye la PR #125. Se inventariaron los cuatro ficheros locales siguientes;
se leyeron sin editarlos ni copiarlos al repositorio:

| Fichero | Fecha local | Bytes | SHA-256 |
|---|---|---:|---|
| `evidencia_7_ean_pendientes_20261110.json` | 2026-11-10 12:40:27 +01:00 | 6,418 | `09E310C225D30A5660D1AE98FD57577FE96D2BF73DB22744DD529C33C7C79FC1` |
| `piloto-72h-historial (2).json` | 2026-11-09 19:08:51 +01:00 | 9,689,886 | `E4B99D23F2A6F0AD31800F7777E93678CE23F7D0AF45CA7B69817C94A2035BF1` |
| `price-watch-continuity-report (1).json` | 2026-11-12 12:01:16 +01:00 | 13,424,820 | `F68E29D384988A230434FDE692F7D89D309D0437E6437934622DAA901A2D3F8B` |
| `price-watch-continuity-report (2).json` | 2026-11-12 12:39:49 +01:00 | 15,655,838 | `7FED7EEF40D719247963254586D20B523E897ABB92DC5AA7E45F1CB768D7090B` |

El corte mas reciente es el informe `(2)`, cuyo latido interno es
`2026-11-12 12:00:42 +01:00` y cuyo build registrado es
`ee110d085b11c5316bf6f1d7351492d81a049e4e`. Su estado `active` describe esa
instantanea; no demuestra que Cloud siga activo ni sano ahora. El piloto de 72
horas es un historial separado y conserva su expiracion del 2026-11-09 a las
16:26. El informe `(1)` es un corte anterior, con latido a las 11:46:07.

En el informe `(2)`, el manifiesto declara 100 EAN y seis competidores. El
resumen contiene 221 pares con HTTP real, 476 comprobaciones con HTTP, 311
aplazados, 175 restringidos, 114 en revision, cero ofertas reutilizadas, 106
ofertas comparables vigentes y una caducada. Los 529 recibos HTTP unicos se
desglosan en 511 comerciales y 18 de `robots.txt`. Al deduplicar por ID, son
tres recibos robots por dominio, uno en cada ciclo diario del 10, 11 y 12 de
noviembre; los 18 son HTTP 200 y no hay repeticiones del mismo origen/agente
dentro de esos ciclos. El cache sigue vigente por origen y agente, con TTL de
24 horas; no se omiten comprobaciones robots ni se modifican permisos. Estas
metricas son solo las del informe guardado, no un diagnostico en vivo.

### Resolucion unica de atributos

`resolve_product_attributes()` valida el CSV de medidas al cargarlo: columnas,
GTIN canonicos y unicos, identidad marca/modelo/color, calibre, puente,
asociacion y limitacion. Enriquece una copia efimera del producto solo para el
mismo GTIN; no escribe en el catalogo. Medidas, identidad o procedencia
contradictorias producen `measurement_conflict` y no una candidata ejecutable.
La matriz, la vista previa de `WatchBatch`, la reevaluacion local y
`PriceLocator` usan `candidate_plan()` y, por tanto, el mismo resolver. El
Dockerfile incluye el CSV requerido por ese flujo.

Los nueve registros de evidencia preservan fuente, tipo de asociacion y
limitacion por separado. Los UPC de 12 digitos se validan y normalizan a GTIN
de 13 digitos, conservando el cero inicial. Los IDs Edel siguen resolviendose
desde la ficha, no desde estos ejemplos indexados. Para una variante de
`ProductGroup`, un precio comun del grupo se conserva al filtrar la `Offer`
de `selectedSize`; precio discrepante sigue siendo ambiguo y el stock se toma
de la oferta de la variante seleccionada. Si la URL de una `Offer` contradice
el `selectedSize` seleccionado, sus datos de precio y stock se descartan y la
ficha queda ambigua. No se convierte una entrega futura en stock. Una
asociacion `model_color_only` se mantiene para trazabilidad, pero no aporta
calibre ni puente a un EAN.

La normalizacion de referencia conserva `DUNE`/`ERIN` aunque aparezcan antes
del codigo Saint Laurent; `OPT` sigue siendo una contradiccion para la familia
de sol. `Coleccion`/`Collection` no contaminan el modelo y `WAYFARER` se trata
como alias de RB2140 solo cuando no forma parte del modelo objetivo; otro
codigo RB continua siendo contradictorio. Las pruebas mantienen los casos de
control de identidad y stock.

### Matriz tras conectar el resolver

La matriz regenerada mantiene exactamente 600 pares unicos, 100 EAN y seis
tiendas. No modifica la clasificacion previa de historicas/candidatas (138 /
153 / 3 / 306). El plan local contiene 239 URLs candidatas; 236 son
localmente elegibles para una comprobacion gobernada. Los 100 EAN tienen un
metodo, enlace historico o ruta documentada; 96 tienen al menos una URL en el
plan local y 95 al menos una candidata elegible. Nada de ello equivale a una
ficha verificada o a una oferta comparable.

| Competidor | Rutas o metodos | URLs en plan local | Elegibles localmente |
|---|---:|---:|---:|
| Optica Optima | 54 | 47 | 47 |
| Amevista | 85 | 84 | 84 |
| Visio Factory | 29 | 7 | 6 |
| Edel-Optics | 82 | 77 | 77 |
| Domanci | 100 | 10 | 8 |
| ConGafasDeSol | 36 | 14 | 14 |
| **Total** | **386** | **239** | **236** |

Cinco EAN no tienen candidata local elegible: Chloe CH0289S 003
(`0889652543574`), Chloe CH0347S 001 (`0889652630922`), Ray-Ban RB4441D
667787 (`8056262268902`), Saint Laurent SL M160 001 (`0889652651774`) y
Saint Laurent SL M94 001 (`0889652359700`). Cuatro no tienen ninguna URL
candidata en el plan; la candidata restante es historica y no se eleva a ficha
verificada. Los motivos y atributos faltantes por cada uno de los 600 pares
constan en la matriz, incluida la limitacion de la evidencia de medidas.

La actualizacion de matriz es local y no genera peticiones. No se altera
vigilancia, manifiesto, permisos, presupuestos, recibos, revisiones ni
sincronizacion Vytria/Odoo; tampoco se ejecutan lotes comerciales.
