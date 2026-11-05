# Revision offline de los 12 pendientes del piloto del 5 de noviembre de 2026

## Alcance y fuentes

Base desplegada durante el piloto:
`51e16a1ddded2550ed9a61c3b49aa752f0743117`. Esta ampliacion de #116
sustituye el diagnostico preliminar que solo disponia del resumen del piloto.
No se ejecutan consultas comerciales, trabajos ni modificaciones en Cloud.

El usuario aporta `Diagnostico_12.json` e `Informe_12_pendientes.txt` en
`Precios/analisis_exportacion_12`. Se ha contrastado el JSON de diagnostico,
SHA256 `d216c68eade17710d3ba0c787be0b106fbb1124c7ff6cd45d43f8287a64fc07d`.
Identifica 12 pares distintos, trabajos, eventos, peticiones, textos originales,
normalizaciones y estado del indice. Es un **diagnostico estructurado derivado**,
no una respuesta HTML ni la exportacion SQL bruta completa.

Ese paquete identifica como origen `evidencia_12_pares_20261105T093842Z.json`,
exportado el `2026-11-05T09:38:42.511559+00:00`, con SHA256 declarado
`300fd171be1f4d234e50adfb669c2ac726199e7bc2145cd3d31c62fa6128712d`.
El archivo bruto no esta en la carpeta aportada: no se afirma haber verificado
independientemente su hash ni campos omitidos del diagnostico. La evidencia
minima utilizada queda versionada en
[pilot_review_12_structured.json](../tests/fixtures/pilot_review_12_structured.json).
Conserva textos, ceros iniciales, IDs y procedencia, sin reconstruir HTML.

Los informes anteriores contienen los mismos 67 pares autorizados:

- CSV: `7cf26df1da3aa69120f2c7845fc5b2673b51fc84b03e5954578448f3b846aa91`.
- JSON: `9e452529cb3c13e05bb19ccba5d9ae048e0597329fcf71f58b607d8bb8383db9`.
- Lista autorizada: `bacd331e87da9dbacf40074b7dd2f27e3162eca028b02efd6a256de412a4a1b6`.

El nuevo diagnostico recoge **23 registros HTTP: diez 301 y trece 200**.
Uno de los 200 es robots (peticion 59), no una ficha. Para los doce pares,
`checked_at` coincide con `updated_at` del trabajo y el evento final del
piloto, segun el informe aportado. Indice/trabajos son snapshots mutables;
esa coincidencia es consistente, no una garantia de inmutabilidad.
`access_requests` carece de `job_id/run_id`: la correlacion por competidor,
EAN, proposito e intervalo no prueba atribucion unica ante concurrencia.
No hay HTML, encabezados `Location` ni canonical historicos.

## 1. Diez Tom Ford de Optima: categoria guardada como product

Los eventos `local_candidates` contienen las URL completas, no la categoria.
Las peticiones registran cada candidata con **HTTP 301**, seguida de la
categoria `https://optica-optima.com/gafas-de-sol/` con **HTTP 200**.
Los facts conservan esa categoria, titulo
`Gafas de Sol Online en tu Optica de Confianza` (con acento en el original),
marca/modelo/color/EAN vacios, `ambiguous=false`, `check_state=product`.
Los diez precios son **null**. El clasificador dejo las identidades sin
confirmar y los trabajos en revision; no hubo oferta aceptada en esos facts.

En las primeras diez filas de esta tabla:

- El prefijo de candidata es `https://optica-optima.com/gafas-de-sol/`.
- El primer ID HTTP corresponde a esa candidata (301); el segundo a la
  categoria (200). El primer evento es generacion y el segundo resultado.
- La causa demostrada es categoria clasificada como `product`, sin identidad.
- Faltan `Location`, canonical y HTML; no se conoce por que la tienda
  respondio asi ni la ubicacion de una ficha correcta.
- Accion: mantener revision; ignorar categoria cacheada como ficha sin
  reescribirla. No reintentar ni sustituir automaticamente la candidata.

| EAN (texto) | Candidata: ultimo componente | Trabajo | Eventos | HTTP | Resultado / accion |
|---|---|---|---|---|---|
| 0889214602701 | tom-ford-ft1044-01a.html | 120 | 7, 8 | 60, 61 | Categoria sin precio; mantener revision |
| 0889214519788 | tom-ford-ft1044-01v.html | 134 | 9, 10 | 62, 63 | Categoria sin precio; mantener revision |
| 0889214402547 | tom-ford-ft1044-52s.html | 148 | 11, 12 | 64, 65 | Categoria sin precio; mantener revision |
| 0889214684080 | tom-ford-ft1044-93b.html | 162 | 13, 14 | 66, 67 | Categoria sin precio; mantener revision |
| 0889214558626 | tom-ford-ft1211-52n.html | 176 | 15, 16 | 68, 69 | Categoria sin precio; mantener revision |
| 0889214635310 | tom-ford-ft1355-28p.html | 218 | 17, 18 | 70, 71 | Categoria sin precio; mantener revision |
| 0889214669742 | tom-ford-ft1360-30a.html | 232 | 19, 20 | 72, 73 | Categoria sin precio; mantener revision |
| 0889214642387 | tom-ford-ft1362-01v.html | 246 | 21, 22 | 74, 75 | Categoria sin precio; mantener revision |
| 0889214643322 | tom-ford-ft1364-01a.html | 260 | 23, 24 | 76, 77 | Categoria sin precio; mantener revision |
| 0889214686305 | tom-ford-ft1396-32c.html | 274 | 25, 26 | 78, 79 | Categoria sin precio; mantener revision |
| 0889214042422 | /gafas-graduadas/tom-ford-ft5634-b-001.html (Optima) | 288 | 27, 28 | 80 (200) | Falso conflicto titulo/identificador; correccion offline, no aceptar revision |
| 8056597684446 | /es/gafas-de-sol/miu-miu-0mu-11ws-mu-11ws-1ab5s0-54.html (domanci.com) | 425 | 43 | 140 (200) | Falsa ambiguedad del titulo repetido; correccion offline, no aceptar revision |

El flujo desplegado genera la URL completa, solicita `row["url"]`, sigue
redirecciones admitidas por sus controles y extrae con `Fetched.url`.
`_match` usa `page.url`, lo que explica como la URL de resultado puede ser
distinta de la candidata. La secuencia 301/200 y los facts son consistentes
con ese flujo, pero **sin Location no se certifica el encabezado de destino
ni cada salto**. Tampoco se atribuye canonical.

Hay peticiones nuevas por cada par y `checked_at` contemporaneo al piloto:
no fue una ejecucion exclusivamente cacheada sin HTTP. No se puede descartar
alguna reutilizacion intermedia ni reconstruir todo el ciclo de cache con
este snapshot. La prueba offline demuestra que #116 ignora esos facts de
categoria al leer cache, sin HTTP ni cambios historicos.

### Proteccion y limites

Se mantiene el rechazo previo a identidad/precios de las rutas exactas
Optima `/gafas-de-sol` y `/gafas-graduadas`, con o sin barra/query.
Tambien se rechazan `CollectionPage` de la URL activa (o sin URL) e
`ItemList` sin `Product` vinculado a la URL completa consultada. Una ficha
con relacionados sigue admitida si su producto esta vinculado a su URL.

Los HTML **sinteticos** demuestran ademas el riesgo de heredar precio/GTIN
de tarjetas si una categoria no se bloquea. **Estos diez facts reales no
contienen precio y no acreditan mezcla historica de precios de tarjetas**.
Una URL plausible/documentada de Tom Ford no queda validada comercialmente
por generar el slug; no se declara producto inexistente ni se cambia el
alcance/autorizacion del adaptador por estas respuestas.

## 2. Optima 0889214042422: FT5634B / 001

La candidata, peticion HTTP 200 y URL de facts coinciden:
`https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html`.

| Fuente persistida | Interpretacion anterior | Interpretacion corregida |
|---|---|---|
| Titulo `Tom Ford FT5634-B 001` | FT5634B / 001 | Sin cambios |
| Identificador 1 `FT5634-B@53001#` | FT5634 / B@53001# (clave B53001) | FT5634B / 001 |
| Identificador 2 `GRDOOP` | Sin referencia modelo/color relevante | Sin cambios, no se elimina |
| `ean_published=0889214042422` | Identico al catalogo | Sin cambios; no supera contradicciones |
| model/color/caliber explicitos vacios; `ambiguous=false` | Sin senales adicionales | Sin cambios |

El primer identificador provocaba los conjuntos falsamente contradictorios
`{FT5634,FT5634B}` y `{001,B53001}`. No se traslada a Optima el titulo
compacto del ejemplo Otticait: ahora hay un texto original de Optima distinto.

La regla esta anclada al identificador completo **`FT5634-B@53ddd#`**,
con exactamente tres digitos de color conservados como texto. No se
generaliza a otros modelos, prefijos numericos o referencias sin marcadores.
`53` se mantiene opaco: **no se deduce calibre 53**, ni puente.
La regla interpreta el identificador, no lo ignora. Otro color/modelo,
un identificador adicional contradictorio, GTIN diferente o pagina ambigua
continuan impidiendo `exact_match`. El texto Otticait `FT5634-B53001`
permanece pendiente, sin esta excepcion.

El precio historico era 137 EUR y `market` estaba vacio. Son datos
persistidos, no oferta comercial validada por este analisis. Faltan HTML
y evidencia comercial adicional para revalidar precio, stock, IVA y mercado.
La correccion offline no acepta la revision existente.

## 3. Domanci 8056597684446: MU 11WS / 1AB5S0

La URL de candidata/facts coincide con la peticion 140 HTTP 200.
El origen documentado del trabajo sigue siendo `catalog`, no busqueda
interna.

- Titulo original: `Miu Miu MU 11WS MU 11WS 1AB5S0 54`.
- Identificadores originales: `0MU_11WS1AB5S05421`, repetido.
- EAN publicado identico; calibre explicito `54-21`; **pagina ambiguous=false**.

La ambiguedad historica se reproduce en **el titulo**, no en el indicador
de pagina: la primera aparicion de MU 11WS produce una referencia sin color
porque la siguiente palabra es MU; la segunda produce MU 11WS / 1AB5S0.
El parser consideraba esas dos referencias distintas. Los identificadores
compactos no eran ambiguos; `caliber_from_code` reconoce 54-21 y evita
tratarlos como otra referencia ordinaria contradictoria. No se atribuye
este veto a variantes/ofertas o a un calibre distinto.

Ahora una aparicion de modelo seguida inmediatamente por el mismo modelo,
en la misma segmentacion, deja que la segunda aporte la referencia.
No se descartan referencias completas con colores diferentes. Un modelo
adyacente distinto de la misma familia queda ambiguo en vez de ignorarse;
se conserva OPT y los vetos de campos explicitos, GTIN y pagina ambigua.
Esto corrige ademas el negativo reproducido `MU 11WS MU 12WS ...`, antes
capaz de ocultar el segundo modelo detras del GTIN.

El precio historico era 227,50 EUR; no es una oferta validada por este
analisis. Sin HTML no se audita otra vez la vinculacion de ofertas/variantes,
stock, IVA o mercado. Se mantiene la revision historica pendiente.

## Trazabilidad, compatibilidad e historicos

- `Fetched.trace`: solicitada, final y saltos HTTP reales
  (`url`, `status`, `location`).
- `ProductPage.url_trace`: tambien candidata y canonical declarados,
  sin seguir canonical ni reemplazar la variante/query activa.
- Rechazos `no_product`: conservan recorrido y motivo en facts.
- Fichas: recorrido en facts, muestras del monitor y evidencia de oferta.
- `_match` conserva notas del extractor; ambiguedad del parser identifica
  titulo/numero de identificador y texto original.
- Campos JSON aditivos con defaults: **sin migracion SQLite ni permisos,
  estados o controles nuevos**. No completar trazas antiguas por suposicion.
- Sin cambios a los 12 historicos, 29 pares no localizados, manifiestos,
  ofertas, permisos, bloqueos, Otticait, Vytria, colas ni recurrencia.

## Validacion

Antes de modificar el parser se ejecutaron los casos con los textos reales:
FT permanecia `probable_match`, el titulo MU daba `ambiguous=true`.
La nueva bateria produjo **3 fallos y 44 pases**: esos dos positivos y el
negativo sintetico de segundo modelo MU 12WS aceptado indebidamente.

Las pruebas distinguen:

- HTML, transporte y contradicciones construidas **sinteticos**.
- Proyecciones de **facts estructurados aportados**, no HTML capturado:
  los dos casos de identidad y las diez categorias cacheadas sin precio.
- Controles de no HTTP, no escritura historica, URL trace, deduplicacion,
  GTIN/modelo/color/OPT contradictorios y permisos existentes.

Comando minimo:

```text
python -m pytest tests/test_price_pilot_review_evidence.py -q
```

La PR debe enlazar pruebas y CI del commit final, incluido Docker.
No se fusiona ni despliega como parte de esta revision.

Resultado local del modulo ampliado: **49 passed**, incluido el bloqueo de
HTTP real y los diez casos de cache historica sin escrituras. Lint focalizado
`E9,F63,F7,F82,F401,F841`, sintaxis Pylance y `git diff --check` correctos.
Se cotejaron las doce proyecciones de la fixture contra el diagnostico
aportado (textos, URLs, trabajos, eventos y peticiones).
El resultado de la bateria ampliada y CI/Docker del SHA definitivo se
publica en #116, sin atribuir al commit nuevo las comprobaciones del anterior.
