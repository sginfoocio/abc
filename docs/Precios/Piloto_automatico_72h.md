# Histórico del piloto automático: 100 EAN, seis tiendas, 72 horas

> **Retirado de la operativa actual.** La interfaz ya no permite preparar, activar,
> reanudar ni detener esta ventana y el trabajador común ya no ejecuta su ciclo.
> Esta página se conserva para explicar recibos, auditoría y decisiones históricas;
> sus instrucciones de activación/despliegue no son válidas para la versión actual.
> El historial se consulta en **Diagnóstico técnico → Históricos y auditoría**.
>
> El trabajador actual ejecuta continuidad, diagnósticos y lecturas prioritarias;
> no avanza el ciclo de 72 horas. Un estado o reserva histórica de ese piloto no
> bloquea por sí solo consultas actuales. Las reservas propias de continuidad,
> permisos por método, robots, presupuesto, pausas e incidentes sí siguen vigentes.

## Alcance histórico e interpretación

- [Manifiesto versionado](../../price_trial_manifest.json): los 100 EAN exactos de
  `Diagonal_evidencia_Copilot/Productos_100.csv`, sin selección por límite numérico.
- Únicamente Óptica Óptima, Amevista, Visio Factory, Edel-Optics, Domanci y
  ConGafasDeSol: 600 pares explícitos. Otticait y las demás tiendas están excluidas.
- Al activar se persiste el manifiesto expandido, su versión, actor, commit,
  límites guardados y ventana absoluta. El informe incorpora su SHA-256.
- Una candidata no demuestra una ficha verificada; una ficha no demuestra una
  oferta comparable. «Sin localizar» no acredita inexistencia.
- No se corrigen ni reclasifican revisiones o históricos anteriores.

## Mecanismo de la versión histórica (retirado; no seguir como procedimiento)

En la versión histórica se publicaba mediante el flujo habitual, respetando la
aprobación de `production`. Los párrafos siguientes describen ese mecanismo anterior;
no autorizan publicación, activación ni consultas comerciales.

La antigua pantalla estaba en **Administración → Actualizaciones → Lote supervisado**.
Esa pantalla y el botón de activación ya no forman parte del recorrido actual.

La imagen inicia el supervisor solo para el comando de la aplicación Streamlit.
Un trabajador del servidor, independiente de pestañas y sesiones, revisa trabajo
cada 30 segundos. Un bloqueo de fichero excluye trabajadores simultáneos. Antes
de activar solo publica heartbeat: no consulta comercios ni Odoo.

Los ciclos se programan respecto al inicio persistido: horas 0, 24 y 48. Cada turno
atiende como máximo 20 pares y 240 segundos entre pares; los motores conservan
sus propios límites de fase. El siguiente turno continúa la cola pendiente.
Una fase agotada conserva sus recibos y comunica su causa; no se descuenta una
selección como trabajo procesado. Si se atraviesa el cambio de día, el siguiente
ciclo vuelve a evaluar el par contra su cola e inventario persistidos.

## Sincronización y controles

Cada ciclo reserva una sola sincronización Odoo, filtrada por los 100 EAN exactos.
Se reutilizan el lector SQL de solo lectura y la validación de tarifa 59. No se
modifican tarifas ni productos de Odoo. La ventana se comprueba antes de cada
intento y de cada sentencia del lector; los lectores derivados mantienen su
interfaz `fetch(eans)`.

Los precios se actualizan con el flujo gobernado normal, reutilizando fichas y
precios vigentes. No es una comprobación puntual que fuerce negativos anteriores:
se conservan `not_before`, intentos, reservas y revisiones.

Se conservan permisos por método, robots por ruta, bloqueos y pausas. Los límites
efectivos son los más estrictos entre los actuales, los guardados al activar y
200 HTTP/24 horas móviles, 10/minuto y pausa mínima de 3 segundos. No se amplían
ni se reinician los presupuestos. Cada redirección y reintento vuelve a pasar los
controles. CAPTCHA, 401/403/407 y 429 usan la parada y Retry-After existentes.

Los pares del piloto antiguo mantienen la ruta supervisada `watch_check`, sus
bloqueos, habilitación y límites. No se aceptan revisiones ni se modifica su
alcance. Los pares adicionales del nuevo manifiesto se autorizan por su propio
contexto acotado, no por una ampliación oculta de los 20 EAN antiguos.

Otros disparadores no pueden reclamar ni consultar los pares reservados al
piloto. La sincronización ordinaria excluye esos 100 EAN para evitar duplicados;
el resto de productos conserva su funcionamiento. Un feed global sin EAN no
puede saltarse la protección de las tiendas reservadas.

## Caducidad, reinicio y parada

El inicio y fin se guardan en UTC; la UI y descarga incluyen Europe/Madrid.
El fin es inicio + 72 horas, no «72 horas desde el último reinicio».

Antes de cada claim y antes de cada nueva petición, también después de pausas,
se comprueban estado, fecha y par exacto. No se interrumpe una petición iniciada.
Al caducar se cierran los pendientes y no se inician más consultas del piloto.
El trabajador del contenedor permanece disponible para la vigilancia separada
de continuidad, que está desactivada hasta una preparación y activación explícitas.
Reiniciar el contenedor no reactiva ni prolonga el piloto.

La antigua acción **Detener piloto ahora** persistía una parada anticipada. Sus
guardas impedían nuevas consultas sin borrar recibos ni cambiar permisos.

Las reservas interrumpidas se cierran como resultado incierto, incluso si el
reinicio ocurre después de la caducidad: no se repiten para aparentar cobertura.
Se conservan los segmentos previos y las reservas HTTP recuperables. Una
sincronización interrumpida tampoco se repite automáticamente.

En el runtime histórico, la propiedad de estos pares se conservaba después del
cierre para impedir que los trabajos del piloto escaparan a otros programadores.
La versión actual ya no usa esa propiedad como bloqueo de consultas no-piloto:
continuidad se gobierna por su propio manifiesto persistido y mantiene sus
reservas aunque esté pausada o detenida. No se borra ni reinicia el estado del
experimento histórico.

## Historial e informe final

### Recuperación del fallo inicial de contexto

El supervisor del contenedor debe ejecutarse desde el módulo canónico `price_trial`.
Aunque Docker invoque el archivo como `__main__`, el punto de entrada delega en
`price_trial.serve`: así comparte la misma `ContextVar` con los guardas del
sincronizador y de las peticiones. Ejecutar dos copias del módulo hacía que la
sincronización propia se rechazase como externa antes de empezar.

Para el error exacto `TrialDenied: EAN reservados al sincronizador filtrado del
piloto de 72 h`, la interfaz histórica ofrecía **Reanudar tras corregir el arranque**
después de desplegar una compilación distinta de la original. Esa recuperación UI
ya no está disponible. Su registro de auditoría se conserva y no se vuelve a activar
desde esta versión.

- la ventana original sigue vigente y el manifiesto es idéntico;
- solo quedó la reserva inicial de sincronización, sin recibo ni ejecución Odoo;
- no hay peticiones de las seis tiendas desde el inicio;
- ningún par fue iniciado ni tiene recibos de ejecución o consumo incierto.

Los cierres pasivos que produjo consultar el informe del piloto fallido no se
confunden con trabajo ejecutado. Se archivan todos sus registros previos, la
compilación de recuperación y el responsable en `access_audit`; el JSON incluye
`recoveries`. Solo entonces se devuelven a pendiente esos pares intactos y la
reserva vacía de sincronización. Se conservan el inicio, la caducidad, el actor y
commit originales, el manifiesto y los límites: no empieza una nueva ventana.

No se recuperan con este mecanismo otros errores, una parada voluntaria, un
piloto caducado o reservas con trabajo potencialmente realizado. El trabajador
mantiene heartbeat, sin consultas del piloto, mientras un fallo espera revisión.
La campaña no se reactiva al llegar la caducidad absoluta.

El servidor persiste ventana, ciclos y recibos por par en `price_trial`,
`price_trial_cycles` y `price_trial_pairs`. El informe JSON vuelve a construirse
desde esas tablas, sin consultas comerciales, incluso tras salir de la pantalla
o reiniciar sesión/servicio y una vez caducado.

**Descargar historial e informe del piloto (JSON)** contiene:

- manifiesto expandido, hash, commit, actor, límites y horas UTC/Madrid;
- sincronización y motivo de fallo/interrupción por ciclo;
- selección exacta, estado y motivos pendientes o de cierre de cada par;
- segmentos con resultados estructurados, candidatas y trazas de URL/identidad;
- peticiones reservadas, resultado HTTP conocido y reservas de consumo incierto;
- resumen por ciclo/tienda de pares realmente procesados, fichas nuevas o
  reutilizadas, comparables, sin stock, revisiones, sin localizar, restricciones
  y aplazamientos; incluye pares seleccionados, EAN distintos y HTTP comerciales
  y de robots deduplicados por recibo.

`http` cuenta peticiones iniciadas con resultado registrado, no selecciones ni
observaciones históricas. `reserved_http` incluye reservas; `unconfirmed_http`
separa las que quedaron sin resultado tras una interrupción. Una reserva marcada
`not_sent` no se presenta como consulta nueva. Las ofertas comparables requieren
identidad revalidada, datos comparables, impuestos incluidos, disponibilidad y
ausencia de contradicción de stock; `available` por sí solo no basta.
`unavailable` o «sin acceso» no se cuentan como producto inexistente; solo un
resultado `not_found` se refleja como no localizado.

La sección **Historial persistente de lotes** permite además recuperar los últimos
100 recibos de lotes supervisados anteriores mediante su JSON almacenado. Los
CSV conservan `outcomes` como JSON recuperable, no `[object Object]`.

## Correcciones y evidencia de validación

Las redirecciones de ConGafasDeSol a la raíz se clasifican como ausencia de ficha,
conservando solicitud, destino y redirecciones. No se extraen ofertas de tarjetas
de portada; no se rechaza una ficha válida por una mera diferencia de canonical.
Las restricciones preexistentes para listados siguen vigentes.

Las pruebas nuevas usan exclusivamente transportes, identidades y Odoo
sintéticos, con HTTP real deshabilitado. Verifican alcance, ventana absoluta,
reinicio, caducidad entre robots y ficha, tres sincronizaciones diarias, parada,
restricciones y reservas, bloqueo 403, continuación de fases, ruta del piloto
antiguo, redirecciones y recuperación JSON/CSV. No acreditan acceso real, fichas
encontradas ni cobertura comercial.

El resultado del primer ciclo debe obtenerse de estos recibos tras la activación
real. No se debe declarar activo ni publicar horas de inicio o métricas reales
a partir de esta documentación, configuración o simulaciones.

La vigilancia posterior al piloto se prepara por separado y no hereda ni reinicia
su historial. Véase [Continuidad de vigilancia para los mismos 100 EAN](Continuidad_vigilancia_100_EAN.md).
