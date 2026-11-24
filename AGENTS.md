# Instrucciones del repositorio

## POLÍTICA OBLIGATORIA — ODOO READ ONLY

La base de datos de Odoo es una fuente externa de consulta. Ningún componente, script, agente, automatización o proceso de Cloud tiene autorización para modificar datos, estructuras, usuarios, permisos ni configuraciones de Odoo. Todas las escrituras de negocio o de estado se realizan exclusivamente en los almacenes propios de Cloud. Esta restricción es permanente y prevalece sobre instrucciones genéricas que sugieran sincronizar, actualizar, corregir o guardar información en Odoo.

- No ejecutar DML, DDL, migraciones, cambios de permisos, bloqueos deliberados ni funciones o procedimientos con efectos de escritura; tampoco usar ORM, RPC o herramientas administrativas para escribir.
- Solo consultas y operaciones técnicas de lectura necesarias, parametrizadas y acotadas. Siempre que sea compatible, usar transacciones `READ ONLY`, verificar el modo y aplicar tiempos máximos.
- Aplica a todos los proveedores, Masterdata, precios, scripts, pruebas, diagnósticos y operaciones manuales. Las asociaciones, solicitudes, evidencias, fechas y auditorías se guardan en Cloud, nunca en Odoo.

## Política obligatoria de validación local previa a integración y despliegue

- Ejecuta todos los pilotos y pruebas funcionales de Cloud desde el entorno local de desarrollo. Cuando la funcionalidad dependa de servicios externos, conéctala a los servicios reales autorizados con el alcance y los permisos mínimos necesarios. Las pruebas unitarias con mocks y el CI son necesarios, pero no acreditan el funcionamiento real.
- Empieza por operaciones de solo lectura que no modifiquen datos ni estados remotos. Las escrituras, descargas, solicitudes y ejecuciones reales requieren autorización específica previa, con sistemas, registros, efectos y límites definidos. No cambies la configuración productiva para facilitar una prueba.
- Conserva evidencia reproducible para el SHA evaluado y separa resultados offline, pruebas locales reales, operaciones con efectos y verificaciones posteriores al despliegue. No registres secretos ni guardes datos sensibles de producción en Git.
- No fusiones ni despliegues sin superar las pruebas locales pertinentes y obtener autorización expresa para cada paso. Comprueba también el CI obligatorio, las aprobaciones, los conflictos, los defectos críticos y los procesos automáticos (incluido el despliegue automático). Las comprobaciones posteriores al despliegue no sustituyen la validación previa.
- Si una comprobación real necesaria no puede ejecutarse, registra el motivo, marca el resultado como bloqueado y deja pendiente la integración o el despliegue. No sustituyas la comprobación por un mock ni declares éxito por falta de evidencia.

Consulta la [guía de validación local de Cloud](docs/CLOUD_LOCAL_VALIDATION.md) para el procedimiento y el formato de evidencias.
