# Candidatas documentales del localizador (04/11/2026)

## Evidencia y base

Base investigada y `origin/main` al comenzar:
`92243628b4e925ba9d549b076580726c383a43ac`; no hay commits posteriores.
Esta rama parte de main y no incluye la auditoria aparcada de las 51 confirmaciones.

Se leyeron los siete documentos de `Precios/Diagonal_evidencia_Copilot`:
`Especificacion_Copilot.json`, `Matriz_14.csv`, `Evidencias_47.csv`,
`Productos_100.csv`, `Enriquecimiento_referencias.csv`,
`Cobertura_marca_categoria.csv` e `Informe_y_Copilot.txt`.
Se cotejaron 47 ejemplos, 100 productos, 100 enriquecimientos y 280 grupos
marca/categoria contra el JSON. La muestra representa 29 grupos documentados,
dos solo historicos y 249 sin muestra; esto no demuestra ausencia comercial.
Solo un GTIN tiene medidas asociadas explicitamente en la investigacion;
otras 11 referencias tienen medidas observadas por modelo/color, no por GTIN.

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

## Matriz de los 14 competidores

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
| Visio Factory | Parcial | Subregla Prada PR27ZS; mappings E041-E044 | 3 | 2 | Nuevas rutas Prada requieren calibre y puente. Cartier tiene handle propio. Los dos RB2140 901 conservan UPC distintos y E043 pendiente, sin asignar talla; 901/58 mantiene su identidad. |
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
