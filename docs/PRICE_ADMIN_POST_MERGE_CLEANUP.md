# Limpieza posterior de la administración del comparador

Documento operativo y técnico. No autoriza borrados de datos, tablas, históricos ni funciones históricas.

| Ubicación | Función actual | Evidencia de uso o incertidumbre | Dependencias | Acción propuesta | Verificación antes de retirar |
|---|---|---|---|---|---|
| `app_pages/price_watch.py` — sección «Piloto anterior» | Configuración y ejecución manual del piloto de 20 EAN y seis competidores. | Existe recuperación, auditoría y regresiones específicas; puede seguir necesitado para pruebas históricas o cierres pendientes. | `price_pilot.py`, `price_trial.py`, `pilot_runs`, `pilot_jobs`, `pilot_budgets`. | Conservar temporalmente; trasladar a Ajustes avanzados sin presentarlo como alcance actual. | Confirmar que no hay ejecuciones activas, que la continuidad cubre el 100×14 y que el piloto cerrado conserva su historial. |
| `app_pages/price_watch.py` — «Preparar cola», «Procesar lote», límites y diagnósticos | Herramientas técnicas de localización y monitorización. | Usadas por pruebas y mantenedores; no forman parte del recorrido principal «Por revisar». | `price_locator.py`, `price_monitor.py`, colas SQLite y presupuestos. | Mantener solo en Ajustes avanzados. | Añadir pruebas de navegación que impidan su aparición en Por revisar. |
| `app_pages/price_comparator.py` — `_render_inicio` antiguo | Estado operativo detallado reemplazado por cifras comerciales y línea compacta. | El bloque antiguo ya no es el recorrido principal; podrían existir enlaces o sesiones que lo referencien internamente. | Estado de continuidad, heartbeat, colas y sincronización. | Simplificar/eliminar duplicados solo tras verificar llamadas externas. | Buscar referencias a las claves `inicio_odoo_*`, `inicio_review`, `inicio_continuity_*`. |
| `_run_web_search` y búsqueda manual histórica | Consulta manual almacena y compara fichas. | Conserva compatibilidad con URL, candidatos y recuperaciones. | `price_web.py`, `price_discovery.py`, `competitor_product_urls`. | Conservar; simplificar solo la presentación si se traslada a la ficha. | Ejecutar pruebas de URL, robots, stock, mercado, moneda e IVA. |
| `pilot_scope()` y validaciones de piloto | Protección heredada de alcance de 20 EAN y seis competidores. | Todavía protege trabajos históricos y cierres antiguos. | `price_trial.pair_reason`, `require_pilot_access`, `price_pilot.py`. | Conservar como fallback histórico; usar continuidad activa primero en recorridos nuevos. | Probar fuera de manifiesto, reserva activa, expiración y ausencia de continuidad. |
| Tablas `pilot_runs`, `pilot_jobs`, `pilot_url_candidates`, `pilot_budgets` | Históricos y auditorías del piloto antiguo. | Evidencia de uso en recepción, recuperaciones y reanudación. | Trabajador existente y auditorías. | No retirar ni limpiar automáticamente. | Respaldos, consultas readonly y prueba de retención antes de cualquier migración. |
| `manual_review_refreshes` e identidad/attribution | Colas persistentes y evidencia de lecturas tras confirmar ficha. | Usadas por monitorización, reintentos y auditoría. | `price_locator.review`, `price_monitor.review_refresh_tick`. | Conservar; no duplicar ni reinterpretar como oferta. | Probar refresco, reinicio, lease, caducidad y ausencia de HTTP duplicado. |
| `_render_saved_reevaluation` | Reevaluación histórica de candidatos guardados. | Es observable en «Por revisar» como herramienta auxiliar; su uso real puede ser limitado. | `price_saved_reevaluation.py`, candidatos locales y auditorías. | Mantener fuera de los controles de ejecución rápidos y revisar uso tras observación. | Medir uso, confirmar que no altera estados ni obras sin evidencia nueva. |
| Mensajes y claves `MONITOR_ENV`, `PRICE_MONITOR_SCHEDULE_ENABLED` | Exposición técnica del planificador periódico general. | Variables deben quedar en diagnóstico; no representan por sí solas el estado de continuidad. | `price_monitor.schedule_enabled`, `price_trial.continuity_row`. | Trasladar explicación técnica a Ajustes avanzados. | Confirmar que una continuidad activa/manual sigue operativa aunque el planificador general esté desactivado. |

## Históricos que no deben eliminarse

- Ofertas vigentes e históricas.
- Decisiones de identidad confirmadas o rechazadas.
- Recibos HTTP y atribución de lectura manual.
- Auditorías de reserva, continuidad y recuperaciones.
- Estados de cola y razones de aplazamiento.

## Verificación de cada retirada futura

Toda retirada futura debe demostrar:

1. Que no existen ejecuciones activas o lotes pendientes.
2. Que el componente no alimenta continuidad, presupuesto, robots o auditoría.
3. Que no hay referencias en pruebas o recorridos administrativos.
4. Que sus datos históricos siguen accesibles o tienen una migración aprobada.
5. Que el cambio no altera los 100 EAN ni los 14 competidores.
