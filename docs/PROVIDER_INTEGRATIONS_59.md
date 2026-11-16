# Integración de proveedores de imágenes (#59): auditoría inicial y diseño

Estado: **implementación parcial; consolidación en #131**. Esta auditoría conserva decisiones
de arquitectura y límites operativos, pero ya existe código para búsqueda masiva de solo lectura,
selección de proveedor y flujos manuales asistidos. Las regresiones offline no acreditan portales,
asociaciones comerciales vigentes ni producción. No se han hecho descargas, accesos a portales ni
consultas productivas a Odoo. Nunca incluir credenciales, tokens ni URLs con token en el repositorio,
issues, logs ni fixtures.

## 1. Auditoría del código existente

| Pieza | Ubicación | Reutilización |
|---|---|---|
| Lectura de pedidos de compra por proveedor | `kering_images.read_orders`, `read_suppliers`, `read_supplier_contacts` | Misma consulta (`purchase_order` → `purchase_order_line` → `product_product.barcode`); el filtro por `commercial_partner_id` ya agrupa partners hijos. Extraer a un módulo común sin cambiar Kering. |
| Orquestación con estados, locks y reintentos | `kering_jobs.KeringScheduler`, `process_pending` | Patrón para un scheduler por proveedor, desactivado por defecto. |
| Repositorio común por EAN | `image_repository.ImageRepository.save(ean, name, content, provider=, origin=, view=, market=, metadata=)` | Único destino de imágenes: checksum SHA-256, huella de píxeles, deduplicación, originales inmutables, `sync_nas`. |
| Nomenclatura y vistas | `image_naming`, `kering_media` | Vistas solo con evidencia; el resto `unknown` + revisión. |
| Miniaturas / biblioteca / ZIP | `image_thumbnails`, `image_library`, `ImageRepository.zip_eans*` | Sin cambios; filtran por `provider`. |
| Almacenamiento y seguridad de rutas | `repository_storage`, `image_work_storage` | Staging de descargas y comprobación de capacidad. |
| Panel de procesos | `process_activity.record_process`, `cloud_dashboard` | Un proceso por proveedor. |
| Correo Graph | `graph_mail_downloader` | Posible canal si el portal notifica aprobaciones por email (no confirmado). |

La observación de que no había conectores era válida al redactarse esta auditoría. Ahora
`provider_images.py`, `provider_images_ui.py`, `marcolin_images.py`, `nuorder_images.py` y
`safilo_images.py` implementan el núcleo manual asistido y los contratos de adaptador, pero no
demuestran que exista un driver real aprobado para los portales. La selección de proveedor y la
búsqueda masiva se consolidan en #131; la consulta de Odoo se ejecuta solo tras una acción explícita
y es de solo lectura. El flujo n8n `n8n/Trasformación Pedidos More.json` sigue transformando
albaranes `SHIP_*` y fijando el proveedor como texto; no identifica proveedores ni productos de
imágenes.

## 2. Relación con proveedores reales de Odoo (por verificar, solo lectura)

Regla: **no clasificar por marca ni nombre; no asociar marcas de NuORDER/Sáfilo al partner 573**.

Consultas de solo lectura a ejecutar con una cuenta de lectura y registrar los resultados (conteos,
sin datos personales) en la issue:

1. `res_partner` por ID 573 y su `commercial_partner_id`; partners hijos (`commercial_partner_id = <commercial de 573>`).
2. Proveedores candidatos de NuORDER y Sáfilo: `res_partner` con `supplier_rank > 0` cuyos nombres
   sugieran Dita, Cutler and Gross, Akoni, Balmain, Linda Farrow, Jacquemus, Safilo (Carrera,
   Carolina Herrera, David Beckham). Confirmarlo con el equipo de compras; el nombre solo propone candidatos.
3. Vínculo producto–proveedor por **tres vías**, comparando cobertura:
   - `product_supplierinfo` (`partner_id` → `product_tmpl_id`/`product_id`);
   - `purchase_order_line` de pedidos con `partner_id` en la familia comercial (vía usada por Kering);
   - `stock_move`/recepciones, si se quiere cobertura de histórico.
4. Calidad de EAN: nulos, longitud ≠ 8/12/13/14, dígito de control inválido, duplicados entre
   variantes, EAN compartidos entre proveedores (conflicto → `needs_review`).
5. Marcas citadas que aparezcan en productos de **otros** proveedores (control negativo exigido por los
   criterios de aceptación).

Resultado esperado: una tabla `proveedor Odoo → portal → marcas`, aprobada por el cliente, almacenada
como configuración (no en código) con el ID de partner como clave.

La búsqueda de #131 muestra la relación proveedor–portal como candidata o pendiente salvo evidencia
explícita de variante, proveedor y portal. Las relaciones por plantilla y las compras históricas no
acreditan por sí solas una ruta vigente; las contradicciones y variantes múltiples requieren revisión.
Abrir o refrescar esa página no consulta Odoo ni crea almacenes o solicitudes.

## 3. Diseño común

Se reutilizan el núcleo de solicitudes y el repositorio común; no se crea otra librería de
imágenes. La búsqueda de #131 es deliberadamente una fase de análisis, separada del flujo manual
de solicitudes:

```
Acción explícita ─► Odoo (solo lectura) ─► propuesta con evidencia y revisión
       │
       └──────────► catálogo de solo lectura ─► archivos verificados ─► visor común

Flujo manual de proveedor ─► SQLite durable ─► ZIP revisado ─► ImageRepository.save
```

- **ProviderDemand**: EAN normalizados y deduplicados por proveedor (reutiliza `read_orders`, rango
  temporal y criterio Kering) menos los EAN con vistas completas en el repositorio y los EAN en solicitudes vivas.
- **Persistencia**: SQLite en `masterdata_data` (como `order_alerts`) con `provider_requests`
  (id, provider, remote_id, state, attempts, last_error, created/updated, next_poll_at) y
  `provider_request_eans` (request_id, ean, result). Transiciones transaccionales (`BEGIN IMMEDIATE`) y
  **append-only de eventos**, igual que el registro de entregas de #19.
- **Estados** (de la issue): `detected → ready → submitted → pending → available → downloaded → imported`,
  más `partial`, `failed`, `needs_review`. Reglas: nunca enviar dos veces el mismo EAN mientras
  exista una solicitud viva; un fallo de red tras enviar una solicitud es **ambiguo** (mismo criterio que
  #19): pasa a `needs_review` y se reconcilia consultando "My pending requests", no se reenvía.
- **Interfaz del adaptador** (mínima): `submit(eans) -> remote_id`, `status(remote_id) -> state`,
  `download(remote_id) -> manifest + ficheros`. Los errores se tipifican como en `kering_images`
  (`LoginFailed`, `SessionExpired`, `InterventionRequired`, …) para que CAPTCHA/MFA detengan la automatización.
- **Importación**: validar cada fichero (tipo real, tamaño, píxeles) y su EAN con evidencia
  (manifiesto/metadatos). Guardar con `provider="marcolin|nuorder|safilo"`, `origin` = id de solicitud y
  `view` solo si hay evidencia; en caso contrario `unknown` y revisión manual. Sin renombrados arbitrarios.
- **Operación**: `dry-run` por defecto, lote piloto con tope configurable, interruptor por proveedor
  (`<PROVIDER>_AUTOMATION_ENABLED=false`), sondeo con backoff y jitter, límite de solicitudes por día,
  comprobar/reintentar manual en la UI, y credenciales solo en secretos del host (nunca en GitHub/CI).

## 4. Proveedores

### Marcolin (partner 573 a verificar)
Flujo comunicado, aún sin revalidación operativa: Multimedia → Assets requests → Product images +
marcas → lista de EAN → validación → confirmaciones → *Profile → Requests* (pendientes/disponibles)
→ descarga (horas o días). El código conserva referencias agrupadas por UPC en XLSX y deja
contradicciones explícitas para revisión; no acredita el driver, la solicitud nº 1 ni una asociación
con el partner 573. **Desconocido**: API, estructura del ZIP, nombres de fichero, límite de EAN por
solicitud, MFA/CAPTCHA y condiciones de uso de la automatización. No inferir que el proveedor esté
confirmado ni priorizar un piloto sin esa evidencia.

### NuORDER (app.next.nuorder.com)
Grupos: Dita, Cutler, Akoni (Akoni y Balmain), Linda Farrow (Linda Farrow y Jacquemus). Sin pasos
documentados: la fase 1 es una exploración manual autorizada de selección por EAN, descarga masiva,
vistas y nomenclatura, y comprobar si ofrece API oficial (preferible a automatizar la web).
No asumir que comparte modelo de solicitudes con Marcolin.

### Sáfilo (youandsafilo.com / commportal.safilo.com, Safilo Omnia)
Flujo comunicado, pendiente de validar con un ZIP real: búsqueda masiva por lista de EAN con coma →
*Add to selection* → selección → *download all selected* (todas las vistas; la descarga desde ficha
da una sola vista). Es síncrono/interactivo.
**Riesgo principal**: nombres de fichero poco reconocibles → exigir manifiesto o metadatos del portal que
demuestren fichero–EAN–vista; si no existen, todo queda en `needs_review` (nunca inferir por orden).
Pendiente confirmar el comportamiento de la URL de búsqueda avanzada aportada y su compatibilidad con automatización.

## 5. Consolidación y alcance vigente

La PR integradora activa es #131. Conserva el selector visual, la búsqueda masiva administrativa
EAN/UPC, la consulta explícita y de solo lectura de Odoo/catálogo, el visor común y las correcciones
de exportación Marcolin; incorpora además las mejoras de reintentos, conservación y revisión de
identidad de Kering. No se crearán PR adicionales para volver a separar estos cambios.

| PR de origen | Cambios conservados | Archivos y commits en #131 | Pendientes reales |
|---|---|---|---|
| #69 | Auditoría, decisiones de arquitectura y límites operativos; se actualizan las afirmaciones obsoletas de “sin implementación” y el antiguo plan de PR. | `docs/PROVIDER_INTEGRATIONS_59.md`; `d78d451` y revisión documental de consolidación. | Aprobar y verificar en Odoo el mapa proveedor–portal–marcas; no se ha consultado Odoo productivo. |
| #86 | Selector visual de cinco proveedores; solo se renderiza el proveedor seleccionado, sin cambiar rutas ni permisos. | `app_enhanced.py`, `provider_images_ui.py`, `tests/test_provider_images_ui.py`; `2c8f9d1`. | Validación visual offline con datos sintéticos; no acredita el flujo real de los portales. |
| #129 | Reintentos acotados, conservación ante fallos, diagnóstico de acceso, revisión de lifestyle y asociación EAN/UPC con evidencia. | `image_library.py`, `image_repository.py`, `kering_images.py`, `kering_images_ui.py`, `kering_jobs.py`, `kering_portal.py`, `tests/test_kering_images.py`, `tests/test_kering_viewer_order.py`; `f512d0e`, `7c27d1d`. | Validar acceso/redirección y asociaciones con evidencia operativa; lifestyle no determina vistas V1/V2/V3. |
| #130 | Exportación XLSX de referencias agrupadas por UPC y revisión explícita de contradicciones. | `marcolin_images.py`, `docs/MARCOLIN_PILOT_PLAN.md`, `tests/test_marcolin_images.py`; `49f6712`. | Validar circuito de recepción/importación real con autorización; no reenviar ni alterar la solicitud nº 1. |
| #131 | Entrada EAN/UPC y CSV/TXT, análisis explícito de catálogo/Odoo de solo lectura, revisión de candidatas, reutilización del visor y exportación. | `app_enhanced.py`, `app_navigation.py`, `bulk_image_search.py`, `app_pages/bulk_image_page.py`, `tests/test_app_navigation.py`, `tests/test_bulk_image_search.py`; `8157400`. La corrección del contrato de ruta/icono se añade en `tests/test_cloud_identity.py`. | Confirmar asociaciones con evidencia autorizada; la búsqueda offline no acredita proveedor, imágenes de portal ni datos productivos. |

La automatización de portales sigue desactivada y fuera del alcance de la búsqueda. Los contratos
manuales no implican validación real de envío, descarga o importación. La solicitud Marcolin nº 1
permanece sin reenvío ni reconciliación artificial. La validación de un ZIP real de Sáfilo, el acceso
y formatos de NuORDER y los errores de acceso/redirección de Kering requieren evidencia propia y
autorización operativa; #59 y las issues funcionales relacionadas permanecen abiertas.

## 6. Riesgos y decisiones pendientes

- Autorización contractual para automatizar los portales (términos de uso) y existencia de API oficial.
- MFA/CAPTCHA: si aparecen, el conector queda en modo asistido (`InterventionRequired`).
- Rotar las contraseñas compartidas en conversaciones; crear cuentas de servicio por portal y secretos separados.
- Mapa proveedor Odoo ↔ marcas aprobado por el cliente (sección 2).
- Límites de solicitud por portal y política de repetición para EAN no encontrados.
- Ubicación del estado (SQLite en `masterdata_data`) y su inclusión en las copias de seguridad.
- Evidencia operativa para las limitaciones señaladas; los checks offline y los datos sintéticos no
  se deben presentar como validación de portal, Odoo productivo o importación real.
