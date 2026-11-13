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

El primer corte incorporo nueve registros documentales de medidas en
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
-   La medida 54-21 asociada al GTIN 8056597875349 en E041 puede sustentar una
  **hipotesis de URL** Bassol. No se escribe en el catalogo ni acredita stock.
  Un calibre explicitamente aportado en `products.caliber` sirve para construir
  otras rutas acotadas; puente se lee del formato existente `54-21`.
- Al ejecutar posteriormente un lote aprobado, las candidatas comprobables
  entran en `discovery_index`, no como fichas verificadas. Se conserva
  `candidate_evidence` JSON por EAN: procedencia, fecha/base, regla, atributos
  usados y referencias E###. Es la unica columna nueva, con valor por defecto
  `{}` y migracion aditiva para SQLite existente.
- El plan completo, incluidas fichas padre, conflictos e historicas no
  ejecutables, se conserva como JSON en `locate_events.metadata` dentro del
  evento del resultado real. No crea una transicion `pending` ficticia ni otra
  fila de auditoria. Al localizar, el plan se pasa al descubrimiento solo para
  priorizar `discovery_sources` ya activos que coincidan con los entrypoints;
  no crea ni activa fuentes. El estado, metodo, resumen y peticiones siguen
  describiendo el resultado real del trabajo.
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
| ConGafasDeSol | No se generan slugs. Se conservan solo las URL exactas E037-E038 y U009-U010; FAUSTO sigue vinculado a FT0711/01A por su referencia concreta. | Las medidas no forman parte de estas rutas y las fichas siguen sin verificarse en esta ampliacion. | No hay entrypoint ejecutable nuevo; persiste el 403. No se prueba otra grafia, se evita la busqueda bloqueada y no se aceptan tarjetas/categorias como fichas. |

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

## Correcciones de la PR #126: contexto Docker, identidad e IVA (12/11/2026)

El HEAD inicial comprobado fue `baf44ff8d349ee36ff34b38818847bdd14e7a9d8`,
sin avances remotos posteriores. En la ejecucion
[43224679620](https://github.com/sginfoocio/abc/actions/runs/43224679620)
las pruebas, lint y sintaxis pasaron; Docker fallo antes del smoke: el `COPY`
del CSV de medidas no podia leer un recurso excluido por `.dockerignore`.
Se conserva `docs/PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv` como ubicacion
estable del motor. El contexto permite unicamente ese fichero de `docs`;
los demas documentos, informes y CSV locales siguen excluidos. El contrato
se comprueba junto con Dockerfile y una importacion/resolucion en una copia
aislada. El smoke real ejecuta con `--network none`: carga nueve registros,
resuelve E041 para Prada PR27ZS 16K08Z a calibre 54/puente 21, comprueba la
procedencia y que el producto original no se modifica. La ausencia del
recurso causa un error explicito, nunca cobertura vacia.

### Amevista: hechos conservados y causa reproducida

El informe `(2)` registra la misma contradiccion `RB2140, WAYFARER` para
`0805289126577`, `0805289126584` y `8056266001833` (checks 1006, 1007 y
1026). La correccion anterior del titulo no bastaba: `Product.model`
tambien puede contener el nombre comercial. Ahora WAYFARER solo se reconoce
como nombre comercial de Ray-Ban RB2140 y FAUSTO de Tom Ford FT0711, con el
codigo de modelo acreditado en la misma referencia. No se eliminan nombres
desconocidos ni otros codigos RB/FT. Un segundo codigo contradictorio, aunque
aparezca despues del color, sigue bloqueando la identidad. El historial del
piloto conserva `Tom Ford FT0711 01A Fausto` y su URL Amevista, pero no es
una comprobacion vigente.

Se reprodujo ademas la perdida de IVA al seleccionar por EAN un ProductGroup:
la extraccion aplicaba la politica configurada, pero la segunda extraccion de
la variante olvidaba esa politica. La politica ya suministrada al extractor
se conserva como procedencia y se aplica a la variante elegida solo si su
oferta no publica tratamiento fiscal propio. Una declaracion explicita de
esa oferta tiene prioridad. La seleccion sigue siendo por GTIN normalizado,
no por orden, y no hereda stock de otra talla. Las comprobaciones de mercado
verificado y sus conflictos siguen impidiendo ofertas comparables fuera del
mercado configurado.

Los seis `vat_unconfirmed` del informe `(2)` son:

| EAN | URL conservada | Dato fiscal guardado |
|---|---|---|
| 8056262410929 | https://www.amevista.com/es/miu-miu-mu-53wv-zvn1o1 | `includes_tax=null`; destino ES |
| 8056262600078 | https://www.amevista.com/es/miu-miu-mu-01yv-14l1o1 | `includes_tax=null`; destino ES |
| 8056262833902 | https://www.amevista.com/es/ray-ban-rb4472-1359-2 | `includes_tax=null`; destino ES |
| 8056597669016 | https://www.amevista.com/es/miu-miu-mu-04uv-1ab1o1 | `includes_tax=null`; destino ES |
| 8056597669023 | https://www.amevista.com/es/miu-miu-mu-04uv-vau1o1 | `includes_tax=null`; destino ES |
| 8056597669047 | https://www.amevista.com/es/miu-miu-mu-04uv-2az1o1 | `includes_tax=null`; destino ES |

Sus hechos estructurados no publican `valueAddedTaxIncluded` ni precio sin
IVA. El informe no exporta `competitor_web_config`, de modo que no acredita
cual era la politica efectiva en ese momento. La perdida de politica es
reproducible, pero no prueba por si sola que los seis precios incluyeran IVA.
La nota de politica incluida en recibos anteriores tampoco certifica su
vigencia o alcance actual. Esa era la limitacion fiscal del corte anterior;
queda resuelta por la confirmacion expresa del responsable de negocio del
12/11/2026, registrada mas abajo para los 14 competidores. Idioma, EUR y
dominio siguen sin servir como prueba fiscal. Los seis checks se reproducen
ahora con hechos reducidos y transporte **sintetico**: la politica registrada
llega a la oferta seleccionada aunque la configuracion antigua sea `unknown`.
No son los cuerpos HTTP originales ni se convierten en ofertas vigentes.

### Edel: fuentes de variante, oferta y mensajes

Los checks 1038 (FT0711 01A, `0889214029614`, radio observado 708602),
1040 (FT0884 01E, `0889214281791`, 1461733), 1067/1069/1070 (los tres
Saint Laurent) conservan la secuencia contradictoria de mensajes indicada.
La causa reproducida no es un cambio de IDs: cuando falta JSON-LD,
`radio_selected=True` creaba inmediatamente una pagina ambigua, incluso
habiendo EAN asociado al ID en `eoDetailListTileSize...`. Se anadian despues
mensajes positivos sobre esa asociacion, sin volver a resolver la oferta.

Ahora se intentan los microdatos del widget principal `eoProductPrice`
antes de declarar datos insuficientes. El EAN del bloque de variante y la
Offer deben pertenecer al mismo ambito Product; no se usan tarjetas ni
`aside`/productos relacionados. Con varias variantes, la oferta/stock
necesita URL `selectedSize` coincidente o un contenedor de ese ID. Solo una
variante documentada permite usar su unica oferta principal sin URL
independiente. El radio por si solo no aporta EAN, precio o stock.
Identificadores Product/Offer/radio contradictorios y UPC/EAN discrepantes
siguen bloqueados, sin precio, disponibilidad ni EAN aceptables.
El precio comun de ProductGroup sigue siendo reutilizable para el mismo
modelo/color; nunca se exige precio distinto por talla.

Los cinco ejemplos se prueban de extremo a extremo con transporte
simulado, incluidos IDs arbitrarios en las regresiones de variante ya
existentes. Los valores observados son datos de test, no constantes del
adaptador. Si la oferta no tiene vinculo suficiente, el motivo distingue
`EAN vinculado, pero ... oferta/stock ...` de ausencia de datos de identidad.
Los mensajes no afirman simultaneamente que falta la asociacion EAN y que
esta existe.

### Limites del corte anterior y comprobaciones posteriormente resueltas

Los informes guardados contienen hechos extraidos, muestras serializadas y
trazas, **no los cuerpos HTML originales**. La fixture historica reducida de
Prada/Edel acredita la gramatica de microdatos, no el HTML actual de FT0711,
FT0884 ni Saint Laurent. Los nuevos tests son reconstrucciones sinteticas
explicitamente rotuladas; no acreditan que las cinco fichas actuales tengan
ya la vinculacion exigida. En aquel corte no se hicieron nuevas consultas
comerciales. La comprobacion publica posterior del 12/11 queda separada
en la seccion final y en su registro de procedencia.

Estos eran los ejemplos representativos pendientes. La politica fiscal
ya no requiere confirmacion adicional y los otros seis casos tienen ahora
HTML publico real contrastado (no el HTML de los informes historicos):

| EAN / tienda | URL exacta | Dato a comprobar y decision que desbloquea |
|---|---|---|
| 8056262833902 / Amevista | https://www.amevista.com/es/ray-ban-rb4472-1359-2 | Resuelto fiscalmente por la decision de negocio para los 14 competidores. Replay de los seis checks, sin inferir porcentaje ni renovar su vigencia. |
| 0889214029614 / Amevista | https://www.amevista.com/es/tom-ford-ft0711-01a-fausto | Resuelto mediante HTML real del 12/11: Product model Fausto, FT0711/01A, GTIN y Offer. No se convierte el recibo historico en oferta vigente. |
| 0889214029614 y 0889214281791 / Edel | https://www.edel-optics.es/FT0711-01A-de-Tom-Ford.html ; https://www.edel-optics.es/FT0884-01E-de-Tom-Ford.html | Resuelto en HTML real del 12/11: radio, EAN/UPC y Offer/stock del mismo Product Id; replay completo offline documentado mas abajo. |
| 0889652429717, 0889652650838 y 0889652675602 / Edel | https://www.edel-optics.es/SL-596-DUNE-006-de-Saint-Laurent.html ; https://www.edel-optics.es/SL-872-ERIN-001-de-Saint-Laurent.html ; https://www.edel-optics.es/SL-908-002-de-Saint-Laurent.html | Resuelto con sus tres respuestas HTML reales actuales y cadena UPC/EAN-control-Offer/stock; no se usan UPC indexados como sustituto. |

### Matriz y controles de esta correccion

La regeneracion local conserva **600 filas unicas, 100 EAN y seis tiendas**.
No cambia ninguna fila respecto al HEAD de partida: 386 pares con ruta/metodo,
239 candidatas, 236 elegibles localmente; 96 EAN con candidata y 95 con alguna
elegible. No se elevan candidatas a fichas verificadas ni ofertas comparables.
El resolver compartido de atributos y las reglas de las otras cuatro tiendas
se conservan. Los controles focalizados incluyen extraccion-identidad-oferta
con transporte simulado, IVA desconocido/explicito/politica, mercado
contradictorio, stock/entrega futura, contradicciones de variante y contrato
Docker. La validacion completa y el build/smoke de la imagen del commit
final se registran en la PR; no se sustituyen por la ejecucion anterior.

No se modificaron los originales inventariados, produccion, vigilancia,
manifiesto, permisos, robots, bloqueos, presupuestos, historicos, revisiones
ni sincronizacion. No se fusiona ni despliega esta PR.

## Cierre fiscal y contraste publico de la PR #126 (12/11/2026)

### Decision de negocio, no revision individual de webs

Se registra en [competitor_requirements.py](../competitor_requirements.py)
la decision versionada `business-vat-included-14-2026-11-12`:
**«Los precios de los 14 competidores incluyen IVA»**. Procedencia:
confirmacion expresa del responsable de negocio en esta conversacion.
La fecha es la de registro de esta decision, no una fecha de revision de
condiciones de cada tienda. No acredita un porcentaje de IVA.

El ambito es la lista explicita de los 14 competidores registrados, por
origen HTTPS exacto (con/sin `www`), no dominios desconocidos o subdominios
arbitrarios. Se aplica como politica de codigo de solo lectura al extraer
y seleccionar variantes, recuperar hechos cacheados y normalizar nuevas
importaciones. Identificador, fuente y fecha se guardan en la evidencia de
la pagina y de cada nueva observacion. No migra, reescribe o rejuvenece
ofertas/recibos anteriores, ni necesita una escritura directa en produccion.
El panel muestra la politica efectiva y su origen, diferenciada de la
configuracion fiscal antigua. No es una autorizacion de acceso.

La misma normalizacion se aplica antes de validar la admision local de una
observacion y antes de generar su fila de importacion. Asi, un hecho
recuperado sin IVA no queda rechazado por la validacion y aceptado despues
por el importador con otra politica; los conflictos explicitos siguen
rechazados en ambos pasos. No se toca el estado de ningun piloto real.

La ausencia de una declaracion fiscal por ficha deja de generar
`vat_unconfirmed` en esas 14 tiendas. Una declaracion explicita de IVA
excluido no se sobrescribe: queda en `manual_review`/`tax_conflict`, sin
oferta comparable. Las contradicciones Offer/precio de venta tambien se
conservan, incluso cuando el precio procede de ProductGroup.
Fuera del registro sigue siendo necesaria evidencia fiscal aplicable.
Mercado, moneda, identidad, disponibilidad y vigencia mantienen sus vetos.

Los checks Amevista 1015, 1024, 1025, 1027, 1028 y 1029 del informe `(2)`
se reproducen con sus EAN, variantes y precios guardados, en transporte
simulado. Los seis conservan IVA incluido hasta la fila normalizada, sin
inventar tipo: `8056262410929`, `8056262600078`, `8056262833902`,
`8056597669016`, `8056597669023`, `8056597669047`.
Esto resuelve su bloqueo fiscal, no acredita seis ofertas actuales.
El precio comun de modelo/color no exige precio independiente por calibre;
el stock se toma exclusivamente de la variante seleccionada por EAN.

### HTML real contrastado, separado de las reconstrucciones de test

El [registro factual de comprobacion](PRICE_CONTINUITY_PUBLIC_CHECKS_20261112.json)
incluye URL solicitada/final, fecha UTC, estado HTTP, SHA-256 del cuerpo
original, hechos extraidos y resultado del replay local. Los cuerpos
completos se conservan localmente, no se incorporan a la imagen ni se
presenta una fixture sintetica como respuesta original.

Se consultaron exactamente **seis fichas y dos robots.txt** entre
16:39:13 y 16:39:50 UTC (17:39 en Madrid): cinco Edel y una Amevista.
Ocho HTTP en total, cero redirecciones, cero reintentos, pausas de 6 s
por competidor, agente `Cloud-PriceComparator`. Se evaluaron las rutas
contra los robots recibidos antes de consultarlas. Sin busquedas, lotes,
trabajos de produccion, CAPTCHA, bloqueos ni cambios de permisos/limites.
El replay posterior reutiliza esos cuerpos con red real deshabilitada
y SQLite desechable; no consulta otra vez las tiendas.

| Tienda / EAN | Modelo-color | Product Id observado | Precio EUR publicado |
|---|---|---|---|
| Edel / 0889214029614 | FT0711 01A | 708602 | 201,60 |
| Edel / 0889214281791 | FT0884 01E | 1461733 | 201,60 |
| Edel / 0889652429717 | SL 596 DUNE 006 | 1971610 | 216,00 |
| Edel / 0889652650838 | SL 872 ERIN 001 | 2352387 | 174,00 |
| Edel / 0889652675602 | SL 908 002 | 2423524 | 126,00 |
| Amevista / 0889214029614 | FT0711 01A FAUSTO | No aplica | 240,00 |

En Edel el HTML real explica otros dos rechazos: el parser de radios no
aplicaba la compatibilidad `--!>` que ya usaba el de microdatos, por lo
que perdia controles posteriores; y hay tres widgets equivalentes
(escritorio, cesta y movil) dentro de `eoProductsPriceBySize-<id>`.
Se conservan solo los widgets del ID seleccionado, en el mismo Product
que el bloque EAN; duplicados identicos son una oferta, diferencias
de precio, fiscalidad o stock siguen siendo ambiguas. Los nombres
duplicados del Product se clasifican por separado, sin concatenar
referencias ni ocultar otro codigo de modelo.

Se comprueba tambien Product URL/SKU, Offer URL/SKU/GTIN cuando existen,
el control marcado y UPC normalizado con cero inicial. Ningun ID observado
esta codificado en el adaptador. Las tarjetas y otras variantes no aportan
stock ni identidad. La Offer de los cinco ejemplos publica `InStock`,
EUR y destino ES; se conservan handling/transit time sin prometer entrega
inmediata a partir de una fecha futura.

FAUSTO publica actualmente Product `model=Fausto`, titulo FT0711 01A,
GTIN `889214029614`, Offer EUR, `InStock` y envio ES: el alias acotado
a FT0711/01A pasa extraccion, normalizacion, identidad e importacion.
No se ha usado el recibo historico como prueba de esa respuesta actual.
Los seis cuerpos reales pasan el recorrido completo offline con las
correcciones. No queda evidencia manual pendiente para estos ejemplos;
no se extrapola este resultado a los otros 36 casos Edel no consultados.

### Matriz final y limites operativos

La matriz regenerada mantiene **100 EAN, seis tiendas y 600 pares unicos**
(incluidos ceros iniciales). Anade la procedencia fiscal registrada y no
la pide de nuevo como dato ausente. No cambia ninguna ruta, candidata,
medida ni elegibilidad local respecto a `cf2b006`.

| Competidor | Ruta/metodo | Candidata local | Elegible local |
|---|---:|---:|---:|
| Optica Optima | 54 | 47 | 47 |
| Amevista | 85 | 84 | 84 |
| Visio Factory | 29 | 7 | 6 |
| Edel-Optics | 82 | 77 | 77 |
| Domanci | 100 | 10 | 8 |
| ConGafasDeSol | 36 | 14 | 14 |
| Total | 386 | 239 | 236 |

**96 EAN** con alguna candidata y **95** con alguna elegible localmente.
Recuperacion de candidatas/elegibilidad en esta ultima correccion: **0**
en cada tienda; bloqueo fiscal resuelto en los seis casos Amevista
reproducidos. Las seis comprobaciones publicas son evidencia puntual
separada, no promocion automatica de candidatas o revisiones.
La matriz sigue siendo un plan local, no un inventario del estado actual
de produccion ni de ofertas comparables vigentes.

Las regresiones incluyen las seis ofertas, alcance de 14 tiendas,
herencia fiscal ProductGroup, ausencia de porcentaje, contradicciones
fiscales, precio comun y stock separado, cinco estructuras Edel observadas
y sus variantes contradictorias, ademas de los controles existentes.
El smoke Docker conserva la carga real de nueve evidencias de medidas y
la resolucion E041 con `--network none`; comprueba ademas el registro
fiscal de 14 tiendas y su herencia en una variante sintetica.
La ejecucion CI/build/smoke del nuevo commit se enlaza en la PR.

No se modifican produccion, vigilancia, manifiestos, permisos, robots,
bloqueos, presupuestos, historicos o revisiones. Sin sincronizacion
Vytria/Odoo, aceptacion automatica, merge, despliegue o activacion.

## Continuidad local de patrones para los mismos 100 EAN (evidencia 12-13/11/2026)

Esta seccion actualiza la matriz de patrones despues del corte anterior; no
reescribe sus resultados historicos. Se conservaron los mismos 100 EAN
(incluidos ceros iniciales), seis competidores y 600 pares unicos. El origen
es el paquete local `Patrones_100_EAN_6_competidores_Copilot`, en particular
`Especificacion_100_EAN_6_competidores.json`; no se consulto a tiendas ni se
leyeron datos actuales de produccion.

### Atributos, procedencia y limites

El registro conserva los nueve registros dimensionales iniciales y anade seis
asociaciones EAN/variante de entradas indexadas N006-N011. Los seis publican
UPC/EAN y medidas en el mismo resultado del indice; no son HTML original,
respuestas HTTP del motor, disponibilidad actual ni prueba de oferta:

| EAN | Modelo-color | Medidas publicadas | Evidencia |
|---|---|---:|---|
| `0889652651774` | Saint Laurent SL M160 001 | 53-19 | N006 |
| `0889652359700` | Saint Laurent SL M94 001 | 53-20 | N007 |
| `8056597669016` | Miu Miu MU 04UV 1AB1O1 | 52-19 | N008 |
| `8056597981361` | Miu Miu MU 01XV VAU1O1 | 50-18 | N009 |
| `8056262600078` | Miu Miu MU 01YV 14L1O1 | 51-16 | N010 |
| `8056262410929` | Miu Miu MU 53WV ZVN1O1 | 53-20 | N011 |

Cada fila en el registro identifica fuente, fecha del paquete, tipo de
asociacion y limitacion. El enlace Domanci MU 53WV `...-51.html` (U005) se
mantiene como dato de ruta distinto: el EAN tiene 53-20, la talla 51 no se
transfiere al EAN ni a su stock y esa candidata queda en revision. N012
conserva una URL Tiffany de indice sin EAN contrastado, por lo que su ruta
no aporta calibre/puente al producto. N013 permite proponer la ruta Edel
RB4441D sin `selectedSize`; la variante sigue pendiente de resolucion en ficha.

El generador combina los ejemplos documentales y suplementarios exactos.
Compara modelo/color y, cuando se publico un GTIN, lo normaliza antes de
asociarlo; una contradiccion mantiene la candidata en revision. Las medidas
de `route_attributes` se guardan separadas de los atributos del EAN, y una
talla/puente distinto o no vinculado no acredita disponibilidad. El plan de descubrimiento compartido es documental: no se ejecuta en vista
previa ni crea autorizaciones. Durante una ejecucion gobernada,
`PriceLocator` solo consulta fuentes ya activas y autorizadas; da prioridad
a una plantilla activa que coincida con los entrypoints del plan. Las paginas
y enlaces resultantes se reutilizan desde `discovery_pages` y
`discovery_index`, sujetos a caducidad; la configuracion local y el permiso
real no se infieren del plan ni de la matriz. `PriceLocator` persiste el plan
y sus candidatos en la auditoria local existente.

ConGafasDeSol deja de generar slugs Prada para EAN sin enlace exacto aportado.
Se retienen las referencias concretas E037-E038 y U009-U010, pero no se
fabrican URL probando grafias; se conserva el bloqueo 403 y la ausencia de
entrypoints ejecutables. En el manifiesto actual queda una candidata
comprobable en esa tienda, FT0711/01A; esto no altera el acceso ni la
comprobacion contra robots.

### Matriz recalculada

La regeneracion local comprobo las 600 filas y 100 EAN, sin HTTP. La
clasificacion historica de origen sigue siendo 138 pares con enlace
historico, 153 con URL candidata guardada, 3 solo en revision y 306 sin URL
guardada. Frente al HEAD previo, el plan local pasa de 239 a 245 pares con
candidata y de 236 a 239 elegibles localmente. Los estados son independientes:
una ruta/metodo documentado no equivale a candidata, y ninguna candidata
acredita ficha ni oferta.

| Competidor | Enlaces historicos | Candidatas antes | Candidatas ahora | Elegibles ahora | En revision | Rutas/metodos ahora | Plan de catalogo | Entry point propuesto | Fuente default en codigo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Optica Optima | 24 | 47 | 49 | 49 | 0 | 56 | 100 | 100 | 0 |
| Amevista | 33 | 84 | 87 | 86 | 1 | 88 | 100 | 100 | 0 |
| Visio Factory | 12 | 7 | 14 | 12 | 2 | 36 | 100 | 100 | 0 |
| Edel-Optics | 12 | 77 | 78 | 78 | 0 | 83 | 100 | 100 | 0 |
| Domanci | 21 | 10 | 16 | 13 | 3 | 100 | 100 | 100 | 100 |
| ConGafasDeSol | 36 | 14 | 1 | 1 | 0 | 36 | 100 | 0 | 0 |
| **Total (pares)** | **138** | **239** | **245** | **239** | **6** | **399** | **600** | **500** | **100** |

Las 245 candidatas incluyen seis casos en revision; las 239 elegibles son
aptas localmente para una comprobacion gobernada, no autorizaciones ni
consultas aprobadas. Los 100 EAN tienen al menos una candidata y 99 tienen
alguna elegible. El unico EAN sin candidata elegible es `0889652543574`
(Chloe CH0289S 003). No se eleva ningun par a ficha verificada u oferta
comparable: ambos estados permanecen `not_checked` en las 600 filas.

Los planes de catalogo estan implementados para las seis tiendas (100 filas
por tienda), pero solo cinco tienen entrypoints documentales; ConGafasDeSol
no recibe un nuevo entrypoint por el 403 historico. Los 100 renglones
Domanci de `fuente_catalogo_predeterminada_en_codigo` reflejan un default del
codigo, no una configuracion activa o permiso vigente de produccion. Para las
600 filas, el estado de fuentes activas, robots y presupuesto queda
`not_assessed` porque la matriz se genera offline. Ningun enlace historico,
URL propuesta, nombre de catalogo o coincidencia de indice se presenta como
producto inexistente cuando falta.

La vista matricial registra por par `plan_descubrimiento_catalogo`,
candidatas, elegibilidad, atributos y pendientes concretos. Los motivos y
las URL exactas restantes se conservan en
[PRICE_COVERAGE_100_EAN_6_STORES.csv](PRICE_COVERAGE_100_EAN_6_STORES.csv);
el resumen por producto permanece en
[PRICE_COVERAGE_EAN_SUMMARY_100.csv](PRICE_COVERAGE_EAN_SUMMARY_100.csv).
El smoke Docker actualizado carga las 15 asociaciones y resuelve E041, y
comprueba con red deshabilitada que la ruta Domanci de talla 51 no reemplaza
las medidas 53-20 del EAN. Las respuestas/URLs indexadas siguen siendo
candidatas, no fichas actuales.

### Integracion del descubrimiento por catalogo

El `discovery_plan` de `candidate_plan()` sigue siendo una propuesta
documental, tomada de `Especificacion_100_EAN_6_competidores.json` (paquete
preparado el 12-13/11/2026). Los hashes de algunos ficheros declarados por la
especificacion no coinciden con la rama actual; por ello se usa como
procedencia historica, no como copia ejecutable ni prueba de estado actual.
En la vista previa se muestran por separado la propuesta, las fuentes
activas encontradas localmente, la precomprobacion de permisos y su motivo,
sin peticiones ni escrituras.

En una pasada aprobada, `PriceLocator` entrega el plan al descubrimiento
existente. Este solamente lee `discovery_sources` activos para el metodo
autorizado y prioriza las plantillas coincidentes con los entrypoints o
enlaces observados. Robots, bloqueos, presupuestos, metodos autorizados y
`GovernedFetcher` siguen controlando cada URL; canonical se registra sin
seguirse automaticamente. La paginacion se limita a enlaces publicados.
`discovery_pages` reutiliza las lecturas vigentes y `discovery_index` persiste
los href y hechos publicados (identidad, referencias y medidas con
procedencia) entre lotes. La tarjeta del catalogo solo permite hallar una
ficha: su precio/stock no se usa como oferta. No se recorre el inventario
600 veces ni se activa automaticamente una propuesta documental.

ConGafasDeSol conserva su estado detenido: no se añade fuente, no se reintenta
el 403 y solo puede reutilizarse el indice historico dentro del acceso ya
permitido. Domanci aparece como default del codigo, pero no se infiere que
estuviera configurado o autorizado localmente en la fecha del corte. Optima,
Amevista, Visio y Edel requieren que sus fuentes ya existan activas y tengan
permiso; la matriz no afirma lo contrario.

Las regresiones sinteticas cubren prioridad de una fuente activa coincidente
frente a otra fuera del plan, recorrido de catalogo a ficha verificada y
monitorizada, reutilizacion del indice y la pagina compartidos, reinicio,
paginacion publicada, caducidad, robots/permisos/bloqueos/presupuesto,
redirecciones/canonical y no uso de precio/stock de tarjetas. No se hacen
consultas comerciales en la generacion de la matriz ni en la vista previa.
