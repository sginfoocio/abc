"""Persistent settings shared by Cloud and the Luxottica sender."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from filelock import FileLock

from db_config import load_env_file
from kering_images import ConfigStore, atomic_write
from provider_order_policy import provider_data_root


PORTAL_URL = "https://my.essilorluxottica.com/es-ES/login"
PORTAL_HOST = "my.essilorluxottica.com"
LOGIN_HOST = "myesluwsb2cp01.b2clogin.com"


@dataclass(frozen=True)
class LuxotticaSettings:
    url: str
    username: str
    password: str
    email: str
    enabled: bool = False
    interval_minutes: int = 60


def validate_portal_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc, parsed.path) != ("https", PORTAL_HOST, "/es-ES/login"):
        raise ValueError("Usa la URL de inicio de sesión de My EssilorLuxottica.")
    if parsed.query or parsed.fragment:
        raise ValueError("La URL de Luxottica no debe incluir datos de sesión.")


class LuxotticaConfig:
    def __init__(self, root: Path | None = None):
        self.path = (root if root is not None else provider_data_root()) / "luxottica_config.enc"

    def cipher(self):
        key_file = os.getenv("LUXOPTICA_ENCRYPTION_KEY_FILE")
        key = Path(key_file).read_text(encoding="ascii").strip() if key_file else os.getenv(
            "LUXOPTICA_ENCRYPTION_KEY")
        return Fernet(key.encode("ascii")) if key else ConfigStore(self.path.parent).cipher()

    def load(self) -> LuxotticaSettings:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            load_env_file()
            url = os.getenv("LUXOPTICA_URL") or os.getenv("ESSILOR_URL") or PORTAL_URL
            if url.rstrip("/") == "https://portal.luxottica.com":
                url = PORTAL_URL
            return LuxotticaSettings(
                url,
                os.getenv("LUXOPTICA_USERNAME") or os.getenv("ESSILOR_USERNAME", ""),
                os.getenv("LUXOPTICA_PASSWORD") or os.getenv("ESSILOR_PASSWORD", ""),
                os.getenv("LUXOPTICA_REQUEST_EMAIL") or "images@diagonaleyewear.com",
            )
        return LuxotticaSettings(**json.loads(self.cipher().decrypt(data)))

    def save(self, *, url, username, password, email, enabled, interval_minutes):
        validate_portal_url(url)
        if not 15 <= interval_minutes <= 1440:
            raise ValueError("El intervalo debe ser de 15 a 1440 minutos.")
        if not username or email.count("@") != 1 or any(c.isspace() for c in email):
            raise ValueError("Indica un usuario y un correo de recepción válidos.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=5):
            old = self.load()
            secret = password or old.password
            if username != old.username and not password:
                raise ValueError("Introduce la contraseña del nuevo usuario.")
            if enabled and not secret:
                raise ValueError("Configura la contraseña antes de activar el envío.")
            settings = LuxotticaSettings(url, username, secret, email, enabled, interval_minutes)
            atomic_write(self.path, self.cipher().encrypt(json.dumps(asdict(settings)).encode("utf-8")))
