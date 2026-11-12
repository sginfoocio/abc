"""The provider-image page is in the navigation, so its modules must ship in the image (#59)."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("provider_images", "provider_images_ui", "provider_odoo_orders",
           "repository_catalog_readonly", "marcolin_images", "safilo_images", "nuorder_images")


def _copied(dockerfile):
    names = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and "--from" not in line:
            names.update(part for part in line.split()[1:-1])
    return names


def test_dockerfile_copies_every_provider_module():
    copied = _copied((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert [m for m in MODULES if f"{m}.py" not in copied] == []


def test_dockerfile_copies_local_dependencies_of_provider_modules():
    copied = _copied((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    for module in MODULES:
        text = (ROOT / f"{module}.py").read_text(encoding="utf-8")
        for dep in set(re.findall(r"^\s*(?:from|import) (\w+)", text, re.M)):
            if (ROOT / f"{dep}.py").exists():
                assert f"{dep}.py" in copied, f"{module} imports {dep}, which the image lacks"


def test_docker_context_allows_only_the_required_documentary_resource():
    resource = "docs/PRICE_COVERAGE_MEASUREMENT_EVIDENCE.csv"
    rules = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    # Ordered Docker negation contract: reopen the directory, exclude its contents, allow one file.
    docs_rules = [rule for rule in rules if rule.lstrip("!").split("/")[0] == "docs"]
    assert docs_rules == ["docs/", "!docs/", "docs/**", "!" + resource]
    assert rules.index("*.csv") < rules.index("!" + resource)
    assert resource in _copied((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert (ROOT / resource).is_file()
    assert "COPY docs " not in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_missing_runtime_evidence_is_an_explicit_failure(tmp_path, monkeypatch):
    import price_url_candidates as candidates

    candidates._measurement_evidence.cache_clear()
    monkeypatch.setattr(candidates, "MEASUREMENT_EVIDENCE_PATH", tmp_path / "missing.csv")
    try:
        with pytest.raises(FileNotFoundError):
            candidates.resolve_product_attributes(
                {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"})
    finally:
        candidates._measurement_evidence.cache_clear()


def test_provider_page_imports_with_only_the_files_the_image_copies(tmp_path):
    """Simulate the image: only Dockerfile COPY sources are available, then import the page."""
    import shutil
    import subprocess
    import sys

    image = tmp_path / "app"
    image.mkdir()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY ") or "--from" in line:
            continue
        *sources, destination = line.split()[1:]
        for source in sources:
            origin = ROOT / source
            target = image / destination.removeprefix("./")
            if destination == ".":
                target = image / origin.name
            if origin.is_dir():
                shutil.copytree(origin, target, dirs_exist_ok=True)
            elif origin.exists():
                if str(destination).endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    target = target / origin.name
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(origin, target)
    code = (
        "import provider_images_ui, provider_odoo_orders, repository_catalog_readonly, "
        "marcolin_images, safilo_images, nuorder_images\n"
        "from app_pages.image_library_page import _visible_previews\n"
        "from image_thumbnails import validate_visible\n"
        "from price_url_candidates import documented_measurement_records, resolve_product_attributes\n"
        "assert len(documented_measurement_records()) == 9\n"
        "result = resolve_product_attributes({'ean': '8056597875349', 'brand': 'Prada', "
        "'model': 'PR 27ZS', 'color': '16K08Z'})\n"
        "assert not result.conflict\n"
        "assert (result.product['caliber'], result.product['bridge']) == ('54', '21')\n"
        "assert result.evidence['fuente_medidas'] == 'E041'\n"
    )
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(image),
                              "PYTHONDONTWRITEBYTECODE": "1"})
    assert run.returncode == 0, run.stderr[-800:]


def test_runtime_import_smoke_covers_provider_modules():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert all(m in workflow for m in MODULES)
