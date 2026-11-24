from pathlib import Path
import subprocess

import pytest

from scripts.ci_test_selection import (
    ROOT_TESTS, SCOPED_DEPENDENCY_TESTS, changed_paths, comparison_base, select,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")


def block(name: str) -> str:
    start = WORKFLOW.index(f"\n  {name}:\n")
    following = [i for i in (WORKFLOW.find("\n  build-and-push:\n", start + 1), WORKFLOW.find("\n  tests:\n", start + 1))
                 if i > 0]
    return WORKFLOW[start:min(following) if following else len(WORKFLOW)]


def trigger(kind: str) -> str:
    start = WORKFLOW.index(f"\n  {kind}:\n")
    end = WORKFLOW.index("\n\n", start)
    section = WORKFLOW[start:end]
    return section[:section.index("\n  pull_request:", 1)] if kind == "push" and "\n  pull_request:" in section else section


def test_pull_requests_run_for_any_base_so_stacked_prs_get_checks():
    section = trigger("pull_request")
    assert "- '**'" in section


def test_push_is_still_limited_to_main_and_master():
    section = trigger("push")
    assert "- main" in section and "- master" in section and "'**'" not in section


def test_tests_job_runs_for_pull_requests_and_is_required_by_the_build():
    assert "\n    if:" not in block("tests")
    assert "fetch-depth: 0" in block("tests")
    assert "needs: tests" in block("build-and-push")
    assert "workflow_dispatch:" in WORKFLOW and "schedule:" in WORKFLOW
    assert "if: github.event_name == 'push' || (github.event_name == 'pull_request'" in block("build-and-push")


def test_publication_and_registry_login_remain_push_only():
    build = block("build-and-push")
    assert "push: ${{ github.event_name == 'push' }}" in build
    login = build[build.index("name: Log in to Container Registry"):]
    assert login.splitlines()[1].strip() == "if: github.event_name == 'push'"
    assert "secrets." not in block("tests")
    assert "pull_request_target" not in WORKFLOW


def test_production_deploy_is_only_triggered_by_a_successful_push_run_on_main():
    production = (ROOT / ".github/workflows/deploy-cloud-production.yml").read_text(encoding="utf-8")
    assert "github.event.workflow_run.event == 'push'" in production
    assert "github.event.workflow_run.head_branch == 'main'" in production


@pytest.mark.parametrize(("files", "included", "root_tests"), [
    (["docs/README.md"], set(), set()),
    (["docs/Precios/100_EAN/busqueda_11_20261122/matriz_11x14.json"],
     {"test_price_url_candidates.py"}, set()),
    (["docs/Precios/100_EAN/busqueda_11_20261122/rutas_candidatas_primarias.json"],
     {"test_price_url_candidates.py", "test_dockerfile_provider_modules.py"}, set()),
    (["docs/Precios/100_EAN/paquete_11x14_20261122.zip"],
     {"test_price_url_candidates.py"}, set()),
    ([".github/workflows/deploy.yml", "scripts/ci_test_selection.py", "tests/test_ci_stacked_prs.py"],
     {"test_ci_stacked_prs.py", "test_cloud_production_deploy.py", "test_release_versioning.py",
      "test_dockerfile_provider_modules.py"}, set()),
    (["gio_cloud.py"], {"test_gio.py"}, set()),
    (["app_enhanced.py"], {"test_gio.py", "test_app_navigation.py"}, set()),
    (["app_pages/provider_orders_page.py"], {"test_provider_order_panel.py", "test_image_library.py"}, set()),
    (["kering_portal.py"], {"test_kering_images.py", "test_repository_storage.py"}, set()),
    (["poll_luxoptica_mail.py"], {"test_luxoptica_stop.py", "test_graph_mail_downloader.py"}, set()),
    (["price_web_fetch.py"], {"test_price_web.py", "test_price_monitor.py"}, set()),
    (["transform_luxottica_masterdata.py"], {"test_provider_odoo_orders.py"}, {ROOT_TESTS[0]}),
    (["validate_masterdata_odoo_dryrun.py"], {"test_app_exports.py"}, {ROOT_TESTS[1]}),
    (["app_exports.py"], {"test_app_exports.py"}, {ROOT_TESTS[0]}),
    (["image_repository.py"], {"test_repository_multi_export.py"}, {ROOT_TESTS[0]}),
    (["db_config.py"], {"tests"}, set(ROOT_TESTS)),
    (["pytest.ini"], {"test_ci_stacked_prs.py", "test_price_monitor.py"}, set()),
    (["requirements.txt"], {"test_dockerfile_provider_modules.py", "test_prestashop_prices.py",
                            "test_vytria_price_sync.py"}, set()),
    (["scripts/dependency_locks.py"], {"test_dependency_locks.py"}, set()),
    (["Dockerfile"], {"test_dockerfile_provider_modules.py"}, set()),
    (["price_comparator.py"], {"test_price_comparator.py", "test_price_analytics.py",
                               "test_price_analytics_dates.py", "test_prestashop_prices.py",
                               "test_price_settings_ui.py", "test_vytria_price_sync.py"}, set()),
    (["prestashop_prices.py"], {"test_prestashop_prices.py"}, set()),
    (["tests/test_vytria_price_sync.py"], {"test_vytria_price_sync.py"}, set()),
    (["app_pages/price_comparator.py"], {"test_prestashop_prices.py", "test_price_analytics.py",
                                         "test_price_analytics_dates.py", "test_price_comparator.py",
                                         "test_price_settings_ui.py", "test_vytria_price_sync.py"}, set()),
    (["new_service.py"], {"tests"}, set()),
    (["new_service.py", "app_exports.py"], {"tests"}, {ROOT_TESTS[0]}),
    (["tests/fixtures/new.json"], {"tests"}, set()),
])
def test_impact_selection(files, included, root_tests):
    inventory = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}
    targets, reasons = select(files, inventory)
    assert set(targets).intersection(ROOT_TESTS) == root_tests
    assert included <= {Path(p).name for p in targets}
    assert ("tests" in targets) == ("tests" in included)
    assert bool(targets) == bool(reasons) == bool(included)


def test_scoped_dependency_keeps_overlapping_domain_tests(monkeypatch):
    monkeypatch.setitem(
        SCOPED_DEPENDENCY_TESTS,
        "app_enhanced.py",
        ("tests/test_provider_order_panel.py",),
    )
    inventory = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}

    targets, _ = select(["app_enhanced.py"], inventory)

    assert {
        "tests/test_provider_order_panel.py",
        "tests/test_gio.py",
        "tests/test_app_navigation.py",
    } <= set(targets)


def test_price_module_selects_direct_regressions_instead_of_entire_price_suite():
    inventory = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}
    targets, reasons = select(["price_references.py"], inventory)
    assert set(targets) == {
        "tests/test_price_references.py",
        "tests/test_price_identity_regression.py",
        "tests/test_price_web_competitor_pages.py",
    }
    assert len(targets) < sum(path.startswith("tests/test_price") for path in inventory)
    assert reasons == ["price dependencies: price_references.py"]


@pytest.mark.parametrize(("source", "expected"), [
    ("price_url_candidates.py", {"tests/test_price_url_candidates.py"}),
    ("price_trial.py", {"tests/test_price_trial.py"}),
    ("price_access.py", {
        "tests/test_price_access.py", "tests/test_price_locator.py", "tests/test_price_pilot.py",
        "tests/test_price_point_check.py", "tests/test_price_review_refresh.py", "tests/test_price_trial.py",
        "tests/test_price_monitor.py", "tests/test_price_recovery.py", "tests/test_manual_review_continuity_repro.py",
    }),
    ("price_diagnostic.py", {
        "tests/test_price_diagnostic.py",
        "tests/test_price_trial.py",
        "tests/test_price_recovery.py",
        "tests/test_manual_review_continuity_repro.py",
        "tests/test_price_web_competitor_pages.py",
    }),
    ("price_monitor.py", {
        "tests/test_manual_review_continuity_repro.py",
        "tests/test_price_monitor.py",
        "tests/test_price_recovery.py",
        "tests/test_price_review_fixes.py",
        "tests/test_price_review_refresh.py",
    }),
    ("price_web.py", {
        "tests/test_price_saved_reevaluation.py",
        "tests/test_price_web_competitor_pages.py",
    }),
    ("price_web_extract.py", {
        "tests/test_price_comparator.py",
        "tests/test_price_continuity_regressions.py",
        "tests/test_price_reviewed_variants.py",
        "tests/test_price_saved_reevaluation.py",
        "tests/test_price_web.py",
        "tests/test_price_web_competitor_pages.py",
    }),
])
def test_price_dependencies_select_focused_regressions(source, expected):
    inventory = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}
    targets, reasons = select([source], inventory)
    assert set(targets) == expected
    assert "tests/test_price_watch.py" not in targets
    assert reasons == [f"price dependencies: {source}"]


def test_vytria_dependency_selection_avoids_the_full_price_suite():
    inventory = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}
    targets, _ = select([
        "prestashop_prices.py", "price_comparator.py", "app_pages/price_comparator.py",
        "app_pages/price_watch.py", "requirements.txt", "Dockerfile",
    ], inventory)
    assert "tests" not in targets
    assert not set(targets).intersection(ROOT_TESTS)
    assert {
        "tests/test_prestashop_prices.py",
        "tests/test_price_comparator.py",
        "tests/test_price_analytics.py",
        "tests/test_price_watch.py",
        "tests/test_vytria_price_sync.py",
        "tests/test_dockerfile_provider_modules.py",
    } <= set(targets)
    assert "tests/test_app_exports.py" not in targets
    assert "tests/test_price_monitor.py" not in targets
    assert len(targets) < sum(path.startswith("tests/test_price") for path in inventory)


def test_stacked_pr_and_multi_commit_push_use_different_bases(tmp_path):
    def run_git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path)

    def commit(file):
        (tmp_path / file).write_text(file, encoding="utf-8")
        run_git("add", file)
        run_git("commit", "-qm", file)
        return run_git("rev-parse", "HEAD").decode().strip()

    run_git("init", "-qb", "main")
    run_git("config", "user.email", "test@example.invalid")
    run_git("config", "user.name", "CI selector test")
    start = commit("README.md")
    base = commit("kering_portal.py")
    run_git("switch", "-qc", "stacked")
    commit("price_monitor.py")
    run_git("mv", "price_monitor.py", "price_scheduler.py")
    tip = commit("price_scheduler.py")
    run_git("switch", "-q", "main")
    run_git("merge", "--no-ff", "-qm", "Synthetic PR merge", "stacked")
    head = run_git("rev-parse", "HEAD").decode().strip()
    payload = {"pull_request": {"base": {"sha": base}, "head": {"sha": tip}}}
    pr_base = comparison_base("pull_request", payload, head, run_git)
    push_base = comparison_base("push", {"before": start, "after": head}, head, run_git)
    assert changed_paths(pr_base, head, run_git) == ["price_scheduler.py"]
    assert changed_paths(push_base, head, run_git) == ["kering_portal.py", "price_scheduler.py"]
    assert changed_paths(tip, head, run_git) == []
    with pytest.raises(ValueError, match="does not match"):
        comparison_base("pull_request", payload, tip, run_git)
    with pytest.raises(ValueError, match="no usable base"):
        comparison_base("push", {"before": "0" * 40, "after": head}, head, run_git)
