"""Admin-only GIO configuration and operational history."""
from datetime import datetime, time, timezone
import json
import sqlite3

from cryptography.fernet import InvalidToken
from filelock import Timeout
import streamlit as st

from cloud_design import page_title
from gio_cloud import (
    CENTRES, STATES, GioConfigStore, GioConnectionError, GioStore,
    check_gio_connection, current_result, data_root, prepare_mail_payload,
)
from gio_reports import MADRID, previous_day


def render():
    if st.session_state.get("auth_role") != "admin":
        st.error("GIO requiere permisos de administrador.")
        st.stop()
    page_title("GIO", "Ventas diarias por centro e importacion manual en Odoo")
    try:
        store = GioStore(data_root())
        config = GioConfigStore(data_root()).load()
        render_manual(store)
        st.divider()
        render_history(store, config)
    except (OSError, sqlite3.Error, InvalidToken):
        st.error("No se puede leer GIO. Revise permisos de almacenamiento y la clave de cifrado del servidor.")
    except ValueError as exc:
        st.error(str(exc))


def render_settings():
    st.subheader("GIO")
    st.caption("Configuración protegida con el almacén cifrado existente de proveedores.")
    try:
        config_store = GioConfigStore(data_root())
        config = config_store.load()
    except (OSError, InvalidToken, ValueError) as exc:
        st.error(f"No se puede leer la configuración GIO: {exc}")
        return

    def save_configuration():
        clear_credentials = st.session_state["gio_clear_credentials"]
        mapping = {
            "company": int(st.session_state["gio_odoo_company"]),
            "journals": {centre: int(st.session_state[f"gio_odoo_journal_{centre}"]) for centre in CENTRES},
            "accounts": {
                code: int(st.session_state[f"gio_odoo_account_{code}"])
                for code in config["mapping"]["accounts"]
            },
        }
        updated = {
            **config,
            "portal_url": st.session_state["gio_portal_url"].strip(),
            "username": "" if clear_credentials else st.session_state["gio_username"].strip(),
            "password": "" if clear_credentials else st.session_state["gio_password"] or config["password"],
            "daily_time": st.session_state["gio_daily_time"].strftime("%H:%M"),
            "recipients": [
                value.strip() for value in st.session_state["gio_recipients"].split(";") if value.strip()
            ],
            "enabled": False,
            "send_enabled": False,
            "mapping": mapping,
            "odoo_import_evidence": st.session_state["gio_odoo_evidence"].strip(),
            "operations_approved": st.session_state["gio_business_approved"],
            "business_evidence": st.session_state["gio_business_evidence"].strip(),
        }
        credentials_changed = any(
            updated[key] != config[key] for key in ("portal_url", "username", "password")
        )
        if credentials_changed:
            updated["last_connection_status"] = "not_checked"
            updated["last_connection_check"] = ""
        try:
            config_store.save_for_user(updated, role=st.session_state.get("auth_role"))
        except (ValueError, PermissionError) as exc:
            st.session_state["gio_settings_message"] = ("error", str(exc))
        except (OSError, InvalidToken) as exc:
            st.session_state["gio_settings_message"] = (
                "error", f"No se pudo guardar la configuración cifrada de GIO: {exc}",
            )
        else:
            st.session_state["gio_settings_message"] = (
                "success", "Configuración GIO guardada de forma cifrada. Automatización y envíos siguen desactivados.",
            )
        st.session_state["gio_password"] = ""
        st.session_state["gio_clear_credentials"] = False

    with st.form("gio_settings", clear_on_submit=True):
        st.text_input("URL del portal GIO", value=config["portal_url"], key="gio_portal_url")
        st.text_input("Usuario GIO", value=config["username"], key="gio_username")
        st.text_input(
            "Contraseña GIO", type="password", placeholder="Vacía conserva la contraseña guardada",
            key="gio_password",
        )
        st.checkbox("Eliminar credenciales GIO guardadas", key="gio_clear_credentials")
        st.time_input(
            "Hora de ejecución diaria (Europe/Madrid)",
            value=time.fromisoformat(config["daily_time"]),
            key="gio_daily_time",
        )
        st.text_input(
            "Destinatarios del correo (separados por punto y coma)",
            value="; ".join(config["recipients"]),
            key="gio_recipients",
        )
        st.toggle(
            "Activar automatización diaria",
            value=False,
            disabled=True,
            key="gio_automation_enabled",
            help="Bloqueado hasta validar la descarga autenticada, el CSV, Odoo y la operación completa.",
        )
        st.toggle(
            "Permitir envíos de correo",
            value=False,
            disabled=True,
            key="gio_mail_enabled",
            help="Los envíos reales permanecerán desactivados hasta completar y autorizar su validación.",
        )
        st.subheader("Configuración contable de Odoo")
        st.caption("IDs técnicos de base de datos; no se escribe en Odoo.")
        st.number_input("ID sociedad Odoo", min_value=0, value=config["mapping"]["company"], key="gio_odoo_company")
        for centre, (name, journal, _) in CENTRES.items():
            st.number_input(
                f"ID diario {name} ({journal})", min_value=0,
                value=config["mapping"]["journals"][centre], key=f"gio_odoo_journal_{centre}",
            )
        for code, value in config["mapping"]["accounts"].items():
            st.number_input(
                f"ID cuenta {code}", min_value=0, value=value, key=f"gio_odoo_account_{code}",
            )
        st.text_area(
            "Evidencia de validación del importador Odoo (versión, sociedad, campos y agrupación)",
            config["odoo_import_evidence"],
            key="gio_odoo_evidence",
        )
        st.checkbox(
            "Operaciones y referencias aprobadas por contabilidad",
            value=config["operations_approved"],
            key="gio_business_approved",
        )
        st.text_area(
            "Referencia de aprobación contable", config["business_evidence"], key="gio_business_evidence",
        )
        st.form_submit_button("Guardar configuración", type="primary", on_click=save_configuration)

    save_message = st.session_state.pop("gio_settings_message", None)
    if save_message:
        level, message = save_message
        st.success(message) if level == "success" else st.error(message)

    status_text = {
        "ok": "Correcta: credenciales aceptadas",
        "failed": "Fallida: revisar credenciales o acceso",
        "not_checked": "Sin comprobar",
    }.get(config["last_connection_status"], "Sin comprobar")
    checked_at = config["last_connection_check"]
    if checked_at:
        checked = datetime.fromisoformat(checked_at).astimezone(MADRID).strftime("%d/%m/%Y %H:%M")
        checked_text = f"{checked} Europe/Madrid"
    else:
        checked_text = "Nunca"
    st.caption(f"Última comprobación: {status_text} · {checked_text}")
    st.caption(
        "La prueba usa la configuración guardada. Valida el acceso, no registra una sesión operativa "
        "ni prueba las descargas."
    )
    if st.button(
        "Probar conexión",
        key="gio_test_connection",
        disabled=not config["username"] or not config["password"],
    ):
        try:
            with st.spinner("Comprobando acceso a GIO..."):
                check_gio_connection(config)
        except (GioConnectionError, ValueError) as exc:
            config["last_connection_status"] = "failed"
            message = str(exc)
            success = False
        else:
            config["last_connection_status"] = "ok"
            message = "Credenciales aceptadas. No se registró una sesión GIO operativa."
            success = True
        config["last_connection_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            config_store.save_for_user(config, role=st.session_state.get("auth_role"))
        except (OSError, InvalidToken, ValueError, PermissionError) as exc:
            st.error(f"No se pudo guardar el resultado de la comprobación: {exc}")
        else:
            if success:
                st.success(message)
            else:
                st.error(message)
            st.rerun()


def identity():
    return {"role": st.session_state.get("auth_role"), "actor": st.session_state.get("auth_user", "")}


def display_state(row, config):
    if row["state"] == "blocked":
        issues = json.loads(row["summary"]).get("issues", [])
        if any(issue.get("code", "").startswith("descuadre_") for issue in issues):
            return "Descuadre contable"
        return "Incidencia del CSV"
    if row["state"] in {"download_error", "internal_error"}:
        return "Error técnico"
    if row["state"] == "no_movements":
        return "Sin movimientos"
    if row["state"] == "candidate":
        if not row.get("result"):
            return "Procesado correctamente (configuración contable pendiente)"
        return "Procesado correctamente (CSV candidato; pendiente de validar Odoo)"
    if row["state"] == "ready" and not current_result(row, config):
        return "Contrato modificado: revalidar CSV"
    if row["state"] == "ready":
        return "Procesado correctamente"
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
    summary = json.loads(row["summary"])
    st.json(summary)
    discrepancies = [
        {"Fila": issue.get("row", ""), "Operación": issue.get("operation") or "No identificada",
         "Incidencia": issue["code"], "Diferencia (EUR)": issue.get("difference", "")}
        for issue in summary.get("issues", []) if issue.get("code", "").startswith("descuadre_")
    ]
    if discrepancies:
        st.subheader("Operaciones con descuadre")
        st.dataframe(discrepancies, hide_index=True)

    preview_key = f"gio_mail_preview_{row['day']}"
    with st.expander("Vista previa del correo diario (no se envía)"):
        st.caption("Vista local de asunto, resumen y adjuntos. No registra ni realiza ningún envío.")
        if st.button("Preparar vista previa", key=f"gio_prepare_mail_preview_{row['day']}"):
            st.session_state[preview_key] = True
        if st.session_state.get(preview_key):
            batch = store.batch(row["day"])
            if batch:
                preview = prepare_mail_payload(store, batch, config, include_candidates=True)
                st.write("Destinatarios:", "; ".join(preview["recipients"]) or "No configurados")
                st.subheader(preview["subject"])
                st.markdown(preview["body"], unsafe_allow_html=True)
                if preview["attachments"]:
                    st.write("Adjuntos de la vista previa")
                    for index, (filename, content) in enumerate(preview["attachments"]):
                        st.download_button(
                            f"Descargar {filename}", content, file_name=filename, mime="text/csv",
                            key=f"gio_mail_preview_attachment_{row['day']}_{index}",
                        )
                else:
                    st.info("No hay adjuntos disponibles para esta fecha.")
    artifacts = store.artifacts(row["day"], row["centre"])
    if artifacts:
        def artifact_label(index):
            artifact = artifacts[index]
            current = current_result(row, config) and artifact["digest"] == row["result"]
            if artifact["kind"] == "result":
                label = "CSV validado" if current else "CSV candidato o historico (NO importar sin revision)"
            elif artifact["kind"] == "original_general":
                label = "original general (todas las tiendas)"
            else:
                label = "original por centro"
            return f"{label} - {artifact['digest'][:10]}"
        selected = st.selectbox("Archivo y version", range(len(artifacts)), format_func=artifact_label)
        artifact = artifacts[selected]
        if st.button("Preparar archivo seleccionado", key="gio_prepare_download"):
            st.session_state["gio_prepared_file"] = {
                "digest": artifact["digest"], "content": store.download(artifact["digest"], **identity())}
        prepared = st.session_state.get("gio_prepared_file", {})
        if prepared.get("digest") == artifact["digest"]:
            filename = (f"GIO-{row['day']}-original-general-{artifact['digest'][:10]}.csv"
                        if artifact["kind"] == "original_general"
                        else f"GIO-{row['day']}-{row['centre']}-{artifact['kind']}-{artifact['digest'][:10]}.csv")
            st.download_button(
                f"Descargar {artifact_label(selected)}", prepared["content"],
                file_name=filename,
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
