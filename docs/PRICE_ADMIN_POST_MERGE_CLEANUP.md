# Limpieza posterior de la administración del comparador

Inventario de simplificación para PR #144. No autoriza borrados de datos, tablas, históricos ni funciones históricas.

## Antes y después de la navegación

Antes, la administración comercial, las revisiones, las colas y el piloto compartían
cinco destinos superiores y seis menús administrativos. Ahora la navegación principal
tiene tres áreas: **Comparativa de precios**, **Administración** y **Diagnóstico técnico**.
Productos y ofertas se ven juntos después de las cifras y gráficas; tiendas, productos
vigilados, revisiones y consumos quedan en Administración; colas, importaciones,
configuración técnica, búsqueda manual avanzada e históricos quedan en Diagnóstico
técnico. Por revisar usa la bandeja del trabajador actual y no el flujo de revisión
del antiguo piloto.

La selección anterior del navegador se migra a su área nueva al volver a abrir la
página. Guardar una URL candidata no se confunde con leerla: la lectura manual de una
ficha solo permite elegir URLs ya verificadas. Al confirmar identidad en «Por revisar»,
se conserva la solicitud prioritaria del trabajador compartido.

Capturas sintéticas antes/después: se enlazarán aquí tras generarse con datos de prueba;
no deben incluirse pantallas ni datos productivos.

| Ubicación | Función actual | Evidencia de uso o incertidumbre | Dependencias | Acción propuesta | Verificación antes de retirar |
|---|---|---|---|---|---|
| `app_pages/price_comparator.py` — navegación superior | Tres áreas comerciales/técnicas en lugar de cinco destinos separados. | Cubierto por AppTest de navegación; las claves de sesión antiguas se traducen. | `TOP_LEVEL_SECTIONS`, `render_admin`, `render_diagnostics`. | Conservar y revisar tras uso real. | Probar rutas, persistencia de filtros y sesiones iniciadas con la navegación anterior. |
| `app_pages/price_watch.py` — piloto de 72 horas | Se retiraron activación, reanudación, parada y el avance periódico de este piloto. El histórico es de solo lectura en Diagnóstico técnico. | La UI y el supervisor dejaron de invocar esas acciones; los datos históricos siguen en tablas dedicadas. No se pudo comprobar el estado de una base productiva desde este entorno. | `price_trial.py`, `price_trial_cycles`, `price_trial_pairs`, `access_audit`. | Mantener histórico/API y tablas; cualquier migración de estado requiere lectura y auditoría antes de ejecutar. | En el despliegue, comprobar que el piloto no está activo, que continuidad sigue autorizada por su manifiesto y que no hay trabajador viejo en paralelo. |
| `app_pages/price_comparator.py` — formularios de configuración, localización y actualización del piloto | Ya no están en las rutas de navegación; el detalle del producto no inicia una segunda búsqueda/lectura. La búsqueda manual por EAN o modelo/color sigue en Diagnóstico técnico y sujeta a permisos. | AppTest y el harness visual comprueban las tres áreas; no se ejecutan consultas comerciales. | `PricePilot`, `PilotDiscovery`, `PriceLocator`, `PriceMonitor`, `manual_review_refreshes`. | Mantener únicamente servicios y datos necesarios para compatibilidad/histórico; retirar helpers muertos tras verificar imports y tablas. | Buscar consumidores externos, comparar candidatos antiguos con `competitor_product_urls` y probar confirmación, lectura prioritaria e idempotencia. |
| `app_pages/price_comparator.py` — bandeja anterior basada en `pilot_url_candidates` | La bandeja diaria ahora usa `PriceLocator` y su lectura prioritaria. Candidatos del antiguo descubrimiento no se borran ni se presentan como ofertas actuales. | El trabajador actual ya no avanza descubrimientos del piloto; el historial puede contener candidatos pendientes sin estado comercial actual. | `price_discovery.py`, `pilot_url_candidates`, `pilot_http_log`, `access_audit`. | Conservar tablas/evidencias; mantener la decisión sobre migrar o exponer solo después de reconciliar contra la bandeja actual. | Reconciliar pares/URLs sin modificar fichas válidas; probar procedencia, revalidación e historial antes de migrar o retirar. |
| `app_pages/price_watch.py` — productos, tiendas, Por revisar, vigilancia | Administración diaria agrupada por objetivo; detalle de tienda conserva métodos, URLs, presupuestos y bloqueos. | Navegación AppTest recorre las secciones sin HTTP externo. | `PriceWatch`, `AccessControl`, `PriceLocator`, `PriceMonitor`. | Conservar; simplificar subcontroles solo con evidencia de uso. | Validar filtros, métodos, estados de URL, consumos y solicitudes prioritarias. |
| Diagnóstico técnico — colas, recuperaciones e importaciones | Herramientas y formularios históricos que no pertenecen a la navegación comercial habitual. | Siguen respaldados por pruebas y servicios compartidos; varias opciones heredadas tienen uso real desconocido. | `price_locator.py`, `price_monitor.py`, `price_pilot.py`, colas SQLite y presupuestos. | Mantener en diagnóstico; retirar solo con referencias de uso y dependencias revisadas. | Verificar autorización, robots, reservas, idempotencia, presupuesto y recibos antes de cada retirada. |
| `app_pages/price_comparator.py` — `_render_inicio` | Resumen con cifras, posición y gráficas seguido del detalle comercial por producto. | Reutiliza el histórico comparador; la ficha detallada está en la tabla de productos para evitar repetir el selector. | Estado de continuidad, heartbeat, colas y sincronización. | Conservar el orden visual y evitar duplicar paneles de comparación. | Comprobar denominadores, ofertas vigentes y filtros compartidos. |
| `_run_web_search` y búsqueda manual histórica | Consulta manual almacena y compara fichas. | Conserva compatibilidad con URL, candidatos y recuperaciones. | `price_web.py`, `price_discovery.py`, `competitor_product_urls`. | Conservar; simplificar solo la presentación si se traslada a la ficha. | Ejecutar pruebas de URL, robots, stock, mercado, moneda e IVA. |
| `pilot_scope()` y validaciones de piloto | Protección heredada de alcance de 20 EAN y seis competidores. | Todavía protege trabajos históricos y cierres antiguos. | `price_trial.pair_reason`, `require_pilot_access`, `price_pilot.py`. | Conservar como fallback histórico; usar continuidad activa primero en recorridos nuevos. | Probar fuera de manifiesto, reserva activa, expiración y ausencia de continuidad. |
| Tablas `price_trial`, `price_trial_cycles`, `price_trial_pairs` | Histórico del piloto de 72 horas; no debe borrar recibos ni auditorías. | Esquema y lecturas siguen soportados; el uso/estado real de producción no está accesible desde esta revisión. | `price_trial.py`, `access_audit`, `price_continuity*`. | Conservar; no ejecutar limpieza ni migración destructiva en esta PR. | Hacer snapshot aprobado, verificar integridad/recuentos y comparar por ID de ejecución antes de cualquier cambio. |
| `manual_review_refreshes` e identidad/attribution | Colas persistentes y evidencia de lecturas tras confirmar ficha. | Usadas por monitorización, reintentos y auditoría. | `price_locator.review`, `price_monitor.review_refresh_tick`. | Conservar; no duplicar ni reinterpretar como oferta. | Probar refresco, reinicio, lease, caducidad y ausencia de HTTP duplicado. |
| `_render_saved_reevaluation` | Reevaluación histórica de candidatos guardados, basada solo en evidencia ya almacenada. | Disponible en diagnóstico avanzado; su uso productivo no se pudo medir. | `price_saved_reevaluation.py`, candidatos locales y auditorías. | Conservar fuera del recorrido diario y revisar uso tras observación. | Confirmar que no altera estados ni consulta webs sin evidencia nueva. |
| Mensajes y claves `MONITOR_ENV`, `PRICE_MONITOR_SCHEDULE_ENABLED` | Exposición técnica del planificador periódico general. | Variables deben quedar en diagnóstico; no representan por sí solas el estado de continuidad. | `price_monitor.schedule_enabled`, `price_trial.continuity_row`. | Trasladar explicación técnica a Ajustes avanzados. | Confirmar que una continuidad activa/manual sigue operativa aunque el planificador general esté desactivado. |

## Capacidad y evidencia pendiente

Los valores predeterminados son por competidor: hasta 200 HTTP en una ventana móvil
de 24 horas, 10 por minuto y una pausa efectiva mínima de 3 segundos. El techo
teórico agregado es 2.800 HTTP/día para 14 competidores, no una garantía de cobertura
de 1.400 pares ni una propuesta para elevar límites. El conteo se deriva de
`access_requests`; no se obtuvieron métricas productivas actuales ni una cola vigente
porque la aplicación compartida no respondió. Por tanto, esta PR no cambia cuotas ni
afirma un consumo o capacidad real. Propuesta futura, no aplicada: mantener límites
por competidor y evaluar un primer ajuste gradual de 200 a 250 HTTP/24 h únicamente
para una tienda cuya autorización y condiciones lo permitan y cuyo recibo demuestre
que el presupuesto es el impedimento; conservar 10/minuto y 3 segundos, medir el
resultado y no repetir el aumento ante 403/429, robots o un bloqueo. No es un límite
global ni una promesa de cobertura y requiere aprobación después de medir la cola.

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
