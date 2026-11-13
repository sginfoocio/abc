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
