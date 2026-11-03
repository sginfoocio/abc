"""Administration UI of the scalable price watch (phase 1): watch list, groups, authorisations, budgets, blocks.

Opening these screens never performs HTTP requests.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from price_access import (DEFAULT_BUDGETS, INCIDENT_KINDS, METHOD_STATES, METHODS, AccessControl,
                          external_search_enabled)
from price_comparator import PriceComparatorStore
from price_locator import JOB_STATES, SOURCE_METHODS, PriceLocator
from price_watch import DEFAULT_GROUP, SOURCES, PriceWatch, WatchReport, eans_from_frame, parse_eans

SECTIONS = ["Productos vigilados", "Añadir productos", "Reglas de vigilancia", "Grupos y frecuencia",
            "Localización automática", "Autorizaciones por método", "Presupuestos", "Bloqueos e incidencias"]


def _actor() -> str:
    return str(st.session_state.get("auth_user") or st.session_state.get("auth_role") or "admin")


def _show_report(report: WatchReport) -> None:
    (st.success if not report.rejected else st.warning)(report.summary)
    if report.rejected:
        st.dataframe(pd.DataFrame(report.rejected, columns=["EAN", "Motivo"]), hide_index=True, width="stretch")


def _render_watchlist(watch: PriceWatch) -> None:
    counts = watch.counts()
    cols = st.columns(4)
    cols[0].metric("Productos vigilados", counts["watched"])
    cols[1].metric("Selección individual", counts["manual"])
    cols[2].metric("Importación masiva", counts["import"])
    cols[3].metric("Por reglas", counts["rule"])
    st.caption(f"Catálogo del comparador: {counts['catalog']} productos. Al añadir productos se preparan sus "
               "búsquedas en todos los competidores; la localización se ejecuta por lotes en «Localización "
               "automática». La monitorización periódica de precios llega en una fase posterior.")
    frame = watch.watchlist()
    if frame.empty:
        st.info("Todavía no hay productos en vigilancia.")
        return
    shown = frame.assign(source=frame["source"].map(SOURCES))
    st.dataframe(shown[["ean", "brand", "model", "color", "commercial_name", "group_name", "source", "rule_name",
                        "added_by", "added_at"]].rename(columns={
        "ean": "EAN", "brand": "Marca", "model": "Modelo", "color": "Color", "commercial_name": "Nombre",
        "group_name": "Grupo", "source": "Origen", "rule_name": "Regla", "added_by": "Añadido por",
        "added_at": "Fecha"}), hide_index=True, width="stretch")
    labels = {row.ean: f"{row.ean} · {row.brand} {row.model} {row.color}" for row in frame.itertuples()}
    selected = st.multiselect("Productos", list(labels), format_func=labels.get, key="watch_selected")
    groups = watch.groups()["name"].tolist()
    c1, c2, c3 = st.columns([2, 1, 1])
    target = c1.selectbox("Mover al grupo", groups, key="watch_move_group")
    if c2.button("Mover", key="watch_move", disabled=not selected, width="stretch"):
        st.success(f"{watch.move(selected, target)} productos movidos a «{target}».")
    if c3.button("Retirar de vigilancia", key="watch_remove", disabled=not selected, width="stretch"):
        st.success(f"{watch.remove(selected, actor=_actor())} productos retirados (el histórico se conserva).")


def _render_add(watch: PriceWatch, store: PriceComparatorStore) -> None:
    groups = watch.groups()["name"].tolist()
    group = st.selectbox("Grupo de destino", groups, index=groups.index(DEFAULT_GROUP), key="watch_add_group")
    st.markdown("**Selección individual**")
    catalog = watch.catalog()
    available = catalog[(catalog["watched"] == 0) & catalog["complete"]]
    labels = {row.ean: f"{row.ean} · {row.brand} {row.model} {row.color} — {row.commercial_name}"
              for row in available.itertuples()}
    picked = st.multiselect("Productos del catálogo", list(labels), format_func=labels.get, key="watch_pick",
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
                    st.info(f"Catálogo: {catalog_report.imported} nuevos, {catalog_report.updated} actualizados, "
                            f"{len(catalog_report.rejected)} rechazados.")
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
        brand = c1.selectbox("Marca", [""] + sorted(catalog["brand"].dropna().unique().tolist()), key="rule_brand")
        category = c2.selectbox("Categoría", [""] + sorted(catalog["category"].dropna().unique().tolist()),
                                key="rule_category")
        model_prefix = c1.text_input("Modelo empieza por", key="rule_model")
        text_contains = c2.text_input("Nombre o modelo contiene", key="rule_text")
        group = st.selectbox("Grupo", watch.groups()["name"].tolist(), key="rule_group")
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
            st.success(f"Regla «{name.strip()}» guardada. Aplíquela para añadir sus productos.")
        except ValueError as exc:
            st.error(str(exc))
    if not rules.empty and st.button("Aplicar reglas activas", key="watch_apply_rules"):
        for rule_name, report in watch.apply_rules(actor=_actor()).items():
            st.write(f"**{rule_name}**: {report.summary}")


def _render_groups(watch: PriceWatch) -> None:
    st.caption("Frecuencia de monitorización: la del grupo si está definida; si no, la de cada competidor "
               "(Administración → Competidores → Actualización automática, 24 h por defecto).")
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
                st.success("Grupo guardado.")
            except ValueError as exc:
                st.error(str(exc))


def _render_methods(access: AccessControl) -> None:
    st.caption("Ningún método está autorizado por defecto: una página pública o un robots.txt permisivo no son "
               "una autorización. robots.txt se sigue comprobando en cada URL aunque el método esté autorizado. "
               "El motor es propio: la búsqueda externa es solo un punto de extensión y está "
               + ("activada en este entorno." if external_search_enabled() else "desactivada."))
    grid = access.methods()
    matrix = grid.pivot(index="name", columns="method", values="status")[list(METHODS)]
    matrix = matrix.replace(METHOD_STATES).rename(columns=METHODS).rename_axis(columns=None)
    st.dataframe(matrix.rename_axis("Competidor").reset_index(), width="stretch", hide_index=True)
    names = dict(zip(grid["competitor_id"], grid["name"]))
    with st.form("access_method_form"):
        c1, c2, c3 = st.columns(3)
        competitor = c1.selectbox("Competidor", sorted(names, key=names.get), format_func=names.get,
                                  key="access_competitor")
        method = c2.selectbox("Método", list(METHODS), format_func=METHODS.get, key="access_method")
        status = c3.selectbox("Decisión", list(METHOD_STATES), format_func=METHOD_STATES.get, key="access_status")
        note = st.text_area("Justificación (condiciones de uso, acuerdo, robots.txt…)", key="access_note")
        evidence = st.text_input("Evidencia (URL de las condiciones, referencia del acuerdo…)", key="access_evidence")
        if st.form_submit_button("Guardar decisión"):
            try:
                access.set_method(competitor, method, status, actor=_actor(), note=note, evidence=evidence)
                st.success("Decisión registrada en la auditoría.")
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
        scope_id = st.selectbox("Competidor", sorted(names, key=names.get), format_func=names.get, key="budget_cid")
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
                st.success("Presupuesto guardado y auditado.")
            except ValueError as exc:
                st.error(str(exc))


def _render_incidents(access: AccessControl, names: dict[str, str]) -> None:
    st.caption("CAPTCHA, 401/403/407 o 429 suspenden el competidor o proveedor afectado. Un 429 se reanuda solo tras "
               "el plazo indicado; el resto exige una decisión humana documentada. Nunca se intenta superar una "
               "protección: reanudar solo procede si el acceso se ha acordado o el bloqueo ya no aplica.")
    incidents = access.incidents()
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
                st.success("Incidencia cerrada y auditada.")
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
    target = c2.selectbox("Competidor", ["Todos", *sorted(names, key=names.get)], key="locate_competitor",
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
        labels = {row.id: f"{row.ean} · {row.brand} {row.model} {row.color} · {row.competitor}"
                  for row in review.itertuples()}
        with st.form("locate_review_form"):
            job_id = st.selectbox("Candidato", list(labels), format_func=labels.get, key="locate_review_job")
            note = st.text_input("Motivo de la decisión", key="locate_review_note")
            accept, reject = st.columns(2)
            decision = ("accept" if accept.form_submit_button("Confirmar ficha") else
                        "reject" if reject.form_submit_button("Descartar ficha") else "")
            if decision:
                row = review.set_index("id").loc[job_id]
                try:
                    locator.review(row["ean"], row["competitor_id"], row["url"], accept=decision == "accept",
                                   actor=_actor(), note=note)
                    st.success("Decisión registrada y auditada.")
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
    with st.expander("Fuentes de descubrimiento (catálogo / feed)"):
        st.caption("Plantillas de páginas de marca o catálogo del propio competidor. Solo se usan si el método "
                   "correspondiente está autorizado y robots.txt lo permite. Variable admitida: {brand}.")
        sources = locator.sources()
        if not sources.empty:
            st.dataframe(sources[["competitor", "method", "url_template", "refresh_hours", "max_pages", "active",
                                  "note", "created_by"]], hide_index=True, width="stretch")
        with st.form("locate_source_form"):
            s1, s2 = st.columns([1, 1])
            cid = s1.selectbox("Competidor", sorted(names, key=names.get), format_func=names.get,
                               key="locate_source_competitor")
            method = s2.selectbox("Tipo", SOURCE_METHODS, key="locate_source_method",
                                  format_func={"catalog": "Catálogo / marca", "feed": "Feed del competidor"}.get)
            template = st.text_input("URL", placeholder="https://tienda.example/marca/{brand}",
                                     key="locate_source_url")
            s3, s4, s5 = st.columns(3)
            refresh = s3.number_input("Caducidad (h)", 1, 24 * 90, 168, key="locate_source_refresh")
            pages = s4.number_input("Máx. páginas", 1, 200, 10, key="locate_source_pages")
            active = s5.checkbox("Activa", value=True, key="locate_source_active")
            note = st.text_input("Nota", key="locate_source_note")
            if st.form_submit_button("Guardar fuente"):
                try:
                    locator.save_source(cid, method, template, actor=_actor(), note=note,
                                        refresh_hours=float(refresh), max_pages=int(pages), active=active)
                    st.success("Fuente guardada.")
                except ValueError as exc:
                    st.error(str(exc))


def render_watch_admin(store: PriceComparatorStore) -> None:
    st.subheader("Vigilancia automática")
    st.caption("Motor escalable propio (fase 1: qué se vigila, permisos y límites; fase 2: localización automática "
               "de fichas). El piloto de 20 EAN sigue funcionando por separado y no usa esta configuración.")
    watch, access = PriceWatch(store), AccessControl(store)
    watch.ensure_defaults()
    access.ensure_defaults()
    PriceLocator(store).seed_sources()
    names = dict(store.competitors()[["id", "name"]].itertuples(index=False, name=None))
    section = st.selectbox("Apartado", SECTIONS, key="price_watch_section")
    if section == "Productos vigilados":
        _render_watchlist(watch)
    elif section == "Añadir productos":
        _render_add(watch, store)
    elif section == "Reglas de vigilancia":
        _render_rules(watch)
    elif section == "Grupos y frecuencia":
        _render_groups(watch)
    elif section == "Localización automática":
        _render_locator(store, names)
    elif section == "Autorizaciones por método":
        _render_methods(access)
    elif section == "Presupuestos":
        _render_budgets(access, names)
    else:
        _render_incidents(access, names)
