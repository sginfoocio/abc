# Informe de recuperación de diez EAN — PR #145

## Alcance y corte de evidencia

Este informe conserva la distinción entre rutas documentadas, capturas históricas y
ofertas actualmente verificadas. La última evidencia local disponible es la
instantánea de solicitudes y pares del **2026-11-20 16:50:32 UTC**, con metadatos de
catálogo leídos a las **17:05:29 UTC**. No es una lectura viva del 21/11. El informe
local de esa fecha registró 71/100 EAN comparados; el dato más reciente de 90/100
aportado por el usuario no se ha podido comprobar desde este entorno.

Fuentes del corte: [matriz de estado 100 × 14](./correcciones_29_edel_amevista_20261120/MATRIZ_100x14_ESTADO_CLOUD_20261120.csv),
[exportación de solicitudes](./correcciones_29_edel_amevista_20261120/solicitudes_exportadas.json)
y [matriz de rutas](./correcciones_29_edel_amevista_20261120/MATRIZ_RUTAS_29_EDEL_AMEVISTA_20261120.csv).

La instantánea conserva los 100 EAN, 14 competidores y 1.400 pares. El corte local
del 20/11 indicaba que la vigilancia no estaba pausada. No se consultaron tiendas,
no se aplicó una recuperación, no se creó una solicitud y no se envió HTTP en esta
tarea. Los IDs y recibos siguientes son evidencia histórica y deben revalidarse
antes de aplicar una acción.

| EAN y variante | Rutas y procedencia conservadas | Solicitud/aprobación y último estado local | Precio, disponibilidad y comparación | Acción pendiente |
|---|---|---|---|---|
| `0843023181451` Cartier CT0550S 001 | Visio Factory: contenido recuperado; Mia Burton: ruta indexada. La ficha Visio guardada se marcó `available` el 16/11, pero venció el 17/11. | Mia Burton #28: `pending`, 0 HTTP en el corte. La URL exacta constaba verificada, pero no había evento `candidate_confirmed`; la aprobación original debe comprobarse. | Sin precio vigente ni oferta comparable en el corte. `available` de la ficha histórica no acredita precio o stock actual. | Previsualizar #28 y su auditoría/recibo. Reutilizarla si sigue pendiente y autorizada; no crear otra solicitud. |
| `8056597886932` Miu Miu MU 52YS 7OE01T | Visio Factory y Óptica Óptima: contenido leído el 16/11; Amevista y otras rutas: candidatas documentales/indexadas. | Óptica Bassol #218: `error`, 9 HTTP, identificación ambigua. Visual-Click #219: `blocked`, 15 HTTP, límite de intentos. Otras URLs exactas no equivalen a una aprobación auditada. | Lecturas históricas de Visio/Óptica Óptima vencieron el 17/11; no hay comparación vigente en el corte. | Previsualizar IDs y recibos; no repetir HTTP para #219 ni reintentar #218 sin resolver identidad y autorización. |
| `8056262407691` Miu Miu MU A06S 14L09Z | Otticait: contenido recuperado; Visio y Óptica Óptima: lecturas históricas; Optical H: ficha exacta por ruta. | Optical H #179 y #294: ambas `error`, 1 HTTP cada una, con `Distinto modelo: catálogo MUA06S, ficha GAFASDESOLMIUMIUA06S`. Las URLs constaban verificadas, pero sin evento de aprobación auditada. | Las lecturas antiguas marcadas `available` vencieron el 17/11; no hay precio comparable vigente en el corte. | Previsualizar ambas solicitudes como un solo par; reusar la normalización acotada solo si marca, MU A06S y 14L09Z se confirman. No duplicar HTTP ni fabricar aprobación. |
| `8056597875349` Prada PR 27ZS 16K08Z | Edel-Optics: contenido recuperado; Visio Factory: ruta indexada; también constan lecturas guardadas en otras rutas. | Hay trabajos históricos completados, pero la exportación no identifica una solicitud actual recuperable ni una aprobación `candidate_confirmed` para esas URLs. | Las lecturas etiquetadas `available` del 13/11 y 16/11 vencieron antes del corte; no hay precio comparable vigente. | Previsualizar trabajos, aprobación, URL y recibos actuales. Reusar solo una solicitud/aprobación que siga válida; solicitar lectura actual únicamente si la vista previa la declara elegible. |
| `0889214625984` Tom Ford FT1310 01A | Domanci: muestra guardada en el corte; Edel-Optics y Mia Burton: rutas exactas/documentadas. | Edel #17: `error`, 1 HTTP, variante extraída no correspondiente a la URL. Mia Burton #290: `error`, 1 HTTP, color no confirmado. | El usuario identificó la muestra Edel #705 como 185,60 € con disponibilidad `unknown`. Domanci figuraba `available` en una muestra guardada del 19/11, vigente solo hasta el 20/11 19:54 UTC; el paquete local no conserva el importe de esa fila. Ninguna de esas muestras acredita disponibilidad o precio actual. | Reproducir offline la muestra exacta #705 si se obtiene su evidencia original. Mantener `unknown` sin prueba de disponibilidad; no repetir HTTP por el error #17. |
| `0889214042422` Tom Ford FT5634B 001 | Óptica Óptima: lectura gobernada; Domanci y otras rutas: muestras/candidatas históricas. | Óptica Óptima #49: `done`, 3 HTTP, 2 intentos; oferta guardada de 186,00 € `available`, observada el 20/11 16:09 UTC y vigente hasta el 21/11 16:09 UTC en ese corte. Visual-Click #260 está `blocked` (7 HTTP; límite de intentos). | El usuario identificó la muestra #777 como 186,00 € con `availability=unknown`. La lectura #49 posterior del corte la registró `available`, pero no se pudo inspeccionar aquí el vínculo entre ambas capturas; venció el 21/11 16:09 UTC. No es una oferta actual verificada. | Reconciliar offline la muestra #777 con el recibo de #49; no convertir `unknown` por defecto. Solo una nueva lectura gobernada si la vista previa actual la requiere y la autoriza. |
| `0889214599834` Tom Ford FT6082B 051 | Edel-Optics, Optical H y Otticait: rutas exactas/indexadas; Optical H también conserva rutas de distintos contextos lingüísticos. | Edel #53: `error`, 1 HTTP, variante extraída no correspondiente a la URL. El trabajo Otticait estaba `pending` por desacuerdo entre mercado y configuración. | El usuario identificó la muestra Edel #720 como 172,80 € con disponibilidad `unknown`. No hay oferta comparable actual en el corte. Una ruta `/fr` o un idioma no prueba mercado francés ni español. | Reproducir offline #720; inspeccionar configuración ES/EUR, contexto real de la oferta e IVA. No forzar ES ni repetir una petición incierta. |
| `0805289126577` Ray-Ban RB2140 901 | Optical H: lectura comparable; Bassol: URL con aprobación auditada; Visio: trabajo reservado al piloto. ConGafasDeSol/Mia Burton muestran 90158, no 901. | Optical H #1: en el corte del 20/11 constaba `done`, 4 HTTP; oferta guardada de 109,85 € `available`, observada el 20/11 16:08 UTC y vigente hasta el 21/11 16:08 UTC. El usuario informa que #1 pasó a `deferred` por presupuesto; ese avance no está accesible aquí. Bassol #297: aprobación y URL exactas, `deferred` por presupuesto, 0 HTTP; el `retry_at` exportado era `2026-11-20T22:44:25.810Z`. Visio #4: `deferred` por reserva/estado del piloto, 0 HTTP. | Optical H era comparable en el corte, pero esa muestra venció el 21/11 16:08 UTC. No se traslada precio ni stock desde 90158. No se conoce aquí el presupuesto/retry actual de #1. | Leer #1 y #297 en vivo; preservar sus presupuestos y fechas, sin mover `retry_at`. Mantener #4 sin reiniciar intentos ni cambiar el color 901 por 90158. |
| `0805289126591` Ray-Ban RB2140 90158 | Mia Burton: URL exacta con aprobación auditada; Óptica Universitaria: ruta documentada; Bassol: candidata; Optical H: ficha con otro EAN. | Mia Burton #298: `error`, 1 HTTP, identificación ambigua; Bassol #6: `error`, 1 HTTP, ambigua. Optical H #7: `error`, 1 HTTP, EAN publicado `0805289126607`. | No hay oferta comparable vigente. El EAN de Optical H no es equivalente y no autoriza a transferir stock. | Revalidar la aprobación de Mia y recibo de #298; resolver variante de Bassol con evidencia. Excluir el EAN distinto de Optical H, sin equivalencia ni disponibilidad transferida. |
| `0889214586650` Tom Ford FT6058B 001 | Amevista: contenido recuperado; Otticait: trabajo guardado; Óptica Óptima/Amevista: rutas alternativas documentadas. | El trabajo Otticait existente estaba `pending`; la última muestra del 19/11 no pudo guardarse porque el mercado no coincidía con la configuración del competidor. No se creó solicitud nueva. | Sin precio comparable vigente en el corte. El error de contexto no demuestra que la tienda carezca del producto ni que el mercado sea FR. | Previsualizar el trabajo y su recibo; corregir/interpretar el contexto solo con evidencia ES/EUR real. No forzar mercado ni enviar una segunda petición mientras la reserva sea incierta. |

## Recuperación preparada y requisitos para aplicar

La recuperación no se ha aplicado. Después del despliegue, con acceso local autorizado
al SQLite operativo y antes de cualquier HTTP, ejecutar una vista previa por EAN:

```powershell
python price_recovery.py --database $DB --manual-reviews --ean 0843023181451
python price_recovery.py --database $DB --manual-reviews --ean 8056597886932
python price_recovery.py --database $DB --manual-reviews --ean 8056262407691
python price_recovery.py --database $DB --manual-reviews --ean 8056597875349
python price_recovery.py --database $DB --manual-reviews --ean 0889214625984
python price_recovery.py --database $DB --manual-reviews --ean 0889214042422
python price_recovery.py --database $DB --manual-reviews --ean 0889214599834
python price_recovery.py --database $DB --manual-reviews --ean 0805289126577
python price_recovery.py --database $DB --manual-reviews --ean 0805289126591
python price_recovery.py --database $DB --manual-reviews --ean 0889214586650
```

La vista previa debe conservar por ID la URL, aprobación, estado, motivo, recibos,
HTTP/intentos y acción propuesta. Revalidar antes de aplicar por IDs exactos; empezar
con un único caso por mecanismo (muestra guardada, normalización de Optical H,
disponibilidad desconocida, mercado Otticait y presupuesto/Ray-Ban), y ampliar solo a
los que sigan elegibles. La aplicación requiere confirmación, actor, motivo y SHA; no
se aplicará un caso ambiguo, bloqueado, aplazado por presupuesto o sin aprobación.

Para una muestra conservada y enlazada inequívocamente a su solicitud, la vista previa
offline es:

```powershell
python price_saved_reevaluation.py --db $DB --evidence $EVIDENCE --request-id <id>
```

Las muestras #705, #777 y #720 no se pudieron enlazar aquí a un paquete de evidencia
local. El preflight de este equipo no encontró Docker ni configuración
`PRESTASHOP_*`; además, la descripción actual de #145 documenta que la cuenta MySQL
revisada tenía privilegios de escritura. Por ello no se intentó conectar ni se usó
esa cuenta. Para esta tarea, **HTTP nuevo = 0**; los contadores anteriores son recibos
de solicitudes históricas y no se suman como nuevas lecturas. El estado actual, las
aprobaciones y el inventario de recibos del 21/11 siguen bloqueados hasta disponer
de un runtime y una conexión real de solo lectura autorizados.
