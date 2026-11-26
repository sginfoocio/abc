"""Pure, non-deploying checks for a future protected Luxottica monitor release."""

import copy
from pathlib import PurePosixPath
import re


SERVICE = "abcd-luxoptica-monitor"
COMMAND = [
    "python", "poll_luxoptica_mail.py", "--interval-minutes", "10",
    "--sender-hint", "luxottica", "--subject-hint", "image",
    "--lookback-days", "30", "--top-messages", "100",
]
REQUIRED_MOUNTS = {"/nas", "/app/data", "/app/image-state", "/app/docs/Luxoptica"}
IMAGE = re.compile(r"ghcr\.io/sginfoocio/abc@sha256:[0-9a-f]{64}")


class MonitorPreflightError(ValueError):
    pass


def prepare_monitor_image_change(config, containers, image):
    """Prepare a paused monitor configuration; never perform a deployment."""
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise MonitorPreflightError("candidate_image_not_immutable")
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        raise MonitorPreflightError("invalid_compose")
    service = config["services"].get(SERVICE)
    if not isinstance(service, dict):
        raise MonitorPreflightError("monitor_missing_from_compose")
    if (service.get("container_name") != SERVICE
            or str(service.get("user")) != "1037:100"
            or service.get("command") != COMMAND):
        raise MonitorPreflightError("monitor_configuration_mismatch")
    if any(key in service for key in ("build", "extends", "env_file", "label_file", "develop")):
        raise MonitorPreflightError("monitor_compose_not_normalized")

    volumes = service.get("volumes")
    if not isinstance(volumes, list):
        raise MonitorPreflightError("invalid_mounts")
    mounts = {}
    for mount in volumes:
        if not isinstance(mount, dict) or mount.get("type") != "bind":
            raise MonitorPreflightError("mount_not_normalized_bind")
        source, target = mount.get("source"), mount.get("target")
        if (not isinstance(source, str) or not PurePosixPath(source).is_absolute()
                or not isinstance(target, str) or not PurePosixPath(target).is_absolute()
                or target in mounts or type(mount.get("read_only", False)) is not bool):
            raise MonitorPreflightError("invalid_mount")
        mounts[target] = (source, not mount.get("read_only", False))
    if not REQUIRED_MOUNTS.issubset(mounts):
        raise MonitorPreflightError("required_mount_missing")

    if not isinstance(containers, list):
        raise MonitorPreflightError("invalid_runtime")
    running = []
    for container in containers:
        if not isinstance(container, dict):
            raise MonitorPreflightError("invalid_runtime")
        labels = (container.get("Config") or {}).get("Labels") or {}
        if ((container.get("State") or {}).get("Running")
                and (container.get("Name") == "/" + SERVICE
                     or (labels.get("com.docker.compose.project") == "cloud-nas"
                         and labels.get("com.docker.compose.service") == SERVICE))):
            running.append(container)
    if len(running) != 1:
        raise MonitorPreflightError("monitor_not_unique")
    current = running[0]
    current_config = current.get("Config") or {}
    labels = current_config.get("Labels") or {}
    if (current.get("Name") != "/" + SERVICE
            or labels.get("com.docker.compose.project") != "cloud-nas"
            or labels.get("com.docker.compose.service") != SERVICE
            or current_config.get("Image") != service.get("image")
            or current_config.get("User") != "1037:100"
            or current_config.get("Cmd") != COMMAND):
        raise MonitorPreflightError("monitor_runtime_mismatch")
    runtime_mounts = {}
    for mount in current.get("Mounts", []):
        target = mount.get("Destination")
        if target in runtime_mounts or type(mount.get("RW")) is not bool:
            raise MonitorPreflightError("invalid_runtime_mount")
        runtime_mounts[target] = (mount.get("Source"), mount["RW"])
    if runtime_mounts != mounts:
        raise MonitorPreflightError("monitor_mount_mismatch")

    result = copy.deepcopy(config)
    result["services"][SERVICE]["image"] = image
    result["services"][SERVICE]["command"] = COMMAND + ["--paused"]
    return result
