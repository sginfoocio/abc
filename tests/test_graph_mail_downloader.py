from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import requests
import zipfile

import graph_mail_downloader as graph


class StubResponse:
    def __init__(self, payload=None, status_code=200, headers=None) -> None:
        self.payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def close(self) -> None:
        self.closed = True


def test_inbox_collection_follows_next_link(monkeypatch) -> None:
    pages = {
        "https://graph.microsoft.com/v1.0/messages": StubResponse(
            {"value": [{"id": "first"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/messages?page=2"}
        ),
        "https://graph.microsoft.com/v1.0/messages?page=2": StubResponse(
            {"value": [{"id": "second"}]}
        ),
    }
    requested_urls = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        return pages[url]

    monkeypatch.setattr(graph.requests, "get", fake_get)

    messages = graph._list_graph_collection("token", "https://graph.microsoft.com/v1.0/messages")

    assert [message["id"] for message in messages] == ["first", "second"]
    assert len(requested_urls) == 2


def test_attachment_listing_follows_next_link(monkeypatch) -> None:
    first_url = graph._attachments_url("images@example.test", "message-id")
    second_url = "https://graph.microsoft.com/v1.0/attachments?page=2"
    pages = {
        first_url: StubResponse(
            {"value": [{"id": "first"}], "@odata.nextLink": second_url}
        ),
        second_url: StubResponse({"value": [{"id": "second"}]}),
    }
    monkeypatch.setattr(graph.requests, "get", lambda url, **kwargs: pages[url])

    attachments = graph._list_attachments("token", "images@example.test", "message-id")

    assert [attachment["id"] for attachment in attachments] == ["first", "second"]


def test_graph_pagination_rejects_external_next_link(monkeypatch) -> None:
    monkeypatch.setattr(
        graph.requests,
        "get",
        lambda *args, **kwargs: StubResponse(
            {"value": [], "@odata.nextLink": "https://graph.microsoft.com.attacker.test/page"}
        ),
    )

    try:
        graph._list_graph_collection("token", "https://graph.microsoft.com/v1.0/messages")
    except ValueError as exc:
        assert "host permitido" in str(exc)
    else:
        raise AssertionError("Se aceptó un nextLink fuera del host de Graph")


def test_graph_retries_429_using_retry_after(monkeypatch) -> None:
    responses = [
        StubResponse(status_code=429, headers={"Retry-After": "0"}),
        StubResponse({"ok": True}),
    ]
    delays = []
    monkeypatch.setattr(graph.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(graph.time, "sleep", delays.append)

    response = graph._request_with_retries(graph.requests.get, "https://graph.microsoft.com/v1.0/me")

    assert response.json() == {"ok": True}
    assert delays == [0.0]


def test_download_link_parser_selects_images_and_ignores_reports() -> None:
    body = (
        '<p>Descargar las imágenes <a href="https://example.test/images.zip">ZIP</a></p>'
        '<p><a href="https://example.test/report.zip">Informe</a></p>'
    )

    assert graph._find_download_links(body) == ["https://example.test/images.zip"]


def test_state_write_is_atomic_when_replace_fails(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"processed_message_ids": ["old"]}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated interruption")

    monkeypatch.setattr(graph.os, "replace", fail_replace)

    try:
        graph._save_state(state_file, {"processed_message_ids": ["new"]})
    except OSError:
        pass
    else:
        raise AssertionError("La escritura simulada debía fallar")

    assert graph._load_state(state_file) == {"processed_message_ids": ["old"]}
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupt_state_fails_closed(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("not-json", encoding="utf-8")

    try:
        graph._load_state(state_file)
    except RuntimeError as exc:
        assert "estado de descarga" in str(exc)
    else:
        raise AssertionError("El estado corrupto se trató como estado vacío")


def test_state_lock_serializes_competing_writers(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    order: list[int] = []

    def write_in_lock(value: int) -> None:
        with graph._state_file_lock(state_file):
            previous = graph._load_state(state_file)
            order.extend(previous.get("values", []))
            graph._save_state(state_file, {"values": [value]})

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write_in_lock, range(12)))

    assert len(order) == 11
    assert len(graph._load_state(state_file)["values"]) == 1


def test_zip_limits_reject_oversized_member_before_extract(tmp_path, monkeypatch) -> None:
    archive_path = tmp_path / "large.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("model__color.png", b"12345")
    monkeypatch.setattr(graph, "MAX_ZIP_MEMBER_BYTES", 4)
    monkeypatch.setattr(graph, "_load_eans_by_image_key", lambda: {})
    monkeypatch.setattr(graph, "_get_market_ids_by_ean", lambda eans: {})

    try:
        graph._extract_zip(archive_path, tmp_path / "images")
    except ValueError as exc:
        assert "demasiado grande" in str(exc)
    else:
        raise AssertionError("Se extrajo un miembro superior al límite")


def test_attachment_download_streams_and_enforces_limit(tmp_path, monkeypatch) -> None:
    class StreamResponse(StubResponse):
        url = "https://graph.microsoft.com/value"

        def iter_content(self, chunk_size):
            yield b"ab"
            yield b"cd"

    monkeypatch.setattr(graph, "MAX_DOWNLOAD_BYTES", 3)
    monkeypatch.setattr(
        graph.requests,
        "get",
        lambda *args, **kwargs: StreamResponse(headers={"content-length": "0"}),
    )
    try:
        graph._download_attachment_file(
            "token",
            "mailbox",
            "message",
            "attachment",
            tmp_path,
            datetime.now(timezone.utc),
            "lote-001",
            "file.bin",
        )
    except ValueError as exc:
        assert "máximo permitido" in str(exc)
    else:
        raise AssertionError("Se descargaron más bytes que el límite")

    assert not (tmp_path / "file.bin").exists()
    assert list(tmp_path.rglob("*.tmp")) == []