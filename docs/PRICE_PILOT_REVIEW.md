# Revision offline de los 12 pendientes del piloto del 5 de noviembre de 2026

## Alcance y fuentes

Base: `51e16a1ddded2550ed9a61c3b49aa752f0743117` (main y version
desplegada durante el piloto). Se revisaron codigo, evidencia documental
local y los resultados existentes, sin HTTP comercial, ejecucion de colas
ni acceso nuevo a SQLite de produccion.

Los informes `piloto-67-resultados.csv` y `piloto-67-resultados.json`
contienen los mismos 67 pares, incluidos los 12 pendientes. Hashes SHA256:

- CSV: `7cf26df1da3aa69120f2c7845fc5b2673b51fc84b03e5954578448f3b846aa91`.
- JSON: `9e452529cb3c13e05bb19ccba5d9ae048e0597329fcf71f58b607d8bb8383db9`.
- Lista autorizada: `bacd331e87da9dbacf40074b7dd2f27e3162eca028b02efd6a256de412a4a1b6`.

Estos informes conservan URL de resultado y nota del trabajo, pero no
incluyen los HTML, `discovery_index.facts`, la evidencia completa de
`locate_events`, ni las filas URL/resultado de `access_requests`.
No equivalen a un registro de respuestas HTTP ni contienen canonical.
Las pruebas nuevas son **sinteticas**, no capturas de las tiendas.

## 1. Diez Tom Ford de Optima: categoria registrada como candidata

Los diez resultados guardados apuntan a
`https://optica-optima.com/gafas-de-sol/`; no tienen ficha verificada,
identidad confirmada ni oferta. Su nota conserva `sample_pattern:optima
[E002]` y marca/modelo/color desconocidos.

Con los EAN, modelos, colores, marca Tom Ford y categoria sol de esa regla,
el adaptador del commit desplegado reconstruye las siguientes candidatas.
**Reconstruccion determinista no significa peticion HTTP acreditada.**
Todas llevan el prefijo `https://optica-optima.com/gafas-de-sol/`.

| EAN (texto) | Candidata reconstruida: ultimo componente |
|---|---|
| 0889214602701 | tom-ford-ft1044-01a.html |
| 0889214519788 | tom-ford-ft1044-01v.html |
| 0889214402547 | tom-ford-ft1044-52s.html |
| 0889214684080 | tom-ford-ft1044-93b.html |
| 0889214558626 | tom-ford-ft1211-52n.html |
| 0889214635310 | tom-ford-ft1355-28p.html |
| 0889214669742 | tom-ford-ft1360-30a.html |
| 0889214642387 | tom-ford-ft1362-01v.html |
| 0889214643322 | tom-ford-ft1364-01a.html |
| 0889214686305 | tom-ford-ft1396-32c.html |

El recorrido de codigo es:

1. `candidate_plan` genera la URL completa, no la categoria. La registra
   en `locate_events(method='local_candidates')` y en
   `discovery_index.candidate_evidence`.
2. `_facts` puede reutilizar `facts` cacheados o solicitar `row["url"]`.
   En una consulta nueva, `WebFetcher.get` sigue unicamente redirecciones
   HTTP admitidas por sus controles y devuelve la URL final en `Fetched.url`.
3. `_facts` extrae usando esa URL; `_match` utiliza `page.url`, no
   necesariamente la candidata original, al guardar el resultado.

El codigo explica **como puede cambiar** la URL registrada, pero los
CSV/JSON no acreditan que los diez casos siguieran una redireccion, su
estado HTTP, sus saltos ni si los hechos procedian de cache. Tampoco
permiten certificar cual se solicito efectivamente. Para resolver eso
faltan las filas ya persistidas de `access_requests` y `discovery_index`,
los eventos completos y, para canonical/contenido, la respuesta original
o su evidencia estructurada. No se han inventado ni solicitado ahora.

### Defecto demostrado y correccion

Antes de corregir, un HTML sintetico con solo `<title>Gafas de sol</title>`
devolvia `ProductPage`. Una categoria podia ademas heredar GTIN, moneda y
precio de metadatos/selector de una tarjeta. Una `CollectionPage` con un
`Product` en el grafo, o un `ItemList` con metadatos globales, tambien podia
acabar como ficha. Esto **no prueba que esos HTML fueran los de Cloud**.

Ahora se rechazan antes de extraer identidad/precios:

- Las rutas exactas de categoria de Optima `/gafas-de-sol` y
  `/gafas-graduadas`, con o sin barra final/query.
- `CollectionPage` de la URL consultada (o sin URL declarada).
- `ItemList` sin un `Product` vinculado a la URL completa consultada.

Una ficha explicitamente vinculada a su URL puede mantener su lista de
productos relacionados. Esta es una barrera basada en senales positivas,
no una afirmacion de detectar todos los listados posibles.
Una categoria historica cacheada se ignora al buscar nuevos candidatos,
sin reescribir ni reclasificar esa fila. Los registros historicos
`probable` del piloto no se corrigen ni aceptan automaticamente.

## 2. Optima 0889214042422: FT5634B / 001

URL de resultado:
`https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html`.
La nota registra modelos `FT5634, FT5634B`, colores `001, B53001` y EAN
identico. El calibre publicado y el interno quedan desconocidos.

La procedencia que permite afirmar el codigo, sin atribuir fuentes que
no estan en el informe:

- `classify` extrae modelos/colores del titulo y de cada SKU/MPN mediante
  `parse_reference`; agrega tambien `model` y `color` explicitos.
- `FT5634B` puede proceder de una referencia completa reconocida o del
  campo `model` explicito; `001`, de una referencia o `color` explicito.
- `FT5634` / `B53001` se reproducen con
  `parse_reference("Tom Ford FT5634-B53001", models=["FT5634B"])`:
  no reconoce el modelo completo, y el fallback separa el ultimo token
  como color. Ese resultado no contiene calibre.
- El paquete documental atribuye **ese titulo concatenado a Otticait
  (E047)**. Para Optima, E003 acredita el alias/slug `FT5634-B`, no ese
  titulo ni un MPN actual.

La mezcla modelo/calibre/color es, por tanto, un riesgo de parseo
reproducible para ese texto: `B53001` puede ser tratado como color sin
descomponerlo. **No se ha demostrado que ese fuera el titulo/MPN de
Optima**, ni que `53` sea el calibre de este EAN. Para atribuir cada senal
real hacen falta `facts` con titulo, model, color, identifiers y las
fuentes estructuradas/HTML originales.

No se cambia ese parser ni se anade una regla universal que separe `B`,
`53` y `001`. La regresion mantiene la revision aunque el GTIN coincida;
un color explicito contradictorio tampoco se acepta. El alias de URL
documentado no es una autorizacion para corregir identidad publicada.

## 3. Domanci 8056597684446: MU 11WS / 1AB5S0

URL de resultado:
`https://domanci.com/es/gafas-de-sol/miu-miu-0mu-11ws-mu-11ws-1ab5s0-54.html`.
Metodo guardado: `catalog`; origen: `/es/marca/miu-miu`.

La nota acredita que el comparador encontro EAN normalizado identico y
calibre informativo `54-21`, pero no una identidad exacta. La condicion
que produjo **ese texto concreto** es el retorno temprano de `classify`:
`candidate["ambiguous"]` verdadero **o** alguna referencia del titulo/
identificadores con `Reference.ambiguous=True`. Este veto ocurre antes de
aceptar el EAN. El calibre no es el motivo de rechazo.

Los informes no contienen cual de esas dos ramas se activo. La primera
puede proceder de variantes no vinculadas univocamente, varias ofertas o
GTIN publicados contradictorios; la segunda de varias interpretaciones
modelo/color en titulo/SKU/MPN. No hay evidencia local suficiente para
elegir una causa concreta. Ni el slug repetido, ni un EAN coincidente, ni
la existencia de otra Miu Miu verificada justifican aceptarla.

Se demuestra una carencia diagnostica: `_match` descartaba `page.notes`
al componer la nota persistida. Una pagina sintetica con dos precios
dejaba solo la identificacion ambigua generica. Ahora conserva tambien
el motivo del extractor. Si el parser encuentra varias referencias,
la razon indica titulo o numero de identificador y texto original.
No se modifica el veto, ni se afirma que Domanci tuviera dos precios.
Para concluir la causa historica faltan el `ProductPage` persistido y,
si no conserva las senales, los nodos/HTML originales.

## Trazabilidad nueva, compatibilidad e historicos

- `Fetched.trace` conserva URL solicitada, final y saltos HTTP reales
  (`url`, `status`, `location`), sin consultar canonical.
- `ProductPage.url_trace` conserva ademas candidata y canonical declarados,
  sin reemplazar la URL/variante activa por canonical ni seguirlo.
- Las comprobaciones rechazadas como `no_product` tambien conservan el
  recorrido en `discovery_index.facts`, con motivo explicito.
- Las fichas conservan el recorrido en facts, muestras del monitor y
  evidencia de oferta mediante el flujo existente.
- No hay cambio de esquema SQLite, migracion, permisos ni estados nuevos.
  Los campos son aditivos con defaults para evidencia anterior. Una
  evidencia antigua sin recorrido no se completa por suposicion.
- No se actualizan los 12 historicos ni las 29 ausencias del piloto.
  Si se revierte a codigo anterior, evaluar primero la compatibilidad con
  nuevos campos JSON; no borrar evidencia para facilitar un rollback.

## Validacion

En la base, las regresiones iniciales produjeron **10 fallos y 2 pases**:
categorias/listados, falta de recorrido URL y motivos de ambiguedad
perdidos. Los escenarios FT contradictorios y una ficha con relacionados
no justifican relajar identidad. Las pruebas adicionales cubren cache
historica sin escrituras/HTTP y trazabilidad persistida en monitor/oferta.

Comando minimo:

```text
python -m pytest tests/test_price_pilot_review_evidence.py -q
```

La PR debe enlazar el resultado focalizado y la CI del commit final,
incluido Docker. No se fusiona ni despliega como parte de esta revision.

Resultado local: **443 passed** en 13 modulos focalizados (incluidas las
14 regresiones nuevas), con comprobaciones de identidad/variantes,
localizador, monitor, autorizacion, robots y confirmacion automatica.
Lint focalizado `E9,F63,F7,F82,F401,F841` sin errores.
