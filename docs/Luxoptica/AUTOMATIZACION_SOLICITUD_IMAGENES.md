# Automatizacion Solicitud de Imagenes Luxoptica

## Objetivo
Automatizar el flujo completo desde MASTERDATA hasta la recepcion y descarga de imagenes de producto por lotes.

## Estado actual implementado

### 1) Transformacion MASTERDATA
- Se transforma el fichero de origen en la app.
- Se normalizan EAN en la columna Barcode.

### 2) Generacion de lotes para Luxoptica
- En la pantalla de Masterdata existe la seccion:
  - Solicitud de imagenes (Luxoptica)
- Se generan archivos .txt en docs/Luxoptica con:
  - 1 EAN por linea
  - Sin prefijo es.
  - Maximo 250 EAN por archivo
- Formato de nombre:
  - upc-products-images-request-YYYYMMDD_HHMMSS-lote-XXX.txt

### 3) Envio en portal Luxoptica
- El flujo operativo actual (ya probado):
  - Login
  - Servicios -> Recursos digitales -> Imagenes de producto
  - Cargar archivo
  - Seleccionar Todas las vistas
  - Enviar solicitud
  - Especificar email de recepcion: **images@diagonaleyewear.com**

### 4) Control de progreso en app
- Por cada lote generado:
  - Boton Descargar lote
  - Boton Marcar como procesado
- Se muestra:
  - progreso N/total
  - mensaje final cuando todos los lotes estan procesados

## Regla de negocio de lotes
- Cada archivo = una solicitud.
- Los lotes se suben uno por uno.
- Tamaño maximo por lote: 250 EAN.

## Estructura de carpetas
- Plantillas y lotes:
  - docs/Luxoptica/
- Ejemplo de plantilla:
  - docs/Luxoptica/upc-products-images-template.txt

## Siguiente fase (pendiente): lectura automatica de buzon y descarga con Microsoft 365

### Objetivo de la fase
Leer correos de respuesta de Luxoptica y descargar automaticamente enlaces/adjuntos de imagenes.

## Decisiones cerradas (2026-07-31)
- Se registrara una app exclusiva en un tenant dedicado para este flujo.
- Se descargaran adjuntos de correo (las imagenes) como canal principal.
- Carpeta destino aprobada: `docs/Luxoptica/descargas/AAAA-MM-DD/lote-XXX/`.

### Arquitectura propuesta
1. App registra metadatos de solicitud por lote:
   - nombre de archivo
   - timestamp de envio
   - email destino
   - estado
2. Proceso backend consulta Microsoft Graph API:
   - buscar correos de Luxoptica
   - filtrar por asunto/remitente/ventana temporal
3. Extraer adjuntos de correo (imagenes).
4. Descargar y guardar en carpeta local por lote.
5. Marcar lote como completado en estado interno.

### Requisitos tecnicos para Microsoft 365
- Aplicacion registrada en Azure AD (Entra ID).
- Credenciales OAuth2 (client_id, tenant_id, client_secret o certificado).
- Permisos Graph recomendados:
  - Mail.Read
  - Mail.ReadWrite (si se van a etiquetar/mover correos)
  - User.Read (opcional para validacion de identidad del token)
- Buzon objetivo:
  - images@diagonaleyewear.com (SharedMailbox dedicado para recepción de imágenes)

### Modo de autenticacion propuesto
- `client_credentials` para proceso backend no interactivo.
- Consentimiento de administrador en el tenant exclusivo.

### Seguridad
- No guardar tokens en codigo.
- Guardar secretos en .env o gestor seguro.
- Rotacion de secretos y auditoria de accesos.

## Flujo objetivo end-to-end
1. Transformar MASTERDATA.
2. Generar lotes de 250 EAN.
3. Enviar solicitudes en Luxoptica (manual asistido o automatizado).
4. Recibir correos de Luxoptica.
5. Descargar imagenes automaticamente con Graph.
6. Marcar lote completado y registrar evidencia.

## Checklist operativo actual
- [ ] Transformacion ejecutada
- [ ] Lotes generados
- [ ] Lotes subidos (uno por uno)
- [ ] Todas las vistas seleccionadas
- [ ] Solicitud enviada a Luxoptica
- [ ] Email especificado: images@diagonaleyewear.com
- [ ] Lotes marcados como procesados en app

## Checklist proximo sprint (M365 token)
- [ ] Alta app en Entra ID
- [ ] Permisos Graph concedidos
- [ ] Token flow implementado
- [ ] Lectura de buzon implementada
- [ ] Descarga de adjuntos implementada
- [ ] Trazabilidad por lote implementada
