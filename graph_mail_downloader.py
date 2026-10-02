from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urlparse

import requests
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL

from db_config import load_db_config, load_env_file


M365_TENANT_ID_ENV = "M365_TENANT_ID"
M365_CLIENT_ID_ENV = "M365_CLIENT_ID"
M365_CLIENT_SECRET_ENV = "M365_CLIENT_SECRET"
M365_MAILBOX_ENV = "M365_MAILBOX"
M365_DOWNLOAD_ROOT_ENV = "M365_DOWNLOAD_ROOT"
GRAPH_MAX_ATTEMPTS = 4
GRAPH_MAX_PAGES = 1000
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 250 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ZIP_MEMBERS = 20000
MAX_ZIP_COMPRESSION_RATIO = 1000


@dataclass
class M365Config:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    download_root: Path


@dataclass
class DownloadSummary:
    messages_scanned: int
    messages_with_attachments: int
    attachments_downloaded: int
    saved_paths: list[str]
    processed_message_ids: list[str]


def load_m365_config() -> M365Config:
    load_env_file()
    return M365Config(
        tenant_id=os.getenv(M365_TENANT_ID_ENV, "").strip(),
        client_id=os.getenv(M365_CLIENT_ID_ENV, "").strip(),
        client_secret=os.getenv(M365_CLIENT_SECRET_ENV, "").strip(),
        mailbox=os.getenv(M365_MAILBOX_ENV, "images@diagonaleyewear.com").strip(),
        download_root=Path(os.getenv(M365_DOWNLOAD_ROOT_ENV, "docs/Luxoptica/descargas").strip() or "docs/Luxoptica/descargas"),
    )


def validate_m365_config(config: M365Config) -> list[str]:
    missing: list[str] = []
    if not config.tenant_id:
        missing.append(M365_TENANT_ID_ENV)
    if not config.client_id:
        missing.append(M365_CLIENT_ID_ENV)
    if not config.client_secret:
        missing.append(M365_CLIENT_SECRET_ENV)
    if not config.mailbox:
        missing.append(M365_MAILBOX_ENV)
    return missing


def _token_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _graph_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    try:
        return min(max(float(retry_after), 0), 60)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            return min(max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0), 60)
        except (TypeError, ValueError, OverflowError):
            return float(min(2**attempt, 30))


def _request_with_retries(
    request_method: Callable[..., requests.Response],
    url: str,
    **kwargs: Any,
) -> requests.Response:
    for attempt in range(GRAPH_MAX_ATTEMPTS):
        try:
            response = request_method(url, **kwargs)
        except requests.RequestException:
            if attempt + 1 >= GRAPH_MAX_ATTEMPTS:
                raise
            time.sleep(min(2**attempt, 30))
            continue

        if response.status_code in {408, 429, 500, 502, 503, 504}:
            if attempt + 1 >= GRAPH_MAX_ATTEMPTS:
                response.raise_for_status()
            delay = _retry_delay(response, attempt)
            response.close()
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response

    raise RuntimeError("Se agotaron los reintentos de la solicitud HTTP")


def _validate_graph_next_link(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com":
        raise ValueError("Microsoft Graph devolvió un @odata.nextLink fuera del host permitido")


def _list_graph_collection(
    token: str,
    first_url: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    headers = _graph_headers(token)
    values: list[dict[str, Any]] = []
    visited_urls: set[str] = set()
    next_url: str | None = first_url

    while next_url:
        _validate_graph_next_link(next_url)
        if next_url in visited_urls:
            raise RuntimeError("Microsoft Graph devolvió un ciclo de paginación")
        if len(visited_urls) >= GRAPH_MAX_PAGES:
            raise RuntimeError("Microsoft Graph excedió el máximo de páginas permitido")
        visited_urls.add(next_url)

        response = _request_with_retries(
            requests.get,
            next_url,
            headers=headers,
            timeout=timeout,
        )
        payload = response.json()
        page_values = payload.get("value", [])
        if not isinstance(page_values, list):
            raise RuntimeError("La colección de Microsoft Graph tiene un formato inválido")
        values.extend(page_values)
        next_url = payload.get("@odata.nextLink")

    return values


def _get_access_token(config: M365Config) -> str:
    response = _request_with_retries(
        requests.post,
        _token_url(config.tenant_id),
        data={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError("No se obtuvo access_token de Microsoft Graph")
    return token


def _inbox_messages_url(mailbox: str, top: int) -> str:
    select_fields = "id,subject,from,receivedDateTime,hasAttachments,isRead"
    return (
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/inbox/messages"
        f"?$select={select_fields}&$orderby=receivedDateTime desc&$top={top}"
    )


def _list_inbox_messages(token: str, mailbox: str, top: int) -> list[dict[str, Any]]:
    return _list_graph_collection(token, _inbox_messages_url(mailbox, top))


def _attachments_url(mailbox: str, message_id: str) -> str:
    fields = "id,name,isInline,size"
    return (
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}/attachments"
        f"?$select={fields}&$top=200"
    )


def _attachment_value_url(mailbox: str, message_id: str, attachment_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}/attachments/{attachment_id}/$value"


def _message_body_url(mailbox: str, message_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}?$select=body"


def _list_attachments(token: str, mailbox: str, message_id: str) -> list[dict[str, Any]]:
    return _list_graph_collection(token, _attachments_url(mailbox, message_id))


def _get_message_body(token: str, mailbox: str, message_id: str) -> str:
    response = _request_with_retries(
        requests.get,
        _message_body_url(mailbox, message_id),
        headers=_graph_headers(token),
        timeout=30,
    )
    return response.json().get("body", {}).get("content", "")


def send_alert_email(
    subject: str,
    html_body: str,
    to_address: str | list[str],
    config: M365Config | None = None,
) -> None:
    """Envia un email desde el buzon configurado (requiere permiso Mail.Send)."""
    config = config or load_m365_config()
    missing = validate_m365_config(config)
    if missing:
        raise RuntimeError(f"Faltan variables de configuracion M365: {', '.join(missing)}")

    direcciones = [to_address] if isinstance(to_address, str) else to_address
    token = _get_access_token(config)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": direccion}} for direccion in direcciones],
        },
        "saveToSentItems": True,
    }
    response = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{config.mailbox}/sendMail",
        headers={**_graph_headers(token), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def _mark_message_read(token: str, mailbox: str, message_id: str) -> None:
    response = _request_with_retries(
        requests.patch,
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}",
        headers={**_graph_headers(token), "Content-Type": "application/json"},
        json={"isRead": True},
        timeout=30,
    )
    response.raise_for_status()


def _find_download_links(body: str) -> list[str]:
    class AnchorParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.text_parts: list[str] = []
            self.text_length = 0
            self.open_anchors: list[tuple[str, int]] = []
            self.links: list[tuple[str, int, int]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "a":
                return
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            if href:
                self.open_anchors.append((href, self.text_length))

        def handle_data(self, data: str) -> None:
            self.text_parts.append(data)
            self.text_length += len(data)

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() == "a" and self.open_anchors:
                href, start = self.open_anchors.pop()
                self.links.append((href, start, self.text_length))

    parser = AnchorParser()
    parser.feed(body or "")
    plain_text = "".join(parser.text_parts).lower()
    links = [
        (
            href,
            plain_text[max(0, start - 350) : start],
            plain_text[start:end],
            plain_text[end : end + 140],
        )
        for href, start, end in parser.links
    ]

    image_links = [
        href
        for href, before, anchor_text, _after in links
        if ("descargar las imágenes" in before or "download the images" in before)
        and "informe" not in f"{anchor_text} {href}".lower()
        and "report" not in f"{anchor_text} {href}".lower()
    ]
    if image_links:
        return image_links
    return [href for href, _, _, _ in links]


def _sanitize_filename(name: str) -> str:
    cleaned = (name or "attachment.bin").strip()
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        cleaned = cleaned.replace(ch, "_")
    return cleaned or "attachment.bin"


def _load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"processed_message_ids": []}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el estado de descarga: {state_file}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"El estado de descarga tiene un formato inválido: {state_file}")
    return payload


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_file.parent,
            prefix=f".{state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(state, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, state_file)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _state_file_lock(state_file: Path) -> Iterator[None]:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_file.with_name(f"{state_file.name}.lock")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        lock_name = hashlib.sha256(str(state_file.resolve()).casefold().encode()).hexdigest()
        mutex_name = f"Local\\DiagonalAbcState-{lock_name}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait_for_single_object.restype = wintypes.DWORD
        release_mutex = kernel32.ReleaseMutex
        release_mutex.argtypes = [wintypes.HANDLE]
        release_mutex.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        mutex_handle = create_mutex(None, False, mutex_name)
        if not mutex_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            wait_result = wait_for_single_object(mutex_handle, 0xFFFFFFFF)
            if wait_result not in (0, 0x80):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                release_mutex(mutex_handle)
        finally:
            close_handle(mutex_handle)
    else:
        import fcntl

        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _parse_graph_dt(value: str) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _detect_lote_from_subject(subject: str) -> str:
    text = (subject or "").lower()
    marker = "lote"
    if marker not in text:
        return "lote-unknown"
    idx = text.find(marker)
    tail = text[idx : idx + 20]
    digits = "".join(ch for ch in tail if ch.isdigit())
    if not digits:
        return "lote-unknown"
    return f"lote-{int(digits):03d}"


def _save_attachment_bytes(content: bytes, root: Path, received_at: datetime, lote: str, file_name: str) -> Path:
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"El adjunto supera el máximo permitido de {MAX_DOWNLOAD_BYTES} bytes")
    date_folder = received_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    target_dir = root / date_folder / lote
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / _sanitize_filename(file_name)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, out_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return out_path


def _download_attachment_file(
    token: str,
    mailbox: str,
    message_id: str,
    attachment_id: str,
    root: Path,
    received_at: datetime,
    lote: str,
    file_name: str,
) -> Path:
    date_folder = received_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    target_dir = root / date_folder / lote
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / _sanitize_filename(file_name)
    response = _request_with_retries(
        requests.get,
        _attachment_value_url(mailbox, message_id, attachment_id),
        headers=_graph_headers(token),
        timeout=60,
        stream=True,
    )
    temporary_path: Path | None = None
    try:
        content_length = int(response.headers.get("content-length", "0") or 0)
        if content_length > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"El adjunto supera el máximo permitido de {MAX_DOWNLOAD_BYTES} bytes")
        downloaded_bytes = 0
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                downloaded_bytes += len(chunk)
                if downloaded_bytes > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"El adjunto supera el máximo permitido de {MAX_DOWNLOAD_BYTES} bytes")
                temporary_file.write(chunk)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, out_path)
    finally:
        response.close()
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return out_path


def _model_from_image_name(file_name: str) -> str:
    model = Path(file_name).name.split("__", 1)[0].strip()
    if model.startswith("0"):
        model = model[1:]
    return _sanitize_filename(model or "modelo-unknown")


def _normalize_image_key(value: str, remove_initial_zero: bool = False) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if remove_initial_zero and normalized.startswith("0"):
        normalized = normalized[1:]
    return normalized


def _image_model_color_key(file_name: str) -> tuple[str, str] | None:
    parts = Path(file_name).stem.split("__")
    if len(parts) < 2:
        return None
    model = _normalize_image_key(parts[0], remove_initial_zero=True)
    color_part = re.sub(r"_\d{3}A$", "", parts[1], flags=re.IGNORECASE)
    color = _normalize_image_key(color_part)
    if not model or not color:
        return None
    return model, color


def _load_eans_by_image_key() -> dict[tuple[str, str], str]:
    """Carga la relacion modelo/color/EAN de los manifiestos de solicitudes."""
    manifest_root = Path(__file__).resolve().parent / "docs" / "Luxoptica"
    matches: dict[tuple[str, str], set[str]] = {}
    for manifest_path in manifest_root.glob("upc-products-images-request-*.manifest.json"):
        try:
            products = json.loads(manifest_path.read_text(encoding="utf-8")).get("products", [])
        except (OSError, json.JSONDecodeError):
            continue
        for product in products:
            key = (
                _normalize_image_key(product.get("modelo", ""), remove_initial_zero=True),
                _normalize_image_key(product.get("color", "")),
            )
            ean = str(product.get("ean", "")).strip()
            if key[0] and key[1] and ean:
                matches.setdefault(key, set()).add(ean)
    return {key: next(iter(eans)) for key, eans in matches.items() if len(eans) == 1}


def _get_market_ids_by_ean(eans: set[str]) -> dict[str, dict[str, str]]:
    """Obtiene los IDs externos de Farfetch y Miinto para los EAN indicados."""
    if not eans:
        return {}

    config = load_db_config()
    if not all([config.host, config.database, config.user]):
        print("   No se pudo consultar Odoo: falta configuracion de base de datos.", flush=True)
        return {}

    engine = create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=config.user,
            password=config.password,
            host=config.host,
            port=config.port,
            database=config.database,
        )
    )
    query = text(
        """
        SELECT pp.barcode AS ean, dpw.siteweb_id, dpw.product_website_id
        FROM product_product pp
        JOIN diagonal_product_website dpw ON dpw.product_id = pp.id
        WHERE pp.barcode IN :eans
          AND dpw.siteweb_id IN (2, 3)
          AND COALESCE(dpw.product_website_id, '') <> ''
        """
    ).bindparams(bindparam("eans", expanding=True))
    market_names = {2: "Miinto", 3: "Farfetch"}
    result: dict[str, dict[str, str]] = {}
    with engine.connect() as conn:
        for row in conn.execute(query, {"eans": sorted(eans)}).mappings():
            result.setdefault(row["ean"], {})[market_names[row["siteweb_id"]]] = row["product_website_id"]
    return result


def _market_view_from_image_name(file_name: str) -> str | None:
    parts = [part.lower() for part in Path(file_name).stem.split("__")]
    if len(parts) < 4:
        return None
    views = {
        ("noshad", "fr"): "V1",
        ("noshad", "qt"): "V2",
        ("shad", "lt"): "V3",
    }
    return views.get((parts[-2], parts[-1]))


def _create_market_images(source: Path, ean_dir: Path, market_ids: dict[str, str]) -> list[Path]:
    view = _market_view_from_image_name(source.name)
    if view is None or source.suffix.lower() != ".png":
        return []

    created: list[Path] = []
    farfetch_id = market_ids.get("Farfetch")
    if farfetch_id:
        destination = ean_dir / "Farfetch" / f"{farfetch_id}_{view}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        created.append(destination)

    miinto_id = market_ids.get("Miinto")
    if miinto_id:
        destination = ean_dir / "Miinto" / f"{miinto_id}_{view}.jpeg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        created.append(destination)

    return created


def _market_pending_file(images_root: Path) -> Path:
    return images_root / ".market_pending.json"


def _refresh_pending_market_images_unlocked(images_root: Path) -> tuple[int, int]:
    """Reintenta crear copias de mercado para originales que aun no tenian ID."""
    pending_file = _market_pending_file(images_root)
    if not images_root.exists():
        return 0, 0

    pending_entries: dict[str, dict[str, Any]] = {}
    for source in images_root.glob("*/*/*.png"):
        if source.parent.name in {"Farfetch", "Miinto", "Pendientes"}:
            continue
        if source.parent.name == "ean-no-identificado" or source.parent.parent.name == "ean-no-identificado":
            continue
        ean = source.parent.name
        pending_entries[str(source)] = {"ean": ean, "missing_markets": ["Farfetch", "Miinto"]}

    market_ids_by_ean = _get_market_ids_by_ean({entry["ean"] for entry in pending_entries.values()})
    completed = 0
    created = 0
    for source_name, entry in list(pending_entries.items()):
        source = Path(source_name)
        market_ids = market_ids_by_ean.get(entry["ean"], {})
        created_paths = _create_market_images(source, source.parent, market_ids)
        created += len(created_paths)
        missing = [market for market in ("Farfetch", "Miinto") if not market_ids.get(market)]
        if missing:
            entry["missing_markets"] = missing
            pending_entries[source_name] = entry
        else:
            completed += 1
            pending_entries.pop(source_name, None)

    if pending_entries:
        _save_state(pending_file, pending_entries)
    elif pending_file.exists():
        pending_file.unlink()

    return completed, created


def _refresh_pending_market_images(images_root: Path) -> tuple[int, int]:
    if not images_root.exists():
        return 0, 0
    state_file = images_root / ".mail_download_state.json"
    with _state_file_lock(state_file):
        return _refresh_pending_market_images_unlocked(images_root)


def _extract_zip(archive_path: Path, images_root: Path) -> list[Path]:
    extracted_paths: list[Path] = []
    target_dir = images_root.resolve()
    eans_by_image_key = _load_eans_by_image_key()
    matched_eans = set(eans_by_image_key.values())
    market_ids_by_ean = _get_market_ids_by_ean(matched_eans)

    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ValueError(f"El ZIP supera el máximo de {MAX_ZIP_MEMBERS} entradas")
        declared_total = 0
        extracted_total = 0
        for member in members:
            if member.is_dir():
                continue
            if member.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ValueError(f"Entrada ZIP demasiado grande: {member.filename}")
            declared_total += member.file_size
            if declared_total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("El ZIP supera el máximo total de extracción permitido")
            compression_ratio = member.file_size / max(member.compress_size, 1)
            if compression_ratio > MAX_ZIP_COMPRESSION_RATIO:
                raise ValueError(f"Ratio de compresión ZIP excesivo: {member.filename}")

            file_name = _sanitize_filename(Path(member.filename).name)
            model_dir = target_dir / _model_from_image_name(file_name)
            image_key = _image_model_color_key(file_name)
            ean = eans_by_image_key.get(image_key, "ean-no-identificado") if image_key else "ean-no-identificado"
            destination = (model_dir / ean / file_name).resolve()
            if destination != target_dir and target_dir not in destination.parents:
                raise ValueError(f"Ruta insegura en ZIP: {member.filename}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            written = 0
            try:
                with archive.open(member) as source, tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as target:
                    temporary_path = Path(target.name)
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        extracted_total += len(chunk)
                        if written > MAX_ZIP_MEMBER_BYTES or extracted_total > MAX_ZIP_TOTAL_BYTES:
                            raise ValueError("El ZIP excedió los límites durante extracción")
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if written != member.file_size:
                    raise ValueError(f"Tamaño extraído no coincide con metadatos: {member.filename}")
                os.replace(temporary_path, destination)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()
            extracted_paths.append(destination)
            market_ids = market_ids_by_ean.get(ean, {})
            extracted_paths.extend(_create_market_images(destination, destination.parent, market_ids))
            if ean != "ean-no-identificado" and (
                not market_ids.get("Farfetch") or not market_ids.get("Miinto")
            ):
                pending_file = _market_pending_file(target_dir)
                pending = _load_state(pending_file)
                pending[str(destination)] = {
                    "ean": ean,
                    "missing_markets": [
                        market for market in ("Farfetch", "Miinto") if not market_ids.get(market)
                    ],
                }
                _save_state(pending_file, pending)

    return extracted_paths


def _download_zip_link(url: str, target_dir: Path) -> Path | None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with _request_with_retries(
        requests.get,
        url,
        allow_redirects=True,
        stream=True,
        timeout=120,
    ) as response:
        final_path = unquote(urlparse(response.url).path)
        if not final_path.lower().endswith(".zip"):
            return None

        file_name = Path(final_path).name or "luxoptica-images.zip"
        archive_path = target_dir / _sanitize_filename(file_name)
        total_bytes = int(response.headers.get("content-length", "0") or 0)
        if total_bytes > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"El ZIP supera el máximo de descarga de {MAX_DOWNLOAD_BYTES} bytes")
        print(f"   Descargando {file_name} ({total_bytes / 1024 / 1024:.1f} MB)...", flush=True)
        downloaded_bytes = 0
        next_report = 50 * 1024 * 1024
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target_dir,
                prefix=f".{archive_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as archive_file:
                temporary_path = Path(archive_file.name)
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"El ZIP supera el máximo de descarga de {MAX_DOWNLOAD_BYTES} bytes"
                        )
                    archive_file.write(chunk)
                    if downloaded_bytes >= next_report:
                        if total_bytes:
                            progress = downloaded_bytes / total_bytes * 100
                            print(f"      {downloaded_bytes / 1024 / 1024:.0f}/{total_bytes / 1024 / 1024:.0f} MB ({progress:.0f}%)", flush=True)
                        else:
                            print(f"      {downloaded_bytes / 1024 / 1024:.0f} MB", flush=True)
                        next_report += 50 * 1024 * 1024
                archive_file.flush()
                os.fsync(archive_file.fileno())
            os.replace(temporary_path, archive_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        print(f"   Descarga completada: {archive_path}", flush=True)
    return archive_path


def download_luxoptica_mail_attachments(
    sender_hint: str = "luxottica",
    subject_hint: str = "image",
    lookback_days: int = 7,
    top_messages: int = 100,
) -> DownloadSummary:
    config = load_m365_config()
    missing = validate_m365_config(config)
    if missing:
        raise RuntimeError(f"Faltan variables de entorno de Microsoft 365: {', '.join(missing)}")

    root = config.download_root
    if not root.is_absolute():
        root = Path(__file__).resolve().parent / root
    root.mkdir(parents=True, exist_ok=True)
    state_file = root / ".mail_download_state.json"
    with _state_file_lock(state_file):
        return _download_luxoptica_mail_attachments_locked(
            sender_hint,
            subject_hint,
            lookback_days,
            top_messages,
            config,
            root,
        )


def _download_luxoptica_mail_attachments_locked(
    sender_hint: str,
    subject_hint: str,
    lookback_days: int,
    top_messages: int,
    config: M365Config,
    root: Path,
) -> DownloadSummary:
    token = _get_access_token(config)
    messages = _list_inbox_messages(token, config.mailbox, top_messages)

    pending_completed, pending_created = _refresh_pending_market_images_unlocked(root)
    if pending_completed or pending_created:
        print(
            f"   Pendientes revisados: {pending_completed} completados, "
            f"{pending_created} copias de mercado creadas.",
            flush=True,
        )

    state_file = root / ".mail_download_state.json"
    state = _load_state(state_file)
    processed_ids: set[str] = set(state.get("processed_message_ids", []))

    now_utc = datetime.now(timezone.utc)
    min_dt = now_utc - timedelta(days=max(1, lookback_days))
    sender_hint_lower = sender_hint.strip().lower()
    subject_hint_lower = subject_hint.strip().lower()

    attachments_downloaded = 0
    messages_with_attachments = 0
    saved_paths: list[str] = []
    newly_processed: list[str] = []

    for msg in messages:
        message_id = msg.get("id", "")
        if not message_id or msg.get("isRead", False):
            continue

        received_at = _parse_graph_dt(msg.get("receivedDateTime", ""))
        if received_at < min_dt:
            continue

        sender_addr = (
            msg.get("from", {})
            .get("emailAddress", {})
            .get("address", "")
            .strip()
            .lower()
        )
        subject = (msg.get("subject", "") or "").strip()

        if sender_hint_lower and sender_hint_lower not in sender_addr:
            if subject_hint_lower and subject_hint_lower not in subject.lower():
                continue

        if not msg.get("hasAttachments", False):
            body = _get_message_body(token, config.mailbox, message_id)
            target_dir = root / received_at.astimezone(timezone.utc).strftime("%Y-%m-%d") / _detect_lote_from_subject(subject)
            target_dir.mkdir(parents=True, exist_ok=True)
            downloaded_from_link = False
            links = _find_download_links(body)
            for link in links:
                archive_path = _download_zip_link(link, target_dir)
                if archive_path is None:
                    continue
                saved_paths.append(str(archive_path))
                saved_paths.extend(str(path) for path in _extract_zip(archive_path, root))
                attachments_downloaded += 1
                downloaded_from_link = True
                break
            if downloaded_from_link:
                _mark_message_read(token, config.mailbox, message_id)
                newly_processed.append(message_id)
            else:
                print(
                    f"   ⚠️ Correo no leído sin ZIP de imágenes detectable: {subject or '[sin asunto]'} "
                    f"({len(links)} enlace(s))",
                    flush=True,
                )
            continue

        attachments = _list_attachments(token, config.mailbox, message_id)
        file_attachments = [
            att
            for att in attachments
            if att.get("@odata.type") == "#microsoft.graph.fileAttachment" and not att.get("isInline", False)
        ]

        if not file_attachments:
            newly_processed.append(message_id)
            continue

        messages_with_attachments += 1
        lote_folder = _detect_lote_from_subject(subject)

        for att in file_attachments:
            att_id = att.get("id", "")
            file_name = att.get("name", "attachment.bin")
            if not att_id:
                continue

            attachment_size = int(att.get("size", 0) or 0)
            if attachment_size > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"El adjunto supera el máximo permitido: {file_name}")
            if att.get("contentBytes"):
                content_bytes = base64.b64decode(att["contentBytes"])
                saved = _save_attachment_bytes(content_bytes, root, received_at, lote_folder, file_name)
            else:
                saved = _download_attachment_file(
                    token,
                    config.mailbox,
                    message_id,
                    att_id,
                    root,
                    received_at,
                    lote_folder,
                    file_name,
                )
            saved_paths.append(str(saved))
            attachments_downloaded += 1

            if saved.suffix.lower() == ".zip":
                extracted_paths = _extract_zip(saved, root)
                saved_paths.extend(str(path) for path in extracted_paths)

        _mark_message_read(token, config.mailbox, message_id)
        newly_processed.append(message_id)

    if newly_processed:
        processed_ids.update(newly_processed)
        state["processed_message_ids"] = sorted(processed_ids)
        _save_state(state_file, state)

    return DownloadSummary(
        messages_scanned=len(messages),
        messages_with_attachments=messages_with_attachments,
        attachments_downloaded=attachments_downloaded,
        saved_paths=saved_paths,
        processed_message_ids=newly_processed,
    )
