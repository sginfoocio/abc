"""Admin-only GIO configuration, durable requests and local artifact history."""
from datetime import datetime, time, timezone
import json
import sqlite3

from cryptography.fernet import InvalidToken
from filelock import Timeout
import streamlit as st

from cloud_design import page_title
from gio_cloud import CENTRES, STATES, GioConfigStore, GioStore, current_result, data_root
from gio_reports import previous_day


def render():
    if st.session_state.get("auth_role") != "admin":
        st.error("GIO requiere permisos de administrador.")
        st.stop()
    page_title("GIO", "Ventas diarias por centro e importacion manual en Odoo")
    try:
        store = GioStore(data_root())
        config_store = GioConfigStore(data_root())
        config = config_store.load()
        settings, manual, history = st.tabs(["Configuracion", "Ejecucion manual", "Historico"])
        with settings:
            render_settings(config_store, config)
        with manual:
            render_manual(store)
        with history:
            render_history(store, config)
    except (OSError, sqlite3.Error, InvalidToken):
        st.error("No se puede leer GIO. Revise permisos de almacenamiento y la clave de cifrado del servidor.")
    except ValueError as exc:
        st.error(str(exc))


def render_settings(config_store, config):
    st.warning("Descarga autenticada pendiente. Automatico bloqueado; envios desactivados de fabrica.")
    st.caption("Las claves se cifran con el mecanismo de proveedores de Cloud. No se muestran las credenciales guardadas.")
    with st.form("gio_settings"):
        enabled = st.toggle("Activar automatizacion diaria", value=config["enabled"], disabled=True)
        daily = st.time_input("Hora diaria (Europe/Madrid)", value=time.fromisoformat(config["daily_time"]))
        recipients = st.text_input("Destinatarios (separados por punto y coma)", "; ".join(config["recipients"]))
        send_enabled = st.toggle("Permitir envios reales solicitados explicitamente", value=config["send_enabled"])
        username = st.text_input("Usuario GIO (dejar vacio para conservar)")
        password = st.text_input("Contrasena GIO (dejar vacio para conservar)", type="password")
        clear = st.checkbox("Eliminar credenciales GIO guardadas")
        st.subheader("Contrato de importacion Odoo")
        st.caption("IDs de base de datos, NO nombres ni codigos. No se escribe en Odoo. "
                   "La evidencia debe acreditar la agrupacion de las cinco lineas en un asiento.")
        mapping = {"company": int(st.number_input("ID sociedad Odoo", min_value=0,
                                                  value=config["mapping"]["company"])),
                   "journals": {}, "accounts": {}}
        for centre, (name, journal, _) in CENTRES.items():
            mapping["journals"][centre] = int(st.number_input(
                f"ID diario {name} ({journal})", min_value=0, value=config["mapping"]["journals"][centre]))
        for code, value in config["mapping"]["accounts"].items():
            mapping["accounts"][code] = int(st.number_input(f"ID cuenta {code}", min_value=0, value=value))
        odoo_evidence = st.text_area("Evidencia de validacion de importacion Odoo (version, sociedad, campos y agrupacion)",
                                     config["odoo_import_evidence"])
        approved = st.checkbox("Operaciones y referencias aprobadas por contabilidad", value=config["operations_approved"])
        business = st.text_area("Referencia de aprobacion contable", config["business_evidence"])
        save = st.form_submit_button("Guardar GIO", icon=":material/save:")
    if save:
        try:
            config_store.save_for_user({
                **config, "enabled": enabled, "daily_time": daily.strftime("%H:%M"),
                "recipients": [value.strip() for value in recipients.split(";") if value.strip()],
                "send_enabled": send_enabled, "mapping": mapping, "odoo_import_evidence": odoo_evidence.strip(),
                "operations_approved": approved, "business_evidence": business.strip(),
                "username": "" if clear else username.strip() or config["username"],
                "password": "" if clear else password or config["password"],
            }, role=st.session_state.get("auth_role"))
            st.success("Configuracion cifrada guardada. No se ha conectado al portal ni enviado correo.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        except (OSError, InvalidToken):
            st.error("No se pudo guardar. Revise la clave de cifrado y los permisos.")


def identity():
    return {"role": st.session_state.get("auth_role"), "actor": st.session_state.get("auth_user", "")}


def display_state(row, config):
    if row["state"] == "ready" and not current_result(row, config):
        return "Contrato modificado: revalidar CSV"
    return STATES.get(row["state"], row["state"])


def render_manual(store):
    st.info("Se encola el trabajo para el worker Cloud existente. No se procesa dentro de esta pantalla.")
    with st.form("gio_manual"):
        day = st.date_input("Fecha de ventas", value=previous_day(datetime.now(timezone.utc)), format="DD/MM/YYYY")
        centres = st.multiselect("Centros", list(CENTRES), default=list(CENTRES),
                                format_func=lambda value: CENTRES[value][0])
        originals = {}
        for centre, (name, _, _) in CENTRES.items():
            originals[centre] = st.file_uploader(f"Original opcional {name} (una fecha y centro)", type=["csv"],
                                                key=f"gio_upload_{centre}", max_upload_size=15)
        send = st.checkbox("Solicitar correo al terminar (requiere permitir envios reales en configuracion)")
        queued = st.form_submit_button("Encolar fecha y centros")
    if queued:
        try:
            files = {centre: uploaded.getvalue() for centre, uploaded in originals.items() if uploaded is not None}
            store.enqueue(day, centres, send=send, originals=files, **identity())
            st.success("Solicitud registrada. Los centros ya existentes no se repiten; use Reintentar para recuperarlos.")
        except (ValueError, PermissionError, Timeout) as exc:
            st.error("Hay otro trabajo activo; vuelva a intentarlo." if isinstance(exc, Timeout) else str(exc))


def render_history(store, config):
    st.button("Actualizar estado", icon=":material/refresh:")
    if not config["send_enabled"]:
        st.caption("Envios reales deshabilitados. Las solicitudes de correo quedan pendientes, no se envian.")
    filtered = st.checkbox("Consultar una fecha historica")
    day = st.date_input("Fecha historica", format="DD/MM/YYYY") if filtered else None
    rows = store.history(day.isoformat() if day else None)
    if not rows:
        st.info("Sin ejecuciones GIO.")
        return
    latest = {}
    for row in rows:
        latest.setdefault(row["centre"], row)
    for centre, (name, _, _) in CENTRES.items():
        row = latest.get(centre)
        st.write(f"{name}: {row['day']} - {display_state(row, config)}" if row else f"{name}: sin ejecuciones")
    st.dataframe([{"Fecha": row["day"], "Centro": CENTRES[row["centre"]][0],
                   "Estado": display_state(row, config), "Correo": row["mail_state"],
                   "Intentos descarga": row["attempts"], "Error": row["error"] or row["mail_detail"]}
                  for row in rows], hide_index=True)
    selection = st.selectbox("Resultado", range(len(rows)),
                             format_func=lambda index: f"{rows[index]['day']} - {CENTRES[rows[index]['centre']][0]}")
    row = rows[selection]
    st.json(json.loads(row["summary"]))
    artifacts = store.artifacts(row["day"], row["centre"])
    if artifacts:
        def artifact_label(index):
            artifact = artifacts[index]
            current = current_result(row, config) and artifact["digest"] == row["result"]
            label = ("CSV validado" if current else "CSV candidato o historico (NO importar sin revision)") \
                if artifact["kind"] == "result" else "original"
            return f"{label} - {artifact['digest'][:10]}"
        selected = st.selectbox("Archivo y version", range(len(artifacts)), format_func=artifact_label)
        artifact = artifacts[selected]
        if st.button("Preparar archivo seleccionado", key="gio_prepare_download"):
            st.session_state["gio_prepared_file"] = {
                "digest": artifact["digest"], "content": store.download(artifact["digest"], **identity())}
        prepared = st.session_state.get("gio_prepared_file", {})
        if prepared.get("digest") == artifact["digest"]:
            st.download_button(
                f"Descargar {artifact_label(selected)}", prepared["content"],
                file_name=f"GIO-{row['day']}-{row['centre']}-{artifact['kind']}-{artifact['digest'][:10]}.csv",
                mime="text/csv", key=f"gio_download_{artifact['kind']}_{artifact['digest']}",
            )
    with st.form("gio_retry"):
        correction = st.file_uploader("Original corregido opcional (se conservan todas las versiones)", type=["csv"],
                                      max_upload_size=15)
        retried = st.form_submit_button("Reintentar centro sin reenviar correo")
    if retried:
        try:
            store.retry(row["day"], row["centre"],
                        original=correction.getvalue() if correction else None, **identity())
            st.success("Reintento registrado.")
        except (ValueError, PermissionError, Timeout) as exc:
            st.error("Trabajo activo; reintente despues." if isinstance(exc, Timeout) else str(exc))
    with st.form("gio_resend"):
        confirm = st.checkbox("He comprobado Elementos enviados y acepto el riesgo de duplicado")
        requested = st.form_submit_button("Solicitar envio / reenvio de esta fecha")
    if requested:
        try:
            store.request_send(row["day"], confirm_duplicate_risk=confirm, **identity())
            st.success("Solicitud de correo registrada. Incluira el estado de todos los centros registrados de la fecha.")
        except (ValueError, PermissionError, Timeout) as exc:
            st.error("Trabajo activo; reintente despues." if isinstance(exc, Timeout) else str(exc))
    st.subheader("Trazabilidad de la fecha")
    st.dataframe(store.events(row["day"]), hide_index=True)
