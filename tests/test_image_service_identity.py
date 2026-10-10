from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def compose_config(*files: str) -> dict:
    if not shutil.which("docker"):
        pytest.skip("Docker Compose unavailable; parsed configuration validated in CI")
    command = ["docker", "compose"]
    for file in files:
        command.extend(["-f", str(ROOT / file)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, "STAGING_IMAGE": "synthetic:test"},
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_only_image_writers_use_cloud_identity_in_preparation_override():
    config = compose_config("docker-compose.yml", "deployment/nfs/image-services.identity.compose.yml")
    production = compose_config("docker-compose.yml")
    writers = {"abcd-app", "abcd-luxoptica-monitor", "abcd-kering-scheduler"}
    for name in writers:
        service = config["services"][name]
        assert service["user"] == "1037:100"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["environment"]["HOME"] == "/home/cloud-images"
        assert "user" not in production["services"][name]
    assert "user" not in config["services"]["abcd-order-alerts"]


def test_staging_identity_and_owner_only_permission_proposal():
    config = compose_config("deployment/nfs/staging.compose.yml")
    assert config["services"]["image-storage-staging"]["user"] == "1037:100"
    script = (ROOT / "deployment/nfs/set-image-permissions.sh").read_text()
    assert "chown 1037:100" in script
    assert "chmod 0700" in script
    assert "chmod 2770" not in script
    assert "chmod -R" not in script and "chown -R" not in script
    assert "CLOUD_NAS_ACL_VERIFIED" in script


def test_nonroot_smoke_is_executed_as_confirmed_nas_identity():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "--user 1037:100" in workflow
    assert "--user 10001:10001" not in workflow
    entrypoint = (ROOT / "scripts/cloud_image_entrypoint.sh").read_text()
    assert "umask 077" in entrypoint and 'test "$(id -G)" = 100' in entrypoint
