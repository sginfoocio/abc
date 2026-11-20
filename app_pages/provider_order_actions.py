"""Confirmed order actions over the existing provider services."""
from contextlib import closing
from datetime import date
from hashlib import sha256
import json
import sqlite3
import time

import streamlit as st
from cryptography.fernet import InvalidToken
from filelock import Timeout
from sqlalchemy.exc import SQLAlchemyError

from app_pages.provider_order_snapshot import (
    COMPLETE, PROVIDERS, associations, history_date, image_evidence, open_existing,
)
from cloud_dashboard import read_journal
import image_repository
from kering_images import ConfigStore, data_root as kering_root
from kering_images_ui import create_portal, service as kering_service
from kering_jobs import prepare_selection
from provider_images import FAILED, PARTIAL, VALID_VIEWS, RequestStateError, ean_key, ean_valid
from provider_images_ui import STATE_LABELS, build_connector, data_root
from provider_odoo_orders import OdooUnavailable, read_purchase_page
from repository_catalog_readonly import DEFAULT_REQUIRED_VIEWS
from repository_storage import state_root

ASSISTED = {"marcolin", "safilo", "nuorder"}
IMPORTABLE = {"ready", "submitted", "pending", "available", "downloaded", "needs_review"}
ERRORS = (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error, SQLAlchemyError, OdooUnavailable, Timeout,
          InvalidToken, RequestStateError)


def require_confirmation(role, user, confirmed):
    if role != "admin" or not isinstance(user, str) or not user.strip() or confirmed is not True:
        raise PermissionError("Se requiere confirmación de un administrador.")
    return user.strip()


def eligible_lines(order):
    eligible, omitted = {}, []
    for line in order["lines"]:
        code = line["ean"]
        report = line.get("image")
        key = ean_key(code) if ean_valid(code) else None
        reason = ("Identidad no acreditada" if not key or line["barcode_conflict"] else
                  "Vistas completas" if report and report["status"] == COMPLETE else
                  "Cobertura no comprobada" if report is None or report["incomplete"] else
                  "Requiere revisión" if any("Revisión abierta" in s for s in line.get("issues", [])) else
                  "Sin SKU confirmado" if order["provider"] in ASSISTED and not line["sku"] else
                  "Referencia repetida" if key in eligible else "")
        if reason:
            omitted.append({"Línea": line["id"], "EAN/UPC": code, "Motivo": reason})
        else:
            eligible[key] = line
    return eligible, omitted


def identity(order):
    return order["id"], order["name"], order["partner_id"], order["supplier"], order["date_order"]


def line_identity(line):
    return line["id"], line["product_id"], line["ean"], line["sku"], line["quantity"]


def reload_order(engine_factory, order):
    links, _ = associations(data_root(), kering_root())
    day = date.fromisoformat(order["date"])
    rows, _ = read_purchase_page(engine_factory(), links, (order["provider"],), day, day, order_id=order["id"])
    if (not rows or identity(rows[0]) != identity(order) or rows[0]["provider"] != order["provider"]
            or rows[0]["state"] not in {"purchase", "done"}
            or rows[0]["line_count"] != len(rows[0]["lines"]) or rows[0]["order_identity_conflict"]):
        raise ValueError("Pedido fuera del alcance autorizado o identificación modificada.")
    current = rows[0]
    current["date"] = order["date"]
    return current


def revalidate(engine_factory, order, selected, root, *, keep_order=False):
    current = reload_order(engine_factory, order)
    if not selected or not set(selected) <= {line_identity(line) for line in current["lines"]
                                             if not line["barcode_conflict"]}:
        raise ValueError("Las referencias han cambiado; vuelva a consultar el pedido.")
    chosen = [line for line in current["lines"] if line_identity(line) in selected]
    scoped = {**current, "lines": chosen}
    evidence, notice = image_evidence([scoped], state_root(root) / ".catalog.sqlite3", root)
    if notice or any((report := evidence.get(ean_key(line["ean"]))) is None or report["incomplete"]
                     or report["status"] == COMPLETE for line in chosen):
        raise ValueError("La cobertura ha cambiado o no se pudo comprobar; actualice la consulta.")
    return {**current, "selected_line_ids": [line["id"] for line in chosen]} if keep_order else scoped


def prepare_assisted(order, reviewer):
    connector = build_connector(order["provider"])
    items = {line["ean"]: {
        "ean": line["ean"], "sku": line["sku"], "product": line["product"], "supplier": order["supplier"],
        "order": order["name"], "date": order["date_order"][:10], "status": "confirmed",
    } for line in order["lines"]}
    return connector.service.store.plan(connector.service.settings, items, mode="manual",
                                        order_evidence=items, reviewer=reviewer)


def matches_request(order, items, requested):
    expected = {(ean_key(line["ean"]), line["sku"]) for line in order["lines"]
                if ean_valid(line["ean"]) and line["sku"] and not line["barcode_conflict"]}
    requested_keys = {ean_key(code) for code in requested}
    if not requested_keys or len(items) != len(requested_keys):
        return False
    return requested_keys == {ean_key(item["ean"]) for item in items
                              if item["status"] == "confirmed" and item["order_name"] == order["name"]
                              and item["supplier"] == order["supplier"]
                              and item["order_date"] == order["date_order"][:10]
                              and (ean_key(item["ean"]), item["sku"]) in expected}


def linked_requests(order):
    path = data_root() / "provider_requests.sqlite"
    if not path.is_file():
        return []
    with closing(open_existing(path)) as db:
        candidates = db.execute("""
            SELECT DISTINCT r.* FROM requests r JOIN request_odoo_items i ON i.request_id=r.id
            WHERE r.provider=? AND i.order_name=? AND i.supplier=? AND i.order_date=?
            ORDER BY r.id DESC LIMIT 50
        """, (order["provider"], order["name"], order["supplier"], order["date_order"][:10])).fetchall()
        linked = []
        for row in candidates:
            rid = row["id"]
            codes = [item["ean"] for item in db.execute(
                "SELECT ean FROM request_eans WHERE request_id=? ORDER BY ean LIMIT 2001", (rid,))]
            items = db.execute("SELECT * FROM request_odoo_items WHERE request_id=? LIMIT 2001", (rid,)).fetchall()
            if matches_request(order, items, codes):
                linked.append({**dict(row), "codes": codes})
    return linked


def latest_kering(order):
    path = kering_root() / "history.sqlite3"
    if not path.is_file():
        return None
    with closing(open_existing(path)) as db:
        row = db.execute("""
            SELECT a.run_id, a.status, a.error, a.results, a.snapshot, r.started, r.ended
            FROM attempts a JOIN runs r ON r.id=a.run_id
            WHERE a.order_id=? AND a.number=? ORDER BY a.attempt DESC LIMIT 1
        """, (order["id"], order["name"])).fetchone()
        return dict(row) if row else None


def kering_action(baseline, selected):
    if baseline is None:
        return "Procesar Kering"
    if baseline["ended"] is None:
        return None
    if baseline["status"] in {"Error", "Interrumpido"}:
        return "Reintentar Kering"
    previous = json.loads(baseline["snapshot"] or "{}")
    previous_ids = previous.get("selected_line_ids")
    if previous_ids is not None and not {line[0] for line in selected} & set(previous_ids):
        return "Procesar otras referencias Kering"
    return None


def kering_submission(engine_factory, order, selected, root, reviewer, baseline):
    config_store = ConfigStore(kering_root())
    config = config_store.load()
    day = date.fromisoformat(order["date"])
    if config.get("auto_enabled") or not config.get("test_mode") or order["id"] not in config.get("test_order_ids", []):
        raise ValueError("Pedido no autorizado en el piloto de Kering.")
    if not kering_action(baseline, selected):
        raise ValueError("El último intento no permite esta selección.")
    supplier = int(config["supplier_id"])
    batch = kering_service()

    def checked_order():
        current_config = config_store.load()
        if current_config != config:
            raise ValueError("Ha cambiado la configuración del piloto.")
        current = revalidate(engine_factory, order, selected, root, keep_order=True)
        if current["partner_id"] != supplier:
            # The panel may have a broader association than the Kering worker.
            from kering_images import configured_orders

            if not configured_orders(engine_factory(), current_config, day, day, [order["id"]]):
                raise ValueError("El contacto no pertenece al alcance Kering.")
        prepare_selection(batch.store, current_config, [current], day, day)
        return {**current, "supplier_id": current["partner_id"], "cursor": None}

    prepared = checked_order()

    def preflight(_):
        if latest_kering(order) != baseline:
            raise ValueError("Otro operador procesó este pedido. Actualice el estado.")

    return batch.submit_for_user(
        [prepared], reviewer, "admin", supplier, day, day, lambda _: checked_order(),
        portal=create_portal(config), origin="Manual", preflight=preflight,
    )


def _confirm(key, label, execute, form_content=None):
    with st.form(key, clear_on_submit=True):
        value = form_content() if form_content else None
        agreed = st.checkbox("Confirmo la acción y su alcance")
        pressed = st.form_submit_button(label)
    if pressed:
        try:
            reviewer = require_confirmation(
                st.session_state.get("auth_role"), st.session_state.get("auth_user"), agreed)
            execute(reviewer, value)
        except PermissionError:
            st.error("Confirme la acción como administrador.")
        except ERRORS:
            st.error("No se pudo completar la acción. Compruebe el alcance, el estado actual y el historial.")


def _luxottica():
    st.caption("Recibos globales de Graph/correo. No acreditan una entrega para este pedido.")
    for key in ("luxoptica-monitor", "luxoptica-mail", "luxoptica-upload"):
        try:
            status = read_journal(key, time.time())
        except ERRORS:
            st.warning("No se pudieron leer los recibos de este proceso.")
            continue
        if status.last_run is None:
            st.text(f"{status.name}: sin recibos")
            continue
        st.text(f"{status.name} · {history_date(status.last_run)} · {status.result}")
        if status.counts:
            st.dataframe([status.counts], hide_index=True)
        if status.attention:
            st.warning(status.attention)
        if status.diagnostic and status.diagnostic.isascii() and status.diagnostic.replace("_", "").isalnum():
            st.text(f"Código registrado: {status.diagnostic}")


def _assisted_request(order, request, engine_factory, root, allowed):
    rid = request["id"]
    provider = order["provider"]
    path = data_root() / "provider_requests.sqlite"
    with closing(open_existing(path)) as db:
        events = [dict(row) for row in db.execute(
            "SELECT at, to_state FROM events WHERE request_id=? ORDER BY id DESC LIMIT 30", (rid,))]
        reviews = [dict(row) for row in db.execute(
            "SELECT id, name, record_id, ean FROM review_items WHERE request_id=? AND status='open' "
            "ORDER BY id LIMIT 50", (rid,))]
    st.text(f"Solicitud {rid} · {STATE_LABELS.get(request['state'], request['state'])} · "
            f"{len(request['codes'])} referencias")
    st.code(", ".join(request["codes"]), language="text")
    if events:
        st.dataframe([{"Fecha": history_date(event["at"]),
                       "Estado": STATE_LABELS.get(event["to_state"], event["to_state"])}
                      for event in events], hide_index=True)
    if request["last_error"]:
        st.warning("La solicitud registra una incidencia; consulte sus eventos.")

    def connector_checked():
        current = reload_order(engine_factory, order)
        if not any(row["id"] == rid for row in linked_requests(current)):
            raise ValueError("La solicitud no está íntegramente vinculada al pedido.")
        return build_connector(provider)

    if st.button("Preparar listado de la solicitud", key=f"bulk_codes_{rid}"):
        connector = build_connector(provider)
        st.download_button("Descargar listado", connector.export_ean_list(rid), file_name=f"solicitud-{rid}.txt")
        if provider == "marcolin":
            st.download_button("Descargar XLSX Marcolin", connector.export_request_xlsx(rid),
                               file_name=f"marcolin-{rid}.xlsx")
    if (provider == "marcolin" and request["remote_id"]
            and st.button("Ver estado diferido registrado", key=f"pstat_{rid}")):
        groups = build_connector(provider).portal_groups(rid)
        st.dataframe(groups, hide_index=True) if groups else st.info("No hay grupos registrados localmente.")

    if request["state"] in IMPORTABLE:
        st.caption("La importación verifica la correspondencia, conserva originales y deriva ambigüedades a revisión.")

        def import_zip(reviewer, upload):
            if upload is None:
                raise ValueError("Seleccione un ZIP.")
            result = connector_checked().import_zip(rid, upload.getvalue(), reviewer=reviewer)
            st.success(f"Importadas: {result['imported']}; reutilizadas: {result['reused']}; "
                       f"revisión: {result['review']}.")
            if result["errors"]:
                st.warning(f"Archivos con incidencias: {len(result['errors'])}.")

        _confirm(f"zip_confirm_{rid}", "Importar ZIP", import_zip,
                 lambda: st.file_uploader("ZIP descargado del portal", type=["zip"]))
    if request["state"] in {FAILED, PARTIAL} and request["mode"] == "manual" and not request["remote_id"]:
        selected = {line_identity(line) for key, line in allowed.items() if key in
                    {ean_key(code) for code in request["codes"]}}
        if selected:
            def retry(reviewer, _):
                current = revalidate(engine_factory, order, selected, root)
                service = connector_checked().service
                new = service.store.retry(service.settings, rid,
                                          manual_codes={ean_key(line["ean"]) for line in current["lines"]},
                                          reviewer=reviewer)
                st.success(f"Reintento local preparado: {new}.") if new else st.warning(
                    "Reintento no disponible: duplicado, límite o solicitud no recuperable.")

            st.caption("El reintento crea una preparación local; no reenvía solicitudes al portal.")
            _confirm(f"retry_confirm_{rid}", "Preparar reintento local", retry)
    if reviews:
        by_id = {item["id"]: item for item in reviews}
        review_id = st.selectbox("Archivo para revisar", list(by_id), index=None,
                                 format_func=lambda i: by_id[i]["name"], key=f"order_review_{rid}")
        if review_id is None:
            return
        item = by_id[review_id]
        views = (getattr(image_repository, "IMAGE_CLASSIFICATIONS", image_repository.IMAGE_VIEWS)
                 if item["record_id"] else sorted(VALID_VIEWS))

        def fields():
            return (st.selectbox("Decisión", ("Clasificar", "Rechazar")),
                    st.selectbox("EAN/UPC", [item["ean"]] if item["record_id"] else request["codes"]),
                    st.selectbox("Vista", views),
                    st.text_input("Motivo de revisión"))

        def resolve(reviewer, decision):
            action, code, view, reason = decision
            connector = connector_checked()
            with connector.request_lock(rid):
                connector.service.resolve_review(review_id, reviewer=reviewer, reason=reason, ean=code,
                                                 view=view, reject=action == "Rechazar")
            st.success("Revisión registrada.")

        _confirm(f"review_confirm_{review_id}", "Registrar revisión", resolve, fields)


def render_order_actions(order, root, engine_factory):
    if st.session_state.get("auth_role") != "admin":
        return
    key = f"order_operations_{order['id']}"
    if not st.checkbox("Mostrar acciones del proveedor", key=key):
        return
    try:
        _render_actions(order, root, engine_factory, key)
    except ERRORS:
        st.error("No se pudieron leer las acciones o el estado local de este proveedor.")


def _render_actions(order, root, engine_factory, key):
    provider = order["provider"]
    if provider == "luxottica":
        _luxottica()
        return
    if (provider not in ASSISTED | {"kering"} or order["state"] not in {"purchase", "done"}
            or order["order_identity_conflict"] or order["line_count"] != len(order["lines"])):
        st.info("No hay acciones para pedidos incompletos, no confirmados o de identidad ambigua.")
        return
    st.caption(f"{PROVIDERS[provider]} · pedido {order['name']}")
    allowed, omitted = eligible_lines(order)
    chosen = st.multiselect("Referencias pendientes", list(allowed), format_func=lambda code: allowed[code]["ean"],
                            key=f"action_lines_{order['id']}")
    if omitted:
        st.dataframe(omitted, hide_index=True)
    if chosen:
        selected = {line_identity(allowed[key]) for key in chosen}
        selection = sha256(repr(sorted(selected)).encode()).hexdigest()[:16]
        st.dataframe([{"EAN/UPC": allowed[key]["ean"], "Vistas pendientes": ", ".join(allowed[key]["image"]["missing"])}
                      for key in chosen], hide_index=True)
        if provider in ASSISTED:
            st.caption("Crea solicitudes locales para trabajo asistido; no envía ni descarga desde el portal.")

            def plan(reviewer, _):
                result = prepare_assisted(revalidate(engine_factory, order, selected, root), reviewer)
                st.success(f"Solicitudes nuevas: {result['created'] or 'ninguna'}; "
                           f"omitidas por duplicado: {len(result['already_live']) + len(result['already_prepared'])}.")

            _confirm(f"plan_{order['id']}_{selection}", "Preparar procesamiento", plan)
        elif provider == "kering":
            baseline = latest_kering(order)
            if baseline and baseline["ended"] is None:
                st.info("Hay un lote activo. Consulte el resultado antes de iniciar otro.")
            elif action := kering_action(baseline, selected):
                st.caption("Ejecuta solo las referencias seleccionadas, respetando los límites del piloto de Kering.")

                def submit(reviewer, _):
                    run_id = kering_submission(engine_factory, order, selected, root, reviewer, baseline)
                    st.success(f"Lote Kering iniciado: {run_id}")

                previous = baseline["run_id"] if baseline else "first"
                _confirm(f"kering_{order['id']}_{selection}_{previous}", action, submit)
    if provider == "kering":
        latest = latest_kering(order)
        if latest:
            st.text(f"Último lote · {history_date(latest['started'])} · {latest['status']}")
            results = json.loads(latest["results"] or "{}")
            if results:
                st.dataframe([
                    {"EAN/UPC": code, **{
                        view: outcome.get("views", {}).get(view, "—") for view in DEFAULT_REQUIRED_VIEWS
                    }}
                    for code, outcome in results.items() if ean_valid(code)
                ], hide_index=True)
            st.caption("«Actualizar estado» lee el progreso registrado sin contactar el portal.")
            st.button("Actualizar estado", key=f"update_{key}")
    else:
        requests = linked_requests(order)
        if requests:
            by_id = {request["id"]: request for request in requests}
            rid = st.selectbox(
                "Solicitud íntegramente vinculada", list(by_id), index=None, key=f"req_{key}")
            if rid is not None:
                _assisted_request(order, by_id[rid], engine_factory, root, allowed)
        else:
            st.info("Sin solicitudes íntegramente vinculadas al pedido.")
