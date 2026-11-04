# Auditoría de confirmaciones y vigilancia

## Alcance y método

Auditoría estática de `main` (`92243628b4e925ba9d549b076580726c383a43ac`) y lectura local de los cuatro CSV proporcionados en `docs/Precios/CSV/`. No se abrió una base productiva ni se consultaron comercios. Los CSV son exportaciones de la aplicación, no una copia de las tablas de confirmaciones; por eso permiten verificar el inventario del lote, pero no el total histórico de confirmaciones manuales. Los ficheros CSV de entrada se conservan localmente y quedan fuera del PR; el informe solo incluye agregados, sin precios ni URLs comerciales.

## Evidencia de los CSV

| Exportación | Filas | Localizar y monitorizar | Monitorizar | Pendiente en piloto | Reutilizar precio vigente |
|---|---:|---:|---:|---:|---:|
| `2026-11-04T12-10_export.csv` | 200 | 140 | 27 | 16 | 17 |
| `2026-11-04T12-54_export.csv` | 200 | 140 | 0 | 16 | 44 |

Ambas exportaciones contienen los mismos 200 pares de 50 EAN × 4 competidores y las mismas 44 URL reutilizables: 39 con procedencia `Piloto: ficha reutilizada` y 5 con procedencia `Ficha registrada / importación`. Entre las dos lecturas, 27 pares pasaron de `Monitorizar` a `Reutilizar precio vigente`, manteniendo su URL. Esos pares ya estaban vinculados a fichas del piloto en la primera exportación; el cambio observado es de vigencia del precio, no evidencia de 27 vínculos recuperados.

En la exportación final, las 44 fichas reutilizables se distribuyen así:

| Competidor | Pares |
|---|---:|
| Amevista | 12 |
| Edel-Optics | 10 |
| Visio Factory | 10 |
| Óptica Óptima | 12 |

`2026-11-04T12-30_export.csv` contiene 63 filas: 57 `Rechazada por identidad` y 6 `Descartada`. No contiene filas confirmadas y, por tanto, no acredita ni refuta el contador 51.

## Hallazgos comprobables en el código

- El contador anterior de `Confirmadas manualmente` se calculaba sobre filas de `pilot_url_candidates` dentro del alcance actual del piloto. Cuenta cada fila confirmada cuyo origen no sea `automatic`, no pares ni URL únicos; también agrupa orígenes vacíos o desconocidos como manuales.
- `pilot_url_candidates` conserva filas por ejecución, par y URL. Las filas confirmadas históricas pueden repetirse para un mismo par o URL. El alcance del piloto actual es de 20 EAN × 6 competidores; no es el conjunto separado de productos y competidores activos de vigilancia.
- `PilotDiscovery.review(accept=True)` vuelve a validar la identidad exacta y, en una misma operación, registra la URL como `verified` en `competitor_product_urls` y confirma el candidato. El monitor solo acepta URLs `verified` para productos vigilados y competidores activos; no consume `pilot_discovery` como índice paralelo.
- `PriceMonitor.inventory()` selecciona la URL verificada más reciente de cada par. Una fila de candidato confirmada no garantiza por sí sola precio vigente ni una ejecución de monitorización: identidad, vínculo, vigilancia activa y frescura del precio son estados diferentes.
- `WatchBatch.preview()` asigna `Pendiente en piloto` a pares no localizados que pertenecen al alcance actual del piloto, salvo estados prioritarios de revisión o recuperación. En el CSV de las 12:54 hay 16 con esa categoría: Amevista 3, Edel-Optics 5, Visio Factory 5 y Óptica Óptima 3. La categoría no demuestra por sí misma una confirmación perdida.

## Clasificación y decisión

1. **Defecto de presentación reproducible por inspección:** el contador previo mezclaba registros y confirmaciones históricas con pares únicos, y no separaba el alcance del piloto del inventario de vigilancia. Se añadió una pantalla administrativa de solo lectura para mostrar registros manuales, pares/URL distintos, filas con procedencia no registrada, disponibilidad en pares vigilados y razones por fila.
2. **Comportamiento ya resuelto:** los 39 pares etiquetados `Piloto: ficha reutilizada` en los dos CSV están disponibles en el inventario de vigilancia. Los otros 5 pares tienen URL registrada/importada. Los 27 cambios entre exportaciones son de `Monitorizar` a precio vigente, sin cambios de URL.
3. **Evidencia insuficiente:** el paquete no incluye las filas `confirmed` de `pilot_url_candidates` ni una exportación de `competitor_product_urls`; no se puede desglosar con datos el 51 entre manuales, filas repetidas, pares distintos, URLs vigentes o elementos históricos/fuera de vigilancia.
4. **Autorización/configuración:** no se modificaron permisos ni métodos de localización. No se prepararon colas ni se recuperaron candidatos. Los 57 candidatos rechazados y 6 descartados del CSV siguen excluidos.

No se justifica una acción de recuperación con la evidencia disponible: el lote ya muestra 44 URL vinculadas y no identifica una URL confirmada válida que falte en el índice verificado. La nueva sección de Administración es informativa; no escribe, no encola y no hace HTTP.

## Consultas SQLite de solo lectura para completar el cotejo

Ejecutar únicamente sobre una copia no productiva o sobre una conexión autorizada de solo lectura. Las consultas no modifican datos.

### Confirmaciones por procedencia, filas, pares y URL

```sql
SELECT review_origin,
       COUNT(*) AS candidate_rows,
       COUNT(DISTINCT competitor_id || char(31) || ean) AS distinct_pairs,
       COUNT(DISTINCT competitor_id || char(31) || ean || char(31) || url) AS distinct_urls
FROM pilot_url_candidates
WHERE state = 'confirmed'
GROUP BY review_origin
ORDER BY review_origin;
```

### Reconstrucción del contador anterior en el alcance actual del piloto

```sql
WITH scope AS (
    SELECT competitors.key AS competitor_id, eans.value AS ean
    FROM price_pilot
    JOIN json_each(price_pilot.eans) AS eans
    JOIN json_each(price_pilot.competitors) AS competitors
    WHERE price_pilot.id = 1
)
SELECT COUNT(*) AS previous_ui_rows,
       COUNT(DISTINCT c.competitor_id || char(31) || c.ean) AS distinct_pairs,
       SUM(c.review_origin = 'manual') AS explicit_manual_rows,
       SUM(COALESCE(c.review_origin, '') NOT IN ('manual', 'automatic')) AS other_or_unknown_origin_rows
FROM pilot_url_candidates AS c
JOIN scope AS s ON s.competitor_id = c.competitor_id AND s.ean = c.ean
WHERE c.state = 'confirmed'
  AND COALESCE(c.review_origin, '') <> 'automatic';
```

### Pares de vigilancia activa con ficha verificada

```sql
SELECT COUNT(*) AS active_watch_pairs,
       COALESCE(SUM(EXISTS (
           SELECT 1
           FROM competitor_product_urls AS u
           WHERE u.ean = w.ean
             AND u.competitor_id = c.id
             AND u.status = 'verified'
       )), 0) AS verified_watch_pairs
FROM watch_products AS w
CROSS JOIN competitors AS c
WHERE w.active = 1 AND c.active = 1;
```

### Confirmaciones manuales sin su misma URL verificada en vigilancia

```sql
SELECT c.id, c.ean, c.competitor_id, c.url, c.run_id, c.reviewed_at,
       CASE
           WHEN w.active IS NOT 1 THEN 'producto fuera de vigilancia'
           WHEN competitor.active IS NOT 1 THEN 'competidor inactivo'
           WHEN exact.status IS NULL THEN 'URL no vinculada'
           WHEN exact.status <> 'verified' THEN 'URL no verificada'
           ELSE 'URL no seleccionada como verificada más reciente'
       END AS reason
FROM pilot_url_candidates AS c
LEFT JOIN watch_products AS w ON w.ean = c.ean
LEFT JOIN competitors AS competitor ON competitor.id = c.competitor_id
LEFT JOIN competitor_product_urls AS exact
       ON exact.ean = c.ean
      AND exact.competitor_id = c.competitor_id
      AND exact.url = c.url
LEFT JOIN competitor_product_urls AS selected
       ON selected.ean = c.ean
      AND selected.competitor_id = c.competitor_id
      AND selected.status = 'verified'
      AND selected.url = (
          SELECT u.url
          FROM competitor_product_urls AS u
          WHERE u.ean = c.ean
            AND u.competitor_id = c.competitor_id
            AND u.status = 'verified'
          ORDER BY u.verified_at DESC, u.url
          LIMIT 1
      )
WHERE c.state = 'confirmed'
  AND c.review_origin = 'manual'
  AND (w.active IS NOT 1 OR competitor.active IS NOT 1
       OR selected.url IS NULL OR selected.url <> c.url)
ORDER BY c.ean, c.competitor_id, c.id;
```

## Validación y límites pendientes

La nueva auditoría usa SQLite temporal en pruebas sintéticas. La suite del comparador verifica conteo de duplicados, historial, procedencia sin registrar, URL alternativa verificada, falta de vínculo, y que la vista no modifica la base ni realiza HTTP. Para concluir los números reales de 51 se necesita el resultado de las consultas de solo lectura anteriores; los cuatro CSV existentes no incluyen el inventario completo de confirmaciones.
