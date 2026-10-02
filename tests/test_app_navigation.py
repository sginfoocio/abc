from app_navigation import build_navigation


class FakeStreamlit:
    def __init__(self) -> None:
        self.groups = None
        self.position = None

    def navigation(self, groups, position):
        self.groups = groups
        self.position = position
        return object()


def _pages() -> dict[str, str]:
    return {key: key for key in (
        "home",
        "master_import",
        "master_dictionary",
        "master_dryrun",
        "abc_home",
        "abc_search",
        "abc_reports",
        "abc_history",
        "abc_detail",
        "luxoptica_images",
        "luxoptica_pending",
        "alerts",
        "settings",
    )}


def test_masterdata_navigation_exposes_only_authorized_areas() -> None:
    streamlit = FakeStreamlit()

    build_navigation(streamlit, "masterdata", _pages())

    assert set(streamlit.groups) == {"Inicio", "Master Data"}
    assert streamlit.position == "sidebar"
    assert streamlit.groups["Master Data"] == ["master_import", "master_dictionary", "master_dryrun"]


def test_admin_navigation_keeps_all_existing_page_groups() -> None:
    streamlit = FakeStreamlit()

    build_navigation(streamlit, "admin", _pages())

    assert set(streamlit.groups) == {
        "Inicio",
        "Análisis ABC",
        "Master Data",
        "Repositorio de imágenes",
        "Alertas",
        "Configuración",
    }