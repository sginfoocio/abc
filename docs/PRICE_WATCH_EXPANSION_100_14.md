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
   modificados. Si la continuidad está activa, los pares sin próxima fecha pueden
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
