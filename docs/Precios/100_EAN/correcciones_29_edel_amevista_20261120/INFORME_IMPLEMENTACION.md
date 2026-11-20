# Correcciones offline de referencias y estado de los 29 EAN

## Alcance y fecha de corte

El trabajo parte del commit desplegado `939dfc6f9a81f1575a040f93d0b5b848dbb5452a` y modifica la rama `fix/price-pending-29-identities`. El contenedor lector `abcd-control` reportó la imagen `ghcr.io/sginfoocio/abc@sha256:9ac62ae3b2ecfbf40e45cf67f1de01ee9cd296a6b3d738ba53241f78755ff3ab`, revisión `939dfc6f9a81f1575a040f93d0b5b848dbb5452a` y estado `healthy`. La instantánea de Cloud se tomó en modo de solo lectura el **2026-11-20 16:50:32 UTC**; no se consultaron tiendas, no se aceptaron fichas, no se crearon solicitudes y no se cambiaron ofertas.

La continuidad persistida seguía activa: **100 EAN × 14 competidores = 1.400 pares únicos**. El heartbeat observado fue `2026-11-20T17:50:18+01:00` y el siguiente ciclo estaba previsto para `2026-11-20T23:27:06+01:00`. La consulta de comparación encontró **71/100 EAN comparados**, 29 sin comparación, y **124 ofertas guardadas vigentes**. Las 124 ofertas que el comparador consideró vigentes eran ES, EUR y disponibles. Es una lectura de estado almacenado, no una nueva validación directa con los comercios.

Los dos CSV de este directorio son matrices estáticas de esa instantánea. La matriz 100 × 14 registra URL aprobada auditada, URLs exactas verificadas, rutas documentales, lectura guardada, vigencia, solicitud actual, impedimento y próxima acción. La matriz de rutas conserva las 102 candidatas históricas y sus niveles/notas, pero advierte por fila que el estado de una oferta del par no demuestra que esa URL concreta sea la fuente de la oferta.

## Estado actual de solicitudes y aprobaciones

La base contenía 298 solicitudes de lectura: 23 `done`, 219 `error`, 16 `pending`, 23 `blocked` y 17 `deferred`. Al reducir a la solicitud más reciente por par, la matriz contiene 204 pares con solicitud: 23 `done`, 125 `error`, 16 `pending`, 23 `blocked` y 17 `deferred`. Los estados `blocked`, `deferred` y `error` no se convierten en reintentos automáticos; se conserva el motivo y, cuando existe, el próximo intento.

Solo **2 pares** tenían una auditoría `candidate_confirmed` en `product_review` que coincidía con una URL exacta verificada. Otros 455 pares tenían una URL exacta verificada sin ese evento de aprobación: la matriz los distingue y no los declara aprobados. Los 943 restantes no tenían ninguno de esos dos indicios.

Las referencias históricas 297 y 298 se comprobaron de nuevo:

| Solicitud | EAN / competidor | Estado actual | Motivo / próximo paso |
|---|---|---|---|
| 297 | 0805289126577 / Óptica Bassol | `deferred`, 0 HTTP | Presupuesto diario agotado; respetar `retry_at=2026-11-20T22:44:25.810Z`. |
| 298 | 0805289126591 / Mia Burton | `error`, 1 HTTP | Identificación ambigua; mantener revisión, no repetir automáticamente la consulta. |

No se ejecutó recuperación, reintento ni consulta comercial para ninguno.

## Cambios de código de esta rama

- `price_references.py`: añade asociaciones acotadas para Bronson/FT1044 y Joey-02/FT1301, alias explícitos de Tiffany, y reconoce `Logotipo Gg` solo con marca, modelo/color y grupo concordantes.
- `price_web_extract.py`: conserva el identificador publicado original además del GTIN normalizado y expone la procedencia de la asociación de Offer.
- `price_monitor.py`: registra la condición concreta de rechazo de Edel. Permite Offer sin URL propia solo cuando el Product seleccionado está vinculado por radio marcado, grupo/Product y GTIN exactos. No elimina las comprobaciones de identidad ni toma el calibre como sustituto de la variante.
- Las regresiones cubren códigos comerciales, marca, color, UPC de una captura parcial de Amevista y Offer de Edel anidada en el Product seleccionado. La captura Amevista es de GG1660S, **no** de los productos Gucci pendientes.

Estos cambios están solo en la rama; aún no están integrados ni desplegados. La prueba sintética de Edel demuestra el vínculo estructural, pero no sustituye el HTML original de las 69 solicitudes históricas. Tampoco se dispone de HTML original de Amevista para los tres modelos destacados. Las rutas aportadas siguen siendo candidatas, no ofertas verificadas.

## Recuento de rutas aportadas

El CSV fuente contiene **102 URLs**, para **29 EAN**, **72 pares producto–competidor** y **12 tiendas**. Las rutas de idiomas/mercados alternativos no se cuentan como ofertas españolas. Los errores y niveles de evidencia proceden del paquete local aportado; los motivos históricos no sustituyen al estado actual guardado en Cloud.

Para obtener comparabilidad actual se debe reutilizar una oferta vigente compatible o el trabajo ya pendiente. Una ruta sin aprobación auditada requiere evidencia de identidad y URL suficiente, seguida de aprobación explícita. Una lectura caducada o ausente se debe cursar por el trabajador gobernado existente. Una solicitud bloqueada, aplazada o con resultado incierto conserva su estado, recibos y control: no se duplica HTTP por cambiarle la fecha.

## Validación y estado de entrega

Validación local de la rama:

- `tests/test_price_references.py` y `tests/test_price_web_competitor_pages.py`: **58 passed**.
- `tests/test_price_monitor.py` sin su prueba independiente de estrés de 1.000 trabajos: **77 passed, 1 deselected**.
- `py_compile` de los módulos, pruebas y smoke modificados: correcto.
- `git diff --check`: correcto.
- Ambas matrices se volvieron a leer como CSV y se verificaron sus claves únicas y cardinalidades: **1.400/1.400** pares y **102/102** URLs; 29 EAN, 72 pares y 12 tiendas en las rutas.

El smoke offline del contenedor ahora comprueba además 124 ofertas almacenadas ES/EUR/disponibles en 71 EAN y las cardinalidades de las dos matrices. Conserva su recuperación sintética auditada e idempotente. La imagen y el smoke Docker aún deben ejecutarse en CI sobre el commit publicado de la PR.

No se ha hecho merge ni despliegue. La promoción y nuevas consultas comerciales quedan fuera del alcance de esta entrega.
