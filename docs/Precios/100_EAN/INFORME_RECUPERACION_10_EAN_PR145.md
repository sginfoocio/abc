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
## Verificación operativa en s2026 — 2026-11-21 (2026-11-21 18:58 UTC)

- **HEAD y CI al inicio de esta comprobación:** la PR #145 seguía abierta/en borrador en `9639213914d4121fcb15fab916f01350eb11ca75`; los jobs `tests` y Docker del run 44937056949 terminaron `success` para ese SHA. No se modificó código en esta verificación.
- **Acceso SSH:** se usó la IP aprobada `192.168.1.55` (no el alias `.55`), `BatchMode=yes` y `StrictHostKeyChecking=yes`. La clave de host ED25519 coincidió con `known_hosts` para esa IP (`SHA256:Wj1Kkqv69Zlx+wD8wBV20ANZNDcUlLmzz5+zFb0SOQk`). Sin embargo, la autenticación como `rubensg` fue rechazada: `Permission denied (publickey,password)`; la clave existente para s2026 fue ofrecida y no aceptada. No se pidió contraseña ni se ejecutaron comandos remotos. Por ello, **no se confirmó que el host responda `s2026`**, ni usuario efectivo, contenedor, versión o montajes actuales.
- El servicio local `ssh-agent` está detenido y no tiene socket/identidades cargadas; la clave explícita también fue rechazada. No se probaron credenciales ni claves de despliegue alternativas.
- **MySQL:** consultas realizadas: **0**. No se leyó el perfil `prestashop_ro`, no se intentó conectar a MySQL y no se usó la evidencia de permisos del 20/11 como estado actual. Quedan sin verificar `CURRENT_USER()`, base/versión, roles y grants efectivos, `Ssl_version`, `Ssl_cipher`, validación de certificado/identidad y acceso desde el runtime real. No se puede afirmar que la cuenta actual sea solo `SELECT`.
- **Precios y comparador:** no se ejecutó el lector de la rama para los 100 EAN, no se cambió ni rejuveneció el precio almacenado, y no se verificó la oferta actual del 15 % para `8056597435581`. El comparador desplegado y su fuente efectiva tampoco se pudieron inspeccionar; la PR sin fusionar no prueba qué versión ejecuta producción. Los 100 precios confirmados anteriormente siguen siendo una instantánea histórica, no una lectura de hoy.
- **Diez EAN pendientes:** solicitudes, aprobaciones, recibos, trabajos, presupuestos y pares no se consultaron en vivo. Se mantiene como última evidencia el corte del 20/11 descrito arriba; el 90/100 comunicado no se atribuye al estado actual. **HTTP nuevo: 0; solicitudes creadas: 0; recuperaciones aplicadas: 0; duplicados introducidos: 0.** No se pausó la vigilancia. La previsualización y las acciones por ID del procedimiento anterior siguen preparadas, pero no se pueden marcar elegibles sin el estado vivo.
- **Bloqueo concreto:** el acceso SSH existente desde esta sesión no autentica, aunque la clave de host guardada para la IP sí coincide. Hace falta que el acceso aprobado para `rubensg` esté disponible/autorice la clave existente en esta sesión, o un mecanismo SSH de solo lectura ya aprobado. No se solicitaron ni copiaron secretos. Tras recuperar acceso, repetir primero el inventario; si los grants no son solo `SELECT`, detener la lectura de precios y pedir al administrador una concesión mínima limitada a las tablas documentadas.
## Selección explícita de identidad SSH — 2026-11-21 (2026-11-21 19:21 UTC)

- Identidad local de ejecución: `IT\Ruben`; `%USERPROFILE%` es `C:\Users\Ruben`. Existe la identidad indicada (`%USERPROFILE%\.ssh\id_ed25519_s2026_copilot`). Su huella pública ED25519 es `SHA256:/fDzGeUpw4acflPUB6p6HyWvA18/zzKXAPodLOFm7JQ` (comentario público `copilot-s2026`). No se mostró ni copió la clave privada.
- No existe `%USERPROFILE%\.ssh\config`; la configuración efectiva para la IP con `-i` explícito es usuario `rubensg`, puerto 22, ese `IdentityFile` e `IdentitiesOnly=yes`. No hay `IdentityAgent`, `ProxyJump`, `ProxyCommand` ni `HostKeyAlias` configurados. La ejecución fijó además `BatchMode=yes` y `StrictHostKeyChecking=yes`.
- El servicio local `ssh-agent` está parado y no hay identidades cargadas. Aun así, OpenSSH firmó la autenticación directamente con el archivo especificado; por tanto, el fallo observado no fue por necesitar desbloquear esa clave ni cargar esa misma identidad al agente. El servidor rechazó la clave ofrecida.
- La conexión de prueba ejecutó únicamente `hostname`; no se recibió salida del servidor porque la autenticación volvió a terminar en `Permission denied (publickey,password)`. La huella del host coincidió con la entrada conocida de la IP; esto no acredita el hostname remoto. No se probaron otras identidades ni se cambiaron `authorized_keys`.
- No se encontró configuración SSH local ni registro/documentación local que identifique otra identidad que hubiese autenticado previamente; la ruta indicada por el usuario es la única que se probó. **No se pidió acción de desbloqueo/carga**, porque la identidad explícita pudo firmar; se necesita identificar/restaurar la autorización SSH que funcionaba, sin compartir secretos por chat.
## Intento de línea base Cloud por interfaz — 2026-11-21 (2026-11-21 19:51 UTC)

- Se confirmó la PR #145 abierta/en borrador en `51c28277745dc4021479f063103169d9ac1aebd6`; el commit solo modifica este informe. El código continúa en `9639213914d4121fcb15fab916f01350eb11ca75`, y los checks de tests/Docker de `51c28277745dc4021479f063103169d9ac1aebd6` terminaron `success` en el run 44940524538. El diff de la PR conserva 24 archivos; no se modificó código en este paso.
- Se abrió únicamente la URL proporcionada `https://cloud.diagonaleyewear.net/comparador-precios`. La página mostró **Acceso restringido** con formulario de inicio de sesión. No se introdujeron credenciales, no se consultaron endpoints alternativos y no se descargó ningún informe. Se solicitó que el acceso se complete en la pestaña compartida, sin enviar secretos por chat; el acceso sigue pendiente.
- En consecuencia, la cobertura actual 100 EAN, 14 competidores y 1.400 pares, el heartbeat frente al avance de cola, errores, presupuestos, solicitudes completas, continuidad, observaciones y evidencias **no quedaron comprobados en vivo**. El 71/100 del 20/11 y el 90/100 comunicado siguen siendo instantáneas históricas; no se presenta ninguna como cifra actual.
- No se reabrió ningún EAN ni se cambió estado: solicitudes creadas=0, lecturas comerciales a competidores=0, recuperaciones=0, duplicados creados=0; vigilancia y reservas no tocadas. La tabla anterior permanece como guía histórica, no como línea base de hoy.
- Siguiente paso pendiente: iniciar sesión en la pestaña compartida y exportar desde la interfaz los informes documentados de comparados/no comparados, solicitudes, continuidad/parejas y observaciones/evidencias. Solo después cruzar IDs y recibos, previsualizar casos aún elegibles y revalidar antes de una recuperación gobernada.

## Revisión viva tras iniciar sesión en Cloud — 2026-11-21 (21:23–21:25 Madrid)

La interfaz autenticada de `https://cloud.diagonaleyewear.net/comparador-precios`
actualizó el resumen a las **21:23 Madrid**:

- **Comparación:** 90/100 EAN, 10 sin competencia comparable y 0 sin precio Vytria.
  Los diez EAN de la tabla anterior siguen fuera de la comparación en este corte.
- **Alcance:** 100 productos vigilados, 14 competidores activos y 1.400 pares.
  Administración muestra 468 fichas verificadas, 182 precios vigentes, 147
  caducados y 344 trabajos pendientes/en ejecución; además, 10 alertas recientes y
  2 incidencias abiertas. La bandeja de revisión manual muestra 40 candidatas y
  0 filas seleccionadas.
- **Continuidad:** el manifiesto visible conserva 100 EAN × 14 tiendas. Figura
  `active`, `paused_at_madrid=NULL`, `stopped_at_madrid=NULL` y `error=""`.
  Heartbeat: `2026-11-21T21:25:04.825117+01:00`; próxima sincronización:
  `2026-11-21T23:27:21.155829+01:00`. Límites visibles: 200 HTTP/24 h por
  competidor, 10/minuto y pausa mínima de 3 segundos. La interfaz indicó
  «Vigilancia activa · Esperando próxima lectura». El heartbeat no demuestra avance
  de cola ni finalización de trabajos.
- La vista previa del lote supervisado no se preparó ni ejecutó. La última
  previsualización legible de Cartier `0843023181451` × Visio Factory (21:15)
  indicaba 1 par, 1 ficha verificada y **0 tareas elegibles**; estado de cola
  `done`, 0 intentos y `no antes de 2026-11-17T19:58:48.681605+00:00`. No se
  reprogramó ni se creó trabajo.
- El lote guardado 4672 (21:07 Madrid) no pudo reconciliarse: no se inspeccionaron
  sus recibos ni el avance por par. La tabla de solicitudes limita su vista a las últimas 100; el botón
  anuncia que el CSV contiene el inventario completo, pero no se obtuvo ese archivo
  en la sesión. Por tanto, las solicitudes antiguas, aprobaciones y recibos siguen
  sin reconciliarse; ausencia en la tabla no se trata como ausencia de solicitud.

### Diez EAN: comparación actual y estado operativo disponible

La comparación actual confirma que los diez siguen sin oferta comparable; los
detalles por solicitud/candidata que siguen se conservan de la vista legible de
21:15. No se pudieron reconciliar con el inventario completo de solicitudes y
recibos ni deben presentarse como una nueva lectura de cada par a las 21:23.

| EAN | Estado visible y decisión operativa |
|---|---|
| `0843023181451` Cartier CT0550S 001 | Sigue sin comparación. El preflight Visio anterior devolvió 0 tareas elegibles; no se aplicó. La solicitud Mia Burton #28 y su aprobación/recibo requieren cotejo con el inventario completo antes de reutilizarla. |
| `8056597886932` Miu Miu MU 52YS 7OE01T | Sigue sin comparación. Los registros legibles anteriores eran Bassol #218 (`error`, identidad ambigua) y Visual-Click #219 (`blocked`, límite de intentos). No se repitió HTTP ni se reiniciaron intentos. |
| `8056262407691` Miu Miu MU A06S 14L09Z | Sigue sin comparación. Optical H #179/#294 mostraban distinto modelo (`MUA06S` frente a `GAFASDESOLMIUMIUA06S`). La bandeja actual muestra 40 candidatas, pero el filtro por EAN no se pudo reconciliar con sus filas; no se confirmó una candidata aprobada para corregirlo. No se aceptó ficha ni se creó solicitud. |
| `8056597875349` Prada PR 27ZS 16K08Z | Sigue sin comparación. No se pudo reconciliar la bandeja de 40 candidatas ni la solicitud/aprobación actual para este EAN; los precios disponibles siguen siendo históricos. Elegibilidad pendiente del CSV completo y recibos. |
| `0889214625984` Tom Ford FT1310 01A | Sigue sin comparación. Mia Burton #290 mostraba una oferta de 188,50 EUR, pero el estado de par/mercado/IVA actual no quedó acreditado y la continuidad filtrada no la contaba como vigente. La muestra Edel #705 mantiene `availability=unknown`; no se declaró oferta. |
| `0889214042422` Tom Ford FT5634B 001 | Sigue sin comparación. Óptica Óptima #49 había guardado 186,00 EUR, pero su vigencia terminó el 21/11 a las 16:09 UTC. La muestra #777 con disponibilidad desconocida no se convirtió en stock. No se solicitó otra lectura. |
| `0889214599834` Tom Ford FT6082B 051 | Sigue sin comparación. Edel #53 constaba con error de variante; la muestra #720 conserva `availability=unknown`. No se forzó mercado ES desde `/fr` ni se reintentó la solicitud. |
| `0805289126577` Ray-Ban RB2140 901 | Sigue sin comparación. Bassol #297 continúa diferida por presupuesto hasta `2026-11-21T22:44:34.444000Z` (23:44 Madrid); no está vencida al corte. Las candidatas 1320 y 1323 permanecen sin seleccionar. No se modificaron presupuesto ni fecha. |
| `0805289126591` Ray-Ban RB2140 90158 | Sigue sin comparación. Mia Burton #298 mostraba ambigüedad; las candidatas 1339 (Bassol, ambigua), 1341 (Óptica Universitaria, EAN solicitado) y 1340 (Optical H, EAN publicado `0805289126607`) no se confirmaron. Se mantiene distinto el EAN `0805289126607`; sin equivalencia ni traslado de stock. |
| `0889214586650` Tom Ford FT6058B 001 | Sigue sin comparación. El trabajo Otticait seguía pendiente por discrepancia entre contexto de mercado y configuración. No se forzó ES/EUR ni se inició otra consulta. |

### Acciones, límites y siguientes pasos

- **HTTP nuevo: 0; solicitudes creadas: 0; fichas confirmadas/descartadas: 0;
  recuperaciones aplicadas: 0; duplicados introducidos: 0.** No se ejecutó ni
  preparó una recuperación masiva, no se pausó/detuvo/reinició la vigilancia y no
  se cambiaron reservas, presupuestos, intentos o fechas.
- Ninguna de las diez se declara «oferta comparable vigente» a partir de una ruta
  candidata, precio histórico, lectura vencida, disponibilidad desconocida o
  contexto ES/EUR incompleto. La única acción elegible comprobada en preflight
  (Cartier × Visio) tuvo cero tareas elegibles; las demás requieren primero
  reconciliar solicitud, aprobación, recibo y estado actual por ID.
- MySQL/SSH no se volvió a probar. Consultas MySQL y lectura actual de precios
  Vytria: **0**; el precio almacenado, la fuente del comparador desplegado y el
  estado de la oferta del 15 % para `8056597435581` siguen sin verificación actual.
  La PR sigue sin desplegarse; no se atribuye el lector de la rama a producción.
- El código no cambió. El HEAD de #145 al iniciar esta actualización era
  `edab5a5a1424dd9a5a755e32718b31888430220c`; esta adición es solo evidencia
  operativa. No se repitieron CI ni Docker manualmente. Sin merge, despliegue ni
  activación.

## Corrección de aprobación histórica y bloqueo de acceso — 2026-11-22 09:47 UTC

- **El estado vivo de Cloud no pudo actualizarse.** Al volver a la pestaña
  `https://cloud.diagonaleyewear.net/comparador-precios`, la interfaz mostró
  «Acceso restringido» y el formulario de inicio de sesión. No se descargaron
  informes ni se consultaron solicitudes, aprobaciones, recibos o continuidad.
  El último dato comprobado sigue siendo el corte histórico del 21/11 a las
  21:23 Madrid (90/100), no una medida actual. La cobertura, heartbeat, cola y
  estado de cada uno de los diez EAN en 22/11 quedan **sin verificar**; la tabla
  anterior no debe interpretarse como reconciliación actual. En particular, no
  se confirmó la aprobación ni la causa de espera de Cartier × Mia Burton #28.
- **Corrección de código en #145, commit `a0c8c6b7abc9038db1fb08c8e066da5f16a785bd`:**
  la reconstrucción de aprobaciones legadas puede usar un evento original
  `review+price` verificable también cuando la solicitud sigue pendiente, si no
  existe auditoría directa. La comprobación de identidad compara instantes
  normalizados (no cadenas con distinta precisión) y conserva las validaciones
  de identidad, producto anterior a la solicitud, URL, actor, motivo, evento y
  unicidad. No crea una aprobación para eventos ausentes o ambiguos ni rejuvenece
  una lectura histórica.
- **Regresión offline:** solicitud pendiente sin auditoría directa ni snapshot,
  aprobación trazada al evento original, preview con 0 HTTP, procesamiento por
  el monitor de la solicitud existente y segunda aplicación sin cambios ni
  HTTP duplicado. Los seis tests focalizados pasaron en **8,68 s**; `ruff
  --select F`, `py_compile` y `git diff --check` pasaron. No se ejecutó una
  lectura comercial ni una recuperación de producción.
- **Operaciones del 22/11:** HTTP nuevo 0; solicitudes creadas o reprogramadas
  0; aprobaciones aplicadas 0; recuperaciones 0; duplicados 0; cambios de
  presupuesto, intentos o vigilancia 0. No se puede afirmar que alguno de los
  diez EAN se haya resuelto, esté en cola o tenga una oferta comparable vigente
  sin la exportación autenticada y la comprobación por ID.
- La ejecución CI del SHA exacto `a0c8c6b7abc9038db1fb08c8e066da5f16a785bd`
  (`45009150798`) seguía en curso a las 09:46 UTC. El workflow de PR debe
  completar tests y build/smoke de Docker con red desactivada y sin publicar la
  imagen; el run visible solo tenía iniciado el trabajo de tests. No se ejecutó
  ni se autorizó despliegue. MySQL y SSH no se consultaron y siguen fuera de
  este trabajo.
- Para completar la reconciliación operativa se requiere restaurar el acceso
  autenticado a Cloud en la pestaña compartida. La página de GitHub abierta en
  esta sesión también solicita iniciar sesión, por lo que no se pudo editar el
  cuerpo de #145 desde la interfaz; no se sustituyó esa actualización por un
  comentario. No hubo merge ni despliegue.

## Reconciliación Cloud tras acceso SSH — 2026-11-22 (10:30:10–10:30:17 UTC)

Esta sección **sustituye los estados de acceso bloqueado de 21/11** para la
reconciliación de competidores. No sustituye evidencia anterior ni cambia precios.
El corte se obtuvo desde el contenedor existente con identidad SSH explícita,
`IdentitiesOnly=yes`, `BatchMode=yes` y `StrictHostKeyChecking=yes`; consultas de
solo lectura sobre la base existente. No se llamó a tiendas ni a endpoints no
documentados.

### Línea base viva y vigilancia

- **Cobertura a las 10:30:10–10:30:17 UTC: 89/100 EAN**. Los 11 no comparables
  fueron `0843023181451`, `8056597886932`, `8056262407691`, `8056597875349`,
  `0889214625984`, `0889214042422`, `0889214599834`, `0805289126577`,
  `0805289126591`, `0889214586650` y `0889214669742`. El último es externo a
  esta recuperación. Ninguno de los diez objetivos entraba en la comparación.
- **Alcance intacto:** 100 productos activos × 14 competidores activos = 1.400
  parejas; continuidad `id=1`, `active`, sin pausa, parada ni error. Heartbeat
  `2026-11-22T10:30:09.687Z`; próxima sincronización `22:27:27.053Z`. Los estados
  eran 442 `review`, 162 `restricted`, 795 `pending` y 1 `running`.
- Heartbeat no equivale a avance real: hubo 106 comprobaciones en la última hora,
  12 `checked_now` y 94 `not_executed`. La última lectura real acabó
  `09:48:44.655Z` para Mia Burton × `8056597989275`, fuera de los diez. La pareja
  `running` en este corte era Optical H × `0889214686374`, fuera del alcance,
  aplazada por HTTP 429; no se intervino.
- El comparador tenía 177 ofertas comparables y 133 caducadas en el conjunto.
  Continuidad: 2.790 recibos HTTP únicos confirmados y 0 sin confirmar. Estas
  cifras globales no convierten una lectura histórica de los diez casos en oferta
  vigente.

### Reconciliación por EAN y rutas con mayor evidencia

| EAN | Ruta/URL prioritaria documentada | Solicitud, aprobación y recibos actuales | Exclusión comprobada y resultado |
|---|---|---|---|
| `0843023181451` Cartier CT0550S 001 | Mia Burton #28: `https://miaburton.com/en/sunglasses/cartier/ct0550s-001`; Visio Factory: `https://www.visiofactory.es/gafas-de-sol/todas-las-solares/cartier-core-range-ct0550s-001-56-18-oro.html` | #28 `pending`, 0 intentos/HTTP/recibos. Existe evento histórico `review+price` #2480, pero la vista previa no lo enlaza como aprobación (`approval_ref` vacío). | La vista previa actual excluye #28 porque la identidad aceptada no se puede acreditar. La muestra histórica de Visio publicó EAN `7630047879029`, distinto del solicitado; no prueba equivalencia. No se reprogramó ni se creó solicitud: falta el vínculo auditable de aprobación para esta URL/EAN exacta, y la ficha Visio tiene conflicto de EAN. |
| `8056597886932` Miu Miu MU 52YS 7OE01T | Bassol #218/#70: `https://www.opticabassol.com/nl-nl/products/miu-miu-0mu-52ys-7oe01t-54`; Visual-Click #219/#71: `https://visual-click.com/products/miu-miu-mu-52ys-7oe01t-54`. Visio/Óptica Óptima tienen solo lecturas anteriores. | Bassol #218 enlaza `locate_event:2670`, 9 recibos/37 intentos, pero es un error histórico no cubierto por la recuperación desplegada; #70 tiene 1 recibo sin aprobación enlazada. Visual-Click #219 está `blocked` (15 recibos/61 intentos); #71 tiene 1 recibo sin aprobación. | El preview excluye por error histórico no cubierto y por estado `blocked`, no por falta de una URL. Las lecturas antiguas de Visio/Óptica Óptima caducaron. Sin reintento, reset ni HTTP nuevo; las identidades y autorizaciones de las filas duplicadas no se mezclan. |
| `8056262407691` Miu Miu MU A06S 14L09Z | Optical H #179/#294: `https://opticalh.com/products/miu-miu-0mu-a06s-14l09z-54-17-64155`; Óptica Óptima histórica: `https://optica-optima.com/gafas-de-sol/miu-miu-mu-a06s-14l09z.html` | Optical H #294 enlaza `locate_event:2746`; #179 no tiene aprobación enlazada. Cada solicitud conserva 1 recibo. Ambas están `error`. | El error guardado es `Distinto modelo: catálogo MUA06S, ficha GAFASDESOLMIUMIUA06S`; el preview desplegado excluye el error histórico por falta de regla conocida. La regla acotada `miu-a06s-merged-descriptor` está en el código de #145, pero no en producción (`4453b635…`). Acción pendiente de ese código: desplegar por el proceso autorizado y volver a previsualizar/revalidar antes de aplicar. Ningún HTTP nuevo. |
| `8056597875349` Prada PR 27ZS 16K08Z | Edel: `https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958`; Visio Factory: `https://www.visiofactory.es/gafas-de-sol/todas-las-solares/pr27zs-16k08z-54-21.html` | No hay solicitud manual ni aprobación identificada para estas rutas en el inventario vivo; no hay recibos que reutilizar. | Los pares conservan, respectivamente, reserva interrumpida con instrucción de no repetir una reserva incierta y estado heredado del piloto. Las muestras son históricas/caducadas. No se fabricó aprobación ni nueva solicitud: falta reconciliar la reserva de Edel y acreditar una autorización vigente para una URL exacta. |
| `0889214625984` Tom Ford FT1310 01A | Edel #17: `https://www.edel-optics.es/FT1310-01A-de-Tom-Ford.html`; Mia #290: `https://miaburton.com/en/sunglasses/tom-ford/ft1310-01a` | #17 `done`, recibos 3526/5909; muestra #705, 185,60 €, EAN exacto, ES/EUR/IVA incluido, producto y oferta enlazados. Mia #290 enlaza `locate_event:2742`, recibo 4035; #18 es registro previo duplicado, recibo 3527. | #17 conserva `schema.org/BackOrder`, por tanto `availability=unknown`, aunque la muestra siga dentro de su ventana de vigencia en el corte. #290 se excluye por error histórico no cubierto. El precio no basta para stock ni comparación. Sin captura nueva; muestra y recibos reutilizados solo para reevaluación offline. |
| `0889214042422` Tom Ford FT5634B 001 | Óptica Óptima #49: `https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html`; Visual-Click #260: `https://visual-click.com/products/tom-ford-ft5634-b-001-53` | #49 `done`, recibos 3558/5102/5103/6163/6164; muestra #777, 186,00 €. #260 está `blocked` (7 recibos); #50 es registro previo duplicado (recibo 3559). | En #49 el EAN y el IVA están acreditados, pero la evidencia de stock es `PreOrder` (`unknown`), el mercado está vacío y la oferta no está acotada a una variante. No se declara comparable ni se infiere disponibilidad del precio. No se repitió HTTP. |
| `0889214599834` Tom Ford FT6082B 051 | Edel #53: `https://www.edel-optics.es/FT6082-B-051-de-Tom-Ford.html`; Optical H documentada: `https://opticalh.com/products/tom-ford-ft6082-b-051-889214599834` | #53 `done`, recibos 3562/5931; muestra #720, 172,80 €, EAN exacto, ES/EUR/IVA incluido, producto/oferta enlazados. | La muestra #720 conserva `BackOrder`, así que `availability=unknown`; el precio no es comparable sin evidencia de stock. La ruta Optical H no tiene una solicitud aprobada que se pueda reutilizar. No se convirtió `unknown` en disponible ni se emitió HTTP. |
| `0805289126577` Ray-Ban RB2140 901 | Optical H #1: `https://opticalh.com/products/gafas-de-sol-ray-ban-wayfarer-rb2140-901-50-22-61980`; Bassol #297/#196: `https://www.opticabassol.com/nl-nl/products/ray-ban-rb2140-wayfarer-901-50`; Visio #4: `https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901l-54-18.html` | #1 seguía `deferred` por HTTP 429: 752 intentos, 18 peticiones, 18 recibos; el último recibido es **6293 (HTTP 429, 10:23:49.158Z)**, generado automáticamente por el trabajador. #297 enlaza `access_audit:341` pero está en `error` por regla histórica no cubierta (11 recibos/12 intentos); #196 es duplicado anterior (recibo 3693). #4 está diferida, sin HTTP ni recibos, por identidad no acreditada. | No se cambió presupuesto ni fecha. A las 10:30 #1 seguía diferida y no había un resultado posterior a 6293; no se reenvió la petición al vencer su `retry_at`. Las lecturas antiguas de 901 caducaron. No se confunde 901 con 90158 ni se transfiere stock. |
| `0805289126591` Ray-Ban RB2140 90158 | Mia #298/#5: `https://miaburton.com/en/sunglasses/ray-ban/wayfarer-rb2140-901-58`; Óptica Universitaria documentada: `https://www.opticauniversitaria.es/gafas-de-sol/ray-ban/wayfarer/ray-ban-wayfarer-rb-2140-54-901-58/176253.html`; Optical H #7: `https://opticalh.com/products/gafas-de-sol-ray-ban-rb2140-90158-54-18-59408` | Mia #298 enlaza `access_audit:342`, pero su error histórico no está cubierto (recibo 5046); #5 es duplicado anterior (3699). Bassol #6 conserva identidad no acreditable (3515). Optical H #7 tiene un recibo (3516). | Optical H publicó `0805289126607`, no el EAN solicitado: no hay equivalencia ni traslado de stock. Óptica Universitaria sigue en revisión por `Marca no confirmado: catálogo RAYBAN, ficha desconocido`; su URL no autoriza consulta. Sin recuperación; se mantienen colores 901 y 90158 separados. |
| `0889214586650` Tom Ford FT6058B 001 | Otticait: `https://www.otticait.com/fr/tom-ford-ft6058-b-001`; Amevista documental: `https://www.amevista.com/es/tom-ford-ft6058-b-001`; Óptica Óptima documental: `https://optica-optima.com/gafas-graduadas/tom-ford-ft6058b-001.html` | No hay solicitud manual ni aprobación auditada que programar. La pareja de continuidad Otticait #1337 está en `review`, con `Máximo de intentos alcanzado: requiere revisión`; último trabajo 19/11. | La muestra anterior de 132,30 € está caducada; carece de mercado/`market_source`, EAN y variante/modelo/color vinculados. Aunque el competidor figura configurado ES, `sales_market` no está verificado; `/fr` no acredita por sí solo ni FR ni ES. Las rutas alternativas son documentales, no ofertas verificadas. No se forzó mercado ni se consultó otra vez. |

### Acciones, recibos, duplicados y límites

- Previsualización viva: global **0 recuperables, 85 ya resueltos, 213 excluidos**.
  Para estos diez: **0 recuperables, 3 ya resueltos (#17/#49/#53), 19 excluidos**.
  Acción propuesta: reuse=0, requeue=0, already-queued=0. Los tres `done` se
  excluyen de nuevas lecturas porque no necesitan otra consulta; sus ofertas
  actuales no son comparables por disponibilidad `unknown`.
- En las solicitudes existentes de los diez hay **81 referencias a recibos,
  todas distintas**; no hay receipt ID duplicado. Se observaron 29 HTTP 200 y
  52 HTTP 429. El único HTTP posterior atribuible al trabajador durante esta
  revisión es el 429 **#6293 de la solicitud #1**; **HTTP iniciado por esta
  revisión: 0**. No hubo reservas nuevas, solicitudes creadas/reprogramadas,
  aprobaciones aplicadas ni recuperaciones.
- Se verificaron siete grupos de solicitudes duplicadas **preexistentes**:
  #70/#218 (Bassol, MU 52YS), #71/#219 (Visual-Click, MU 52YS), #179/#294
  (Optical H, MU A06S), #18/#290 (Mia Burton, FT1310), #50/#260 (Visual-Click,
  FT5634B), #196/#297 (Bassol, RB2140 901) y #5/#298 (Mia Burton, RB2140 90158).
  No se añadió ninguna solicitud ni recibo; no se afirma que el histórico esté
  libre de duplicados.
- Para auditoría, recibos guardados por ID de solicitud (listas completas del
  inventario a las 10:30 UTC): #1 `[3513,3514,5095,5096,6150,6152,6153,6154,6155,6156,6157,6158,6159,6160,6161,6162,6279,6293]`;
  #4 `[]`; #5 `[3699]`; #6 `[3515]`; #7 `[3516]`; #17 `[3526,5909]`;
  #18 `[3527]`; #28 `[]`; #49 `[3558,5102,5103,6163,6164]`; #50 `[3559]`;
  #53 `[3562,5931]`; #70 `[3578]`; #71 `[3579]`; #179 `[3676]`;
  #196 `[3693]`; #218 `[4112,4135,4182,4203,4275,4299,4308,4387,4415]`;
  #219 `[4010,4074,4087,4204,4265,4276,4309,4388,4416,4675,4698,4720,4734,4740,4784]`;
  #260 `[4014,4064,4114,4183,4225,4236,4340]`; #290 `[4035]`;
  #294 `[4039]`; #297 `[5243,5253,5286,5298,5310,5323,5336,5456,5466,5469,6258]`;
  #298 `[5046]`.
- No se detuvo ni reinició el trabajador, no se borraron bloqueos, no se
  cambiaron intentos, presupuestos, fechas, aprobaciones ni estados mediante SQL.
  No se consultó MySQL ni se modificó el precio propio. No hubo merge ni
  despliegue. Producción seguía en `abcd-control`, revisión
  `4453b635ff35e0858748af4be11639f038986432`; la regla MUA06S aún no está
  desplegada.

### Código, CI/Docker y PR

- #145 estaba abierta/en borrador en el HEAD `55e8521d613e5cd202a29f27e55afd80c9b0fb0e`
  al obtener la línea base; el cambio de código vigente para esta recuperación
  es `a0c8c6b7abc9038db1fb08c8e066da5f16a785bd`. No se modificó código en esta
  reconciliación; por ser solo evidencia/documentación no se repitieron tests ni
  CI manualmente.
- CI del commit de código: run `45009150798` seguía `in_progress`; el job `tests`
  permanecía en “Select and run offline tests” desde `09:47:02Z`. Docker depende
  de ese job y **no está verificado** para este código. El run `45009326841` del
  HEAD documental `55e8521` estaba `pending` sin jobs; los checks combinados del
  HEAD seguían `pending`. No se afirma CI ni Docker aprobado para el HEAD actual.
- El navegador GitHub de esta sesión mostró **Sign in** en #145 y no ofrece
  controles de edición autenticados. Por eso este informe sí se actualiza en la
  misma rama de #145, pero **el cuerpo de la PR no se pudo editar**; no se
  sustituyó por un comentario ni se afirma que su descripción esté al día.
- La comprobación de cuenta/permisos/conexión MySQL permanece separada y sin
  consultar. La vigilancia de competidores permanece activa; los casos que
  requieren el código MUA06S quedan pendientes de despliegue autorizado y
  posterior previsualización, y los demás no se forzaron porque faltan las
  condiciones exactas descritas arriba.

## Reconciliación de estado Cloud — 2026-11-22 12:24 UTC

Lectura de solo lectura posterior al corte de las 10:30 UTC. El dashboard pasó de
89/100 a **73/100 EAN con ofertas confirmadas** (27 sin resultados). El
inventario del monitor, con una métrica distinta, cuenta **70/100 EAN con algún
par comparable**; la diferencia de tres EAN no queda reconciliada con la
evidencia exportada y no se atribuye a una causa supuesta. La vista de
continuidad contabilizó 115 ofertas comparables a nivel de pares; no equivale
al número de EAN cubiertos.

El alcance sigue en **100 EAN × 14 competidores = 1.400 pares**. Continuidad
`id=1` está activa, sin pausa/parada/error; heartbeat `12:23:38Z`. Los pares se
distribuyen en 444 `review`, 162 `restricted`, 793 `pending` y 1 `running`.
En la última hora se registraron 3 `checked_now` y 178 `not_executed`. El EAN
`0889214669742` también aparece sin comparación; se informa aparte y no
sustituye ninguno de los diez.

### Estado actual por EAN

| EAN | Competidor/URL prioritaria | Solicitudes y aprobación conservada | Bloqueo concreto al corte | Corrección/acción; HTTP | Resultado actual |
|---|---|---|---|---|---|
| `0843023181451` Cartier CT0550S 001 | Mia Burton #28: [CT0550S 001](https://miaburton.com/en/sunglasses/cartier/ct0550s-001); alternativa Visio: [Cartier CT0550S](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/cartier-core-range-ct0550s-001-56-18-oro.html) | #28 sigue pendiente, sin intentos ni recibos. Evento histórico `review+price` #2480 existe, pero no está enlazado como aprobación de #28. | La aprobación no es trazable a la solicitud/URL. La muestra histórica de Visio publicó EAN `7630047879029`, distinto del solicitado; no prueba equivalencia de variante. | No se creó ni reprogramó solicitud y no se fabricó aprobación. HTTP nuevo 0; muestra histórica no rejuvenecida. | Sin oferta comparable vigente; falta reconstruir el vínculo auditado o una decisión humana. |
| `8056597886932` Miu Miu MU 52YS 7OE01T | Bassol #218: [MU 52YS](https://www.opticabassol.com/nl-nl/products/miu-miu-0mu-52ys-7oe01t-54); Visual-Click #219: [MU 52YS](https://visual-click.com/products/miu-miu-mu-52ys-7oe01t-54) | #218 refiere `locate_event:2670` (9 recibos/37 intentos); #70 es duplicada y no enlaza aprobación. #219 está bloqueada (15 recibos/61 intentos); #71 es duplicada sin enlace. | #218 conserva un error técnico histórico fuera de la regla desplegada; #219 alcanzó `blocked`. Lecturas Visio/Óptica Óptima son antiguas/caducadas. | #145 contiene recuperación limitada de errores técnicos con aprobación trazable; no está desplegada. #218 requiere preview/revalidación tras despliegue; #219 no se reinicia por máximo de intentos. HTTP nuevo 0. | No hay oferta comparable vigente; solo #218 podría volver a previsualizarse si la validación de elegibilidad lo confirma. |
| `8056262407691` Miu Miu MU A06S 14L09Z | Optical H #294/#179: [MU A06S 14L09Z](https://opticalh.com/products/miu-miu-0mu-a06s-14l09z-54-17-64155); alternativa histórica Óptica Óptima: [MU A06S](https://optica-optima.com/gafas-de-sol/miu-miu-mu-a06s-14l09z.html) | #294 refiere `locate_event:2746`; #179 no tiene aprobación enlazada. Cada una conserva un recibo; no se consultó dos veces la misma ficha. | Producción rechaza `GAFASDESOLMIUMIUA06S` frente a `MUA06S`; #179/#294 duplican URL y trabajo. | La normalización acotada con marca/modelo/color y la recuperación auditada están en #145, no desplegadas. Mantener #294 como única ruta con referencia de aprobación; tras despliegue, preview/revalidar antes de una sola lectura. HTTP nuevo 0. | Sin oferta comparable vigente; corrección de código preparada, recuperación pendiente de despliegue. |
| `8056597875349` Prada PR 27ZS 16K08Z | Edel: [PR 27ZS 16K08Z](https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html?selectedSize=1907958); Visio: [PR27ZS 16K08Z](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/pr27zs-16k08z-54-21.html) | No aparece solicitud manual ni aprobación identificada para estas URLs en el inventario vivo; no hay recibos que reutilizar. | Edel conserva una reserva incierta; el otro par tiene estado heredado del piloto. Sin aprobación trazable no es elegible para la recuperación histórica. | No se reenviaron reservas ni se creó aprobación/solicitud. La recuperación de errores técnicos de #145 no sustituye la aprobación faltante. HTTP nuevo 0. | Sin oferta comparable vigente; hace falta reconciliar la reserva y acreditar autorización para una URL exacta. |
| `0889214625984` Tom Ford FT1310 01A | Edel #17: [FT1310 01A](https://www.edel-optics.es/FT1310-01A-de-Tom-Ford.html); Mia Burton #290: [FT1310 01A](https://miaburton.com/en/sunglasses/tom-ford/ft1310-01a) | #17 está `done`, con muestra #705 y recibos #3526/#5909. #290 refiere `locate_event:2742`, recibo #4035; #18 es duplicada (#3527). | Muestra #705: 185,60 €, identidad y contexto ES/EUR/IVA exactos, pero `BackOrder` implica disponibilidad `unknown`. El replay guardado no acredita stock. | Se reutilizó solo la evidencia guardada para evaluación offline; HTTP nuevo 0. No se asignó disponibilidad por precio/botón de compra. | Sin oferta comparable vigente; necesita disponibilidad publicada y vinculada a esa oferta/variante. |
| `0889214042422` Tom Ford FT5634B 001 | Óptica Óptima #49: [FT5634B 001](https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html); Visual-Click #260: [FT5634B 001](https://visual-click.com/products/tom-ford-ft5634-b-001-53) | #49 `done`, muestra #777 y recibos 3558/5102/5103/6163/6164. #260 está bloqueada; #50 es duplicada. No se generó aprobación nueva. | Muestra #777: 186,00 €, `PreOrder`/`unknown`, mercado vacío y variante de oferta sin vínculo probado. | Evidencia conservada/replay offline; HTTP nuevo 0. No se repitió la captura ni se infirió stock. | Sin oferta comparable vigente; falta mercado y disponibilidad de la variante seleccionada. |
| `0889214599834` Tom Ford FT6082B 051 | Edel #53: [FT6082B 051](https://www.edel-optics.es/FT6082-B-051-de-Tom-Ford.html); alternativa documentada Optical H: [FT6082B 051](https://opticalh.com/products/tom-ford-ft6082-b-051-889214599834) | #53 `done`, muestra #720 y recibos 3562/5931. No consta solicitud aprobada para la ruta alternativa de Optical H. | Muestra #720: 172,80 €, EAN/contexto identificados, pero `BackOrder` deja disponibilidad `unknown`. | Replay guardado sin red; HTTP nuevo 0. No se convirtió `unknown` en disponible ni se abrió otra solicitud. | Sin oferta comparable vigente; falta evidencia explícita de stock asociada a la variante. |
| `0805289126577` Ray-Ban RB2140 901 | Optical H #1: [RB2140 901](https://opticalh.com/products/gafas-de-sol-ray-ban-wayfarer-rb2140-901-50-22-61980); Bassol #297/#196; Visio #4: [RB2140 901](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901l-54-18.html) | #1 permanece diferida tras 429/recibo 6293; #297 refiere `access_audit:341`; #4 sigue sin HTTP y con identidad no acreditada. | El dashboard cuenta oferta #655: 109,85 €, EAN publicado exacto, ES/EUR/IVA incluido, `available`, variante `57108062601561`, muestra capturada 20/11 16:08 y `checked_at` 22/11 12:15. Pero el par de continuidad sigue `review`: último check #1994/recibo 2349, 14/11. No se identificó un recibo que vincule el check de 12:15 a una captura nueva. | Se conserva el 429 y no se insistió. No se trasladó stock ni se atribuyó la oferta a una recuperación de esta revisión; HTTP iniciado por nosotros 0. | El dashboard cuenta 1 oferta comparable, pero monitor/continuidad no la reconcilian. Se informa como discrepancia, no como caso recuperado. |
| `0805289126591` Ray-Ban RB2140 90158 | Mia #298/#5: [RB2140 90158](https://miaburton.com/en/sunglasses/ray-ban/wayfarer-rb2140-901-58); Óptica Universitaria: [RB2140 90158](https://www.opticauniversitaria.es/gafas-de-sol/ray-ban/wayfarer/ray-ban-wayfarer-rb-2140-54-901-58/176253.html); Optical H #7 | #298 refiere `access_audit:342`; #5 es duplicada; #6 Bassol conserva identidad no acreditada. | Optical H publicó EAN `0805289126607`, que no es el solicitado. Visio/Óptica Universitaria siguen siendo rutas documentadas, no prueba de identidad/stock en este corte. `901` y `90158` permanecen separados. | No se reutilizó el stock de otro EAN, no se reabrió el 429 ni se duplicó solicitud. HTTP nuevo 0. | Sin oferta comparable vigente para `90158`; falta evidencia de la variante exacta y disponibilidad. |
| `0889214586650` Tom Ford FT6058B 001 | Otticait #1337: [FT6058B 001](https://www.otticait.com/fr/tom-ford-ft6058-b-001); alternativas documentadas [Amevista](https://www.amevista.com/es/tom-ford-ft6058-b-001) y [Óptica Óptima](https://optica-optima.com/gafas-graduadas/tom-ford-ft6058b-001.html) | Sin solicitud manual ni aprobación auditada para reintento. #1337 sigue `review`/máximo de intentos. | Muestra histórica caducada; faltan `market_source`, EAN/variante y vínculo de modelo/color. La configuración ES del competidor y la ruta `/fr` no acreditan el mercado efectivo. | Sin reset genérico ni reintento. No hay evidencia para forzar ES; HTTP nuevo 0. | Sin oferta comparable vigente; requiere contexto ES/EUR acreditado o evidencia de una alternativa documentada. |

### Operaciones, cobertura e idempotencia

- El preview global vigente registró 65 `already_queued`, 24 resueltos y 209
  excluidos; `reuse=0` y `requeue=0`. No se aplicó ningún elemento ni se añadió
  una solicitud. Las filas ya en cola no se cuentan como acciones nuevas.
- Esta revisión inició **0 HTTP**, creó **0 solicitudes**, aplicó **0
  recuperaciones** y generó **0 recibos**. Se reutilizaron sin red las muestras
  guardadas #705/#777/#720 para confirmar que `unknown` sigue sin acreditar
  disponibilidad. El recibo 6403 (429, Optical H) pertenece al trabajador y a
  otro EAN, no a esta revisión.
- Los siete grupos duplicados listados en la línea base de las 10:30 UTC siguen
  sin cambios; no se fusionaron ni borraron registros. No hubo duplicados
  nuevos. El único grupo entre los diez con dos solicitudes a una misma ficha,
  Optical H #179/#294, conserva #294 como la ruta con referencia de aprobación
  y no generó HTTP adicional.
- No se modificó vigilancia, estado, pausa, intentos, fechas, presupuesto,
  bloqueo ni reserva; no hubo SQL de escritura. No se consultó MySQL ni se
  cambió el precio propio. No hubo merge ni despliegue.

### Código y validación de #145

- HEAD de código verificado: `610467807ad11b39ff12adfdf0ea62e6d465ae7a`.
  El cambio operativo de recuperaciones históricas y aprobaciones trazables
  está en la rama, pero producción sigue en
  `4453b635ff35e0858748af4be11639f038986432`; la recuperación de #218/#294 y
  cualquier otra acción dependiente de ese código requiere despliegue
  autorizado y una nueva previsualización/revalidación. No se desplegó.
- Regresión local seleccionada: **5 archivos, 324 passed, 2 skipped, 1
  deseleccionado (estrés 500 EAN), 39,14 s**. Validaciones focalizadas
  adicionales: `test_ci_stacked_prs.py` 31, `test_dockerfile_provider_modules.py`
  6 y `test_price_saved_reevaluation.py` 9 pasaron. `flake8`, `py_compile` y
  `git diff --check` pasaron; no hubo fallos. Se omitieron las suites completas
  de precios, masterdata y estrés.
- CI del HEAD de código: run
  [45020471779](https://github.com/sginfoocio/abc/actions/runs/45020471779),
  tests y build/Docker `success`. El job Docker comprobó identidad de artefacto
  (`EXPECTED_COMMIT=6104678…`), imports y `smoke_price_diagnostic.py` con
  `--network none`, filesystem de solo lectura y UID/GID `1037:100`; no publicó
  la imagen por ser un evento de PR. El informe actualiza documentación
  únicamente, por lo que no se repitió CI manualmente. PR #145 sigue abierta/en
  borrador, sin merge ni despliegue.
