# Instrucciones del repositorio

## Política obligatoria de validación local previa a integración y despliegue

- Ejecuta todos los pilotos y pruebas funcionales de Cloud desde el entorno local de desarrollo. Cuando la funcionalidad dependa de servicios externos, conéctala a los servicios reales autorizados con el alcance y los permisos mínimos necesarios. Las pruebas unitarias con mocks y el CI son necesarios, pero no acreditan el funcionamiento real.
- Empieza por operaciones de solo lectura que no modifiquen datos ni estados remotos. Las escrituras, descargas, solicitudes y ejecuciones reales requieren autorización específica previa, con sistemas, registros, efectos y límites definidos. No cambies la configuración productiva para facilitar una prueba.
- Conserva evidencia reproducible para el SHA evaluado y separa resultados offline, pruebas locales reales, operaciones con efectos y verificaciones posteriores al despliegue. No registres secretos ni guardes datos sensibles de producción en Git.
- No fusiones ni despliegues sin superar las pruebas locales pertinentes y obtener autorización expresa para cada paso. Comprueba también el CI obligatorio, las aprobaciones, los conflictos, los defectos críticos y los procesos automáticos (incluido el despliegue automático). Las comprobaciones posteriores al despliegue no sustituyen la validación previa.
- Si una comprobación real necesaria no puede ejecutarse, registra el motivo, marca el resultado como bloqueado y deja pendiente la integración o el despliegue. No sustituyas la comprobación por un mock ni declares éxito por falta de evidencia.

Consulta la [guía de validación local de Cloud](docs/CLOUD_LOCAL_VALIDATION.md) para el procedimiento y el formato de evidencias.
