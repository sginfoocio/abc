from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def compose_config(*files: str, profiles: tuple[str, ...] = ()) -> dict:
    if not shutil.which("docker"):
        pytest.skip("Docker Compose unavailable; parsed configuration validated in CI")
    command = ["docker", "compose"]
    for profile in profiles:
        command.extend(["--profile", profile])
    for file in files:
        command.extend(["-f", str(ROOT / file)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, "STAGING_IMAGE": "synthetic:test"},
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("separate_state", [False, True])
def test_luxottica_sender_reads_catalog_from_mounted_state(tmp_path, monkeypatch, separate_state):
    images = tmp_path / "images"
    state = tmp_path / "state"
    monkeypatch.setenv("IMAGE_REPOSITORY_HOST_ROOT", str(images))
    monkeypatch.setenv("IMAGE_REPOSITORY_ROOT", "/app/repo/images")
    monkeypatch.setenv("IMAGE_REPOSITORY_MOUNT_HOST_ROOT", "")
    monkeypatch.setenv("IMAGE_REPOSITORY_MOUNT_ROOT", "")
    if separate_state:
        monkeypatch.setenv("IMAGE_REPOSITORY_STATE_HOST_ROOT", str(state))
    else:
        monkeypatch.setenv("IMAGE_REPOSITORY_STATE_HOST_ROOT", "")
    service = compose_config("docker-compose.yml", profiles=("luxottica-sender",))["services"][
        "abcd-luxottica-sender"]
    catalog = next(volume for volume in service["volumes"] if volume["target"] == "/app/image-state")
    assert service["environment"]["IMAGE_REPOSITORY_STATE_ROOT"] == catalog["target"]
    assert Path(catalog["source"]) == (state if separate_state else images)
    assert catalog["read_only"]
    assert catalog.get("bind", {}).get("create_host_path", False) is False
    image_volume = next(volume for volume in service["volumes"] if volume["target"] == "/app/repo/images")
    assert Path(image_volume["source"]) == images and image_volume["read_only"]


def test_luxottica_sender_mounts_entire_nfs_export_for_read_only_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_REPOSITORY_MOUNT_HOST_ROOT", str(tmp_path / "nas"))
    monkeypatch.setenv("IMAGE_REPOSITORY_MOUNT_ROOT", "/nas")
    monkeypatch.setenv("IMAGE_REPOSITORY_ROOT", "/nas/images")
    monkeypatch.setenv("IMAGE_REPOSITORY_NFS_SOURCE", "nas:/export")
    service = compose_config("docker-compose.yml", profiles=("luxottica-sender",))["services"][
        "abcd-luxottica-sender"]
    nas = next(volume for volume in service["volumes"] if volume["target"] == "/nas")
    assert Path(nas["source"]) == tmp_path / "nas" and nas["read_only"]
    assert service["environment"]["IMAGE_REPOSITORY_MOUNT_ROOT"] == "/nas"
    assert service["environment"]["IMAGE_REPOSITORY_ROOT"] == "/nas/images"
    assert service["environment"]["IMAGE_REPOSITORY_NFS_SOURCE"] == "nas:/export"


@pytest.mark.skipif(os.name == "nt", reason="POSIX entrypoint test")
@pytest.mark.parametrize("command,read_only", [
    (["python", "-B", "luxottica_order_scheduler.py"], True),
    (["python", "poll_luxoptica_mail.py"], False),
])
def test_entrypoint_uses_read_only_guard_only_for_sender(tmp_path, command, read_only):
    log = tmp_path / "calls"
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    python.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "CALLS": str(log), "IMAGE_REPOSITORY_NFS_SOURCE": "nas:/export"}
    subprocess.run(["sh", str(ROOT / "scripts" / "cloud_image_entrypoint.sh"), *command],
                   env=env, check=True, capture_output=True, text=True)
    calls = log.read_text().splitlines()
    assert len(calls) == 2 and calls[1] == " ".join(command[1:])
    assert ("allow_read_only=True" in calls[0]) is read_only
    assert ("check_backup_root" in calls[0]) is not read_only
    assert ("open_catalog" in calls[0]) is read_only


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
    service = config["services"]["image-storage-staging"]
    assert service["user"] == "1037:100"
    environment = service["environment"]
    assert environment["IMAGE_REPOSITORY_WORK_ROOT"] == "/nas/staging/work"
    assert environment["IMAGE_REPOSITORY_STAGING_ROOT"] == "/nas/staging"
    assert environment["IMAGE_REPOSITORY_WORK_NFS_SOURCE"].endswith("/cloud-imagenes")
    volumes = {volume["target"]: volume for volume in service["volumes"]}
    assert volumes["/nas"]["source"] == "/mnt/cloud-imagenes"
    history = volumes["/nas/staging/source/Kering/history.sqlite3"]
    assert history["source"].startswith("/opt/cloud-image-staging/state/")
    assert history["read_only"]
    assert volumes["/staging/state"]["source"] == "/opt/cloud-image-staging/state"
    assert environment["IMAGE_REPOSITORY_WORK_ROOT"] != environment["IMAGE_REPOSITORY_BACKUP_ROOT"]
    # Compose versions may omit false-valued defaults from their normalized JSON.
    assert all(volume.get("bind", {}).get("create_host_path", False) is False
               for volume in service["volumes"])
    source = (ROOT / "deployment/nfs/staging.compose.yml").read_text()
    assert source.count("create_host_path: false") == len(service["volumes"])
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


def test_production_startup_checks_storage_and_deploy_requires_separate_approval():
    entrypoint = (ROOT / "scripts/cloud_image_entrypoint.sh").read_text()
    assert 'if test -n "${IMAGE_REPOSITORY_NFS_SOURCE:-}"' in entrypoint
    assert entrypoint.index("check_image_root") < entrypoint.index('exec "$@"')
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "Deploy to Server" not in workflow
    assert "CLOUD_AUTO_DEPLOY" not in workflow
    manual = (ROOT / ".github/workflows/deploy-cloud-production.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in manual
    assert "environment: production" in manual


def test_staging_capture_uses_matching_local_history_and_source_run(monkeypatch):
    run = "capture-synthetic"
    snapshot = f"/opt/cloud-image-staging/state/frozen/{run}/Kering/history.sqlite3"
    target = f"/nas/staging/source/{run}/Kering/history.sqlite3"
    monkeypatch.setenv("STAGING_HISTORY_SNAPSHOT", snapshot)
    monkeypatch.setenv("STAGING_HISTORY_TARGET", target)
    config = compose_config("deployment/nfs/staging.compose.yml")
    service = config["services"]["image-storage-staging"]
    history = next(volume for volume in service["volumes"] if volume["target"] == target)
    assert history["source"] == snapshot
    assert history["read_only"]
    assert history.get("bind", {}).get("create_host_path", False) is False
    assert service["environment"]["IMAGE_REPOSITORY_STATE_ROOT"] == "/staging/state"
