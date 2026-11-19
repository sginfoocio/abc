# Rutas y cobertura de los 38 productos

## Alcance y fuentes

Se conserva el manifiesto histórico de 100 EAN y 14 competidores. El paquete recibido en
esta carpeta aporta 38 EAN, 38 rutas principales y 92 adicionales: 130 URLs únicas de 13
competidores. Los 38 EAN aparecen en la matriz 100×14; no se añade ni se retira ningún
producto o competidor.

La búsqueda informa de 36 fichas con contenido recuperado y dos referencias solo
indexadas. Dos rutas principales están en inglés. Veintisiete productos tienen una
referencia numérica que coincide con su EAN al normalizar UPC válidos de 12 dígitos con
cero inicial. Son datos de la investigación aportada, no resultados comerciales actuales:
parte procede de caché, no hay HTML original y la investigación no cubre exhaustivamente
los 532 pares.

## Incorporación al localizador

La causa reproducible era que el localizador solo cargaba los ejemplos y patrones ya
registrados en `price_url_candidates.py`; no leía `38-fichas-y-rutas.json`. Ahora carga
las rutas como candidatas documentales con EAN, competidor, modelo, color, URL, procedencia,
fecha, idioma, nivel de evidencia y notas. La carga no crea fichas confirmadas, lecturas,
ofertas, precios ni permisos.

El planificador exige el EAN asociado y vuelve a comprobar marca, modelo y color contra el
catálogo. Las rutas de otros EAN no se transfieren. Los UPC y EAN publicados permanecen
como referencias documentales; no se usan como GTIN de una lectura actual. Una ruta con
locale explícito no español queda bloqueada como candidata española. Las rutas de mercado
no acreditado siguen necesitando la validación de mercado, moneda e impuestos del lector
gobernado. Las rutas nuevas se consultan únicamente por `PriceLocator`, con permisos,
robots, límites, bloqueos y recibos existentes.

Cuando una ruta aportada coincide con una ficha ya registrada, se conserva la URL más
específica (incluido `selectedSize`) y se adjunta la nueva procedencia a la misma candidata,
evitando una segunda lectura de la misma página. Las rutas indexadas o agotadas no se
convierten por ese hecho en identidad confirmada, precio vigente o stock.

El caso Mia Burton SL 872 ERIN 001 conserva las dos referencias publicadas (`889652650838`
y `8056376664324`) como evidencia contradictoria pendiente. No se consultó la ficha actual,
por lo que no se decide cuál corresponde ni se presenta la identidad como resuelta.

## Matriz de los pares del alcance solicitado

[`MATRIZ_38_EAN_14_COMPETIDORES.csv`](./MATRIZ_38_EAN_14_COMPETIDORES.csv) contiene los
532 pares únicos (38×14), generados offline con
[`scripts/generate_38_coverage_matrix.py`](../../../scripts/generate_38_coverage_matrix.py).
Incluye las URLs documentadas y su procedencia por par, y separa los campos históricos de
los actuales. Hay rutas para 88 pares y ninguna ruta documentada para 444; «sin ruta en el
paquete» no significa que el producto no exista.

Todos los 532 pares tienen `current_read_status=no_ejecutada`,
`current_identity=no_verificada`, `current_comparable=no_verificada` y recibos actuales
vacíos. No hay importe, mercado, IVA o disponibilidad actuales en la matriz. Los datos
históricos se conservan en columnas independientes y no se reutilizaron ni rejuvenecieron.

| Resultado | Antes: snapshot histórico del 17/11 | Después de esta incorporación |
|---|---:|---:|
| EAN del catálogo de 100 con comparación vigente acreditada | 0/100 | No revalidado; 0 ofertas nuevas acreditadas |
| Productos de estos 38 con oferta comparable vigente acreditada | 0/38 | 0/38 en la evidencia disponible; estado vivo no comprobado |
| Competidores comparables por producto de estos 38 | 0 por producto | 0 acreditados; estado vivo no comprobado |
| Ofertas comparables por tienda | 0 en las 14 tiendas | 0 acreditadas en este trabajo |
| Ofertas comparables nuevas | — | 0 |
| Consultas actuales nuevas | — | 0 |
| Lecturas históricas reutilizadas | — | 0 |
| Pares con ruta documental | — | 88/532 |

El resumen histórico del catálogo completo registra 0/100 ofertas comparables, 3
identidades verificadas y 7 lecturas guardadas. En los 38 productos, la matriz histórica
detallada registra 0/532 ofertas comparables, 1 identidad verificada y 4 lecturas
guardadas. No se presentan esas lecturas como actuales.

Rutas documentales aportadas por competidor (no ofertas):

| Competidor | Rutas | Ofertas actuales comparables |
|---|---:|---:|
| Amevista | 25 | 0 |
| Blickers | 4 | 0 |
| ConGafasDeSol | 1 | 0 |
| Domanci | 1 | 0 |
| Edel-Optics | 10 | 0 |
| Mia Burton | 14 | 0 |
| Optical H | 5 | 0 |
| Otticait Eyewear | 23 | 0 |
| Óptica Bassol | 21 | 0 |
| Óptica Óptima | 3 | 0 |
| Óptica Universitaria | 1 | 0 |
| Visio Factory | 6 | 0 |
| Visual-Click | 16 | 0 |
| Céline Opticien Lunetier | 0 | 0 |
| **Total** | **130** | **0** |

## Impedimento operativo y límites

Se volvió a comprobar la aplicación compartida en `127.0.0.1:8602`; el equipo rechazó la
conexión. No se obtuvo acceso verificable al estado vivo de la continuidad, trabajador,
cola, presupuesto, permisos ni recibos. Por tanto, no fue posible previsualizar pares
elegibles, comprobar lecturas vigentes, lanzar consultas gobernadas ni validar sus
resultados. No se sustituyó ese flujo por HTTP, navegador, replay o caché.

La matriz documenta el impedimento para cada par. Las 130 rutas son hipótesis para el
lector, no pruebas de que una tienda tenga hoy la ficha o una oferta comparable. Las
consultas actuales, los recibos y la resolución de SL 872 quedan pendientes de que la
continuidad desplegada esté accesible y autorice cada par.

## Verificación offline

Las pruebas focalizadas cubren la carga de las 130 rutas, el alcance exacto por EAN,
separación de mercados y variantes, referencias UPC, nombres comerciales intercalados,
el caso SL 872, lectura sintética por el lector gobernado, revisión sin admitir una oferta
y no duplicación al repetir el trabajo. El smoke de Docker comprueba que la evidencia JSON
está en la imagen. Estas comprobaciones no sustituyen CI ni una lectura comercial actual.
