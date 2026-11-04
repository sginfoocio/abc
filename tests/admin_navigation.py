"""Shared navigation for legacy-operation regressions under the consolidated menu."""


def navigate(app, section, *, timeout=90):
    diagnostics = {
        "Configuración general": "Parámetros generales",
        "Datos e importaciones": "Importaciones técnicas",
        "Historial y diagnósticos": "Auditoría, históricos y exportaciones",
        "Piloto y consultas": "Piloto anterior",
        "Recuperación de búsquedas": "Recuperación de búsquedas",
    }
    if section in diagnostics:
        app.selectbox(key="price_admin_section").set_value("Configuración y diagnóstico").run(timeout=timeout)
        app.selectbox(key="watch_diagnostic_task").set_value(diagnostics[section]).run(timeout=timeout)
    else:
        app.selectbox(key="price_admin_section").set_value(section).run(timeout=timeout)
        if section == "Competidores":
            app.selectbox(key="competitor_card_section").set_value(
                "Fuentes y conectores").run(timeout=timeout)
    return app


def watch_section(app, section, *, timeout=30):
    if section in ("Productos vigilados", "Añadir productos", "Reglas de vigilancia", "Grupos y frecuencia"):
        app.selectbox(key="price_admin_section").set_value("Productos en vigilancia").run(timeout=timeout)
        task = {"Reglas de vigilancia": "Reglas", "Grupos y frecuencia": "Grupos y frecuencia"}.get(
            section, "Añadir / importar")
        app.selectbox(key="watch_product_task").set_value(task).run(timeout=timeout)
    elif section in ("Localización automática", "Precios y monitorización"):
        app.selectbox(key="price_admin_section").set_value("Actualizaciones").run(timeout=timeout)
        app.selectbox(key="watch_update_task").set_value("Colas y revisión (avanzado)").run(timeout=timeout)
        app.selectbox(key="watch_advanced_task").set_value(
            "Localización" if section == "Localización automática" else "Monitorización").run(timeout=timeout)
    elif section in ("Alertas", "Bloqueos e incidencias"):
        app.selectbox(key="price_admin_section").set_value("Alertas e incidencias").run(timeout=timeout)
        app.selectbox(key="watch_alert_task").set_value(
            "Alertas comerciales" if section == "Alertas" else "Incidencias y recuperación").run(timeout=timeout)
    else:
        app.selectbox(key="price_admin_section").set_value("Competidores").run(timeout=timeout)
        app.selectbox(key="competitor_card_section").set_value(
            "Acceso y métodos" if section == "Autorizaciones por método" else "Presupuestos y límites").run(
                timeout=timeout)
    return app
