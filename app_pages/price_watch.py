"""Administration UI of the scalable price watch (phase 1): watch list, groups, authorisations, budgets, blocks.

Opening these screens never performs HTTP requests.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from price_scheduler import CompetitorScheduler
    from price_web import WebPriceSearch

from price_access import (DEFAULT_BUDGETS, INCIDENT_KINDS, METHOD_STATES, METHODS, AccessControl,
                          external_search_enabled)
from price_comparator import PriceComparatorStore, normalize_gtin
from price_locator import (ADAPTERS, JOB_STATES, MAX_DEPTH, SCHEDULE_ENV, SOURCE_KINDS, PriceLocator,
                           schedule_enabled)
from price_monitor import (ALERTS, STATES, SCHEDULE_ENV as MONITOR_ENV, PriceMonitor,
                           schedule_enabled as monitor_enabled)
from price_watch import DEFAULT_GROUP, SOURCES, PriceWatch, WatchReport, eans_from_frame, parse_eans
from vytria_price_sync import STATUS_LABELS, SyncInProgress, VytriaPriceSync
from app_pages.price_options import ordered, product_eans

def _actor() -> str:
    return str(st.session_state.get("auth_user") or st.session_state.get("auth_role") or "admin")


_REVIEW_EXPECTED_FIELDS = (
    "candidate_status", "match_status", "model_code", "color_code", "caliber", "ean_published",
    "candidate_source", "candidate_note", "candidate_verified_at", "job_state", "job_url",
    "job_updated_at", "watch_active", "competitor_active",
)


def _review_expected_candidate(row, *, job_id=None) -> dict:
    def scalar(value):
        if pd.isna(value):
            return None
        return value.item() if hasattr(value, "item") else value

    job_id = row["id"] if job_id is None else job_id
    return {"job_id": scalar(job_id),
            **{name: scalar(row[name]) for name in _REVIEW_EXPECTED_FIELDS}}


def _show_report(report: WatchReport) -> None:
    st.session_state["watch_report"] = report
    st.rerun()


def _refresh(message: str) -> None:
    st.session_state["watch_notice"] = message
    st.rerun()


def _notices() -> None:
    if message := st.session_state.pop("watch_notice", ""):
        st.success(message)
    if report := st.session_state.pop("watch_report", None):
        (st.success if not report.rejected else st.warning)(report.summary)
        if report.rejected:
            st.dataframe(pd.DataFrame(report.rejected, columns=["EAN", "Motivo"]), hide_index=True, width="stretch")
    if report := st.session_state.pop("watch_catalog_report", None):
        st.info(f"Catálogo: {report.imported} nuevos, {report.updated} actualizados, {len(report.rejected)} rechazados.")
        if report.rejected:
            st.dataframe(pd.DataFrame({"Rechazo de catálogo": report.rejected}), hide_index=True, width="stretch")


def _render_watchlist(watch: PriceWatch) -> None:
    counts = watch.counts()
    cols = st.columns(4)
    cols[0].metric("Productos vigilados", counts["watched"])
    cols[1].metric("Selección individual", counts["manual"])
    cols[2].metric("Importación masiva", counts["import"])
    cols[3].metric("Por reglas", counts["rule"])
    st.caption(f"Catálogo del comparador: {counts['catalog']} productos. Al añadir productos se preparan sus "
               "búsquedas en todos los competidores. Utilice Actualizaciones → Lote supervisado para revisar "
               "y ejecutar el circuito sin preparar cada cola por separado.")
    frame = watch.watchlist()
    if frame.empty:
        st.info("Todavía no hay productos en vigilancia.")
        return
    c1, c2 = st.columns(2)
    query = c1.text_input("Filtrar productos vigilados", key="watch_list_query").strip()
    brands = c2.multiselect("Marcas vigiladas", ordered(frame["brand"].unique()), key="watch_list_brands")
    if query:
        text = frame[["ean", "brand", "model", "color"]].fillna("").agg(" ".join, axis=1)
        frame = frame[text.str.contains(query, case=False, regex=False)]
    if brands:
        frame = frame[frame["brand"].isin(brands)]
    st.caption(f"{len(frame)} productos en la selección. Los contadores superiores son totales de vigilancia.")
    shown = frame.assign(source=frame["source"].map(SOURCES))
    st.dataframe(shown[["ean", "brand", "model", "color", "commercial_name", "group_name", "source", "rule_name",
                        "added_by", "added_at"]].rename(columns={
        "ean": "EAN", "brand": "Marca", "model": "Modelo", "color": "Color", "commercial_name": "Nombre",
        "group_name": "Grupo", "source": "Origen", "rule_name": "Regla", "added_by": "Añadido por",
        "added_at": "Fecha"}), hide_index=True, width="stretch")
    labels = {row.ean: f"{row.ean} · {row.brand} {row.model} {row.color}" for row in frame.itertuples()}
    selected = st.multiselect("Productos", product_eans(frame), format_func=labels.get, key="watch_selected")
    groups = ordered(watch.groups()["name"])
    c1, c2, c3 = st.columns([2, 1, 1])
    target = c1.selectbox("Mover al grupo", groups, key="watch_move_group")
    if c2.button("Mover", key="watch_move", disabled=not selected, width="stretch"):
        _refresh(f"{watch.move(selected, target)} productos movidos a «{target}».")
    if c3.button("Retirar de vigilancia", key="watch_remove", disabled=not selected, width="stretch"):
        _refresh(f"{watch.remove(selected, actor=_actor())} productos retirados (el histórico se conserva).")


def _render_add(watch: PriceWatch, store: PriceComparatorStore) -> None:
    groups = ordered(watch.groups()["name"])
    group = st.selectbox("Grupo de destino", groups, index=groups.index(DEFAULT_GROUP), key="watch_add_group")
    st.markdown("**Selección individual**")
    catalog = watch.catalog()
    available = catalog[(catalog["watched"] == 0) & catalog["complete"]]
    labels = {row.ean: f"{row.ean} · {row.brand} {row.model} {row.color} — {row.commercial_name}"
              for row in available.itertuples()}
    picked = st.multiselect("Productos del catálogo", product_eans(available), format_func=labels.get, key="watch_pick",
                            placeholder="Buscar por EAN, marca, modelo o color")
    incomplete = int((~catalog["complete"]).sum())
    if incomplete:
        st.caption(f"{incomplete} productos del catálogo no se ofrecen porque les falta marca, modelo o color.")
    if st.button("Añadir seleccionados", key="watch_add_manual", disabled=not picked):
        _show_report(watch.add(picked, actor=_actor(), group=group, source="manual"))

    st.markdown("**Importación masiva de EAN**")
    text = st.text_area("Pegue EAN (uno por línea, o separados por comas)", key="watch_bulk_text", height=120)
    upload = st.file_uploader("…o suba un CSV/Excel con columna EAN", type=["csv", "xlsx"], key="watch_bulk_file")
    update_catalog = st.checkbox("Actualizar antes el catálogo con las columnas MARCA, MODELO, COLOR… del fichero",
                                 key="watch_bulk_catalog",
                                 help="Usa la importación de catálogo existente; sin esta opción solo se vigilan EAN "
                                      "que ya estén en el catálogo.")
    if st.button("Importar EAN", key="watch_add_bulk", disabled=not (text.strip() or upload)):
        try:
            eans, rejected = parse_eans(text)
            if upload is not None:
                frame = (pd.read_csv(upload, dtype=str) if upload.name.lower().endswith(".csv")
                         else pd.read_excel(upload, dtype=str)).fillna("")
                if update_catalog:
                    catalog_report = store.import_products(frame)
                    st.session_state["watch_catalog_report"] = catalog_report
                file_eans, file_rejected = eans_from_frame(frame)
                eans += [ean for ean in file_eans if ean not in eans]
                rejected += file_rejected
            report = watch.add(eans, actor=_actor(), group=group, source="import")
            report.rejected = rejected + report.rejected
            _show_report(report)
        except ValueError as exc:
            st.error(str(exc))


def _render_rules(watch: PriceWatch) -> None:
    st.caption("Las reglas se aplican de forma explícita: añaden los productos que cumplen los criterios y nunca "
               "retiran productos. Los criterios no distinguen mayúsculas.")
    rules = watch.rules()
    if not rules.empty:
        st.dataframe(rules[["name", "brand", "category", "model_prefix", "text_contains", "group_name", "active",
                            "last_applied_at", "last_matched"]].rename(columns={
            "name": "Regla", "brand": "Marca", "category": "Categoría", "model_prefix": "Modelo empieza por",
            "text_contains": "Texto", "group_name": "Grupo", "active": "Activa", "last_applied_at": "Última aplicación",
            "last_matched": "Coincidencias"}), hide_index=True, width="stretch")
    catalog = watch.catalog()
    with st.form("watch_rule_form"):
        name = st.text_input("Nombre de la regla", key="rule_name")
        c1, c2 = st.columns(2)
        brand = c1.selectbox("Marca", [""] + ordered(catalog["brand"].dropna().unique().tolist()), key="rule_brand")
        category = c2.selectbox("Categoría", [""] + ordered(catalog["category"].dropna().unique().tolist()),
                                key="rule_category")
        model_prefix = c1.text_input("Modelo empieza por", key="rule_model")
        text_contains = c2.text_input("Nombre o modelo contiene", key="rule_text")
        group = st.selectbox("Grupo", ordered(watch.groups()["name"]), key="rule_group")
        active = st.checkbox("Activa", value=True, key="rule_active")
        preview = st.form_submit_button("Previsualizar")
        save = st.form_submit_button("Guardar regla")
    criteria = {"brand": brand, "category": category, "model_prefix": model_prefix, "text_contains": text_contains}
    if preview:
        matches = watch.preview_rule(**criteria)
        st.info(f"{len(matches)} productos del catálogo cumplen los criterios "
                f"({int((matches['watched'] == 0).sum()) if not matches.empty else 0} aún no vigilados).")
        st.dataframe(matches.head(200), hide_index=True, width="stretch")
    if save:
        try:
            watch.save_rule(name, actor=_actor(), group=group, active=active, **criteria)
            _refresh(f"Regla «{name.strip()}» guardada. Aplíquela para añadir sus productos.")
        except ValueError as exc:
            st.error(str(exc))
    if not rules.empty and st.button("Aplicar reglas activas", key="watch_apply_rules"):
        reports = watch.apply_rules(actor=_actor())
        _refresh(" · ".join(f"{name}: {report.summary}" for name, report in reports.items()) or "Sin reglas activas.")


def _render_groups(watch: PriceWatch) -> None:
    st.caption("Frecuencia de monitorización: la del grupo si está definida; si no, la de cada competidor "
               "(Administración → Competidores → Frecuencia de actualización, 24 h por defecto).")
    groups = watch.groups()
    st.dataframe(groups.rename(columns={"name": "Grupo", "interval_hours": "Cada (horas)", "note": "Nota",
                                        "products": "Productos"}), hide_index=True, width="stretch")
    with st.form("watch_group_form"):
        name = st.text_input("Grupo", key="group_name")
        own = st.checkbox("Frecuencia propia del grupo", key="group_own")
        hours = st.number_input("Cada (horas)", 1.0, 720.0, 24.0, 1.0, key="group_hours")
        note = st.text_input("Nota", key="group_note")
        if st.form_submit_button("Guardar grupo"):
            try:
                watch.save_group(name, hours if own else None, note)
                _refresh("Grupo guardado.")
            except ValueError as exc:
                st.error(str(exc))


def _render_methods(access: AccessControl, competitor_id: str | None = None) -> None:
    st.caption("Ningún método está autorizado por defecto: una página pública o un robots.txt permisivo no son "
               "una autorización. robots.txt se sigue comprobando en cada URL aunque el método esté autorizado. "
               "El motor es propio: la búsqueda externa es solo un punto de extensión y está "
               + ("activada en este entorno." if external_search_enabled() else "desactivada."))
    grid = access.methods()
    if competitor_id is not None:
        grid = grid[grid["competitor_id"] == competitor_id]
    matrix = grid.pivot(index="name", columns="method", values="status")[list(METHODS)]
    matrix = matrix.replace(METHOD_STATES).rename(columns=METHODS).rename_axis(columns=None)
    st.dataframe(matrix.rename_axis("Competidor").reset_index(), width="stretch", hide_index=True)
    names = dict(zip(grid["competitor_id"], grid["name"]))
    with st.form("access_method_form"):
        c1, c2, c3 = st.columns(3)
        competitor = (next(iter(names)) if len(names) == 1 else c1.selectbox(
            "Competidor", ordered(names, names.get), format_func=names.get, key="access_competitor"))
        method = c2.selectbox("Método", list(METHODS), format_func=METHODS.get, key="access_method")
        status = c3.selectbox("Decisión", list(METHOD_STATES), format_func=METHOD_STATES.get, key="access_status")
        note = st.text_area("Justificación (condiciones de uso, acuerdo, robots.txt…)", key="access_note")
        evidence = st.text_input("Evidencia (URL de las condiciones, referencia del acuerdo…)", key="access_evidence")
        if st.form_submit_button("Guardar decisión"):
            try:
                access.set_method(competitor, method, status, actor=_actor(), note=note, evidence=evidence)
                _refresh("Decisión registrada en la auditoría.")
            except ValueError as exc:
                st.error(str(exc))
    detail = grid[grid["status"] != "pending"]
    if not detail.empty:
        st.markdown("**Decisiones registradas**")
        st.dataframe(detail.assign(method=detail["method"].map(METHODS), status=detail["status"].map(METHOD_STATES))[
            ["name", "method", "status", "note", "evidence", "decided_by", "decided_at"]],
                     hide_index=True, width="stretch")


def _render_budgets(access: AccessControl, names: dict[str, str]) -> None:
    st.caption(f"Ventana móvil de 24 h. Valor inicial por competidor: {DEFAULT_BUDGETS['competitor']['daily_limit']}"
               f"/día y {DEFAULT_BUDGETS['competitor']['per_minute_limit']}/min. La pausa efectiva de un competidor es "
               "la mayor entre la de su presupuesto y la de su configuración web.")
    budgets = access.budgets()
    if len(names) == 1 and not budgets.empty:
        budgets = budgets[budgets["scope_id"].isin(names) | budgets["scope"].eq("provider")]
    external = external_search_enabled()
    if not external and not budgets.empty:
        budgets = budgets[budgets["scope"] == "competitor"]
    if not budgets.empty:
        shown = budgets.assign(scope_id=budgets.apply(
            lambda row: names.get(row["scope_id"], row["scope_id"]), axis=1))
        st.dataframe(shown[["scope", "scope_id", "daily_limit", "per_minute_limit", "effective_pause", "used_24h",
                            "custom"]].rename(columns={
            "scope": "Ámbito", "scope_id": "Competidor / proveedor", "daily_limit": "Al día",
            "per_minute_limit": "Por minuto", "effective_pause": "Pausa (s)", "used_24h": "Usadas 24 h",
            "custom": "Personalizado"}), hide_index=True, width="stretch")
    scope = "competitor"
    if external:  # optional extension point, hidden while the engine runs only on its own methods
        scope = st.radio("Ámbito", ["competitor", "provider"], horizontal=True, key="budget_scope",
                         format_func={"competitor": "Competidor", "provider": "Proveedor de búsqueda externa"}.get)
    if scope == "competitor":
        scope_id = (next(iter(names)) if len(names) == 1 else st.selectbox(
            "Competidor", ordered(names, names.get), format_func=names.get, key="budget_cid"))
    else:
        scope_id = st.text_input("Identificador del proveedor", key="budget_provider").strip().lower()
    if not scope_id:
        return
    try:
        current = access.budget(scope, scope_id)
    except ValueError as exc:
        st.error(str(exc))
        return
    with st.form("budget_form"):
        c1, c2, c3 = st.columns(3)
        daily = c1.number_input("Consultas al día", 0, 100_000, int(current["daily_limit"]), key="budget_daily")
        minute = c2.number_input("Consultas por minuto", 1, 600, int(current["per_minute_limit"]), key="budget_minute")
        pause = c3.number_input("Pausa mínima (s)", 0.0, 3600.0, float(current["pause_seconds"] or 0.0), 0.5,
                                key="budget_pause")
        if st.form_submit_button("Guardar presupuesto"):
            try:
                access.set_budget(scope, scope_id, daily_limit=int(daily), per_minute_limit=int(minute),
                                  pause_seconds=float(pause) or None, actor=_actor())
                _refresh("Presupuesto guardado y auditado.")
            except ValueError as exc:
                st.error(str(exc))


def _render_incidents(access: AccessControl, names: dict[str, str]) -> None:
    st.caption("CAPTCHA, 401/403/407 o 429 suspenden el competidor o proveedor afectado. Un 429 se reanuda solo tras "
               "el plazo indicado; el resto exige una decisión humana documentada. Nunca se intenta superar una "
               "protección: reanudar solo procede si el acceso se ha acordado o el bloqueo ya no aplica.")
    incidents = access.incidents()
    if len(names) == 1 and not incidents.empty:
        incidents = incidents[incidents["scope_id"].isin(names)]
    if incidents.empty:
        st.success("Sin incidencias de bloqueo registradas.")
        return
    shown = incidents.assign(scope_id=incidents["scope_id"].map(lambda value: names.get(value, value)),
                             kind=incidents["kind"].map(lambda value: INCIDENT_KINDS.get(value, value)))
    st.dataframe(shown[["id", "scope", "scope_id", "kind", "method", "url", "detected_at", "retry_after", "status",
                        "resolved_by", "resolution_note"]], hide_index=True, width="stretch")
    open_rows = incidents[incidents["status"] == "open"]
    if open_rows.empty:
        return
    with st.form("incident_form"):
        incident = st.selectbox("Incidencia abierta", open_rows["id"].tolist(), key="incident_id")
        note = st.text_area("Motivo para reanudar", key="incident_note")
        if st.form_submit_button("Marcar como resuelta"):
            try:
                access.resolve_incident(int(incident), actor=_actor(), note=note)
                _refresh("Incidencia cerrada y auditada.")
            except ValueError as exc:
                st.error(str(exc))


def _render_saved_reevaluation(store: PriceComparatorStore) -> None:
    from filelock import Timeout
    from price_saved_reevaluation import SavedSampleReevaluation

    evidence = Path(__file__).resolve().parents[1] / "docs" / "Precios" / "100_EAN" / \
        "Paquete_revision_7_casos_20261117.zip"
    with st.expander("Reevaluación local de muestra guardada"):
        st.caption("Sin HTTP: revalida una solicitud histórica, conserva su fecha y no renueva muestras caducadas.")
        if not evidence.is_file():
            st.error("Paquete histórico no disponible.")
            return
        request_id = st.number_input("ID de solicitud manual", min_value=1, step=1, key="saved_reeval_request")
        if st.button("Previsualizar reevaluación", key="saved_reeval_preview"):
            try:
                st.session_state["saved_reeval_preview"] = SavedSampleReevaluation(
                    store, evidence).preview(int(request_id))
            except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
                st.error(str(exc))
        preview = st.session_state.get("saved_reeval_preview")
        if preview:
            st.json(preview)
            with st.form("saved_reeval_apply"):
                actor = st.text_input("Responsable", value=_actor(), key="saved_reeval_actor")
                note = st.text_input("Motivo", key="saved_reeval_note")
                approved = st.checkbox("Confirmo reevaluación local sin HTTP", key="saved_reeval_confirm")
                commit = st.text_input("Versión/commit de 40 caracteres", key="saved_reeval_commit")
                submitted = st.form_submit_button("Aplicar reevaluación auditada")
            if submitted:
                try:
                    if not approved:
                        raise ValueError("Confirmación expresa obligatoria")
                    result = SavedSampleReevaluation(store, evidence).apply(
                        int(request_id), actor=actor, note=note, build_commit=commit,
                        approved=True)
                    st.json(result)
                    st.session_state.pop("saved_reeval_preview", None)
                except (ValueError, Timeout, TimeoutError, sqlite3.Error) as exc:
                    st.error(str(exc))


def _render_locator(store: PriceComparatorStore, names: dict[str, str], *, advanced: bool = True) -> None:
    """Review tray and optional advanced locator tools.

    The review mechanics stay shared; advanced queue, limits and diagnostics are only shown to
    the settings page that opts into them.
    """
    locator = PriceLocator(store)
    if advanced:
        st.caption("Motor propio: busca cada producto vigilado por marca + modelo + código de color en las fuentes "
                   "autorizadas (URL ya verificada, feed, sitemap, páginas de marca/catálogo, buscador interno), "
                   "guarda un índice local reutilizable y confirma solo coincidencias exactas. Sin servicios externos.")
        c1, c2, c3, c4 = st.columns([1.2, 1.5, 1, 1])
        if c1.button("Preparar cola", key="locate_enqueue", width="stretch",
                     help="Crea las búsquedas pendientes de los productos vigilados (sin consultas HTTP)."):
            result = locator.enqueue()
            st.success(f"{result['created']} búsquedas nuevas · {result['relocate']} relocalizaciones · "
                       f"{result['reopened']} reabiertas tras cambios de autorización.")
        target = c2.selectbox("Competidor", ["Todos", *ordered(names, names.get)], key="locate_competitor",
                              format_func=lambda value: names.get(value, value))
        max_jobs = c3.number_input("Máx. productos", 1, 2000, 50, key="locate_max_jobs")
        deadline = c4.number_input("Plazo (min)", 1, 120, 10, key="locate_deadline")
        if st.button("Procesar lote", key="locate_run", type="primary",
                     help="Consulta solo métodos autorizados, dentro de presupuestos, pausas y robots.txt."):
            with st.spinner("Localizando fichas…"):
                report = locator.run(None if target == "Todos" else [target], max_jobs=int(max_jobs),
                                     deadline_seconds=float(deadline) * 60)
            st.success(report.summary)
            for cid, reason in report.stopped.items():
                st.warning(f"{names.get(cid, cid)}: {reason}")

        status = locator.status()
        totals = status.groupby("state")["jobs"].sum() if not status.empty else pd.Series(dtype=int)
        cols = st.columns(5)
        for col, (label, keys) in zip(cols, [
            ("Verificadas", ("done",)),
            ("Pendientes", ("pending", "running", "waiting")),
            ("Revisión manual", ("review",)),
            ("Sin acceso / bloqueadas", ("unavailable", "blocked")),
            ("Sin ficha / fallidas", ("not_found", "failed")),
        ]):
            col.metric(label, int(sum(totals.get(key, 0) for key in keys)))
        if not status.empty:
            pivot = status.assign(state=status["state"].map(lambda s: JOB_STATES.get(s, s))).pivot_table(
                index="competitor", columns="state", values="jobs", aggfunc="sum", fill_value=0)
            st.dataframe(pivot, width="stretch")

    review = locator.jobs(states=("review",), limit=None)
    st.markdown(f"**Revisión manual** ({len(review)})")
    if report := st.session_state.pop("manual_review_action_results", None):
        st.dataframe(pd.DataFrame(report), hide_index=True, width="stretch")
    if review.empty:
        st.caption("No hay candidatas pendientes de revisión manual.")
    else:
        if st.button("Restablecer filtros", key="review_reset_filters"):
            for key in ("review_competitor_filter", "review_brand_filter", "review_model_filter",
                        "review_ean_filter", "review_color_filter", "review_type_filter",
                        "review_reason_filter", "review_sort_column", "review_sort_direction"):
                st.session_state.pop(key, None)
            st.session_state["manual_review_selected_ids"] = []
            st.session_state["manual_review_grid_revision"] = (
                int(st.session_state.get("manual_review_grid_revision", 0)) + 1)
            st.session_state.pop("manual_review_bulk_preview", None)
            st.rerun()
        identity_fields = ["id", "ean", "competitor_id", "url", "detail", "updated_at",
                           *_REVIEW_EXPECTED_FIELDS]
        candidate_signature = hashlib.sha256(json.dumps(
            review[identity_fields].astype("string").fillna("").to_dict("records"),
            ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        c1, c2 = st.columns(2)
        competitors = c1.multiselect("Competidores", ordered(review["competitor"].unique()),
                                    key="review_competitor_filter")
        brands = c2.multiselect("Marcas", ordered(review["brand"].unique()), key="review_brand_filter")
        c1, c2, c3 = st.columns(3)
        model_query = c1.text_input("Modelo o referencia", key="review_model_filter").strip()
        ean_query = c2.text_input("EAN/UPC", key="review_ean_filter").strip()
        color_query = c3.text_input("Código de color", key="review_color_filter").strip()
        c1, c2, c3 = st.columns([1.2, 1.5, 1])
        review_types = c1.multiselect("Tipo de revisión", ordered(review["kind"].fillna("").unique()),
                                      key="review_type_filter")
        reason_query = c2.text_input("Motivo de revisión", key="review_reason_filter").strip()
        sort_column = c3.selectbox(
            "Ordenar por", ["updated_at", "ean", "brand", "model", "color", "competitor", "kind", "detail"],
            format_func=lambda value: {"updated_at": "Fecha", "ean": "EAN", "brand": "Marca",
                                       "model": "Modelo", "color": "Color", "competitor": "Competidor",
                                       "kind": "Tipo", "detail": "Motivo"}[value],
            key="review_sort_column")
        ascending = st.selectbox("Dirección", ["Descendente", "Ascendente"],
                                 key="review_sort_direction") == "Ascendente"
        filter_signature = hashlib.sha256(json.dumps({
            "competitors": sorted(competitors), "brands": sorted(brands), "model": model_query,
            "ean": ean_query, "color": color_query, "types": sorted(review_types), "reason": reason_query,
            "sort": sort_column, "ascending": ascending,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        previous_filter = st.session_state.get("review_filter_signature")
        previous_candidates = st.session_state.get("review_candidates_signature")
        selection_reset = bool((previous_filter and previous_filter != filter_signature)
                               or (previous_candidates and previous_candidates != candidate_signature))
        if selection_reset:
            st.session_state["manual_review_selected_ids"] = []
            st.session_state.pop("manual_review_bulk_preview", None)
            st.session_state["manual_review_grid_revision"] = (
                int(st.session_state.get("manual_review_grid_revision", 0)) + 1)
            st.warning("Cambió el filtro o el conjunto de candidatas. Se limpió la selección y la vista previa.")
        st.session_state["review_filter_signature"] = filter_signature
        st.session_state["review_candidates_signature"] = candidate_signature

        filtered = review.copy()
        if competitors:
            filtered = filtered[filtered["competitor"].isin(competitors)]
        if brands:
            filtered = filtered[filtered["brand"].isin(brands)]
        for value, column in ((model_query, "model"), (ean_query, "ean"), (color_query, "color")):
            if value:
                # Cast to text before matching so leading zeroes in EAN/UPC are never lost.
                filtered = filtered[filtered[column].fillna("").astype(str).str.contains(
                    value, case=False, regex=False)]
        if review_types:
            filtered = filtered[filtered["kind"].isin(review_types)]
        if reason_query:
            reason = filtered["detail"].fillna("").astype(str) + " " + filtered["kind"].fillna("").astype(str)
            filtered = filtered[reason.str.contains(reason_query, case=False, regex=False)]
        filtered = filtered.sort_values(sort_column, ascending=ascending, kind="stable", na_position="last")

        selected_ids = set(st.session_state.get("manual_review_selected_ids", []))
        st.session_state["manual_review_selected_ids"] = [
            int(value) for value in selected_ids if int(value) in set(review["id"].astype(int))]
        metrics = st.columns(3)
        metrics[0].metric("Total de revisiones", len(review))
        metrics[1].metric("Resultados filtrados", len(filtered))
        metrics[2].metric("Filas seleccionadas", len(st.session_state["manual_review_selected_ids"]))
        grid = filtered[["id", "ean", "brand", "model", "color", "competitor", "kind", "detail", "url",
                         "updated_at"]].copy()
        grid.insert(0, "Seleccionar", grid["id"].astype(int).isin(
            st.session_state["manual_review_selected_ids"]))
        grid = grid.rename(columns={
            "id": "ID", "ean": "EAN", "brand": "Marca", "model": "Modelo / referencia", "color": "Color",
            "competitor": "Competidor", "kind": "Tipo", "detail": "Motivo / evidencia", "url": "URL",
            "updated_at": "Fecha"})
        revision = int(st.session_state.get("manual_review_grid_revision", 0))
        grid_key = f"manual_review_grid_{filter_signature[:10]}_{candidate_signature[:10]}_{revision}"
        edited = st.data_editor(
            grid, key=grid_key, hide_index=True, width="stretch", num_rows="fixed",
            disabled=[column for column in grid.columns if column != "Seleccionar"],
            column_config={
                "Seleccionar": st.column_config.CheckboxColumn("Seleccionar", default=False),
                "URL": st.column_config.LinkColumn("URL"),
            })
        if "Seleccionar" in edited:
            st.session_state["manual_review_selected_ids"] = [
                int(row_id) for row_id in edited.loc[edited["Seleccionar"], "ID"].tolist()]
        selected_ids = set(st.session_state["manual_review_selected_ids"])
        controls = st.columns(2)
        if controls[0].button(f"Seleccionar resultados filtrados ({len(filtered)})",
                              key="review_select_filtered", disabled=filtered.empty):
            st.session_state["manual_review_selected_ids"] = filtered["id"].astype(int).tolist()
            st.session_state["manual_review_grid_revision"] = revision + 1
            st.session_state.pop("manual_review_bulk_preview", None)
            st.rerun()
        if controls[1].button("Limpiar selección", key="review_clear_selection", disabled=not selected_ids):
            st.session_state["manual_review_selected_ids"] = []
            st.session_state["manual_review_grid_revision"] = revision + 1
            st.session_state.pop("manual_review_bulk_preview", None)
            st.rerun()

        if st.button("Preparar vista previa de seleccionadas", key="review_prepare_bulk",
                     disabled=not selected_ids):
            indexed = review.set_index("id")
            snapshots = {int(job_id): _review_expected_candidate(indexed.loc[job_id], job_id=job_id)
                         for job_id in sorted(selected_ids) if job_id in indexed.index}
            st.session_state["manual_review_bulk_preview"] = {
                "filter_signature": filter_signature, "candidate_signature": candidate_signature,
                "snapshots": snapshots,
            }
        preview = st.session_state.get("manual_review_bulk_preview")
        if preview and (preview["filter_signature"] != filter_signature
                        or preview["candidate_signature"] != candidate_signature):
            st.session_state.pop("manual_review_bulk_preview", None)
            preview = None
            st.warning("La vista previa quedó obsoleta; vuelva a seleccionar y previsualizar las fichas.")
        if preview:
            preview_rows = review[review["id"].astype(int).isin(preview["snapshots"])]
            st.markdown(f"**Vista previa: {len(preview_rows)} fichas seleccionadas**")
            st.caption("La acción afecta únicamente a las filas marcadas en esta vista previa, "
                       "no a otras fichas ocultas ni a todos los resultados del filtro.")
            st.dataframe(preview_rows[["ean", "brand", "model", "color", "competitor", "url"]].rename(
                columns={"ean": "EAN", "brand": "Marca", "model": "Modelo", "color": "Color",
                         "competitor": "Competidor", "url": "URL"}), hide_index=True, width="stretch",
                column_config={"URL": st.column_config.LinkColumn("URL")})
            with st.form("review_bulk_confirm_form"):
                st.text_input("Responsable", value=_actor(), disabled=True, key="review_bulk_actor")
                bulk_note = st.text_input("Motivo de confirmación", key="review_bulk_note")
                confirmed = st.checkbox(
                    f"Confirmo expresamente este conjunto de {len(preview_rows)} fichas",
                    key="review_bulk_confirmed")
                apply_bulk = st.form_submit_button("Confirmar seleccionadas y solicitar lectura")
                discard_bulk = st.form_submit_button("Descartar seleccionadas")
            if apply_bulk or discard_bulk:
                if not bulk_note.strip() or not confirmed or preview_rows.empty:
                    st.error("Indique el motivo y confirme expresamente el conjunto previsualizado.")
                else:
                    results = []
                    indexed = review.set_index("id")
                    for job_id, expected in preview["snapshots"].items():
                        row = indexed.loc[job_id]
                        try:
                            if discard_bulk:
                                locator.review(
                                    row["ean"], row["competitor_id"], row["url"], accept=False,
                                    actor=_actor(), note=bulk_note, update_price=False,
                                    expected_candidate=expected)
                                results.append({"ID": job_id, "EAN": row["ean"], "Competidor": row["competitor"],
                                                "Resultado": "Ficha descartada; sin consulta HTTP ni lectura"})
                            else:
                                refresh_id = locator.review(
                                    row["ean"], row["competitor_id"], row["url"], accept=True,
                                    actor=_actor(), note=bulk_note, update_price=True,
                                    expected_candidate=expected)
                                results.append({"ID": job_id, "EAN": row["ean"], "Competidor": row["competitor"],
                                                "Resultado": f"Ficha aceptada; lectura {refresh_id} en cola"})
                        except (ValueError, sqlite3.Error) as exc:
                            results.append({"ID": job_id, "EAN": row["ean"], "Competidor": row["competitor"],
                                            "Resultado": f"Excluida: {exc}"})
                    st.session_state["manual_review_action_results"] = results
                    st.session_state["manual_review_selected_ids"] = []
                    st.session_state.pop("manual_review_bulk_preview", None)
                    st.session_state["manual_review_grid_revision"] = revision + 1
                    st.rerun()

        filtered_ids = filtered["id"].astype(int).tolist()
        individual_ids = filtered_ids
        if individual_ids:
            indexed = review.set_index("id")
            labels = {job_id: (f"{indexed.loc[job_id, 'brand']} · {indexed.loc[job_id, 'model']} · "
                               f"{indexed.loc[job_id, 'color']} · {indexed.loc[job_id, 'competitor']} · "
                               f"{indexed.loc[job_id, 'ean']}") for job_id in individual_ids}
            with st.form("locate_review_form"):
                job_id = st.selectbox("Ficha para confirmar o descartar", individual_ids,
                                      format_func=lambda value: labels[value], key="locate_review_job")
                note = st.text_input("Motivo de la decisión individual", key="locate_review_note")
                accept, reject = st.columns(2)
                decision = ("accept" if accept.form_submit_button("Confirmar ficha y actualizar precio") else
                            "reject" if reject.form_submit_button("Descartar ficha") else "")
            if decision:
                row = indexed.loc[job_id]
                try:
                    refresh_id = locator.review(
                        row["ean"], row["competitor_id"], row["url"], accept=decision == "accept",
                        actor=_actor(), note=note, update_price=decision == "accept",
                        expected_candidate=_review_expected_candidate(row, job_id=job_id))
                    st.session_state["manual_review_action_results"] = [{
                        "ID": int(job_id), "EAN": row["ean"], "Competidor": row["competitor"],
                        "Resultado": (f"Ficha aprobada · lectura {refresh_id} en cola"
                                      if decision == "accept" else "Ficha descartada"),
                    }]
                except (ValueError, sqlite3.Error) as exc:
                    st.session_state["manual_review_action_results"] = [{
                        "ID": int(job_id), "EAN": row["ean"], "Competidor": row["competitor"],
                        "Resultado": f"No aplicada: {exc}",
                    }]
                st.session_state["manual_review_selected_ids"] = []
                st.session_state.pop("manual_review_bulk_preview", None)
                st.session_state["manual_review_grid_revision"] = revision + 1
                st.rerun()

    if advanced:
        _render_saved_reevaluation(store)

    st.markdown("**Progreso persistente de confirmaciones y lecturas**")
    monitor = PriceMonitor(store)
    progress = monitor.review_refreshes()
    if progress.empty:
        st.caption("Todavía no hay solicitudes de lectura por revisión manual.")
    else:
        st.caption("La tabla muestra las últimas 100 solicitudes; el CSV incluye el inventario completo. "
                   "Importe la columna EAN como texto para conservar los ceros iniciales.")
        st.download_button("Exportar todas las solicitudes de lectura (CSV)",
                           monitor.review_refreshes(limit=None).to_csv(index=False).encode("utf-8-sig"),
                           "solicitudes-lectura-manual.csv", "text/csv", key="review_refresh_export")
        progress = progress.copy()
        def _refresh_status(row):
            if row["status"] == "done":
                return ("Lectura terminada · consultar vigencia y comparador"
                        if pd.notna(row["eligible"]) and bool(row["eligible"])
                        else "Lectura terminada · no comparable")
            if row["status"] == "deferred":
                return f"Aplazado · próximo intento {row['retry_at'] or 'pendiente'}"
            return {
                "pending": "Ficha aprobada · esperando lectura",
                "running": "Lectura en curso",
                "blocked": "Aprobada · bloqueada",
                "error": "Error técnico / identidad: revisar el detalle",
            }.get(row["status"], row["status"])

        progress["Resultado"] = progress.apply(_refresh_status, axis=1)
        st.dataframe(progress[["id", "ean", "brand", "model", "color", "competitor", "url", "actor",
                              "reason", "Resultado", "detail", "http_requests", "requested_at", "retry_at"]]
                     .rename(columns={"id": "Lectura", "ean": "EAN", "brand": "Marca", "model": "Modelo",
                                      "color": "Color", "competitor": "Competidor", "url": "URL",
                                      "actor": "Responsable", "reason": "Motivo", "detail": "Detalle",
                                      "http_requests": "Peticiones registradas", "requested_at": "Solicitada",
                                      "retry_at": "Reintento más próximo"}),
                     hide_index=True, width="stretch", column_config={"URL": st.column_config.LinkColumn("URL")})

    if advanced:
        with st.expander("Últimas búsquedas"):
            recent = locator.jobs(limit=200)
            if recent.empty:
                st.caption("Sin búsquedas todavía.")
            else:
                st.dataframe(recent.assign(state=recent["state"].map(lambda s: JOB_STATES.get(s, s)))[
                    ["ean", "brand", "model", "color", "competitor", "kind", "state", "method", "url", "detail",
                     "updated_at"]], hide_index=True, width="stretch")
        with st.expander("Índice local de fichas"):
            st.caption("Fichas y enlaces ya descubiertos: se reutilizan entre productos y ejecuciones. Las páginas de "
                       "marca/catálogo solo se vuelven a consultar cuando caducan.")
            st.dataframe(locator.index_summary().rename(columns={
                "competitor": "Competidor", "indexed": "Enlaces indexados", "checked": "Fichas comprobadas",
                "pages": "Páginas de listado", "last_refresh": "Última actualización", "sources": "Fuentes activas"}),
                hide_index=True, width="stretch")
        _render_sources(locator, names)
        _render_locate_schedule(locator, names)


def _render_sources(locator: PriceLocator, names: dict[str, str]) -> None:
    with st.expander("Fuentes de descubrimiento (marca, colección, categoría, feed)"):
        st.caption("Listados del propio competidor. Solo se usan si el método correspondiente (catálogo o feed) "
                   "está autorizado y robots.txt lo permite. Variable admitida: {brand}. Se leen de lo más "
                   "concreto a lo más amplio (marca → colección → categoría) y solo se pasa al nivel siguiente si "
                   "el anterior no dio candidatos. Los sublistados se siguen únicamente si cumplen el patrón y "
                   "nombran una marca vigilada, hasta la profundidad indicada.")
        sources = locator.sources()
        sources = sources[sources["competitor_id"].isin(names)] if not sources.empty else sources
        if not sources.empty:
            st.dataframe(sources.assign(kind=sources["kind"].map(lambda k: SOURCE_KINDS.get(k, k)))[
                ["competitor", "kind", "url_template", "follow_pattern", "max_depth", "adapter", "refresh_hours",
                 "max_pages", "active", "note", "created_by"]], hide_index=True, width="stretch")
        with st.form("locate_source_form"):
            s1, s2 = st.columns([1, 1])
            cid = s1.selectbox("Competidor", ordered(names, names.get), format_func=names.get,
                               key="locate_source_competitor")
            kind = s2.selectbox("Clase de listado", list(SOURCE_KINDS), key="locate_source_kind",
                                format_func=SOURCE_KINDS.get)
            template = st.text_input("URL", placeholder="https://tienda.example/marca/{brand}",
                                     key="locate_source_url")
            f1, f2, f3 = st.columns([2, 1, 1])
            follow = f1.text_input("Sublistados a seguir (patrón de ruta)", placeholder=r"^/gafas-de-sol/[a-z-]+$",
                                   key="locate_source_follow")
            depth = f2.number_input("Profundidad", 0, MAX_DEPTH, 0, key="locate_source_depth")
            adapter = f3.selectbox("Adaptador", ordered(ADAPTERS, lambda name: ADAPTERS[name].label), key="locate_source_adapter",
                                   format_func=lambda name: ADAPTERS[name].label)
            s3, s4, s5 = st.columns(3)
            refresh = s3.number_input("Caducidad (h)", 1, 24 * 90, 168, key="locate_source_refresh")
            pages = s4.number_input("Máx. páginas", 1, 200, 10, key="locate_source_pages")
            active = s5.checkbox("Activa", value=True, key="locate_source_active")
            note = st.text_input("Nota", key="locate_source_note")
            if st.form_submit_button("Guardar fuente"):
                try:
                    locator.save_source(cid, "feed" if kind == "feed" else "catalog", template, actor=_actor(),
                                        note=note, refresh_hours=float(refresh), max_pages=int(pages), active=active,
                                        kind=kind, follow_pattern=follow, max_depth=int(depth), adapter=adapter)
                    _refresh("Fuente guardada.")
                except ValueError as exc:
                    st.error(str(exc))


def _render_locate_schedule(locator: PriceLocator, names: dict[str, str]) -> None:
    with st.expander("Planificación periódica de la localización"):
        if schedule_enabled():
            st.info("Planificación activa en este servidor: se ejecutan los competidores habilitados.")
        else:
            st.warning(f"Planificación desactivada en este servidor ({SCHEDULE_ENV} no activo): aunque se "
                       "habilite un competidor, no se hará ninguna consulta periódica.")
        plan = locator.schedule()
        plan = plan[plan["competitor_id"].isin(names)]
        st.dataframe(plan.drop(columns=["competitor_id"]).rename(columns={
            "competitor": "Competidor", "enabled": "Activa", "interval_hours": "Cada (h)", "max_jobs": "Máx. productos",
            "deadline_minutes": "Plazo (min)", "next_run_at": "Próxima", "last_run_at": "Última",
            "last_summary": "Resultado"}), hide_index=True, width="stretch")
        cid = (next(iter(names)) if len(names) == 1 else st.selectbox(
            "Competidor", ordered(names, names.get), format_func=names.get, key="locate_schedule_competitor"))
        with locator.store._connect() as con:
            saved = con.execute("SELECT * FROM locate_schedule WHERE competitor_id=?", (cid,)).fetchone()
        current = dict(saved) if saved else {"interval_hours": 24, "max_jobs": 200,
                                             "deadline_minutes": 15, "enabled": 0}
        if plan.empty:
            st.info("Competidor inactivo: puede editar su plan, pero no se ejecutará mientras siga inactivo.")
        if st.session_state.get("locate_plan_cid") != cid:
            for key in ("locate_schedule_interval", "locate_schedule_jobs", "locate_schedule_deadline",
                        "locate_schedule_enabled"):
                st.session_state.pop(key, None)
            st.session_state["locate_plan_cid"] = cid
        with st.form("locate_schedule_form"):
            p2, p3 = st.columns(2)
            p4, p5 = st.columns(2)
            interval = p2.number_input("Cada (h)", 1.0, 720.0, float(current["interval_hours"]), key="locate_schedule_interval")
            jobs = p3.number_input("Máx. productos", 1, 5000, int(current["max_jobs"]), key="locate_schedule_jobs")
            deadline = p4.number_input("Plazo (min)", 1.0, 240.0, float(current["deadline_minutes"]), key="locate_schedule_deadline")
            enabled = p5.checkbox("Activa", value=bool(current["enabled"]), key="locate_schedule_enabled")
            if st.form_submit_button("Guardar planificación"):
                try:
                    locator.configure_schedule(cid, enabled=enabled, interval_hours=float(interval),
                                               max_jobs=int(jobs), deadline_minutes=float(deadline), actor=_actor())
                    _refresh("Planificación guardada y auditada.")
                except ValueError as exc:
                    st.error(str(exc))
        runs = locator.runs(20)
        runs = runs[runs["competitor_id"].isin(names)] if not runs.empty else runs
        if not runs.empty:
            st.dataframe(runs[["competitor", "trigger", "status", "started_at", "finished_at", "processed",
                               "requests", "summary"]], hide_index=True, width="stretch")


def _render_monitor(store: PriceComparatorStore, names: dict[str, str], *, advanced: bool = True) -> None:
    import price_analytics as analytics
    from price_monitor import schedule_enabled

    monitor = PriceMonitor(store)
    sync = VytriaPriceSync(store)
    with st.expander("Precio Vytria y ciclo diario"):
        st.caption("Fuente operativa: Odoo tarifa 59, ES/EUR con IVA. Contraste con el precio publicado en "
                   "PrestaShop pendiente. Vigencia máxima de los precios Vytria vigilados: 24 h.")
        daily = st.toggle("Sincronizar Vytria antes de la vigilancia diaria", value=sync.auto_enabled,
                          key="watch_vytria_daily")
        if daily != sync.auto_enabled:
            sync.set_auto_enabled(daily)
        st.caption("Esta planificación periódica y la continuidad vigente son mecanismos separados. "
                   "Su estado no impide solicitar aquí una lectura manual autorizada.")
        if advanced:
            st.caption(
                f"Diagnóstico: planificación periódica general "
                f"{'activa' if schedule_enabled() else 'desactivada'} "
                "(PRICE_MONITOR_SCHEDULE_ENABLED); la localización usa su propia planificación."
            )
        approved_sync = st.checkbox("Autorizo la lectura supervisada de Odoo para los EAN vigilados",
                                    key="watch_vytria_approved")
        if st.button("Sincronizar solo Vytria vigilados", disabled=not approved_sync or sync.is_running(),
                     key="watch_vytria_sync"):
            try:
                with st.spinner("Leyendo tarifa 59 de los productos vigilados…"):
                    result = sync.run(trigger="watch:manual", watched_only=True)
                if result.status == "error":
                    st.error(result.error)
                else:
                    st.success(f"Vytria: {result.status} · {result.monitored} EAN · {result.issues} incidencias")
            except SyncInProgress as exc:
                st.warning(str(exc))
        st.caption("Objetivo inicial: 50 EAN frente a Visio Factory, Edel-Optics, Amevista, Óptica Óptima, "
                   "Domanci y Óptica Bassol. Seleccione los EAN en Productos vigilados y habilite únicamente "
                   "los métodos y competidores con autorización documentada. No hay un límite de 50.")
    st.caption("Solo fichas verificadas de productos vigilados. No repite búsquedas. Identidad: marca + modelo + "
               "color; calibre no vinculante. Solo se comparan ofertas disponibles con mercado ES, EUR e IVA "
               "incluido confirmados. Caducidad: un intervalo del grupo o competidor (24 h por defecto).")
    from price_trial import continuity_row
    continuity = continuity_row(store)
    if continuity and continuity["status"] == "active":
        st.caption("Vigilancia de continuidad activa. La programación periódica general no gobierna esta selección.")
    elif continuity and continuity["status"] == "paused":
        st.warning("Vigilancia de continuidad pausada. Las consultas periódicas están detenidas; las acciones manuales siguen sujetas a autorización.")
    elif continuity and continuity["status"] == "stopped":
        st.warning("Vigilancia de continuidad detenida. Las consultas manuales no pueden iniciarse hasta reanudarla.")
    elif not monitor_enabled():
        st.caption("La programación periódica general está desactivada. Puede usar una consulta manual autorizada.")
    cid = st.selectbox("Competidor", ordered(names, names.get), format_func=names.get,
                       key="monitor_competitor")
    if advanced:
        c1, c2 = st.columns(2)
        maximum = c1.number_input("Máx. fichas del lote", 1, 5000, 10, key="monitor_max_jobs")
        deadline = c2.number_input("Plazo del lote (min)", 1, 120, 5, key="monitor_deadline")
        approved = st.checkbox("He revisado autorización, fuentes y presupuesto para este lote",
                               key="monitor_approved")
        if st.button("Preparar monitorización (sin HTTP)", key="monitor_enqueue"):
            st.success(f"{monitor.enqueue([cid])} fichas añadidas a la cola.")
        if st.button("Actualizar precios: lote supervisado", key="monitor_run", disabled=not approved):
            with st.spinner("Actualizando fichas verificadas…"):
                report = monitor.run([cid], max_jobs=int(maximum), deadline_seconds=float(deadline) * 60)
            st.success(report.summary)
            for target, reason in report.stopped.items():
                st.warning(f"{names.get(target, target)}: {reason}")

    frame = monitor.inventory()
    if frame.empty:
        st.info("Añada productos a vigilancia y localice sus fichas para comenzar.")
    else:
        brands = st.multiselect("Marcas", ordered(frame["brand"].unique()), key="monitor_brands")
        status = st.multiselect("Estados", frame["status"].unique().tolist(), key="monitor_status")
        query = st.text_input("Buscar EAN, modelo o color", key="monitor_query").strip()
        shown = frame[frame["competitor_id"] == cid]
        if brands:
            shown = shown[shown["brand"].isin(brands)]
        if status:
            shown = shown[shown["status"].isin(status)]
        if query:
            matched = shown[["ean", "brand", "model", "color"]].astype(str).agg(" ".join, axis=1)
            shown = shown[matched.str.contains(query, case=False, regex=False)]
        date_issues = int(shown["date_issue"].ne("").sum())
        if date_issues:
            st.warning(f"{date_issues} registros sin fecha de vigencia válida en la selección. Se conservan las "
                       "observaciones, pero no se utilizan en la comparación. Revise sus estados; "
                       "no es necesario repetir el lote.")
        cols = st.columns(2)
        for i, label in enumerate(["Actualizado", "Precio caducado", "Sin stock", "Sin precio", "Sin localizar"]):
            cols[i % 2].metric(label, int(shown["status"].eq(label).sum()))
        st.caption(f"Selección: {len(shown)} pares · Total global: {len(frame)} pares, "
                   f"{int(frame['status'].eq('Actualizado').sum())} actualizados. "
                   "Los contadores superiores respetan competidor, marca, estado y búsqueda.")
        shown = shown.assign(own_price_status=shown["own_price_status"].map(
            lambda value: STATUS_LABELS.get(value, value) if value else "Sin precio sincronizado"))
        st.dataframe(shown[["ean", "brand", "model", "color", "competitor", "own_price_eur", "price", "best_price",
                            "difference_eur", "difference_pct", "availability", "currency", "includes_tax",
                            "own_price_source", "own_price_checked_at", "own_price_status",
                            "last_ok_at", "status", "url", "provenance", "interval_hours"]].rename(columns={
            "ean": "EAN interno", "brand": "Marca", "model": "Modelo", "color": "Color",
            "own_price_source": "Origen Vytria", "own_price_checked_at": "Última lectura Vytria",
            "own_price_status": "Estado Vytria",
            "competitor": "Competidor", "own_price_eur": "Vytria €", "price": "Precio observado",
            "best_price": "Mejor competidor global €", "difference_eur": "Vytria − competidor €",
            "difference_pct": "Diferencia % sobre Vytria", "availability": "Stock", "currency": "Moneda",
            "includes_tax": "IVA incluido", "last_ok_at": "Última lectura", "status": "Estado", "url": "Ficha",
            "provenance": "Procedencia de la ficha", "interval_hours": "Frecuencia efectiva (h)"}),
            hide_index=True, width="stretch", column_config={"Ficha": st.column_config.LinkColumn("Ficha")})
        st.caption("La tabla conserva precios observados no comparables, pero deja sus diferencias en blanco. "
                   "La comparativa general y los KPIs usan el mismo histórico, excluyendo precios no vigentes.")
        from price_batch import observation_export

        observations = observation_export(store)
        observations = observations[observations["ean"].isin(shown["ean"]) &
                                    observations["competitor"].eq(names[cid])]
        st.download_button("Exportar observaciones detalladas (CSV)",
                           observations.to_csv(index=False).encode("utf-8-sig"), "observaciones.csv", "text/csv",
                           key="monitor_observations_export")
        st.caption("Incluye observaciones históricas de los productos filtrados; origen, ficha, método, "
                   "estado de identidad, IVA y fechas originales. No ejecuta consultas HTTP.")
        with st.expander("Evolución histórica del producto"):
            ean = st.selectbox("EAN", product_eans(shown), key="monitor_history_ean")
            series = analytics.price_series(store, ean, 30) if ean else pd.DataFrame()
            if series.empty:
                st.info("Todavía no hay observaciones históricas.")
            else:
                st.plotly_chart(analytics.price_figure(series), width="stretch", key="monitor_history")
        latest = frame.assign(last_ok_at=frame["last_ok_at"].fillna("")).groupby(
            "competitor", as_index=False)["last_ok_at"].max()
        st.caption("Última lectura correcta por competidor (global).")
        st.dataframe(latest.rename(columns={"competitor": "Competidor", "last_ok_at": "Última lectura correcta"}),
                     hide_index=True, width="stretch")
    if not advanced:
        return
    with st.expander("Trabajos de monitorización"):
        jobs = monitor.jobs()
        if jobs.empty:
            st.caption("Sin trabajos.")
        else:
            st.dataframe(jobs.assign(state=jobs["state"].map(STATES))[
                ["ean", "competitor", "state", "attempts", "not_before", "last_ok_at", "detail"]],
                hide_index=True, width="stretch")
        if st.button("Reintentar trabajos fallidos de este competidor", key="monitor_retry"):
            st.success(f"{monitor.retry_failed(cid, actor=_actor())} trabajos reabiertos, sin consultas todavía.")
    with st.expander("Frecuencia y planificación de monitorización"):
        with store._connect() as con:
            config = con.execute("SELECT * FROM monitor_schedule WHERE competitor_id=?", (cid,)).fetchone()
            hours = con.execute("SELECT interval_hours FROM competitor_schedule WHERE competitor_id=?",
                                (cid,)).fetchone()
        with st.form("monitor_schedule_form"):
            interval = st.number_input("Cada (horas), salvo frecuencia del grupo", 1.0, 720.0,
                                       float(hours[0] if hours else 24), key=f"monitor_hours_{cid}")
            enabled = st.checkbox("Habilitar este competidor para planificación",
                                  value=bool(config and config["enabled"]), key=f"monitor_enabled_{cid}")
            if st.form_submit_button("Guardar monitorización"):
                try:
                    monitor.configure(cid, enabled=enabled, interval_hours=interval, max_jobs=int(maximum),
                                      actor=_actor())
                    st.success("Configuración guardada. No se activa sin el interruptor del servidor.")
                except ValueError as exc:
                    st.error(str(exc))
    st.caption("Bloqueos, autorizaciones y consumo HTTP se consultan y gestionan en los apartados existentes "
               "«Autorizaciones por método», «Presupuestos» y «Bloqueos e incidencias».")


def _render_alerts(store: PriceComparatorStore) -> None:
    monitor = PriceMonitor(store)
    st.info("Resumen por correo preparado; envío automático desactivado. No se envía ningún mensaje.")
    with st.form("monitor_alert_threshold"):
        threshold = st.number_input("Umbral de cambio de precio (%)", 0.1, 100.0,
                                    float(store.get_setting("alert_threshold_pct")), key="monitor_threshold")
        if st.form_submit_button("Guardar umbral"):
            try:
                store.set_setting("alert_threshold_pct", str(threshold))
                st.success("Umbral guardado.")
            except ValueError as exc:
                st.error(str(exc))
    alerts = monitor.alerts()
    if alerts.empty:
        st.caption("Sin alertas registradas.")
    else:
        st.dataframe(alerts.assign(kind=alerts["kind"].map(ALERTS))[
            ["at", "ean", "brand", "model", "color", "competitor", "kind", "old_value", "new_value", "detail"]],
            hide_index=True, width="stretch")
    st.download_button("Descargar resumen para correo", monitor.email_summary(), file_name="resumen-vigilancia.txt",
                       mime="text/plain", key="monitor_email")


ADMIN_SECTIONS = ("Resumen", "Productos", "Tiendas", "Por revisar", "Vigilancia y consumos")
DIAGNOSTIC_SECTIONS = (
    "Ajustes técnicos", "Importaciones", "Históricos y auditoría", "Colas y recuperaciones",
    "Búsqueda manual (avanzada)",
)


def _render_overview(store: PriceComparatorStore) -> None:
    monitor = PriceMonitor(store)
    watch = PriceWatch(store).watchlist()
    inventory = monitor.inventory()
    with store._connect() as con:
        competitors = con.execute("SELECT COUNT(*) FROM competitors WHERE active=1").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM locate_jobs WHERE state IN ('pending','waiting','running')").fetchone()[0]
        pending += con.execute("SELECT COUNT(*) FROM monitor_jobs WHERE state IN ('pending','waiting','running')").fetchone()[0]
    metrics = [
        ("Productos vigilados", len(watch)), ("Competidores activos", competitors),
        ("Fichas verificadas", int(inventory["located"].sum()) if not inventory.empty else 0),
        ("Precios vigentes", int(inventory["status"].eq("Actualizado").sum()) if not inventory.empty else 0),
        ("Precios caducados", int(inventory["status"].eq("Precio caducado").sum()) if not inventory.empty else 0),
        ("Trabajos pendientes / en ejecución", pending),
    ]
    for offset in range(0, len(metrics), 2):
        cols = st.columns(2)
        for col, (label, value) in zip(cols, metrics[offset:offset + 2]):
            col.metric(label, value)
    st.caption("Lectura local. Abrir la Administración no prepara lotes, no consulta competidores "
               "ni activa la planificación o el correo.")
    if not inventory.empty:
        # Empty dates must be normalized before aggregating mixed pandas object columns.
        coverage = inventory.assign(last_ok_at=inventory["last_ok_at"].fillna("")).groupby(
            "competitor", as_index=False).agg(productos=("ean", "count"), fichas=("located", "sum"),
                                              ultima_consulta=("last_ok_at", "max"))
        st.dataframe(coverage.rename(columns={"competitor": "Competidor", "productos": "Vigilados",
                                             "fichas": "Fichas verificadas", "ultima_consulta": "Último éxito"}),
                     hide_index=True, width="stretch")
        st.markdown("#### Precios de Vytria")
        own = inventory.drop_duplicates("ean").copy()
        own["own_price_status"] = own["own_price_status"].map(
            lambda value: STATUS_LABELS.get(value, value) if value else "Sin precio sincronizado")
        st.dataframe(own[["ean", "own_price_eur", "own_price_status", "own_price_checked_at", "own_price_source"]].rename(
            columns={"ean": "EAN", "own_price_eur": "Vytria €", "own_price_status": "Estado",
                     "own_price_checked_at": "Última lectura", "own_price_source": "Origen"}),
            hide_index=True, width="stretch")
    alerts = monitor.alerts(limit=10)
    incidents = AccessControl(store).incidents()
    st.caption(f"Alertas recientes: {len(alerts)} · Incidencias abiertas: "
               f"{int(incidents['status'].eq('open').sum()) if not incidents.empty else 0}.")
    if not alerts.empty:
        st.dataframe(alerts, hide_index=True, width="stretch")


def _render_store_urls(store: PriceComparatorStore, cid: str) -> None:
    from price_monitor import PriceMonitor
    from price_web import WebPriceSearch, validate_competitor_url

    competitor = store.competitors().set_index("id").loc[cid]
    st.subheader("URLs y fichas")
    with store._connect() as connection:
        urls = pd.read_sql_query(
            "SELECT ean,url,status,source,note,submitted_at,verified_at FROM competitor_product_urls "
            "WHERE competitor_id=? ORDER BY ean,url",
            connection,
            params=(cid,),
        )
    if urls.empty:
        st.info("Todavía no hay URLs guardadas para esta tienda.")
    else:
        st.dataframe(urls.rename(columns={
            "ean": "EAN", "url": "URL", "status": "Estado", "source": "Origen",
            "note": "Nota", "submitted_at": "Guardada", "verified_at": "Verificada",
        }), hide_index=True, width="stretch",
            column_config={"URL": st.column_config.LinkColumn("URL")})
    watched = PriceWatch(store).watchlist()
    options = watched["ean"].tolist() if not watched.empty else []
    if not options:
        st.info("Añada primero los productos que desea vigilar.")
        return
    ean = st.selectbox("Producto", options, key=f"store_url_ean_{cid}")
    url = st.text_input("URL candidata o sustituta", key=f"store_url_value_{cid}",
                        placeholder="https://...")
    replace = st.checkbox("Sustituir las URL actuales de este producto",
                          value=False, key=f"store_url_replace_{cid}",
                          help="Conserva las URL anteriores como histórico y marca la sustitución.")
    if st.button("Guardar URL", key=f"store_url_save_{cid}", disabled=not url.strip()):
        try:
            normalized = normalize_gtin(ean)
            search = WebPriceSearch(store)
            config = search.config(cid)
            validated = validate_competitor_url(url.strip(), config["domain"])
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with store._connect() as connection, connection:
                connection.execute("""INSERT INTO competitor_product_urls(
                    competitor_id,ean,url,match_status,status,verified_at,source,note,submitted_at
                ) VALUES(?,?,?,'manual_review','pending_verification','','user',
                    'Aportada manualmente; pendiente de verificar',?)
                ON CONFLICT(competitor_id,ean,url) DO UPDATE SET status='pending_verification',
                    note=excluded.note,submitted_at=excluded.submitted_at""",
                                   (cid, normalized, validated, stamp))
                if replace:
                    connection.execute("""UPDATE competitor_product_urls SET status='replaced'
                        WHERE competitor_id=? AND ean=? AND url<>? AND status IN
                        ('verified','probable','pending_verification','needs_relocation')""",
                                       (cid, normalized, validated))
                connection.execute("""INSERT INTO access_audit(
                    scope,scope_id,action,previous,current,actor,note,at
                ) VALUES('competitor_url',?,?,'',?,'user','URL guardada sin confirmar identidad ni oferta',?)""",
                                   (f"{cid}:{normalized}", "saved",
                                    "Sustituida" if replace else "Añadida", stamp))
            st.success("URL guardada como candidata. Esta acción no confirma identidad, oferta, stock ni autorización.")
        except (ValueError, sqlite3.Error) as exc:
            st.error(str(exc))

    verified = urls[(urls["ean"] == normalize_gtin(ean)) & urls["status"].eq("verified")]
    verified_urls = verified["url"].drop_duplicates().tolist()
    if len(verified_urls) > 1:
        selected_url = st.selectbox("Ficha confirmada para leer", verified_urls,
                                    key=f"store_url_verified_{cid}")
    else:
        selected_url = verified_urls[0] if verified_urls else None
    if not verified_urls:
        st.info("Esta URL aún no está confirmada. Al confirmar la identidad en «Por revisar», "
                "el trabajador solicitará la lectura prioritaria automáticamente.")
    if st.button("Solicitar lectura de ficha confirmada", key=f"store_url_request_{cid}",
                 disabled=not selected_url):
        try:
            normalized = normalize_gtin(ean)
            refresh = PriceMonitor(store).enqueue([cid], [normalized], exact_url=selected_url)
            st.info(f"Solicitud registrada: {refresh}. El trabajador decidirá reutilización, espera o bloqueo.")
        except (ValueError, sqlite3.Error) as exc:
            st.error(str(exc))


def render_competitor_card(store: PriceComparatorStore, cid: str, search: WebPriceSearch,
                           scheduler: CompetitorScheduler, states_frame: pd.DataFrame) -> None:
    from app_pages.price_comparator import _render_competitor_panels

    competitor = store.competitors().set_index("id").loc[cid]
    names = {cid: str(competitor["name"])}
    access = AccessControl(store)
    if st.session_state.get("competitor_card_cid") != cid:
        for key in list(st.session_state):
            if key.startswith(("access_", "budget_", "locate_source_", "locate_schedule_")):
                st.session_state.pop(key, None)
        st.session_state["competitor_card_cid"] = cid
    st.caption("Una ficha por competidor. Los cambios no autorizan otros métodos ni activan ejecuciones.")
    with st.expander("Ficha del competidor"):
        st.write(f"{competitor['name']} · {competitor['domain']} · "
                 f"{competitor['market'] or 'Mercado pendiente'} / {competitor['currency'] or 'Moneda pendiente'}")
        with st.form(f"competitor_active_{cid}"):
            active = st.checkbox("Competidor activo en el comparador", value=bool(competitor["active"]),
                                 key=f"competitor_active_value_{cid}")
            if st.form_submit_button("Guardar estado activo"):
                try:
                    record = {"id": cid, **competitor.to_dict(), "active": int(active)}
                    store.save_competitors(pd.DataFrame([record]))
                    _refresh("Estado guardado. No se han modificado permisos ni planificaciones.")
                except ValueError as exc:
                    st.error(str(exc))
    task = st.selectbox("Configuración del competidor", [
        "URLs y fichas", "Acceso y métodos", "Fuentes y conectores", "Presupuestos y límites",
        "Frecuencia de actualización", "Incidencias y bloqueos",
    ], key="competitor_card_section")
    if task == "URLs y fichas":
        _render_store_urls(store, cid)
    elif task == "Acceso y métodos":
        _render_methods(access, cid)
    elif task == "Fuentes y conectores":
        _render_sources(PriceLocator(store), names)
        with st.expander("Conector web, mercado, IVA y conector anterior (avanzado)"):
            _render_competitor_panels(search, scheduler, states_frame, cid)
    elif task == "Presupuestos y límites":
        _render_budgets(access, names)
    elif task == "Incidencias y bloqueos":
        _render_incidents(access, names)
    elif task == "Frecuencia de actualización":
        with store._connect() as con:
            plan = con.execute("SELECT * FROM monitor_schedule WHERE competitor_id=?", (cid,)).fetchone()
            interval = con.execute("SELECT interval_hours FROM competitor_schedule WHERE competitor_id=?", (cid,)).fetchone()
        st.caption("Monitorización: ficha verificada; localización: fichas nuevas. "
                   "La frecuencia efectiva utiliza la sobrescritura del producto/grupo cuando existe.")
        with st.form("competitor_monitor_plan"):
            hours = st.number_input("Intervalo de monitorización (horas)", min_value=1.0, max_value=720.0,
                                    value=float(interval[0] if interval else 24))
            enabled = st.checkbox("Planificación habilitada para este competidor", value=bool(plan and plan["enabled"]))
            if st.form_submit_button("Guardar frecuencia"):
                try:
                    PriceMonitor(store).configure(cid, interval_hours=hours, enabled=enabled,
                                                  max_jobs=plan["max_jobs"] if plan else 200, actor=_actor())
                    _refresh("Frecuencia guardada. No se ha ejecutado ningún lote.")
                except ValueError as exc:
                    st.error(str(exc))
        _render_locate_schedule(PriceLocator(store), names)


def _render_trial_history(store: PriceComparatorStore) -> None:
    from price_trial import trial_row

    row = trial_row(store)
    if row is None:
        st.info("No hay una ejecución histórica del piloto de 72 horas.")
        return
    with store._connect() as con:
        cycles = [dict(item) for item in con.execute(
            "SELECT * FROM price_trial_cycles ORDER BY cycle").fetchall()]
        pairs = [dict(item) for item in con.execute(
            "SELECT cycle,competitor_id,ean,state,started_at,finished_at,next_check_at,receipt "
            "FROM price_trial_pairs ORDER BY cycle,competitor_id,ean").fetchall()]
        audits = [dict(item) for item in con.execute(
            "SELECT id,actor,action,at,note FROM access_audit WHERE scope='price_trial' AND scope_id='1' "
            "ORDER BY id").fetchall()]
    for pair in pairs:
        pair["receipt"] = json.loads(pair["receipt"] or "{}")
    history = {
        "status": row["status"], "started_at": row["started_at"], "expires_at": row["expires_at"],
        "stopped_at": row["stopped_at"], "manifest": json.loads(row["manifest"]),
        "cycles": cycles, "pairs": pairs, "audit": audits,
    }
    st.caption("Solo consulta histórica. No activa, reanuda ni detiene trabajos, y no realiza consultas.")
    st.write({key: history[key] for key in ("status", "started_at", "expires_at", "stopped_at")})
    if pairs:
        pair_frame = pd.DataFrame(pairs)
        st.dataframe(pair_frame.groupby(["state"], as_index=False).size().rename(
            columns={"state": "Estado", "size": "Pares"}), hide_index=True)
    st.download_button("Descargar histórico del piloto (JSON)",
                       json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8"),
                       "piloto-72h-historial.json", "application/json", key="trial_history_export")


def _render_continuity(store: PriceComparatorStore, service=None) -> None:
    from filelock import Timeout
    from build_info import load_build_info
    from price_trial import PriceTrial, continuity_config

    service = service or PriceTrial(store)
    with st.expander("Continuidad de vigilancia · misma selección, activación independiente"):
        config = continuity_config()
        report = service.continuity_report()
        frozen = report["manifest"] if report else config["manifest"]
        config = {**config, "manifest": frozen}
        st.caption(
            ("Configuración preparada y desactivada. " if report is None else "Selección persistida de continuidad. ")
            + f"100 EAN × {len(frozen['competitors'])} tiendas. "
            "La sincronización Vytria/Odoo es solo de lectura y queda limitada a esos 100 EAN. "
            "El SHA mostrado corresponde al manifiesto original de origen."
        )
        st.write({
            "manifest_sha256": config["manifest_sha256"],
            "responsable": "obligatorio al preparar y al activar; no se asigna una identidad por suposición",
            "frecuencia de ofertas verificadas": f"{config['offer_check_interval_hours']} horas",
            "frecuencia de sincronización": f"{config['interval_hours']} horas",
            "presupuesto móvil": f"{config['daily_http_limit']} HTTP / 24 h por competidor",
            "límite por minuto": config["per_minute_limit"],
            "pausa efectiva mínima": f"{config['minimum_pause_seconds']} segundos",
            "controles": config["controls"],
            "inicio": "instante de activación, persistido en UTC y Madrid; no hay consultas mientras esté preparada",
        })
        st.json({"eans": frozen["eans"], "competitors": frozen["competitors"], "pairs": frozen["pairs"]})
        st.download_button(
            "Descargar configuración exacta de continuidad",
            json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
            "price-watch-continuity.json", "application/json", key="continuity_config_download",
        )
        if report is None:
            try:
                prepared = service.continuity_preflight()
            except ValueError as exc:
                st.warning(str(exc))
                return
            st.dataframe(pd.DataFrame([
                {"competitor_id": cid, **budget} for cid, budget in prepared["limits"].items()
            ]), hide_index=True)
            actor = st.text_input("Responsable de la preparación", value=config["responsible_default"] or "",
                                  key="continuity_prepare_actor")
            if st.button("Registrar configuración preparada (sin consultas)", key="continuity_prepare"):
                try:
                    service.prepare_continuity(actor=actor)
                    _refresh("Configuración persistida como preparada; no se ha activado ni consultado ninguna tienda.")
                except (ValueError, RuntimeError, Timeout) as exc:
                    st.error(str(exc))
            return
        st.write({name: report[name] for name in (
            "status", "actor", "build_commit", "prepared_at_madrid", "started_at_madrid",
            "paused_at_madrid", "stopped_at_madrid", "heartbeat_at_madrid", "next_sync_at_madrid", "error",
        )})
        st.dataframe(pd.DataFrame([report["summary"]]), hide_index=True)
        st.download_button(
            "Descargar informe de continuidad",
            json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            "price-watch-continuity-report.json", "application/json", key="continuity_report_download",
        )
        if report["status"] == "prepared":
            info = load_build_info()
            actor = st.text_input("Responsable de la activación", value=report["actor"] or "",
                                  key="continuity_activate_actor")
            approved = st.checkbox(
                "Autorizo iniciar ahora la continuidad exacta descrita arriba",
                key="continuity_activation_approval",
            )
            if st.button("Activar continuidad", key="continuity_activate",
                         disabled=not approved or not info["build"].startswith("actions-")):
                try:
                    service.activate_continuity(actor=actor, build_commit=info["commit"])
                    _refresh("Continuidad activada por aprobación explícita. El trabajador empezará el ciclo persistido.")
                except (ValueError, RuntimeError, Timeout) as exc:
                    st.error(str(exc))
        elif report["status"] == "active":
            actor = st.text_input("Responsable del control", value=_actor(), key="continuity_control_actor")
            if st.button("Pausar continuidad", key="continuity_pause"):
                service.pause_continuity(actor=actor)
                _refresh("Continuidad pausada; estado y próximas fechas conservados.")
            if st.button("Detener continuidad", key="continuity_stop"):
                service.stop_continuity(actor=actor)
                _refresh("Continuidad detenida; historial y recibos conservados.")
            if len(report["manifest"]["competitors"]) == 6:
                with st.expander("Ampliar a 14 competidores · mismos 100 EAN"):
                    try:
                        expansion = service.continuity_expansion_preflight()
                    except ValueError as exc:
                        st.warning(str(exc))
                    else:
                        st.caption(
                            "Añade 800 pares a los 600 existentes. Conserva estados, revisiones, recibos y fechas. "
                            "Usa el trabajador actual, con límites de 200 HTTP/24 h, 10/min y pausa mínima de 3 s. "
                            "No habilita otros planificadores ni realiza consultas durante la ampliación. "
                            "Los bloqueos y robots siguen aplicándose por ruta. El calibre no cambia el precio."
                        )
                        st.json({"competitors": expansion["manifest"]["competitors"],
                                 "distinct_eans": 100, "selected_pairs": 1400})
                        info = load_build_info()
                        with st.form("continuity_expand_form"):
                            expansion_actor = st.text_input("Responsable de la ampliación", value=_actor())
                            expansion_note = st.text_input("Motivo de la ampliación")
                            expansion_approved = st.checkbox("Autorizo ampliar ahora los mismos 100 EAN a las 14 tiendas")
                            submitted = st.form_submit_button(
                                "Activar ampliación a 14 tiendas",
                                disabled=not info["build"].startswith("actions-"))
                        if submitted:
                            try:
                                service.expand_continuity(actor=expansion_actor, approved=expansion_approved,
                                                          build_commit=info["commit"], note=expansion_note)
                                _refresh("Ampliación activada y auditada: 100 EAN × 14 tiendas; trabajador existente.")
                            except (ValueError, RuntimeError, Timeout) as exc:
                                st.error(str(exc))
        elif report["status"] == "paused":
            actor = st.text_input("Responsable del control", value=_actor(), key="continuity_resume_actor")
            if st.button("Reanudar continuidad", key="continuity_resume"):
                service.resume_continuity(actor=actor)
                _refresh("Continuidad reanudada desde las fechas persistidas.")
            if st.button("Detener continuidad", key="continuity_stop_paused"):
                service.stop_continuity(actor=actor)
                _refresh("Continuidad detenida; historial y recibos conservados.")
        if report["status"] in ("active", "paused"):
            _render_interrupted_recovery(store)
            _render_selected_recovery(store, report["id"])
        if report["status"] in ("active", "paused"):
            _render_continuity_capture(service, frozen, active=report["status"] == "active")
        if report["status"] == "active" and report["summary"]["restricted_pairs"]:
            key = f"continuity_reevaluation_{report['id']}"
            with st.expander("Reevaluación auditada de pares restringidos"):
                st.caption(
                    "La vista previa solo lee el inventario y los permisos locales; no consulta tiendas ni "
                    "consume presupuesto. Solo reprograma pares restringidos con candidato y acceso vigentes. "
                    "No modifica revisiones, recibos ni históricos."
                )
                if st.button("Previsualizar pares reprogramables", key=key + "_preview"):
                    try:
                        st.session_state[key] = service.reevaluate_restricted_pairs()
                    except (ValueError, RuntimeError, Timeout) as exc:
                        st.error(str(exc))
                preview = st.session_state.get(key)
                if preview is not None:
                    st.dataframe(pd.DataFrame([{
                        "EAN": item["ean"], "Competidor": item["competitor_id"],
                        "Reprogramable": item["eligible"], "Candidatas": " · ".join(item["candidate_urls"]),
                        "Motivo": item["reason"], "Próxima comprobación": item["next_check_at"],
                    } for item in preview]), width="stretch", hide_index=True)
                    eligible = any(item["eligible"] for item in preview)
                    with st.form(key + "_apply"):
                        actor = st.text_input("Responsable de la reevaluación", value=_actor(),
                                              key=key + "_actor")
                        note = st.text_input("Motivo de la reevaluación", key=key + "_note")
                        confirmed = st.checkbox("Confirmo reprogramar solo los pares elegibles",
                                                key=key + "_confirm")
                        submitted = st.form_submit_button(
                            "Aplicar reevaluación auditada", disabled=not eligible, key=key + "_submit")
                    if submitted:
                        if not confirmed:
                            st.warning("Confirma la reevaluación antes de aplicar cambios.")
                        elif not actor.strip():
                            st.warning("Indica un responsable para la reevaluación.")
                        elif not note.strip():
                            st.warning("Indica el motivo de la reevaluación.")
                        else:
                            try:
                                result = service.reevaluate_restricted_pairs(
                                    actor=actor, note=note, apply=True)
                                if any(item.get("applied", False) for item in result):
                                    st.session_state.pop(key, None)
                                    _refresh(
                                        "Reevaluación auditada; ningún par fue consultado durante la acción.")
                                else:
                                    st.warning(
                                        "No hay pares elegibles actualmente; no se aplicaron cambios.")
                            except (ValueError, RuntimeError, Timeout) as exc:
                                st.error(str(exc))
        if report["pairs"]:
            st.dataframe(pd.DataFrame(report["pairs"]), width="stretch", hide_index=True)


def _render_interrupted_recovery(store: PriceComparatorStore) -> None:
    import hashlib
    import sqlite3

    from filelock import Timeout
    from build_info import load_build_info
    from price_recovery import apply, preview

    with st.expander("Recuperación auditada de interrupciones"):
        st.caption("La vista previa no consulta tiendas ni modifica la base. La aplicación revalida los recibos, "
                   "las revisiones y los bloqueos; no autoriza consultas ni modifica permisos. Introduzca su "
                   "identidad real; el campo no autentica al responsable.")
        allowlist = st.file_uploader("Lista histórica de 182 pares (JSON)", type=["json"], key="interrupted_list")
        historical_report = st.file_uploader("Informe histórico vinculado (JSON)", type=["json"],
                                             key="interrupted_report")
        if not allowlist or not historical_report:
            return
        source = (hashlib.sha256(allowlist.getvalue()).hexdigest(),
                  hashlib.sha256(historical_report.getvalue()).hexdigest())
        if st.session_state.get("interrupted_preview_source") != source:
            st.session_state.pop("interrupted_preview_rows", None)
        if st.button("Previsualizar recuperación", key="interrupted_preview"):
            try:
                st.session_state["interrupted_preview_rows"] = preview(
                    store.database_path, allowlist.getvalue(), historical_report.getvalue())
                st.session_state["interrupted_preview_source"] = source
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                st.session_state.pop("interrupted_preview_rows", None)
                st.error(f"No se pudo validar la recuperación: {exc}")
        rows = st.session_state.get("interrupted_preview_rows")
        if rows is None:
            return
        st.dataframe(pd.DataFrame([{k: row[k] for k in ("ean", "competitor_id", "eligible", "reason")}
                                   for row in rows]), hide_index=True)
        info = load_build_info()
        with st.form("interrupted_apply_form"):
            actor = st.text_input("Responsable", value="")
            note = st.text_input("Motivo de recuperación")
            approved = st.checkbox("Autorizo aplicar solo los pares elegibles revalidados")
            submitted = st.form_submit_button("Aplicar recuperación auditada",
                                              disabled=not info["build"].startswith("actions-"))
        if submitted:
            try:
                result = apply(store.database_path, allowlist.getvalue(), historical_report.getvalue(),
                               actor=actor, note=note, build_commit=info["commit"], approved=approved)
                st.session_state.pop("interrupted_preview_rows", None)
                st.success(f"Aplicados {sum(row['eligible'] for row in result)} pares; "
                           f"excluidos {sum(not row['eligible'] for row in result)}.")
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, Timeout) as exc:
                st.error(f"No se pudo aplicar la recuperación: {exc}")


def _render_selected_recovery(store: PriceComparatorStore, continuity_id: int) -> None:
    import sqlite3
    from filelock import Timeout
    from build_info import load_build_info
    from price_recovery import apply_selected, preview_selected

    with st.expander("Recuperación auditada de un cierre posterior"):
        st.caption("Seleccione un único par y el ID de su última comprobación. La aplicación revalida "
                   "manifiesto, recibos, reservas y revisiones bajo los bloqueos compartidos.")
        pair_id = st.number_input("ID del par", min_value=1, step=1, key="selected_recovery_pair")
        check_id = st.number_input("ID de la comprobación esperada", min_value=1, step=1,
                                   key="selected_recovery_check")
        selection = (continuity_id, int(pair_id), int(check_id))
        if st.session_state.get("selected_recovery_selection") != selection:
            st.session_state.pop("selected_recovery_preview", None)
        if st.button("Previsualizar cierre seleccionado", key="selected_recovery_preview_button"):
            try:
                row = preview_selected(store.database_path, continuity_id=selection[0],
                                       pair_id=selection[1], check_id=selection[2])
                st.session_state["selected_recovery_selection"] = selection
                st.session_state["selected_recovery_preview"] = row
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                st.session_state.pop("selected_recovery_preview", None)
                st.error(f"No se pudo validar el cierre: {exc}")
        row = st.session_state.get("selected_recovery_preview")
        if row is None:
            return
        st.dataframe(pd.DataFrame([{k: row[k] for k in ("pair_id", "ean", "competitor_id", "eligible", "reason")}]),
                     hide_index=True)
        info = load_build_info()
        with st.form("selected_recovery_apply_form"):
            actor = st.text_input("Responsable", value="")
            note = st.text_input("Motivo de recuperación")
            approved = st.checkbox("Autorizo aplicar solo este cierre si sigue siendo elegible")
            submitted = st.form_submit_button("Aplicar recuperación del cierre seleccionado",
                                              disabled=not info["build"].startswith("actions-"))
        if submitted:
            try:
                result = apply_selected(store.database_path, continuity_id=selection[0], pair_id=selection[1],
                                        check_id=selection[2], actor=actor, note=note,
                                        build_commit=info["commit"], approved=approved)
                st.session_state.pop("selected_recovery_preview", None)
                if result["eligible"]:
                    st.success("Cierre recuperado; sujeto a los controles vigentes.")
                else:
                    st.warning(f"No recuperado: {result['reason']}")
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, Timeout) as exc:
                st.error(f"No se pudo aplicar la recuperación: {exc}")


def _render_approved_read_recovery(store: PriceComparatorStore, names: dict[str, str]) -> None:
    import sqlite3
    from filelock import Timeout
    from build_info import load_build_info
    from price_recovery import apply_manual_reviews, preview_manual_reviews

    st.caption(
        "La vista previa es local y de solo lectura: reevalúa muestras y artefactos conservados, "
        "sin HTTP. La aplicación revalida aprobaciones, identidad, vigencia, permisos, robots, "
        "presupuesto y bloqueos; las lecturas nuevas las ejecuta únicamente el monitor existente."
    )
    columns = st.columns(3)
    competitor_ids = columns[0].multiselect(
        "Competidores", options=sorted(names), format_func=lambda value: names[value],
        key="approved_read_competitors",
    )
    ean_query = columns[1].text_input("EAN", key="approved_read_ean")
    reason_query = columns[2].text_input("Motivo o evidencia", key="approved_read_reason")
    filters = (tuple(competitor_ids), ean_query.strip(), reason_query.strip())
    if st.session_state.get("approved_read_filters") != filters:
        st.session_state.pop("approved_read_preview", None)
        st.session_state.pop("approved_read_selection", None)
        st.session_state.pop("approved_read_approval_selection", None)
        st.session_state["approved_read_filters"] = filters

    if st.button("Previsualizar lecturas aprobadas", key="approved_read_preview_button"):
        try:
            st.session_state["approved_read_preview_revision"] = (
                int(st.session_state.get("approved_read_preview_revision", 0)) + 1)
            st.session_state["approved_read_preview"] = preview_manual_reviews(
                store.database_path,
                competitor_ids=competitor_ids or None,
                ean_query=ean_query,
                reason_query=reason_query,
            )
            st.session_state["approved_read_selection"] = []
            st.session_state["approved_read_approval_selection"] = []
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            st.session_state.pop("approved_read_preview", None)
            st.error(f"No se pudo preparar la vista previa: {exc}")

    preview = st.session_state.get("approved_read_preview")
    if preview is None:
        return
    counts = preview["action_counts"]
    metrics = st.columns(5)
    metrics[0].metric("Reutilizaciones locales", counts["reuse"])
    metrics[1].metric("Reprogramaciones", counts["requeue"])
    metrics[2].metric("Ya en cola", counts["already_queued"])
    metrics[3].metric("Exclusiones", counts["excluded"])
    metrics[4].metric("HTTP nuevos en esta acción", 0)

    actionable = [row for row in preview["requests"]
                  if row["action"] in {"local_reevaluation", "requeue", "recover_corrected_error"}]
    if actionable:
        selected = set(st.session_state.get("approved_read_selection", []))
        grid_key = (f"approved_read_grid_{st.session_state.get('approved_read_preview_revision', 0)}_"
                    f"{hashlib.sha256(repr(filters).encode()).hexdigest()[:10]}")
        grid = pd.DataFrame([{
            "Seleccionar": row["id"] in selected,
            "ID": row["id"],
            "EAN": row["ean"],
            "Competidor": names.get(row["competitor_id"], row["competitor_id"]),
            "URL aprobada": row["url"],
            "Aprobación": row["approval_id"],
            "Error histórico": row["detail"],
            "Resultado local": (
                ", ".join(item["result"] for item in
                          row["evidence"].get("artifact_evaluations", []))
                or row["evidence"]["sample_kind"]
            ),
            "Oferta comparable": ("Sí · muestra vigente" if row["action"] == "local_reevaluation"
                                  else "No · requiere lectura gobernada"),
            "Acción / motivo": f"{row['action']}: {row['reason']}",
            "HTTP previos": row["http_requests"],
            "Recibos": ", ".join(
                f"{receipt['id']}:{receipt['result'] or 'reserved'}"
                for receipt in row["evidence"]["receipt_summaries"]),
        } for row in actionable])
        edited = st.data_editor(
            grid, key=grid_key, hide_index=True, width="stretch", num_rows="fixed",
            disabled=[column for column in grid.columns if column != "Seleccionar"],
            column_config={"URL aprobada": st.column_config.LinkColumn("URL aprobada")},
        )
        st.session_state["approved_read_selection"] = edited.loc[
            edited["Seleccionar"], "ID"].astype(int).tolist()
    else:
        st.info("No hay solicitudes de lecturas recuperables en estos filtros.")

    excluded = [row for row in preview["requests"] if row["category"] == "excluded"]
    if excluded:
        with st.expander(f"Exclusiones actuales ({len(excluded)})"):
            st.dataframe(pd.DataFrame([{
                "ID": row["id"],
                "EAN": row["ean"],
                "Competidor": names.get(row["competitor_id"], row["competitor_id"]),
                "URL aprobada": row["url"],
                "Aprobación": row["approval_id"],
                "Error histórico": row["detail"],
                "Motivo de exclusión": row["reason"],
                "Artefactos": ", ".join(
                    item["result"] for item in row["evidence"].get("artifact_evaluations", [])),
                "HTTP previos": row["http_requests"],
                "Recibos": ", ".join(
                    f"{receipt['id']}:{receipt['result'] or 'reserved'}"
                    for receipt in row["evidence"]["receipt_summaries"]),
            } for row in excluded]), hide_index=True, width="stretch")

    approvals = preview["approvals_without_request"]
    if approvals:
        st.dataframe(pd.DataFrame([{
            "Aprobación": row["approval_id"],
            "EAN": row["ean"],
            "Competidor": names.get(row["competitor_id"], row["competitor_id"]),
            "URL aprobada": row["url"],
            "Acción": row["action"],
            "Motivo": row["reason"],
        } for row in approvals]), hide_index=True, width="stretch")
        approval_labels = {
            row["approval_id"]: f"{row['approval_id']} · {row['ean']} · "
                                f"{names.get(row['competitor_id'], row['competitor_id'])}"
            for row in approvals if row["action"] in {"create", "reuse_saved_sample"}
        }
        selected_approvals = st.multiselect(
            "Aprobaciones auditadas elegibles sin solicitud",
            options=list(approval_labels),
            format_func=approval_labels.get,
            key="approved_read_approval_selection",
        )
    else:
        selected_approvals = []
        st.caption("No hay aprobaciones auditadas sin solicitud en estos filtros.")

    request_ids = st.session_state.get("approved_read_selection", [])
    info = load_build_info()
    with st.form("approved_read_apply_form"):
        actor = st.text_input("Responsable", value=_actor(), key="approved_read_actor")
        note = st.text_input("Motivo de aplicación", key="approved_read_note")
        approved = st.checkbox("Autorizo aplicar solo las filas elegibles seleccionadas",
                               key="approved_read_confirm")
        submitted = st.form_submit_button(
            "Aplicar reevaluación auditada",
            disabled=not (request_ids or selected_approvals) or not info["build"].startswith("actions-"),
        )
    if submitted:
        try:
            result = apply_manual_reviews(
                store.database_path, actor=actor, note=note, build_commit=info["commit"], approved=approved,
                request_ids=request_ids or None, approval_ids=selected_approvals or None,
            )
            st.session_state.pop("approved_read_preview", None)
            st.session_state.pop("approved_read_selection", None)
            st.session_state.pop("approved_read_approval_selection", None)
            changed = set(result["changed"])
            linked = {item["request_id"] for item in result["linked"]}
            selected_requests = set(request_ids)
            request_rows = [{
                "ID": row["id"],
                "EAN": row["ean"],
                "Competidor": names.get(row["competitor_id"], row["competitor_id"]),
                "Estado final": ("done" if row["action"] == "local_reevaluation" else "pending")
                if row["id"] in changed else "ya en cola" if row["id"] in linked else "sin cambios",
                "Acción": row["action"],
                "Motivo": row["reason"],
            } for row in result["requests"] if row["id"] in selected_requests]
            approval_rows = [{
                "Aprobación": row.get("approval_id"),
                "Solicitud": row.get("request_id"),
                "Acción": row["action"],
                "Motivo": row["reason"],
            } for row in result["approval_results"]]
            outcomes = [*request_rows, *approval_rows]
            if changed or linked or any(row["action"] == "created" for row in approval_rows):
                st.success("Aplicación auditada completada; no se hizo HTTP. "
                           "Las nuevas lecturas quedan a cargo del monitor.")
            else:
                st.info("No hubo cambios: las filas ya estaban resueltas/en cola o dejaron de ser elegibles.")
            if outcomes:
                st.dataframe(pd.DataFrame(outcomes), hide_index=True, width="stretch")
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, Timeout) as exc:
            st.error(f"No se pudo aplicar la reevaluación: {exc}")


def _render_continuity_capture(service, frozen: dict, *, active: bool = True) -> None:
    import sqlite3

    from filelock import Timeout
    from build_info import load_build_info

    with st.expander("Captura supervisada de un par"):
        st.caption("Procesa solo un par pendiente y vencido con ficha ya verificada, usando el trabajador y "
                   "sus controles existentes. No fuerza fechas ni acepta revisiones. El HTML se conserva solo "
                   "si supera las comprobaciones de tamaño y datos sensibles. Introduzca su identidad real; "
                   "el campo no autentica al responsable.")
        cid = st.selectbox("Competidor exacto", options=list(frozen["competitors"]),
                           format_func=lambda value: frozen["competitors"][value], key="capture_competitor")
        ean = st.selectbox("EAN exacto", options=frozen["eans"], key="capture_ean")
        info = load_build_info()
        with st.form("continuity_capture_form"):
            actor = st.text_input("Responsable", value="")
            note = st.text_input("Motivo de captura")
            approved = st.checkbox("Autorizo procesar este único par si está vencido y permitido")
            submitted = st.form_submit_button("Capturar durante la comprobación supervisada",
                                              disabled=not active or not info["build"].startswith("actions-"))
        if submitted:
            try:
                st.session_state["continuity_capture_result"] = service.capture_continuity(
                    cid, ean, actor=actor, note=note, build_commit=info["commit"], approved=approved)
                st.success("Respuesta guardada en la auditoría junto a su comprobación de continuidad.")
            except (ValueError, RuntimeError, sqlite3.Error, Timeout) as exc:
                st.session_state.pop("continuity_capture_result", None)
                st.error(f"No se guardó una captura: {exc}")
        result = st.session_state.get("continuity_capture_result")
        if result is not None:
            st.download_button("Descargar evidencia de captura", json.dumps(result, ensure_ascii=False).encode("utf-8"),
                               "price-continuity-capture.json", "application/json", key="continuity_capture_download")

    with st.expander("Diagnóstico supervisado de fichas en revisión"):
        st.caption("Consulta una candidata documentada o ficha aceptada cuya lectura falló, sin aceptar otra "
                   "revisión ni modificar ofertas o plazos. "
                   "Usa permisos, robots y presupuestos existentes; los rechazos también se auditan. Introduzca "
                   "su identidad real; el campo no autentica al responsable.")
        cid = st.selectbox("Competidor", options=list(frozen["competitors"]),
                           format_func=lambda value: frozen["competitors"][value], key="diagnostic_competitor")
        ean = st.selectbox("EAN", options=frozen["eans"], key="diagnostic_ean")
        candidates = service.diagnostic_candidates(cid, ean)
        st.button("Actualizar estado", key="diagnostic_refresh")
        results = service.diagnostic_results(cid, ean)
        retried_ids = {r["retry_of"] for r in results if r["retry_of"] is not None}
        retryable_ids = set()
        for result in results:
            if result["status"] != "rejected" or result["id"] in retried_ids:
                continue
            try:
                audit = json.loads(result["result"])
            except (TypeError, json.JSONDecodeError):
                st.warning(f"Resultado diagnóstico {result['id']} ilegible; no se propone reintento.")
                continue
            if (isinstance(audit, dict) and audit.get("stage") == "lock_and_scope"
                    and audit.get("requests") == []
                    and (audit.get("error_type") == "PilotError"
                         or (audit.get("error_type") == "EvidenceError"
                             and result["reason"] == "El almacenamiento no acredita permisos privados"))):
                retryable_ids.add(result["id"])
        if results:
            labels = {"pending": "pendiente", "running": "ejecutando", "captured": "capturado",
                      "deferred": "aplazado", "expired": "caducado", "rejected": "rechazado"}
            display = [{**r, "estado": ("reintentable (sujeto a comprobación)"
                        if r["id"] in retryable_ids
                        else labels.get(r["status"], r["status"]))} for r in results]
            st.dataframe(pd.DataFrame(display), hide_index=True)
            available = [r for r in results if r["available"]]
            if available:
                chosen = st.selectbox("Evidencia guardada", available,
                                      format_func=lambda r: f"{r['id']} · {r['finished_at']}",
                                      key="diagnostic_saved")
                try:
                    artifact = service.diagnostic_artifact(chosen["id"])
                    st.download_button("Descargar evidencia sanitizada", artifact,
                                       "price-diagnostic-sanitized.html", "text/html", key="diagnostic_download")
                except (ValueError, sqlite3.Error) as exc:
                    st.warning(f"Evidencia no disponible: {exc}")
        if not candidates:
            st.info("No hay fichas documentadas elegibles para diagnóstico de este par.")
            return
        url = st.selectbox("URL documentada o ficha aceptada", candidates, key="diagnostic_url")
        info = load_build_info()
        retryable = [r for r in results if r["id"] in retryable_ids and r["url"] == url]
        with st.form("diagnostic_capture_form"):
            actor = st.text_input("Responsable", value="")
            note = st.text_input("Motivo del diagnóstico")
            approved = st.checkbox("Autorizo una consulta diagnóstica gobernada")
            submitted = st.form_submit_button("Solicitar evidencia diagnóstica",
                                              disabled=not info["build"].startswith("actions-"))
            retried = st.form_submit_button("Reintentar rechazo previo a HTTP",
                                            disabled=not retryable or not info["build"].startswith("actions-"))
        if submitted or retried:
            try:
                if retried:
                    saved = service.retry_diagnostic(retryable[0]["id"], actor=actor, note=note,
                                                     build_commit=info["commit"], approved=approved)
                else:
                    saved = service.request_diagnostic(
                        cid, ean, url, actor=actor, note=note, build_commit=info["commit"], approved=approved)
                labels = {"pending": "pendiente", "running": "ejecutando", "deferred": "aplazada",
                          "captured": "capturado", "rejected": "rechazado", "expired": "caducado"}
                label = labels.get(saved["status"], saved["status"])
                if saved["status"] in {"pending", "running", "deferred"}:
                    st.success(f"Solicitud {saved['id']}: {label}. El trabajador la procesará cuando sea posible.")
                else:
                    st.info(f"Solicitud {saved['id']}: {label}. Consulte el resultado; no se repetirá automáticamente.")
            except (ValueError, RuntimeError, sqlite3.Error, Timeout) as exc:
                st.error(f"No se pudo registrar la solicitud: {exc}")


def _render_batch(store: PriceComparatorStore, names: dict[str, str]) -> None:
    from price_batch import WatchBatch, batch_pairs_export

    with st.expander("Historial persistente de lotes"):
        with store._connect() as con:
            history = con.execute("""SELECT id,started_at,summary FROM locate_runs
                WHERE trigger IN ('watch:batch','watch:point_check') ORDER BY id DESC LIMIT 100""").fetchall()
        if history:
            saved = st.selectbox("Ejecución guardada", [dict(r) for r in history],
                                 format_func=lambda r: f"{r['id']} · {r['started_at']}", key="batch_history")
            st.download_button("Descargar ejecución guardada (JSON)", saved["summary"].encode("utf-8"),
                               f"lote-{saved['id']}.json", "application/json", key="batch_history_json")
    watch = PriceWatch(store).watchlist()
    if watch.empty:
        st.info("Añada productos en vigilancia para preparar una actualización.")
        return
    labels = {row["ean"]: f"{row['ean']} · {row['brand']} {row['model']} {row['color']}"
              for row in watch.to_dict("records")}
    eans = st.multiselect("Productos del lote", product_eans(watch), format_func=labels.get, key="batch_eans")
    cids = st.multiselect("Competidores del lote", ordered(names, names.get), format_func=names.get, key="batch_competitors")
    point_check = st.checkbox("Comprobación puntual: nuevo intento de negativos y lectura actual de fichas",
                              key="batch_point_check")
    st.caption("Una sola pasada por par seleccionado. No modifica frecuencias, no acepta revisiones ni anula "
               "permisos, robots, bloqueos, pausas, presupuestos o reservas. Las decisiones del piloto histórico "
               "no amplían ni bloquean la selección vigente.")
    scope = (tuple(sorted(eans)), tuple(sorted(cids)), point_check)
    if scope != st.session_state.get("batch_selection") or st.session_state.pop("batch_reset", False):
        st.session_state["batch_approved"] = False
        st.session_state.pop("batch_prepared", None)
    st.session_state["batch_selection"] = scope
    st.caption("1. Seleccione · 2. Revise permisos y consumo · 3. Prepare sin HTTP · "
               "4. Confirme y ejecute. Las colas de localización y precios siguen siendo independientes.")
    if not eans or not cids:
        return
    batch = WatchBatch(store)
    try:
        preview = batch.preview(eans, cids, point_check=point_check)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.caption("Comprobación local de la selección completa, antes del límite de pares del lote:")
    for competitor, items in preview.groupby("competitor"):
        http = int(items.loc[items["ready"], "minimum_http"].sum())
        st.caption(f"{competitor}: {len(items)} pares · {int(items['located'].sum())} fichas verificadas · "
                   f"{int(items['ready'].sum())} tareas aptas · HTTP base {http}, "
                   f"disponibles {int(items['remaining_http'].iloc[0])} en 24 h.")
    display = preview.rename(columns={
        "ean": "EAN", "competitor": "Competidor", "action": "Trabajo necesario", "url": "Ficha reutilizada",
        "provenance": "Procedencia", "status": "Precio", "interval_hours": "Frecuencia efectiva (h)", "access": "Acceso",
        "minimum_http": "HTTP base", "remaining_http": "HTTP restantes hoy", "estimate": "Previsión"})
    display = display.rename(columns={"defer_reason": "Motivo de aplazamiento", "queue_state": "Estado de cola",
                                      "not_before": "No antes de", "attempts": "Intentos"})
    st.dataframe(display[["EAN", "Competidor", "Trabajo necesario", "Precio", "Procedencia", "Ficha reutilizada",
                          "Motivo de aplazamiento", "Estado de cola", "No antes de", "Intentos"]],
                 hide_index=True, width="stretch", column_config={
                     "Ficha reutilizada": st.column_config.LinkColumn("Ficha reutilizada", display_text="Ver ficha")})
    with st.expander("Detalle de permisos, consumo y frecuencias"):
        st.dataframe(display[["EAN", "Competidor", "Frecuencia efectiva (h)", "Acceso", "HTTP base",
                              "HTTP restantes hoy", "Previsión"]], hide_index=True, width="stretch")
    with st.expander("Descubrimiento por catálogo: propuesta y estado local (sin HTTP)"):
        st.caption("Las URL documentales no se activan ni autorizan por aparecer en el plan. Solo se usan para "
                   "priorizar fuentes ya activas; ficha, robots, redirecciones y presupuesto se comprueban "
                   "de nuevo mediante el lector gobernado.")
        catalog_rows = []
        for row in preview.to_dict("records"):
            plan = row["catalog_discovery_plan"]
            state = row["catalog_execution"]
            if not plan:
                continue
            proposed = plan.get("entrypoints", [])
            observed = plan.get("observed_links", [])
            configured = state.get("configured_sources", [])
            catalog_rows.append({
                "EAN": row["ean"],
                "Competidor": row["competitor"],
                "Categoría": plan.get("category", ""),
                "Fuentes propuestas": " | ".join(proposed),
                "Enlaces observados (no activados)": " | ".join(observed),
                "Fuentes activas": " | ".join(
                    f"{source['url_template']} ({source['kind']}/{source['adapter']})"
                    for source in configured
                ),
                "Estado local": state.get("execution_state", ""),
                "Precomprobación": state.get("preflight_passed", False),
                "Motivo": state.get("reason", ""),
            })
        if catalog_rows:
            st.dataframe(pd.DataFrame(catalog_rows), hide_index=True, width="stretch")
        else:
            st.info("No se requiere descubrimiento por catálogo para esta selección.")
    with st.expander("Candidatas locales y requisitos pendientes (sin HTTP)"):
        st.caption("Hipótesis de la investigación del 04/11/2026, no fichas verificadas ni ofertas. "
                   "La generación no autoriza consultas ni acredita mercado, IVA o stock. "
                   "Los ejemplos conservan su locale y la URL completa de variante.")
        candidates = []
        for row in preview.to_dict("records"):
            for candidate in row["local_candidates"]:
                candidates.append({"EAN": row["ean"], "Competidor": row["competitor"],
                                   "URL candidata": candidate["url"], "Estado": candidate["state"],
                                   "Regla": candidate["rule"], "Procedencia": candidate["origin"],
                                   "Atributos": json.dumps(candidate["attributes"], ensure_ascii=False),
                                   "Evidencias": ", ".join(candidate["evidence"]), "Motivo": candidate["reason"]})
        if candidates:
            st.dataframe(pd.DataFrame(candidates), hide_index=True, width="stretch",
                         column_config={"URL candidata": st.column_config.LinkColumn("URL candidata")})
        requirements = preview.loc[preview["candidate_reason"].ne(""),
                                   ["ean", "competitor", "candidate_reason_code", "candidate_reason"]]
        if not requirements.empty:
            st.dataframe(requirements.rename(columns={"ean": "EAN", "competitor": "Competidor",
                         "candidate_reason_code": "Requisito", "candidate_reason": "Motivo"}),
                         hide_index=True, width="stretch")
    st.warning("El consumo de localización es variable. El mínimo no es una cuota reservada. "
               "Robots, paginación, pausas, límites compartidos y bloqueos se comprueban de nuevo al ejecutar. "
               "Las fichas verificadas se reutilizan; las reservas heredadas no detienen la cola actual.")
    if st.button("Preparar trabajos sin HTTP", key="batch_prepare"):
        try:
            batch.prepare(eans, cids, point_check=point_check)
            st.session_state["batch_prepared"] = scope
            st.session_state["batch_approved"] = False
            _refresh("Lote preparado sin HTTP. Revise la vista previa y confirme la ejecución.")
        except ValueError as exc:
            st.error(str(exc))
    prepared = st.session_state.get("batch_prepared") == scope
    sync = st.checkbox("Sincronizar primero Vytria (todos los productos vigilados)", value=True, key="batch_sync")
    maximum = st.number_input("Máximo de pares a procesar", min_value=1, max_value=5000, value=300, key="batch_max")
    approval_scope = (scope, sync, int(maximum))
    if approval_scope != st.session_state.get("batch_approval_scope"):
        st.session_state["batch_approved"] = False
    st.session_state["batch_approval_scope"] = approval_scope
    approved = st.checkbox("Autorizo las consultas del lote y, si está marcada, la sincronización de Vytria",
                           key="batch_approved")
    if st.button("Ejecutar lote supervisado", key="batch_execute", disabled=not (prepared and approved)):
        try:
            result = batch.execute(eans, cids, approved=approved, sync_vytria=sync, max_jobs=int(maximum),
                                   point_check=point_check)
            st.session_state["batch_result"] = result
            st.session_state.pop("batch_prepared", None)
            st.session_state["batch_reset"] = True
            _refresh("Lote terminado. Revise resultados e incidencias; la planificación no se ha modificado.")
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if result := st.session_state.get("batch_result"):
        st.markdown("#### Resultado del último lote")
        if result["vytria"]:
            st.write("Sincronización de Vytria", result["vytria"])
            if result["vytria"]["status"] == "error":
                st.error(result["vytria"]["error"])
        st.dataframe(pd.DataFrame(result["runs"]), hide_index=True, width="stretch")
        st.caption(f"Pares realmente procesados: {result['processed_pairs']}. "
                   "Un trabajo procesado puede reutilizar datos sin realizar una petición nueva.")
        pair_export = batch_pairs_export(result["pairs"])
        st.dataframe(pair_export, hide_index=True, width="stretch")
        st.download_button("Exportar pares del lote (CSV)",
                           pair_export.to_csv(index=False).encode("utf-8-sig"),
                           "resultado-lote-pares.csv", "text/csv", key="batch_pairs_csv_export")
        st.download_button("Exportar resultado del lote (JSON)",
                           json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
                           "resultado-lote.json", "application/json", key="batch_result_export")
        if result["skipped"]:
            st.info("Pares sin ejecución: precio vigente, revisión pendiente o restricciones de acceso.")
            st.dataframe(pd.DataFrame(result["skipped"]), hide_index=True, width="stretch")
        if result["deferred"]:
            st.warning(f"{result['deferred']} pares aptos no procesados: límite, reserva, bloqueo o cambio "
                       "de estado. Consulte el motivo por par; una selección no cuenta como ejecución.")


def render_admin(store: PriceComparatorStore, data: dict) -> None:
    from app_pages import price_comparator as page

    _notices()
    watch, access = PriceWatch(store), AccessControl(store)
    watch.ensure_defaults()
    access.ensure_defaults()
    PriceLocator(store).seed_sources()
    names = {r["id"]: r["name"] for r in store.competitors().to_dict("records")}
    if st.session_state.get("price_admin_section") not in ADMIN_SECTIONS:
        st.session_state.pop("price_admin_section", None)
    section = st.selectbox("Administración", ADMIN_SECTIONS, key="price_admin_section")
    if section == "Resumen":
        _render_overview(store)
    elif section == "Productos":
        _render_watchlist(watch)
        task = st.selectbox("Gestionar productos", ["Añadir / importar", "Reglas", "Grupos y frecuencia",
                                                  "Estado de localización"], key="watch_product_task")
        if task == "Añadir / importar":
            _render_add(watch, store)
        elif task == "Reglas":
            _render_rules(watch)
        elif task == "Grupos y frecuencia":
            _render_groups(watch)
        else:
            inventory = PriceMonitor(store).inventory()
            if not inventory.empty:
                st.dataframe(inventory.assign(locate_state=inventory["locate_state"].map(JOB_STATES))[
                    ["ean", "brand", "model", "color", "competitor", "located", "locate_state", "locate_detail",
                     "url", "provenance", "interval_hours"]].rename(columns={
                         "ean": "EAN", "brand": "Marca", "model": "Modelo", "color": "Color",
                         "competitor": "Competidor", "located": "Ficha verificada", "locate_state": "Localización",
                         "locate_detail": "Detalle", "url": "Ficha", "provenance": "Procedencia",
                         "interval_hours": "Frecuencia efectiva (h)"}),
                    hide_index=True, width="stretch")
    elif section == "Tiendas":
        st.caption("Ficha de cada tienda: estado, mercado, URLs, métodos, frecuencia, consumos y bloqueos.")
        page._render_competitors(store, data)
    elif section == "Por revisar":
        st.caption("Candidatas y problemas en una sola bandeja. Confirmar una ficha solicita la lectura prioritaria "
                   "al trabajador existente; descartar no consulta la tienda.")
        _render_locator(store, names, advanced=False)
    else:
        task = st.selectbox(
            "Vigilancia y consumo",
            ["Estado y consumos", "Lote supervisado", "Continuidad vigente"],
            key="watch_vigilance_task",
        )
        if task == "Lote supervisado":
            active = store.competitors()
            active_names = {r["id"]: r["name"] for r in active[active["active"].eq(1)].to_dict("records")}
            _render_batch(store, active_names)
        elif task == "Estado y consumos":
            _render_monitor(store, names, advanced=False)
        else:
            _render_continuity(store)


def render_diagnostics(store: PriceComparatorStore, data: dict) -> None:
    from app_pages import price_comparator as page

    _notices()
    access = AccessControl(store)
    access.ensure_defaults()
    PriceWatch(store).ensure_defaults()
    PriceLocator(store).seed_sources()
    names = {r["id"]: r["name"] for r in store.competitors().to_dict("records")}
    if st.session_state.get("price_diagnostic_section") not in DIAGNOSTIC_SECTIONS:
        st.session_state.pop("price_diagnostic_section", None)
    section = st.selectbox("Diagnóstico técnico", DIAGNOSTIC_SECTIONS, key="price_diagnostic_section")
    if section == "Ajustes técnicos":
        page._render_settings(store)
    elif section == "Importaciones":
        page._render_data_sources(store)
    elif section == "Históricos y auditoría":
        page._render_admin_history(store)
        with st.expander("Histórico del piloto de 72 horas"):
            _render_trial_history(store)
    elif section == "Colas y recuperaciones":
        task = st.selectbox(
            "Herramienta de diagnóstico",
            ["Localización automática", "Monitorización", "Incidencias y recuperación",
             "Reevaluar lecturas aprobadas"],
            key="diagnostic_queue_task",
        )
        if task == "Localización automática":
            _render_locator(store, names)
        elif task == "Monitorización":
            _render_monitor(store, names)
        elif task == "Incidencias y recuperación":
            _render_incidents(access, names)
        elif task == "Reevaluar lecturas aprobadas":
            _render_approved_read_recovery(store, names)
    else:
        st.caption("Búsqueda manual acotada; se mantienen permisos, presupuesto, pausas, robots y bloqueos.")
        page._render_web_finder(store)
