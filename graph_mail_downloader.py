from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64
import json
import os
import zipfile
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from db_config import load_env_file


M365_TENANT_ID_ENV = "M365_TENANT_ID"
M365_CLIENT_ID_ENV = "M365_CLIENT_ID"
M365_CLIENT_SECRET_ENV = "M365_CLIENT_SECRET"
M365_MAILBOX_ENV = "M365_MAILBOX"
M365_DOWNLOAD_ROOT_ENV = "M365_DOWNLOAD_ROOT"


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


def _get_access_token(config: M365Config) -> str:
    response = requests.post(
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
    response = requests.get(_inbox_messages_url(mailbox, top), headers=_graph_headers(token), timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", [])


def _attachments_url(mailbox: str, message_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}/attachments?$top=200"


def _attachment_value_url(mailbox: str, message_id: str, attachment_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}/attachments/{attachment_id}/$value"


def _message_body_url(mailbox: str, message_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}?$select=body"


def _list_attachments(token: str, mailbox: str, message_id: str) -> list[dict[str, Any]]:
    response = requests.get(_attachments_url(mailbox, message_id), headers=_graph_headers(token), timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", [])


def _get_message_body(token: str, mailbox: str, message_id: str) -> str:
    response = requests.get(_message_body_url(mailbox, message_id), headers=_graph_headers(token), timeout=30)
    response.raise_for_status()
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
    response = requests.patch(
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{message_id}",
        headers={**_graph_headers(token), "Content-Type": "application/json"},
        json={"isRead": True},
        timeout=30,
    )
    response.raise_for_status()


def _find_download_links(body: str) -> list[str]:
    import re
    from html import unescape

    html = unescape(body or "")
    return re.findall(r"href=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)


def _sanitize_filename(name: str) -> str:
    cleaned = (name or "attachment.bin").strip()
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        cleaned = cleaned.replace(ch, "_")
    return cleaned or "attachment.bin"


def _load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"processed_message_ids": []}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_message_ids": []}


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
    date_folder = received_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    target_dir = root / date_folder / lote
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / _sanitize_filename(file_name)
    out_path.write_bytes(content)
    return out_path


def _model_from_image_name(file_name: str) -> str:
    model = Path(file_name).name.split("__", 1)[0].strip()
    if model.startswith("0"):
        model = model[1:]
    return _sanitize_filename(model or "modelo-unknown")


def _extract_zip(archive_path: Path, images_root: Path) -> list[Path]:
    extracted_paths: list[Path] = []
    target_dir = images_root.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue

            file_name = _sanitize_filename(Path(member.filename).name)
            model_dir = target_dir / _model_from_image_name(file_name)
            destination = (model_dir / file_name).resolve()
            if destination != target_dir and target_dir not in destination.parents:
                raise ValueError(f"Ruta insegura en ZIP: {member.filename}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as target:
                target.write(source.read())
            extracted_paths.append(destination)

    return extracted_paths


def _download_zip_link(url: str, target_dir: Path) -> Path | None:
    with requests.get(url, allow_redirects=True, stream=True, timeout=120) as response:
        response.raise_for_status()
        first_chunk = next(response.iter_content(64), b"")
        final_path = unquote(urlparse(response.url).path)
        if not final_path.lower().endswith(".zip"):
            return None

        file_name = Path(final_path).name or "luxoptica-images.zip"
        archive_path = target_dir / _sanitize_filename(file_name)
        total_bytes = int(response.headers.get("content-length", "0") or 0)
        print(f"   Descargando {file_name} ({total_bytes / 1024 / 1024:.1f} MB)...", flush=True)
        downloaded_bytes = len(first_chunk)
        next_report = 50 * 1024 * 1024
        with archive_path.open("wb") as archive_file:
            archive_file.write(first_chunk)
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    archive_file.write(chunk)
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes >= next_report:
                        if total_bytes:
                            progress = downloaded_bytes / total_bytes * 100
                            print(f"      {downloaded_bytes / 1024 / 1024:.0f}/{total_bytes / 1024 / 1024:.0f} MB ({progress:.0f}%)", flush=True)
                        else:
                            print(f"      {downloaded_bytes / 1024 / 1024:.0f} MB", flush=True)
                        next_report += 50 * 1024 * 1024
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

    token = _get_access_token(config)
    messages = _list_inbox_messages(token, config.mailbox, top_messages)

    root = config.download_root
    if not root.is_absolute():
        root = Path(__file__).resolve().parent / root
    root.mkdir(parents=True, exist_ok=True)

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
            for link in _find_download_links(body):
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

            content_bytes: bytes
            if att.get("contentBytes"):
                content_bytes = base64.b64decode(att["contentBytes"])
            else:
                value_resp = requests.get(
                    _attachment_value_url(config.mailbox, message_id, att_id),
                    headers=_graph_headers(token),
                    timeout=60,
                )
                value_resp.raise_for_status()
                content_bytes = value_resp.content

            saved = _save_attachment_bytes(content_bytes, root, received_at, lote_folder, file_name)
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
