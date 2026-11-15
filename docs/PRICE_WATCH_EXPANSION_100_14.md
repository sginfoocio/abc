# Ampliación explícita de continuidad: 100 EAN y 14 competidores

El 13 de noviembre de 2026 Ruben autorizó fichas y sitemaps de los 14 competidores,
y después solicitó activar los mismos 100 EAN frente a esas 14 tiendas.
Cloud confirmó 100 productos vigilados y continuidad activa para seis tiendas.
Los planificadores generales estaban desactivados. Activarlos por separado no
amplía el manifiesto y puede duplicar la sincronización o tropezar con sus reservas.

## Activación después de desplegar

Administración → Actualizaciones → Lote supervisado → Continuidad de vigilancia
→ **Ampliar a 14 competidores · mismos 100 EAN**. Revisar el inventario propuesto,
indicar responsable y motivo, confirmar y pulsar **Activar ampliación a 14 tiendas**.
La comprobación se repite en el servidor al aplicar. Si el trabajador tiene una
reserva activa, el cambio falla sin modificaciones; se puede repetir la misma acción.

La operación añade exactamente 800 pares a los 600 existentes en una transacción.
Conserva sus IDs, revisiones, comprobaciones, observaciones, recibos, próximas fechas,
fecha de inicio y próxima sincronización. La auditoría `expand_scope` conserva el
manifiesto y los presupuestos anteriores completos, responsable, motivo y commit.
Repetir la operación no añade pares ni auditorías duplicadas. Un fallo de escritura
revierte toda la ampliación. El manifiesto original del piloto y su archivo permanecen
intactos; no se prolonga el piloto de 72 horas.

Se reutiliza el trabajador de continuidad actual. No se habilitan los planificadores
generales, no se crea un segundo sincronizador y la lectura existente de Vytria/Odoo
continúa limitada a los 100 EAN originales, con su fecha programada conservada.
Los ocho competidores añadidos quedan reservados a esta continuidad incluso cuando
se pausa o detiene: otros disparadores no pueden consultar esos pares.

Los presupuestos existentes de las seis tiendas se conservan. Para las nuevas se
respeta el límite más estricto: máximo móvil 200 HTTP/24 h, 10/min y pausa mínima
efectiva de 3 segundos. Los permisos se comprueban al aplicar, sin modificarlos.
Robots por ruta/agente, incidentes, CAPTCHA, bloqueos, restricciones, stock y revisiones
siguen vigentes. Tener 1.400 pares seleccionados no significa 1.400 fichas ni ofertas.
El calibre no cambia el precio del mismo modelo/color; no se transfieren disponibilidad
ni EAN entre variantes contradictorias. No se ha modificado el extractor.

## Límite conocido de Bassol

En la prueba real previa, EAN `0889652429717`, el sitemap eligió una ficha `nl-nl`.
El motor registró revisión porque no vinculó una oferta a la variante solicitada:
22 HTTP, ninguna oferta comparable nueva. Esta PR no acepta esa revisión ni afirma
resolver el extractor o la selección de locale de Bassol. Sus casos dudosos se
conservarán en revisión al ampliar el alcance.

## Validación

Pruebas offline de atomicidad, idempotencia, preservación del histórico, permisos,
presupuestos más estrictos, incidentes, reservas, formulario y recorrido real del
trabajador con transporte y sincronizador sintéticos. La vista previa ahora filtra
el inventario por los EAN y competidores seleccionados antes de comprobar presupuestos;
autorizar otras tiendas no rompe una ejecución congelada de seis competidores.
No se ejecutan consultas comerciales en las pruebas ni se activa producción al desplegar.

## Recuperación de interrupciones: preparación offline

El informe histórico del 15 de noviembre contiene 182 cierres técnicos en revisión,
distribuidos entre seis tiendas y 85 EAN. La lista vinculada a ese informe no es
una autorización de ejecución ni demuestra que los pares sigan siendo elegibles.
La exportación histórica contiene 246 fichas verificadas agregadas por tienda;
no identifica por sí sola el estado actual de cada par. Las capturas HTML originales
de Bassol, Optical H, Visual-Click y Mia Burton no están disponibles: la asociación
entre variante y oferta sigue pendiente de validación. El calibre no modifica el
precio del mismo modelo/color, pero tampoco acredita identidad ni stock de otra variante.

La previsualización de `price_recovery.py` solo lee una base SQLite existente y no
realiza HTTP. Exige la lista original y su informe con SHA-256 coincidente, el
manifiesto exacto y los últimos cierres esperados. Excluye, entre otros, cambios de
estado, recibos no vacíos, reservas desde el inicio del intento (también sin EAN),
colas en revisión o ejecución, candidatas pendientes y evidencias posteriores.
No se han comprobado las exclusiones en la base operativa.
También se excluyen fichas con identidad pendiente y reservas que no permitan
atribuir con seguridad el competidor; una reserva identificada de otra tienda
no bloquea el par. Una reserva de ámbito de competidor sin identificador atribuible
también se considera incierta. Los saldos compartidos se calculan y reservan en la base
operativa: una base aislada no comparte esa contabilidad. No lanzar consultas
paralelas desde una copia ni ejecutar código de la rama contra la base operativa.

Para una futura intervención **expresamente autorizada**:

1. Verificar despliegue, copia de seguridad, permisos del operador, estado de la
   continuidad, controles de acceso, incidentes, pausas, robots y presupuestos.
   No modificar estas condiciones para ejecutar la recuperación.
2. Ejecutar primero `python price_recovery.py --database <base> --allowlist <lista>
   --report <informe>` y conservar la salida. Examinar individualmente los motivos
   de exclusión y confirmar que solo se contemplan los cierres técnicos autorizados.
3. Solo con autorización específica, añadir `--apply --approved --actor <responsable>
   --note <motivo> --commit <sha-completo-del-build>`. El operador debe confirmar
   por separado que ese SHA corresponde al build instalado: el argumento no lo
   verifica. La aplicación bloquea continuidad y tiendas, revalida en una transacción,
   conserva fechas y registra una auditoría. Una repetición no reabre los pares ya
   modificados; una aplicación sin pares elegibles también registra sus exclusiones.
   Si la continuidad está activa, los pares sin próxima fecha pueden
   procesarse en cuanto se liberen los bloqueos; los controles normales del trabajador
   siguen siendo obligatorios. `--approved` es una confirmación del operador, no un
   mecanismo de autenticación ni una autorización por sí misma.
4. Comprobar la auditoría y los estados tras la aplicación; investigar cualquier
   exclusión, reserva incierta o cambio inesperado sin forzar nuevas consultas.

Las correcciones de aplazamiento respetan la fecha restrictiva `not_before` aunque
una oferta anterior esté vencida; no restauran automáticamente revisiones históricas.
Las categorías `reused_negative` y `reused_data` distinguen respuestas negativas
o datos reutilizados de ofertas comparables y de consultas comerciales nuevas.
La ejecución real y su efecto quedan pendientes de autorización y validación operativa.

El informe contiene un agregado de 44 ofertas comparables calculado sobre el
inventario al generar el informe. Los últimos inventarios guardados en recibos,
evaluados frente al latido de `2026-11-15T10:57:39.743076+00:00`, reconstruyen
43 ofertas para 34 EAN. Son fuentes temporales distintas: el informe no incluye
una instantánea final del inventario por par, por lo que no permite identificar
la oferta de diferencia ni acreditar cuál es el estado actual. Sus 1.197 recibos
HTTP únicos sí se pueden deduplicar por ID y coinciden con el agregado del informe.
Los informes anteriores de seis tiendas registran 84 y 106 ofertas, no el número
de productos; no acreditan por sí solos la caída comunicada de 70 a 37 productos.

Si un ProductGroup publica un precio común y el calibre asociado al EAN
contradice el catálogo, el extractor conserva ese precio como evidencia, no como
oferta comparable: identidad y stock permanecen en revisión. La corrección usa
fixtures sintéticas; no sustituye las respuestas HTML originales aún pendientes
de obtener mediante un flujo supervisado y presupuestado.

El resumen histórico de 44 ofertas procede de `PriceMonitor.inventory()` al
generar el informe. La reconstrucción conservadora obtiene 43 a partir del
último `inventory_after` conservado por cada par y evalúa su vigencia en el
latido histórico de 2026-11-15 10:57:39 UTC. Son instantáneas y momentos de
evaluación distintos: falta el inventario final por par utilizado por el
resumen, por lo que no se puede identificar de forma inequívoca la oferta
que explica la diferencia. Ninguna de las dos cifras demuestra el estado actual.

## Acciones supervisadas tras una integración autorizada

En la sección de continuidad, un administrador puede cargar la lista histórica y
el informe para previsualizar la recuperación. La aplicación requiere responsable,
motivo y confirmación; revalida los archivos y el estado bajo los bloqueos existentes.
En recuperación y capturas el campo de responsable comienza vacío: el operador debe
introducir su identidad real, que no queda autenticada por ese campo.
Una continuidad activa puede procesar inmediatamente los pares recuperados. No
se aplicó ninguna recuperación durante el desarrollo.

La captura supervisada selecciona un único competidor y EAN del manifiesto. Solo
procesa el trabajo normal si el par está pendiente y vencido y existe una ficha
verificada: no fuerza `not_before`, no acepta revisiones ni sincroniza Vytria.
Rechaza también cualquier trabajo de localización no terminado, para evitar
que el lote lance una búsqueda antes de la captura. Usa
el mismo trabajador, bloqueo, permisos, robots y reservas de la vigilancia. La
respuesta nueva se vincula al ID del chequeo y se guarda en auditoría con su hash,
URL, redirecciones y resultado de extracción; también puede descargarse en JSON.
El cuerpo se limita a 1 MiB y se rechaza si contiene indicadores de datos sensibles.
Antes de reservar HTTP también se rechazan URLs con credenciales, fragmentos o
parámetros distintos de `variant` y `selectedSize`; esto puede dejar pendientes
fichas legítimas con otras queries, que no deben adaptarse ni forzarse sin revisión.
Ese filtro es conservador, no una garantía de que un sitio nunca publique datos
privados: revisar la evidencia antes de compartirla. Si no se puede conservar el
cuerpo, el chequeo normal ya puede haberse efectuado y consumido presupuesto;
no repetirlo sin revisar su recibo y próxima fecha. No se guarda ningún cuerpo en
caso de rechazo. No se realizaron capturas reales en el desarrollo.

La acción separada «Diagnóstico supervisado de candidatas en revisión» permite
seleccionar una URL ya documentada para el competidor y EAN exactos, incluso con
el par en revisión. Acepta candidatas pendientes del índice, piloto, localizador
o recibos de continuidad; no acepta URLs rechazadas ni arbitrarias. Revalida el
alcance y la candidata bajo los bloqueos de continuidad y competidor, respeta
aplazamientos, permisos, robots y presupuestos, y utiliza la sesión gobernada del
localizador sin ejecutar su cola comercial. No cambia identidad, ofertas,
revisiones ni programación. Cada intento deja una auditoría iniciada antes de
pedir los bloqueos, con responsable, motivo, estado y reservas identificables;
si el proceso se interrumpe, las reservas mantienen el identificador de auditoría.
Cuando el par también pertenece al piloto anterior, se exige su comprobación
supervisada existente, incluidos su bloqueo y presupuesto; si está desactivado,
el diagnóstico se rechaza en lugar de eludirlo.

Se conservan fecha, URLs, redirecciones, canonical, SHA-256 y HTML solo si pasan
los filtros de tamaño y posibles datos sensibles. El canonical se registra como
evidencia y nunca se consulta; los destinos de redirección deben superar los
mismos controles y pertenecer al mismo host de la candidata. Los filtros son
conservadores y no pueden garantizar ausencia de todos los datos privados:
revisar la evidencia antes de compartirla. Un bloqueo HTTP sí abre el incidente
de seguridad habitual; no se intenta eludir. Un rechazo puede haber consumido
reservas de robots o de la ficha y debe revisarse antes de repetir.
El cuerpo se guarda en la auditoría de la base SQLite compartida: el límite de
1 MiB es por respuesta, no una cuota agregada. Antes de capturar hay que verificar
la privacidad efectiva del volumen, las copias y el acceso a la base, y establecer
un límite operativo de almacenamiento. Sin esas garantías no se deben guardar
capturas reales.

Después de desplegar solo con autorización independiente, verificar el build,
presupuestos compartidos, permisos, robots, incidentes, estado del par y su
próxima fecha. Para cada captura autorizada, revisar el recibo, hash, identidad,
mercado y stock antes de contabilizar una oferta; no cambiar rutas o métodos para
sortear bloqueos. Las fichas no verificadas y las revisiones necesitan un circuito
de revisión propio: esta acción no las promueve ni sustituye esa validación.
