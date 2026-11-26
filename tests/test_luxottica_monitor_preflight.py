import copy

import pytest

from deployment.cloud.luxottica_monitor_preflight import (
    COMMAND, MonitorPreflightError, prepare_monitor_image_change,
)


OLD = "ghcr.io/sginfoocio/abc@sha256:" + "a" * 64
NEW = "ghcr.io/sginfoocio/abc@sha256:" + "b" * 64


@pytest.fixture
def snapshot():
    mounts = [
        {"type": "bind", "source": "/host/nas", "target": "/nas"},
        {"type": "bind", "source": "/host/data", "target": "/app/data"},
        {"type": "bind", "source": "/host/catalog", "target": "/app/image-state"},
        {"type": "bind", "source": "/host/docs", "target": "/app/docs/Luxoptica"},
    ]
    config = {"services": {
        "abcd-app": {"image": "unchanged"},
        "abcd-luxoptica-monitor": {
            "image": OLD, "container_name": "abcd-luxoptica-monitor", "user": "1037:100",
            "command": COMMAND[:], "volumes": mounts,
        },
    }}
    container = {
        "Name": "/abcd-luxoptica-monitor",
        "State": {"Running": True},
        "Config": {
            "Image": OLD, "User": "1037:100", "Cmd": COMMAND[:],
            "Labels": {"com.docker.compose.project": "cloud-nas",
                       "com.docker.compose.service": "abcd-luxoptica-monitor"},
        },
        "Mounts": [{"Source": m["source"], "Destination": m["target"], "RW": True} for m in mounts],
    }
    return config, [container]


def test_changes_only_monitor_image_without_modifying_input(snapshot):
    config, containers = snapshot
    before = copy.deepcopy(config)
    updated = prepare_monitor_image_change(config, containers, NEW)
    assert config == before
    assert updated["services"]["abcd-app"] == before["services"]["abcd-app"]
    updated["services"]["abcd-luxoptica-monitor"]["image"] = OLD
    assert updated == before


@pytest.mark.parametrize("image", ["latest", "ghcr.io/sginfoocio/abc:latest", NEW.upper()])
def test_rejects_mutable_or_untrusted_image(snapshot, image):
    with pytest.raises(MonitorPreflightError, match="candidate_image_not_immutable"):
        prepare_monitor_image_change(*snapshot, image)


def test_rejects_duplicate_running_monitor(snapshot):
    config, containers = snapshot
    duplicate = copy.deepcopy(containers[0])
    duplicate["Name"] = "/another-monitor"
    containers.append(duplicate)
    with pytest.raises(MonitorPreflightError, match="monitor_not_unique"):
        prepare_monitor_image_change(config, containers, NEW)


def test_ignores_stopped_rollback_container(snapshot):
    config, containers = snapshot
    old = copy.deepcopy(containers[0])
    old["Name"] = "/old-monitor"
    old["State"]["Running"] = False
    containers.append(old)
    prepare_monitor_image_change(config, containers, NEW)


@pytest.mark.parametrize("change", ["mount", "command", "user", "old_image"])
def test_rejects_runtime_or_mount_drift(snapshot, change):
    config, containers = snapshot
    current = containers[0]
    if change == "mount":
        current["Mounts"][2]["Source"] = "/other/catalog"
    elif change == "command":
        current["Config"]["Cmd"][-1] = "101"
    elif change == "user":
        current["Config"]["User"] = "0"
    else:
        current["Config"]["Image"] = NEW
    with pytest.raises(MonitorPreflightError):
        prepare_monitor_image_change(config, containers, NEW)


def test_rejects_missing_catalog_mount(snapshot):
    config, containers = snapshot
    config["services"]["abcd-luxoptica-monitor"]["volumes"].pop(2)
    with pytest.raises(MonitorPreflightError, match="required_mount_missing"):
        prepare_monitor_image_change(config, containers, NEW)
