# Casos #133 y reintento diagnóstico de Bassol

## Alcance y fuentes

Se corrigió el análisis offline de las solicitudes 2, 3 y 4 del EAN
`0805289126577`, y se preparó un reintento explícito para la solicitud 1 de
Bassol. No se consultaron tiendas, no se reintentó Bassol, no se ejecutó
recuperación ni la vigilancia, y no se modificaron sus fechas, planificación o
presupuestos. El manifiesto de continuidad conserva sus 100 EAN y 14
competidores.

Fuentes verificadas:

- Informe histórico `continuidad_100ean_14competidores_20261115.json`,
  SHA-256 `ac6fc55fbae23efc787ee68385a22130fcdeb2af68a79e73bddd0c6d04d30524`.
- Paquete de entrada `evidencias_133_20261116.zip`, SHA-256
  `bb52a03e699c968bd4c032766b95194215c47f662c501ca22726eeddbe154106`.
- [Evidencia reproducible seleccionada](./Precios/100_EAN/evidencia_replay_133_20261116.zip),
  SHA-256 `e7e3ca3d137dac473095c2ec291318492f26a7cf939899bdc2550aa5406aba66`.
  Incluye el LEEME, los TSV y los tres HTML sanitizados; excluye las capturas
  de pantalla.
- [Matriz de los 1.400 pares](./PRICE_DIAGNOSTIC_REPLAY_1400.xlsx), SHA-256
  `2fd090b1d94c06ae5ecf3c71cbf3bfbcb30ad5ba6548b345a66323745a6bd0d1`.

Los HTML del paquete son reconstrucciones sanitizadas parciales de JSON-LD,
no respuestas completas originales. Los valores que siguen son evidencia de
esas capturas, no fichas verificadas ni ofertas actuales.

## Causas y correcciones

| Caso | Causa | Corrección y resultado |
|---|---|---|
| Optical H, solicitud 2 | La única `Offer` enlaza al `Product` mediante la URL base y un `variant` en la URL de oferta; antes no quedaba establecida esa relación. | Solo se vincula cuando la URL base, el GTIN del `Product`, la oferta única y sus identificadores son coherentes. Se conserva `variant_id` y la URL seleccionada en la evidencia y en la oferta registrada. El GTIN `805289126577` se normaliza a `0805289126577` como texto. La captura respalda RB2140 901, 109,85 EUR e `InStock`; no acredita vigencia actual ni ejecuta aceptación. |
| Visual-Click, solicitud 3 | El título contiene `901/32`; el SKU comienza por `0805289183051`, pero no publica un GTIN que vincule ese SKU al EAN objetivo. La oferta de variante no corresponde a una identidad acreditada para el par. | La normalización distingue `901/32` de `901`; no convierte prefijos numéricos de SKU en GTIN sin regla documentada. La ficha se rechaza por color distinto y sus 124 EUR y `InStock` no se registran como precio o stock del EAN. El motivo queda en el historial; la caché previa de precio/stock se invalida al rechazar la identidad. No se declara que no exista otra ficha correcta. |
| Mia Burton, solicitud 4 | Tres ofertas describen medidas distintas y comparten 174 EUR, pero la captura no publica EAN ni el código de color `901`; `black` y el sufijo de URL no resuelven esa identidad. | El extractor conserva el precio común como dato candidato solo si las ofertas concuerdan; las medidas distintas dejan disponibilidad `unknown`. Con la captura conservada la identidad queda `probable`, no se inventa EAN ni se atribuye stock individual. El precio no pasa a oferta comparable mientras falte acreditar marca, modelo y color contra el catálogo. |
| Bassol, solicitud 1 | La solicitud original se rechazó antes de HTTP por privacidad. El envío ordinario devuelve el mismo rechazo y no crea un intento nuevo. | Se añade `retry_of` para un reintento explícito, con un hijo auditable y el registro original intacto. Bajo los bloqueos compartidos se comprueban manifiesto, estado/aplazamientos, candidata documentada, recibos y reservas atribuibles al intento anterior y posteriores aplicables, privacidad, cuota, permisos, bloqueo y presupuesto. El trabajador vuelve a comprobar privacidad y controles de ruta/agente antes de HTTP. El texto `901-32-50` ya no se interpreta como calibre `32-50`; no se acepta el EAN por URL o por una captura. |

El reintento solo es elegible para el rechazo de privacidad acreditado cuyo
resultado y auditoría coinciden y no tiene peticiones ni reservas atribuibles
o inciertas. Consumo, permisos denegados, bloqueos, candidatas modificadas,
incertidumbre o un hijo ya existente impiden crear otro intento. El doble envío
devuelve el mismo hijo; ni el estado ni las fechas originales se reescriben.
La interfaz distingue `reintentable (sujeto a comprobación)`, `pendiente`,
`ejecutando`, `capturado`, `rechazado` y `caducado`, y expone responsable,
motivo, fecha y commit.

## Reevaluación offline del manifiesto

La matriz contiene 1.400 combinaciones únicas; coteja exactamente los 100 EAN,
los 14 competidores y el estado histórico del informe. No hay pares añadidos,
omitidos ni reprogramados.

- 275 pares contienen hechos de identidad históricos y 3 contienen una de las
  capturas sanitizadas parciales. Los 1.122 restantes no tienen muestra
  estructurada suficiente para volver a evaluar extracción; se conservan sin
  inferir elegibilidad.
- El replay cambia dos clasificaciones de identidad: Optical H pasa de
  `probable_match` a `exact_match`; Visual-Click pasa de `probable_match` a
  `rejected`. Mia Burton permanece `probable_match`, con precio candidato y
  stock desconocido. Esto no verifica una ficha, acepta una revisión ni crea
  una oferta comparable.
- En el resto de los hechos históricos no se reconstruyen relaciones
  `Product`/`Offer` ni vigencia. Otticait presenta identidad coincidente en el
  histórico, pero la URL conservada es un blog y no aporta una oferta con
  precio; no es una alternativa comparable. No se afirma que no exista otra
  fuente autorizada.
- El cierre indicado como par 529 / comprobación 2795 se mantiene excluido por
  la petición o reserva posterior. La base operativa no se abrió; esa
  exclusión no se revalidó ni se alteró.

La matriz preserva las 14 políticas de IVA incluido sin inferir porcentajes.
Mercado, moneda, identidad, disponibilidad y vigencia siguen siendo
condiciones independientes. Los pares sin evidencia nueva no se vuelven a
programar; tampoco se repiten los 182 cierres históricos.

## Procedimiento posterior a un despliegue autorizado

1. Verificar el commit desplegado y que SQLite, WAL/SHM, directorio y
   almacenamiento cumplan los permisos privados exigidos. Un fallo de esta
   comprobación debe ocurrir antes de cualquier HTTP.
2. En la interfaz, seleccionar la candidata documentada de Bassol para el EAN
   `0805289126577`, conservar la URL original, introducir responsable y motivo,
   confirmar y pulsar **Reintentar rechazo previo a HTTP**.
3. Confirmar el ID del hijo, `retry_of`, responsable, motivo, fecha y commit.
   El estado pendiente solo indica que el trabajador puede evaluarlo, no que
   vaya a superar la revalidación. No repetir un rechazo terminal ni un intento
   con consumo incierto.
4. Si se captura la página, revisar el artefacto parcial y sus recibos. Verificar
   la identidad publicada contra el EAN, modelo y color; `901-32` no equivale a
   `901`. Capturar no acepta identidad ni crea una oferta.
5. Reevaluar selectivamente las cuatro filas del EAN en la matriz. No ejecutar
   lote de recuperación, búsqueda masiva, aceptación de revisiones ni cambios
   de fechas; no tocar el par 529 / comprobación 2795 ni la vigilancia.
6. Solo con evidencia de identidad y controles comerciales independientes
   acreditados podrá un operador valorar la comparación. Esta PR no activa esa
   acción.

## Validación

Las regresiones usan los bytes conservados en el paquete seleccionado y
transporte falso sin red. Cubren GTIN con cero inicial y variante de Optical H,
rechazo de Visual-Click, precio común y stock desconocido de Mia Burton,
normalización del slug Bassol, rechazo previo a HTTP, reintento enlazado,
auditoría, concurrencia, reservas, permisos, presupuesto, bloqueos, robots y
revalidación del trabajador. En el checkout con manifiestos canónicos, la
batería dirigida de continuidad, referencias, web y reintentos pasó: **216
tests**. La comprobación flake8 selectiva y la compilación de los módulos
modificados también pasaron. El CI completo y el build/smoke Docker sobre el
HEAD final siguen pendientes.

El smoke de Docker carga el paquete parcial dentro del contenedor, comprueba la
identidad de las tres capturas, usa el usuario real UID 1037, verifica SQLite
y auxiliares privados y se ejecuta con red deshabilitada. Docker no está
instalado en el entorno local; la ejecución del build y smoke corresponde al
check de CI de esta PR. No se fusionó, desplegó, ejecutó recuperación, reintento
real ni cambio de vigilancia.
