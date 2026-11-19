"""Shared navigation for administration and diagnostic regressions."""


def _diagnostics(app, section, *, timeout):
    if app.radio(key="top_level_section").value != "Diagnóstico técnico":
        app.radio(key="top_level_section").set_value("Diagnóstico técnico").run(timeout=timeout)
    app.selectbox(key="price_diagnostic_section").set_value(section).run(timeout=timeout)
    return app


def navigate(app, section, *, timeout=90):
    diagnostics = {
        "Configuración general": ("Ajustes técnicos", None),
        "Datos e importaciones": ("Importaciones", None),
        "Historial y diagnósticos": ("Históricos y auditoría", None),
        "Piloto y consultas": ("Búsqueda manual (avanzada)", None),
        "Recuperación de búsquedas": ("Colas y recuperaciones", "Incidencias y recuperación"),
    }
    if section in diagnostics:
        page, task = diagnostics[section]
        _diagnostics(app, page, timeout=timeout)
        if task and task != "Incidencias y recuperación":
            app.selectbox(key="price_pilot_section").set_value(task).run(timeout=timeout)
        elif task:
            app.selectbox(key="diagnostic_queue_task").set_value(task).run(timeout=timeout)
        return app
    if section == "Competidores":
        app.radio(key="top_level_section").set_value("Administración").run(timeout=timeout)
        app.selectbox(key="price_admin_section").set_value("Tiendas").run(timeout=timeout)
        app.selectbox(key="competitor_card_section").set_value("Fuentes y conectores").run(timeout=timeout)
    else:
        app.radio(key="top_level_section").set_value("Administración").run(timeout=timeout)
        app.selectbox(key="price_admin_section").set_value(section).run(timeout=timeout)
    return app


def watch_section(app, section, *, timeout=30):
    product_sections = ("Productos vigilados", "Añadir productos", "Reglas de vigilancia", "Grupos y frecuencia")
    if section in product_sections:
        app.radio(key="top_level_section").set_value("Administración").run(timeout=timeout)
        app.selectbox(key="price_admin_section").set_value("Productos").run(timeout=timeout)
        task = {"Reglas de vigilancia": "Reglas", "Grupos y frecuencia": "Grupos y frecuencia"}.get(
            section, "Añadir / importar")
        app.selectbox(key="watch_product_task").set_value(task).run(timeout=timeout)
    elif section in ("Localización automática", "Precios y monitorización", "Alertas", "Bloqueos e incidencias"):
        _diagnostics(app, "Colas y recuperaciones", timeout=timeout)
        task = {
            "Localización automática": "Localización automática",
            "Precios y monitorización": "Monitorización",
            "Alertas": "Incidencias y recuperación",
            "Bloqueos e incidencias": "Incidencias y recuperación",
        }[section]
        app.selectbox(key="diagnostic_queue_task").set_value(task).run(timeout=timeout)
    else:
        app.radio(key="top_level_section").set_value("Administración").run(timeout=timeout)
        app.selectbox(key="price_admin_section").set_value("Tiendas").run(timeout=timeout)
        app.selectbox(key="competitor_card_section").set_value(
            "Acceso y métodos" if section == "Autorizaciones por método" else "Presupuestos y límites"
        ).run(timeout=timeout)
    return app
