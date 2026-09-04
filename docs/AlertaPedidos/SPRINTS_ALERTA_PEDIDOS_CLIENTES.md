# Sprints de Implementacion: Alerta de Pedidos de Clientes Vigilados

## Contexto

Nuevo apartado en la app (Streamlit) para comprobar si algun cliente incluido
en una "lista de vigilancia" (mantenida por el usuario) tiene pedidos en Odoo,
y enviar un email de alerta cuando se detecte coincidencia.

## Decisiones confirmadas (respuestas del usuario)

| Pregunta | Decision |
|---|---|
| Trigger de comprobacion | Solo bajo demanda (boton). El cron se anade en un sprint posterior. |
| Que es "un pedido" | Cualquier `sale.order`, incluye borradores/cotizaciones. |
| Coincidencia de nombre | Nombre exacto en `res_partner.name` (cliente) O `res_partner.name` (dirección de entrega). No parcial, no email/NIF. |
| Origen de la lista vigilada | Archivo de configuracion simple (texto/JSON) en el servidor, editable desde la app. |
| Ventana temporal a revisar | Filtro de fechas configurable en la UI (Desde/Hasta), por defecto ultimos 30 dias. Sin historico de "ya alertado". |
| Cuenta de envio | `images@diagonaleyewear.com` via Graph API. Requiere anadir permiso `Mail.Send` en el registro de aplicacion de Azure (actualmente solo tiene permisos de lectura de correo). |
| Destinatario de la alerta | `roberto@diagonaleyewear.com` (configurable por variable de entorno). |
| Filtro de estado del pedido | Selector "Pendientes" / "Todos". Pendientes = `state = 'sale'` y (no facturado del todo o con lineas sin entregar por completo). Todos = sin filtro de estado. |
| Campo de fecha del filtro | `sale_order.date_order`. |

## Riesgo conocido / limitacion aceptada en MVP

Al no llevar historico de pedidos ya alertados, si se pulsa el boton varias
veces con el mismo pedido abierto todavia en Odoo, se volvera a enviar la
alerta para ese mismo pedido. Es un comportamiento aceptado para el MVP
(uso manual, bajo demanda). Se resuelve en el Sprint 4 (cron) anadiendo
un registro de "ultima ejecucion / IDs ya notificados".

## Preguntas todavia abiertas (a confirmar antes de Sprint 2)

- ?El permiso `Mail.Send` para `images@diagonaleyewear.com` ya esta disponible
  en Azure AD o hay que solicitarlo a IT/administrador del tenant?
- ?"Pedidos abiertos" excluye los cancelados (`state = 'cancel'`) o se
  revisan literalmente todos los `sale_order` sin filtrar por estado?
- Formato exacto de la lista de vigilancia: ?nombres exactos tal cual figuran
  en `res_partner.name`, uno por linea/JSON, sin mayusculas/tildes especiales
  a normalizar?
- ?El boton vive dentro del apartado nuevo (pagina propia) o dentro de una
  pagina existente (ej. Configuracion)?

---

## Sprint 0 - Cierre funcional (0.5 dia)
Objetivo: cerrar el resto de decisiones pendientes antes de programar.

Tareas:
- Confirmar disponibilidad del permiso `Mail.Send` en el App Registration
  de `images@diagonaleyewear.com` (Azure Portal > API permissions).
- Confirmar filtro de estado de pedido (todos vs excluir cancelados).
- Confirmar formato del fichero de lista de vigilancia (JSON propuesto:
  `{"clientes": ["Nombre Exacto 1", "Nombre Exacto 2"]}`).

Entregable:
- Especificacion funcional v1.0 aprobada (variante de este documento).

Criterio DoD:
- No quedan preguntas abiertas de la seccion anterior.

## Sprint 1 - Gestion de la lista de vigilancia (0.5-1 dia)
Objetivo: permitir al usuario mantener la lista de nombres desde la app.

Tareas:
- Crear `watchlist_clientes.json` (fichero de configuracion en servidor).
- Modulo `watchlist_config.py`: `load_watchlist()`, `add_customer()`,
  `remove_customer()`, `save_watchlist()`.
- Nueva pagina Streamlit "Alerta Pedidos" con formulario para anadir/quitar
  nombres y ver la lista actual.

Entregable:
- Pagina funcional donde el usuario gestiona la lista sin tocar codigo.

Criterio DoD:
- Anadir/eliminar un nombre persiste en el fichero y se refleja al recargar.

## Sprint 2 - Comprobacion contra Odoo (1 dia) - IMPLEMENTADO
Objetivo: consultar `sale_order` + `res_partner` en la BD de Odoo y detectar
coincidencias exactas con la lista de vigilancia en cliente O dirección de entrega.

Tareas:
- Query SQL (via `db_config.py` / patron de `db_loader.py`) que devuelve
  pedidos cuyo `res_partner.name` (cliente) o `partner_shipping_id.name`
  (dirección de entrega) coinciden exactamente con algun nombre de la lista,
  con filtro de rango de fechas (`date_order`) y filtro de estado
  (Pendientes/Todos).
- Modulo `check_pedidos_vigilados.py` con funcion
  `find_matching_orders(clientes, fecha_desde, fecha_hasta, solo_pendientes)
  -> DataFrame` (pedido, cliente, dirección_entrega, fecha, estado,
  invoice_status, total).
- Boton "Comprobar ahora" en la pagina nueva, con selectores de fecha
  Desde/Hasta y radio "Pendientes"/"Todos", que ejecuta la consulta y
  muestra resultado en tabla (o "sin coincidencias").

Entregable:
- Boton operativo que detecta coincidencias reales contra Odoo con filtros
  de fecha, estado y busqueda dual (cliente + direccion de entrega).

Criterio DoD:
- Prueba manual con un nombre de cliente conocido con pedido abierto
  devuelve resultado correcto; con nombre sin pedidos devuelve vacio.
- Cambiar el filtro de fechas o el estado (Pendientes/Todos) cambia el
  resultado mostrado.
- La búsqueda encuentra también pedidos donde el nombre figura en la
  dirección de entrega, no solo en el cliente principal.

## Sprint 3 - Envio de alerta por email (0.5-1 dia) - IMPLEMENTADO
Objetivo: enviar email via Graph API (`images@diagonaleyewear.com`) cuando
el boton detecta coincidencias.

Tareas:
- Permiso `Mail.Send` concedido en el App Registration (confirmado por el usuario).
- Funcion `send_alert_email()` en `graph_mail_downloader.py` usando endpoint
  `POST /users/{mailbox}/sendMail` con el mismo flujo de client credentials
  ya existente.
- Cuerpo del email con tabla de pedidos detectados (cliente, numero de
  pedido, fecha, estado, estado factura, importe).
- Destinatario configurable via variable de entorno `ALERT_RECIPIENT_EMAIL`
  (default `roberto@diagonaleyewear.com`).
- Integrado en el flujo del boton "Comprobar ahora" (solo si hay
  coincidencias).

Entregable:
- Boton que, al detectar coincidencias, envia el email y muestra
  confirmacion en la interfaz.

Criterio DoD:
- Email recibido correctamente en un envio de prueba con datos reales.

## Sprint 4 - Programacion via cron (0.5-1 dia)
✅ IMPLEMENTADO

Objetivo: automatizar la comprobacion periodica sin intervencion manual.

Tareas:
- Script standalone `run_alerta_pedidos.py` (equivalente a
  `poll_luxoptica_mail.py` / `scheduler_luxoptica.py`) que ejecuta la misma
  logica de los Sprints 2-3 (solo pedidos "Pendientes").
- Registro de pedidos ya notificados en `alerta_pedidos_notificados.json`
  para evitar alertas duplicadas en ejecuciones sucesivas del cron.
- Programacion: 2 ejecuciones diarias (07:45 y 12:00) via crontab del host,
  invocando el script dentro del contenedor Docker (`docker compose exec`).

Entregable:
- Script ejecutable de forma independiente (`run_alerta_pedidos.py`).
- Volumen Docker para persistir `alerta_pedidos_notificados.json`.
- Entradas de crontab documentadas en el README de despliegue.

Criterio DoD:
- Ejecucion programada detecta y notifica solo pedidos nuevos desde la
  ultima ejecucion.

