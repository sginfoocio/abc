"""Image bytes and durable local state have independent, validated storage roots."""
import os
from pathlib import Path

from scripts.nfs_repository_guard import require_nfs, require_nfs_directory, EXPECTED_SOURCE, _unescape


STAGING_ROLES = {"source", "images", "backups", "recovery", "work"}


def staging_root() -> Path | None:
    configured = os.getenv("IMAGE_REPOSITORY_STAGING_ROOT", "").strip()
    if not configured:
        return None
    if ".." in Path(configured).parts:
        raise ValueError("Staging no admite rutas con ..")
    root = Path(os.path.abspath(configured))
    mount_value = os.getenv("IMAGE_REPOSITORY_STAGING_MOUNT_ROOT", "").strip()
    if not mount_value:
        raise ValueError("Staging requiere montaje explicito")
    if ".." in Path(mount_value).parts:
        raise ValueError("Montaje staging no admite aliases")
    mount = Path(os.path.abspath(mount_value))
    if root.name != "staging" or root.parent != mount:
        raise ValueError("Staging debe ser hijo directo staging del montaje")
    require_nfs_directory(root, mount, EXPECTED_SOURCE)
    return root


def check_staging_role(path: Path, role: str) -> bool:
    root = staging_root()
    if root is None:
        return False
    if ".." in path.parts or path.absolute().resolve() != path.absolute():
        raise ValueError("Ruta staging no admite aliases")
    canonical = Path(os.path.abspath(path))
    if role not in STAGING_ROLES or canonical != root / role:
        raise ValueError(f"Ruta staging debe ser exactamente {role}, sin padres ni aliases")
    mount = Path(os.environ["IMAGE_REPOSITORY_STAGING_MOUNT_ROOT"]).absolute()
    require_nfs_directory(canonical, mount, EXPECTED_SOURCE)
    return True


def check_migration_target(path: Path, role: str = "images") -> None:
    if path.absolute().resolve() != path.absolute() or ".." in path.parts:
        raise ValueError("Destino de migracion no admite aliases")
    if check_staging_role(path, role):
        return
    canonical = path.resolve()
    reserved = (Path("/mnt/cloud-imagenes/staging").resolve(), Path("/nas/staging").resolve())
    if "staging" in canonical.parts or (canonical / "staging").exists() or any(
            canonical == root or canonical in root.parents for root in reserved):
        raise ValueError("Destino de migracion no puede contener ni mezclar staging")


def excluded_staging_path(path: Path) -> bool:
    return "staging" in path.parts


def filesystem_mount(path: Path):
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists():
        return None
    path = path.resolve()
    matches = []
    for line in mountinfo.read_text(encoding="utf-8").splitlines():
        before, _, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        mountpoint = Path(_unescape(fields[4]))
        if path == mountpoint or mountpoint in path.parents:
            matches.append((mountpoint, filesystem[0], _unescape(filesystem[1])))
    return max(reversed(matches), key=lambda item: len(item[0].parts)) if matches else None


def require_local(path: Path):
    mount = filesystem_mount(path)
    if mount and mount[1] in {"nfs", "nfs4", "cifs", "smb3"}:
        raise ValueError("El estado SQLite requiere almacenamiento local persistente")


def check_image_root(root: Path):
    if check_staging_role(root, "images"):
        return
    if excluded_staging_path(root):
        raise ValueError("Repositorio productivo no puede apuntar a staging")
    expected = os.getenv("IMAGE_REPOSITORY_NFS_SOURCE", "").strip()
    if expected:
        require_nfs(root, expected)
    else:
        mount = filesystem_mount(root)
        if mount and mount[1] in {"nfs", "nfs4"}:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_NFS_SOURCE explicita")


def check_write_path(path: Path):
    staging = staging_root()
    if staging is not None:
        canonical = Path(os.path.abspath(path))
        mount = Path(os.environ["IMAGE_REPOSITORY_STAGING_MOUNT_ROOT"]).absolute()
        if canonical == mount or mount in canonical.parents:
            if canonical.resolve() != canonical:
                raise ValueError("Escritura staging mediante alias no permitida")
            for role in ("images", "backups", "recovery", "work"):
                base = staging / role
                if canonical == base or base in canonical.parents:
                    check_staging_role(base, role)
                    actual = filesystem_mount(canonical)
                    if actual != (mount, "nfs4", EXPECTED_SOURCE) and actual != (mount, "nfs", EXPECTED_SOURCE):
                        raise ValueError("Destino staging cubierto por otro montaje/alias")
                    return
            raise ValueError("Escritura fuera de destinos privados staging; source es congelada")
    expected = os.getenv("IMAGE_REPOSITORY_NFS_SOURCE", "").strip()
    if expected:
        configured = os.getenv("IMAGE_REPOSITORY_ROOT", "").strip()
        if not configured:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_ROOT explicita")
        root = Path(configured).resolve()
        if path.resolve() == root or root in path.resolve().parents:
            check_image_root(root)
    backup_source = os.getenv("IMAGE_REPOSITORY_BACKUP_NFS_SOURCE", "").strip()
    if backup_source:
        configured = os.getenv("IMAGE_REPOSITORY_BACKUP_ROOT", "").strip()
        if not configured:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_BACKUP_ROOT explicita")
        root = Path(configured).resolve()
        if path.resolve() == root or root in path.resolve().parents:
            require_nfs(root, backup_source)
    mount = filesystem_mount(path)
    if mount and mount[1] in {"nfs", "nfs4"}:
        if mount[2] not in {expected, backup_source}:
            raise ValueError("Escritura NFS sin exportacion esperada")
        require_nfs(mount[0], mount[2])


def validate_state_root(images: Path, root: Path) -> None:
    require_local(root)
    if staging_root() is not None:
        configured = os.getenv("IMAGE_REPOSITORY_STATE_ROOT", "").strip()
        if not configured or root != Path(configured).resolve():
            raise ValueError("Estado de ensayo debe ser la raiz local explicita, no otra base")
    if root != images and (root in images.parents or images in root.parents):
        raise ValueError("Imagenes y estado deben tener raices separadas, no solapadas")


def state_root(images: Path) -> Path:
    from db_config import load_env_file
    load_env_file()
    configured = os.getenv("IMAGE_REPOSITORY_STATE_ROOT", "").strip()
    root = Path(configured).resolve() if configured else images.resolve()
    validate_state_root(images.resolve(), root)
    return root


def backup_root(images: Path) -> Path:
    configured = os.getenv("IMAGE_REPOSITORY_BACKUP_ROOT", "").strip()
    return Path(configured).resolve() if configured else state_root(images)


def check_backup_root(images: Path, backups: Path):
    check_image_root(images)
    if check_staging_role(backups, "backups"):
        require_distinct_replica(images, backups)
        return
    if backups == images or backups in images.parents or images in backups.parents:
        if state_root(images) != images:
            raise ValueError("Backups de imagenes requieren ubicacion independiente")
    expected = os.getenv("IMAGE_REPOSITORY_BACKUP_NFS_SOURCE", "").strip()
    if expected:
        require_nfs(backups, expected)
    mount = filesystem_mount(backups)
    if mount and mount[1] in {"nfs", "nfs4"}:
        if not expected or mount[2] != expected:
            raise ValueError("Exportacion de backups no acreditada")
        require_distinct_replica(images, backups)


def require_distinct_replica(source: Path, target: Path):
    original_source, original_target = source.absolute(), target.absolute()
    if original_source.resolve() != original_source or original_target.resolve() != original_target:
        raise ValueError("Origen y replica no admiten aliases simbolicos")
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("El NAS debe ser una replica separada")
    if target.exists() and source.exists() and os.path.samefile(source, target):
        raise ValueError("Origen y replica son el mismo almacenamiento")
    left, right = filesystem_mount(source), filesystem_mount(target)
    if left and right and left[1] in {"nfs", "nfs4"} and right[1] in {"nfs", "nfs4"} and left[2] == right[2]:
        staging = staging_root()
        if staging is not None and source.parent == target.parent == staging and (
                source.name in STAGING_ROLES and target.name in STAGING_ROLES):
            check_staging_role(source, source.name)
            check_staging_role(target, target.name)
            return
        raise ValueError("No replicar sobre la misma exportacion NFS, aunque cambie el montaje")
