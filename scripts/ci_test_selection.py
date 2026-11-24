"""Select the offline CI suite from the changes actually checked out."""

import json
import os
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path


ROOT_TESTS = ("test_transform_luxottica_masterdata.py", "test_validate_masterdata_odoo_dryrun.py")
RULES = {
    "ci": (
        (".github/workflows/deploy.yml", "scripts/ci_test_selection.py"),
        ("tests/test_ci_stacked_prs.py", "tests/test_cloud_production_deploy.py",
         "tests/test_release_versioning.py", "tests/test_dockerfile_provider_modules.py"),
    ),
    "gio": (
        ("gio_cloud.py", "gio_reports.py", "app_pages/gio.py"),
        ("tests/test_gio.py",),
    ),
    "images": (
        ("app_pages/*image*.py", "app_pages/provider*.py", "image*.py", "repository*.py", "bulk_image*.py",
         "provider*.py", "market_publication.py", "migrate_image*.py", "marcolin*.py", "safilo*.py", "nuorder*.py",
         "scripts/*image*.py", "scripts/provider_requests_migrate.py", "scripts/audit_provider_odoo.py",
         "scripts/marcolin_odoo_pilot.py"),
        ("tests/test_*image*.py", "tests/test_bulk_image*.py", "tests/test_repository*.py", "tests/test_provider*.py",
         "tests/test_catalog_reader_parity.py", "tests/test_market_publication.py", "tests/test_kering*.py",
         "tests/test_graph_mail_downloader.py", "tests/test_marcolin*.py", "tests/test_audit_provider_odoo.py",
         "tests/test_nfs_repository_guard.py", "tests/test_app_navigation.py"),
    ),
    "kering": (
        ("kering*.py", "scripts/validate_kering_portal.py"),
        ("tests/test_kering*.py", "tests/test_provider*.py", "tests/test_cloud_dashboard.py",
         "tests/test_image*.py", "tests/test_repository*.py", "tests/test_market_publication.py"),
    ),
    "luxottica": (
        ("*luxoptica*.py", "graph_mail_downloader.py", "app_pages/luxoptica.py", "service_stop.py",
         "scripts/smoke_luxoptica_stop.py"),
        ("tests/test_graph_mail_downloader.py", "tests/test_luxoptica_stop.py", "tests/test_validate_luxottica.py",
         "tests/test_image_repository.py", "tests/test_repository_storage.py", "tests/test_cloud_dashboard.py",
         "tests/test_provider_order_panel.py"),
    ),
    "prices": (
        ("price_*.py", "prestashop_prices.py", "price_*.json", "vytria_price_sync.py",
         "competitor_requirements.py", "app_pages/price*.py",
         "scripts/*price*.py", "scripts/audit_vytria*.py", "scripts/reevaluate_discovery_candidates.py"),
        ("tests/test_price*.py", "tests/test_vytria*.py", "tests/test_audit_vytria*.py",
         "tests/test_manual_review_continuity_repro.py"),
    ),
    "odoo-masterdata": (
        ("db_config.py", "db_loader*.py", "app_exports.py", "*masterdata*.py", "database/*"),
        ("tests/test_app_exports.py", "tests/test_abcd_snapshots*.py", "tests/test_audit*.py",
         "tests/test_provider*.py", "tests/test_marcolin_odoo_pilot.py", "tests/test_kering_images.py",
         "tests/test_adyen*.py", "tests/test_order_alert*.py"),
    ),
}
PRICE_SOURCE_TESTS = {
    "price_access.py": (
        "tests/test_price_access.py",
        "tests/test_price_locator.py",
        "tests/test_price_pilot.py",
        "tests/test_price_point_check.py",
        "tests/test_price_review_refresh.py",
        "tests/test_price_trial.py",
        "tests/test_price_monitor.py",
        "tests/test_price_recovery.py",
        "tests/test_manual_review_continuity_repro.py",
    ),
    "app_pages/price_comparator.py": (
        "tests/test_prestashop_prices.py",
        "tests/test_price_analytics.py",
        "tests/test_price_analytics_dates.py",
        "tests/test_price_comparator.py",
        "tests/test_price_settings_ui.py",
        "tests/test_vytria_price_sync.py",
    ),
    "app_pages/price_watch.py": (
        "tests/test_price_watch.py",
        "tests/test_vytria_price_sync.py",
    ),
    "price_analytics.py": ("tests/test_price_analytics.py", "tests/test_price_analytics_dates.py"),
    "price_comparator.py": (
        "tests/test_prestashop_prices.py",
        "tests/test_price_analytics.py",
        "tests/test_price_analytics_dates.py",
        "tests/test_price_comparator.py",
        "tests/test_price_settings_ui.py",
        "tests/test_vytria_price_sync.py",
    ),
    "price_diagnostic.py": (
        "tests/test_price_diagnostic.py",
        "tests/test_price_trial.py",
        "tests/test_price_recovery.py",
        "tests/test_manual_review_continuity_repro.py",
        "tests/test_price_web_competitor_pages.py",
    ),
    "price_trial.py": ("tests/test_price_trial.py",),
    "price_monitor.py": (
        "tests/test_manual_review_continuity_repro.py",
        "tests/test_price_monitor.py",
        "tests/test_price_recovery.py",
        "tests/test_price_review_fixes.py",
        "tests/test_price_review_refresh.py",
    ),
    "price_references.py": (
        "tests/test_price_references.py",
        "tests/test_price_identity_regression.py",
        "tests/test_price_web_competitor_pages.py",
    ),
    "price_url_candidates.py": ("tests/test_price_url_candidates.py",),
    "price_saved_reevaluation.py": ("tests/test_price_saved_reevaluation.py",),
    "price_recovery.py": (
        "tests/test_price_recovery.py",
        "tests/test_manual_review_continuity_repro.py",
        "tests/test_price_review_fixes.py",
        "tests/test_price_review_refresh.py",
    ),
    "prestashop_prices.py": ("tests/test_prestashop_prices.py",),
    "price_web.py": (
        "tests/test_price_saved_reevaluation.py",
        "tests/test_price_web_competitor_pages.py",
    ),
    "price_web_extract.py": (
        "tests/test_price_comparator.py",
        "tests/test_price_continuity_regressions.py",
        "tests/test_price_reviewed_variants.py",
        "tests/test_price_saved_reevaluation.py",
        "tests/test_price_web.py",
        "tests/test_price_web_competitor_pages.py",
    ),
    "scripts/smoke_price_diagnostic.py": ("tests/test_dockerfile_provider_modules.py",),
    "vytria_price_sync.py": ("tests/test_prestashop_prices.py", "tests/test_vytria_price_sync.py"),
}
SCOPED_DEPENDENCY_TESTS = {
    "pytest.ini": ("tests/test_ci_stacked_prs.py", "tests/test_price_monitor.py"),
    "Dockerfile": ("tests/test_dockerfile_provider_modules.py",),
    "docs/Precios/100_EAN/busqueda_11_20261122/matriz_11x14.json": (
        "tests/test_price_url_candidates.py",
    ),
    "docs/Precios/100_EAN/busqueda_11_20261122/rutas_candidatas_primarias.json": (
        "tests/test_price_url_candidates.py",
        "tests/test_dockerfile_provider_modules.py",
    ),
    "docs/Precios/100_EAN/paquete_11x14_20261122.zip": (
        "tests/test_price_url_candidates.py",
    ),
    "requirements-dev.txt": (
        "tests/test_dockerfile_provider_modules.py",
        "tests/test_prestashop_prices.py",
        "tests/test_vytria_price_sync.py",
    ),
    "requirements.in": (
        "tests/test_dockerfile_provider_modules.py",
        "tests/test_prestashop_prices.py",
        "tests/test_vytria_price_sync.py",
    ),
    "requirements.txt": (
        "tests/test_dockerfile_provider_modules.py",
        "tests/test_prestashop_prices.py",
        "tests/test_vytria_price_sync.py",
    ),
}
GLOBAL_DEPENDENCIES = ("db_config.py", "pyproject.toml", "setup.cfg", "conftest.py",
                       "tests/conftest.py")
MASTERDATA_DEPENDENCIES = {
    ROOT_TESTS[0]: ("transform_luxottica_masterdata.py", ROOT_TESTS[0], "app_exports.py",
                    "image_repository.py", "repository_storage.py", "service_stop.py"),
    ROOT_TESTS[1]: ("validate_masterdata_odoo_dryrun.py", ROOT_TESTS[1]),
}
DOCS = ("*.md",)


def matches(path, patterns):
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def comparison_base(event, payload, head, run_git=git):
    if event == "pull_request":
        pr = payload["pull_request"]
        base, tip = pr["base"]["sha"], pr["head"]["sha"]
        parents = run_git("rev-list", "--parents", "-n", "1", head).decode().split()
        if parents != [head, base, tip]:
            raise ValueError("checked-out merge commit does not match PR base/head")
    elif event == "push":
        base = payload.get("before")
        if payload.get("after") != head:
            raise ValueError("checked-out commit does not match push tip")
    else:
        raise ValueError(f"{event}: complete suite requested")
    if not base or not re.fullmatch(r"[a-f0-9]{40}", base) or base == "0" * 40:
        raise ValueError("no usable base commit")
    return base


def changed_paths(base, head, run_git=git):
    # Disabling rename detection includes both paths for moves and deletions.
    output = run_git("diff", "--name-only", "--no-renames", "-z", base, head, "--")
    return sorted(os.fsdecode(path) for path in output.split(b"\0") if path)


def select(paths, test_files):
    tests, reasons = set(), []
    broad = False
    for path in paths:
        if matches(path, DOCS):
            continue
        if path in SCOPED_DEPENDENCY_TESTS:
            group_tests = {test for test in SCOPED_DEPENDENCY_TESTS[path] if test in test_files}
            if group_tests:
                tests.update(group_tests)
                reasons.append(f"scoped dependency: {path}")
            else:
                broad = True
                reasons.append(f"conservative regression: missing dependency tests for {path}")
            continue
        if matches(path, GLOBAL_DEPENDENCIES):
            return ["tests", *ROOT_TESTS], [f"shared dependency or test configuration: {path}"]
        groups = [name for name, (source, suite) in RULES.items() if matches(path, (*source, *suite))]
        covered = False
        for name in groups:
            if name == "prices":
                if path in test_files:
                    group_tests = {path}
                    reason = f"direct price regression: {path}"
                else:
                    group_tests = {test for test in PRICE_SOURCE_TESTS.get(path, ()) if test in test_files}
                    if group_tests:
                        reason = f"price dependencies: {path}"
                    else:
                        group_tests = {test for test in test_files if matches(test, RULES[name][1])}
                        reason = f"conservative price regression: unclassified source {path}"
                covered |= bool(group_tests)
                tests.update(group_tests)
                reasons.append(reason)
                continue
            group_tests = {test for test in test_files if matches(test, RULES[name][1])}
            covered |= bool(group_tests)
            tests.update(group_tests)
            reasons.append(f"{name}: {path}")
        if path in test_files:
            tests.add(path)
        roots = {test for test, inputs in MASTERDATA_DEPENDENCIES.items() if path in inputs}
        tests.update(roots)
        if roots:
            reasons.append(f"masterdata dependency: {path}")
        if not (covered or roots or path in test_files):
            broad = True
            reasons.append(f"conservative regression (shared or unclassified): {path}")
    return (["tests", *sorted(tests.intersection(ROOT_TESTS))] if broad else sorted(tests)), reasons


def main():
    head = git("rev-parse", "HEAD").decode().strip()
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    try:
        base = comparison_base(os.environ["GITHUB_EVENT_NAME"], payload, head)
        paths = changed_paths(base, head)
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        paths = []
        targets, reasons = ["tests", *ROOT_TESTS], [f"complete suite: {error}"]
        docs_only = False
    else:
        targets, reasons = select(paths, {p.as_posix() for p in Path("tests").rglob("test_*.py")})
        docs_only = bool(paths) and all(matches(path, DOCS) for path in paths)
        if docs_only:
            subprocess.run(["git", "diff", "--check", base, head, "--"], check=True)
    benchmark = bool(targets) and ("tests" in targets or "tests/test_image_thumbnails.py" in targets)
    safe_reasons = [json.dumps(reason, ensure_ascii=True).replace("`", "\\u0060") for reason in reasons]
    summary = "\n".join(("### Offline CI selection", "```text", *(safe_reasons or ["No functional changes"]),
                         "Tests: " + json.dumps(targets).replace("`", "\\u0060"), "```", ""))
    print(summary, flush=True)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write(summary)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"documentation_only={str(docs_only).lower()}\nbenchmark={str(benchmark).lower()}\n")
    if targets:
        subprocess.run(["python", "-m", "pytest", "-q", "-m", "not stress", "--", *targets], check=True)


if __name__ == "__main__":
    main()
