"""Administration UI of the scalable price watch (phase 1): watch list, groups, authorisations, budgets, blocks.

Opening these screens never performs HTTP requests.
"""
from __future__ import annotations

import json
import pandas as pd
import streamlit as st
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from price_scheduler import CompetitorScheduler
    from price_web import WebPriceSearch

from price_access import (DEFAULT_BUDGETS, INCIDENT_KINDS, METHOD_STATES, METHODS, AccessControl,
                          external_search_enabled)
from price_comparator import PriceComparatorStore
from price_locator import (ADAPTERS, JOB_STATES, MAX_DEPTH, SCHEDULE_ENV, SOURCE_KINDS, PriceLocator,
                           schedule_enabled)
from price_monitor import (ALERTS, STATES, SCHEDULE_ENV as MONITOR_ENV, PriceMonitor,
                           schedule_enabled as monitor_enabled)
from price_watch import DEFAULT_GROUP, SOURCES, PriceWatch, WatchReport, eans_from_frame, parse_eans
from vytria_price_sync import STATUS_LABELS, SyncInProgress, VytriaPriceSync
from app_pages.price_options import ordered, product_eans

def _actor() -> str:
    return str(st.session_state.get("auth_user") or st.session_state.get("auth_role") or "admin")


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


def _render_locator(store: PriceComparatorStore, names: dict[str, str]) -> None:
    st.caption("Motor propio: busca cada producto vigilado por marca + modelo + código de color en las fuentes "
               "autorizadas (URL ya verificada, feed, sitemap, páginas de marca/catálogo, buscador interno), guarda "
               "un índice local reutilizable y confirma solo coincidencias exactas. Sin servicios externos. Un "
               "bloqueo o un límite suspende el competidor; la falta de autorización nunca se registra como "
               "«producto inexistente».")
    locator = PriceLocator(store)
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
    for col, (label, keys) in zip(cols, [("Verificadas", ("done",)), ("Pendientes", ("pending", "running", "waiting")),
                                         ("Revisión manual", ("review",)),
                                         ("Sin acceso / bloqueadas", ("unavailable", "blocked")),
                                         ("Sin ficha / fallidas", ("not_found", "failed"))]):
        col.metric(label, int(sum(totals.get(key, 0) for key in keys)))
    if not status.empty:
        pivot = status.assign(state=status["state"].map(lambda s: JOB_STATES.get(s, s))).pivot_table(
            index="competitor", columns="state", values="jobs", aggfunc="sum", fill_value=0)
        st.dataframe(pivot, width="stretch")

    review = locator.jobs(states=("review",))
    st.markdown(f"**Revisión manual** ({len(review)})")
    if review.empty:
        st.caption("No hay coincidencias dudosas pendientes.")
    else:
        st.dataframe(review[["ean", "brand", "model", "color", "competitor", "url", "detail", "updated_at"]].rename(
            columns={"ean": "EAN", "brand": "Marca", "model": "Modelo", "color": "Color", "competitor": "Competidor",
                     "url": "Ficha", "detail": "Evidencia", "updated_at": "Fecha"}),
            hide_index=True, width="stretch", column_config={"Ficha": st.column_config.LinkColumn("Ficha")})
        labels = {row.id: f"{row.brand} · {row.model} · {row.color} · {row.competitor} · {row.ean}"
                  for row in review.itertuples()}
        with st.form("locate_review_form"):
            job_id = st.selectbox("Candidato", ordered(labels, labels.get), format_func=labels.get, key="locate_review_job")
            note = st.text_input("Motivo de la decisión", key="locate_review_note")
            accept, reject = st.columns(2)
            decision = ("accept" if accept.form_submit_button("Confirmar ficha") else
                        "reject" if reject.form_submit_button("Descartar ficha") else "")
            if decision:
                row = review.set_index("id").loc[job_id]
                try:
                    locator.review(row["ean"], row["competitor_id"], row["url"], accept=decision == "accept",
                                   actor=_actor(), note=note)
                    _refresh("Decisión registrada y auditada.")
                except ValueError as exc:
                    st.error(str(exc))

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

    monitor = PriceMonitor(store)
    sync = VytriaPriceSync(store)
    with st.expander("Precio Vytria y ciclo diario"):
        st.caption("Fuente operativa: Odoo tarifa 59, ES/EUR con IVA. Contraste con el precio publicado en "
                   "PrestaShop pendiente. Vigencia máxima de los precios Vytria vigilados: 24 h.")
        daily = st.toggle("Sincronizar Vytria antes de la vigilancia diaria", value=sync.auto_enabled,
                          key="watch_vytria_daily")
        if daily != sync.auto_enabled:
            sync.set_auto_enabled(daily)
        st.caption("El ciclo requiere también PRICE_MONITOR_SCHEDULE_ENABLED y cada competidor habilitado. "
                   "La localización conserva su planificación independiente. No se activa ningún servicio aquí.")
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
    if not monitor_enabled():
        st.warning(f"Monitorización periódica desactivada ({MONITOR_ENV}). Los lotes supervisados son explícitos.")
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


ADMIN_SECTIONS = (
    "Panel general", "Productos en vigilancia", "Competidores", "Actualizaciones",
    "Alertas e incidencias", "Configuración y diagnóstico",
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
        "Acceso y métodos", "Fuentes y conectores", "Presupuestos y límites",
        "Frecuencia de actualización", "Incidencias y bloqueos",
    ], key="competitor_card_section")
    if task == "Acceso y métodos":
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


def _render_trial(store: PriceComparatorStore) -> None:
    from filelock import Timeout
    from build_info import load_build_info
    from price_trial import PriceTrial, manifest

    service = PriceTrial(store)
    with st.expander("Piloto automático de 72 horas · 100 EAN × seis tiendas"):
        report = service.report()
        if report is None:
            frozen = manifest()
            st.caption("Tres ciclos diarios, Odoo tarifa 59 de solo lectura y los 600 pares explícitos del manifiesto. "
                       "No se cambian permisos ni presupuestos; Otticait queda excluida. El cierre es absoluto.")
            st.download_button("Descargar manifiesto de 600 pares (JSON)",
                               json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8"),
                               "piloto-72h-manifiesto.json", "application/json", key="trial_manifest")
            try:
                prepared = service.preflight()
            except ValueError as exc:
                st.warning(str(exc))
                return
            st.dataframe(pd.DataFrame(prepared["limits"].values()), hide_index=True)
            info = load_build_info()
            approved = st.checkbox("Autorizo activar los 600 pares durante 72 horas y sincronizar solo esos 100 EAN",
                                   key="trial_approval")
            if st.button("Activar piloto de 72 horas", key="trial_activate",
                         disabled=not approved or not info["build"].startswith("actions-")):
                try:
                    service.activate(actor=_actor(), build_commit=info["commit"])
                    _refresh("Ventana persistida. El trabajador del servidor ejecutará el primer ciclo.")
                except (ValueError, RuntimeError, Timeout) as exc:
                    st.error(str(exc))
            return
        st.write({name: report[name] for name in ("status", "build_commit", "started_at", "expires_at",
                                                "started_at_madrid", "expires_at_madrid", "heartbeat_at", "error")})
        if report["recoveries"]:
            recovery = report["recoveries"][-1]
            st.caption(f"Compilación de recuperación: {recovery['note']['recovery_build_commit']} · "
                       "El commit de activación y la ventana original se conservan.")
        st.caption("La configuración no demuestra ejecución: compruebe el heartbeat y los recibos de cada ciclo. "
                   "Los demás disparadores no pueden consultar estos pares, tampoco después de caducar.")
        st.dataframe(pd.DataFrame.from_dict(report["summary"], orient="index"), width="stretch")
        st.download_button("Descargar historial e informe del piloto (JSON)",
                           json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                           "piloto-72h-historial.json", "application/json", key="trial_report")
        if report["status"] == "failed":
            try:
                service.recovery_preflight()
            except ValueError as exc:
                st.warning(f"Recuperación sin reintentos: {exc}")
            else:
                st.info("El fallo inicial no dejó sincronizaciones ni peticiones. Puede reanudar la misma "
                        "ventana; se conservará en auditoría el estado anterior completo.")
                info = load_build_info()
                approved = st.checkbox("Autorizo reanudar el piloto fallido sin ampliar su fecha de cierre",
                                       key="trial_resume_approval")
                if st.button("Reanudar tras corregir el arranque", key="trial_resume",
                             disabled=not approved or not info["build"].startswith("actions-")):
                    try:
                        service.resume_initial_sync_failure(actor=_actor(), build_commit=info["commit"])
                        _refresh("Piloto recuperado con su ventana original. El trabajador continuará sin duplicar consultas.")
                    except (ValueError, RuntimeError, Timeout) as exc:
                        st.error(str(exc))
        if report["status"] == "active" and st.button("Detener piloto ahora", key="trial_stop"):
            service.stop(actor=_actor())
            _refresh("Piloto detenido: no se iniciarán consultas nuevas. Los recibos se conservan.")


def _render_batch(store: PriceComparatorStore, names: dict[str, str]) -> None:
    from price_batch import WatchBatch, batch_pairs_export

    _render_trial(store)
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
               "permisos, robots, bloqueos, pausas, presupuestos o reservas. Los pares del piloto antiguo "
               "se registran en una nueva ejecución supervisada del piloto, con las fuentes autorizadas actuales.")
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
                                      "not_before": "No antes de", "attempts": "Intentos", "pilot_route": "Flujo piloto"})
    st.dataframe(display[["EAN", "Competidor", "Trabajo necesario", "Precio", "Procedencia", "Ficha reutilizada",
                          "Motivo de aplazamiento", "Estado de cola", "No antes de", "Intentos", "Flujo piloto"]],
                 hide_index=True, width="stretch", column_config={
                     "Ficha reutilizada": st.column_config.LinkColumn("Ficha reutilizada", display_text="Ver ficha")})
    with st.expander("Detalle de permisos, consumo y frecuencias"):
        st.dataframe(display[["EAN", "Competidor", "Frecuencia efectiva (h)", "Acceso", "HTTP base",
                              "HTTP restantes hoy", "Previsión"]], hide_index=True, width="stretch")
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
               "Las fichas verificadas se reutilizan; los pares pendientes del piloto se gestionan en el piloto.")
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
            st.info("Pares sin ejecución: precio todavía vigente, gestión en piloto, revisión o restricciones de acceso.")
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
    section = st.selectbox("Administración", ADMIN_SECTIONS, key="price_admin_section")
    if section == "Panel general":
        _render_overview(store)
    elif section == "Productos en vigilancia":
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
    elif section == "Competidores":
        page._render_competitors(store, data)
    elif section == "Actualizaciones":
        task = st.selectbox("Actualización", ["Lote supervisado", "Precios y observaciones",
                                             "Colas y revisión (avanzado)"], key="watch_update_task")
        if task == "Lote supervisado":
            active = store.competitors()
            active_names = {r["id"]: r["name"] for r in active[active["active"].eq(1)].to_dict("records")}
            _render_batch(store, active_names)
        elif task == "Precios y observaciones":
            _render_monitor(store, names, advanced=False)
        else:
            advanced = st.selectbox("Herramienta avanzada", ["Localización", "Monitorización"],
                                    key="watch_advanced_task")
            if advanced == "Localización":
                _render_locator(store, names)
            else:
                _render_monitor(store, names)
    elif section == "Alertas e incidencias":
        task = st.selectbox("Consultar", ["Alertas comerciales", "Incidencias y recuperación"], key="watch_alert_task")
        if task == "Alertas comerciales":
            _render_alerts(store)
        else:
            _render_incidents(access, names)
    else:
        task = st.selectbox("Diagnóstico", ["Parámetros generales", "Importaciones técnicas",
                                           "Auditoría, históricos y exportaciones", "Piloto anterior",
                                           "Recuperación de búsquedas"], key="watch_diagnostic_task")
        if task == "Parámetros generales":
            page._render_settings(store)
        elif task == "Importaciones técnicas":
            page._render_data_sources(store)
        elif task == "Auditoría, históricos y exportaciones":
            page._render_admin_history(store)
        elif task == "Recuperación de búsquedas":
            page._render_budget_recovery(store)
        else:
            from price_pilot import PricePilot

            sub = st.selectbox("Tarea del piloto", ["Configuración del piloto", "Localización supervisada",
                                                    "Actualización de precios", "Búsqueda manual"],
                               key="price_pilot_section")
            pilot = PricePilot(store)
            if sub == "Configuración del piloto":
                page._render_pilot(store)
            elif sub == "Localización supervisada":
                page._render_discovery(pilot, pilot.is_running())
            elif sub == "Actualización de precios":
                page._render_pilot_price_refresh(store)
            else:
                page._render_web_finder(store)
