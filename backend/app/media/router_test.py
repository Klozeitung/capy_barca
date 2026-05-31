"""
Tests for the media upload router.

All filesystem writes are redirected to a per-test temp directory via the
``tmp_upload_dir`` autouse fixture, keeping the real ``static/`` tree clean.
HTTP auth is stubbed by the ``mock_auth`` autouse fixture.
"""
import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def mock_auth():
    with patch("app.media.router.validate_token", return_value=True):
        yield


@pytest.fixture
def http_client():
    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


@pytest.fixture(autouse=True)
def tmp_upload_dir(tmp_path, monkeypatch):
    """Redirect STATIC_ROOT to a throwaway temp dir for every test."""
    import app.media.router as media_module

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", upload_dir)
    yield upload_dir


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _upload(
    http_client,
    category: str,
    block_id: str,
    content: bytes = b"data",
    filename: str = "test.bin",
    mime: str = "application/octet-stream",
):
    return http_client.post(
        f"/api/media/upload/{category}/{block_id}",
        files={"file": (filename, io.BytesIO(content), mime)},
    )


def _mock_httpx(html: str):
    """Return a context manager that patches httpx.AsyncClient to return *html*."""
    mock_response = MagicMock()
    mock_response.text = html

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    return patch("httpx.AsyncClient", return_value=mock_client)


# ─── Upload: status and response shape ───────────────────────────────────────


def test_upload_image_returns_200(http_client):
    resp = _upload(http_client, "image", str(uuid.uuid4()), filename="x.png", mime="image/png")
    assert resp.status_code == 200


def test_upload_video_returns_200(http_client):
    resp = _upload(http_client, "video", str(uuid.uuid4()), filename="v.mp4", mime="video/mp4")
    assert resp.status_code == 200


def test_upload_audio_returns_200(http_client):
    resp = _upload(http_client, "audio", str(uuid.uuid4()), filename="a.mp3", mime="audio/mpeg")
    assert resp.status_code == 200


def test_upload_pdf_returns_200(http_client):
    resp = _upload(http_client, "pdf", str(uuid.uuid4()), filename="doc.pdf", mime="application/pdf")
    assert resp.status_code == 200


def test_upload_file_returns_200(http_client):
    resp = _upload(http_client, "file", str(uuid.uuid4()), filename="doc.txt", mime="text/plain")
    assert resp.status_code == 200


def test_upload_drive_returns_200(http_client):
    resp = _upload(http_client, "drive", str(uuid.uuid4()), filename="note.txt", mime="text/plain")
    assert resp.status_code == 200


def test_upload_response_has_required_fields(http_client):
    resp = _upload(http_client, "image", str(uuid.uuid4()), filename="x.png", mime="image/png")
    body = resp.json()
    for field in ("file_uuid", "url", "filename", "size", "mime"):
        assert field in body


def test_upload_response_filename_preserved(http_client):
    resp = _upload(http_client, "image", str(uuid.uuid4()), filename="photo.jpg", mime="image/jpeg")
    assert resp.json()["filename"] == "photo.jpg"


def test_upload_response_size_matches_content(http_client):
    content = b"hello world"
    resp = _upload(http_client, "file", str(uuid.uuid4()), content=content, filename="hw.txt")
    assert resp.json()["size"] == len(content)


# ─── Upload: URL paths ────────────────────────────────────────────────────────


def test_upload_image_url_has_media_image_prefix(http_client):
    resp = _upload(http_client, "image", str(uuid.uuid4()), filename="x.png", mime="image/png")
    assert resp.json()["url"].startswith("/static/uploads/media/image/")


def test_upload_video_url_has_media_video_prefix(http_client):
    resp = _upload(http_client, "video", str(uuid.uuid4()), filename="v.mp4", mime="video/mp4")
    assert resp.json()["url"].startswith("/static/uploads/media/video/")


def test_upload_audio_url_has_media_audio_prefix(http_client):
    resp = _upload(http_client, "audio", str(uuid.uuid4()), filename="a.mp3", mime="audio/mpeg")
    assert resp.json()["url"].startswith("/static/uploads/media/audio/")


def test_upload_pdf_url_has_media_pdf_prefix(http_client):
    resp = _upload(http_client, "pdf", str(uuid.uuid4()), filename="doc.pdf", mime="application/pdf")
    assert resp.json()["url"].startswith("/static/uploads/media/pdf/")


def test_upload_file_url_has_files_prefix(http_client):
    resp = _upload(http_client, "file", str(uuid.uuid4()), filename="doc.txt", mime="text/plain")
    assert resp.json()["url"].startswith("/static/uploads/files/")


def test_upload_drive_url_contains_block_id(http_client):
    block_id = str(uuid.uuid4())
    resp = _upload(http_client, "drive", block_id, filename="note.txt", mime="text/plain")
    assert block_id in resp.json()["url"]


# ─── Upload: filesystem ───────────────────────────────────────────────────────


def test_upload_writes_file_to_disk(http_client, tmp_upload_dir):
    content = b"binary content"
    block_id = str(uuid.uuid4())
    resp = _upload(http_client, "file", block_id, content=content, filename="bin.dat")
    file_uuid = resp.json()["file_uuid"]
    matches = list((tmp_upload_dir / "files").glob(f"{file_uuid}*"))
    assert len(matches) == 1
    assert matches[0].read_bytes() == content


def test_upload_drive_creates_block_subdirectory(http_client, tmp_upload_dir):
    block_id = str(uuid.uuid4())
    _upload(http_client, "drive", block_id, filename="a.txt")
    assert (tmp_upload_dir / "drives" / block_id).is_dir()


# ─── Upload: error handling ───────────────────────────────────────────────────


def test_upload_unknown_category_returns_400(http_client):
    resp = _upload(http_client, "foobar", str(uuid.uuid4()))
    assert resp.status_code == 400


def test_upload_requires_auth(http_client):
    with patch("app.media.router.validate_token", return_value=False):
        resp = _upload(http_client, "image", str(uuid.uuid4()))
    assert resp.status_code == 401


# ─── Delete ───────────────────────────────────────────────────────────────────


def _create_fake_file(tmp_upload_dir, category: str, block_id: str, file_uuid: str, ext: str = ".png"):
    if category == "image":
        target_dir = tmp_upload_dir / "media" / "image"
    elif category == "file":
        target_dir = tmp_upload_dir / "files"
    else:
        target_dir = tmp_upload_dir / "drives" / block_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{file_uuid}{ext}"
    path.write_bytes(b"x")
    return path


def test_delete_file_returns_200(http_client, tmp_upload_dir):
    block_id = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "image", block_id, file_uuid)
    resp = http_client.delete(f"/api/media/image/{block_id}/{file_uuid}")
    assert resp.status_code == 200


def test_delete_response_contains_uuid(http_client, tmp_upload_dir):
    block_id = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "image", block_id, file_uuid)
    resp = http_client.delete(f"/api/media/image/{block_id}/{file_uuid}")
    assert resp.json()["deleted"] == file_uuid


def test_delete_removes_file_from_disk(http_client, tmp_upload_dir):
    block_id = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    path = _create_fake_file(tmp_upload_dir, "image", block_id, file_uuid)
    http_client.delete(f"/api/media/image/{block_id}/{file_uuid}")
    assert not path.exists()


def test_delete_drive_file_returns_200(http_client, tmp_upload_dir):
    block_id = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", block_id, file_uuid, ext=".txt")
    resp = http_client.delete(f"/api/media/drive/{block_id}/{file_uuid}")
    assert resp.status_code == 200


def test_delete_nonexistent_file_returns_404(http_client):
    resp = http_client.delete(f"/api/media/image/{uuid.uuid4()}/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_delete_unknown_category_returns_400(http_client):
    resp = http_client.delete(f"/api/media/foobar/{uuid.uuid4()}/{uuid.uuid4()}")
    assert resp.status_code == 400


def test_delete_requires_auth(http_client):
    with patch("app.media.router.validate_token", return_value=False):
        resp = http_client.delete(f"/api/media/image/{uuid.uuid4()}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ─── Capacity ─────────────────────────────────────────────────────────────────


def test_capacity_returns_200(http_client):
    resp = http_client.get("/api/media/capacity")
    assert resp.status_code == 200


def test_capacity_response_has_required_fields(http_client):
    resp = http_client.get("/api/media/capacity")
    body = resp.json()
    for field in ("total_bytes", "used_bytes", "free_bytes"):
        assert field in body


def test_capacity_values_are_non_negative(http_client):
    resp = http_client.get("/api/media/capacity")
    body = resp.json()
    assert body["total_bytes"] >= 0
    assert body["used_bytes"] >= 0
    assert body["free_bytes"] >= 0


def test_capacity_requires_auth(http_client):
    with patch("app.media.router.validate_token", return_value=False):
        resp = http_client.get("/api/media/capacity")
    assert resp.status_code == 401


def test_capacity_creates_static_root_if_missing(http_client, tmp_upload_dir, monkeypatch):
    """Endpoint must not crash when STATIC_ROOT does not yet exist."""
    import app.media.router as media_module
    missing = tmp_upload_dir / "nonexistent"
    monkeypatch.setattr(media_module, "STATIC_ROOT", missing)
    assert not missing.exists()
    resp = http_client.get("/api/media/capacity")
    assert resp.status_code == 200
    assert missing.exists()


# ─── Bookmark ─────────────────────────────────────────────────────────────────


def test_bookmark_returns_200(http_client):
    with _mock_httpx("<html><title>Test</title></html>"):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.status_code == 200


def test_bookmark_returns_url(http_client):
    with _mock_httpx(""):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["url"] == "https://example.com"


def test_bookmark_extracts_og_title(http_client):
    html = '<meta property="og:title" content="Hello World" />'
    with _mock_httpx(html):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["title"] == "Hello World"


def test_bookmark_falls_back_to_title_tag(http_client):
    html = "<html><head><title>Page Title</title></head></html>"
    with _mock_httpx(html):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["title"] == "Page Title"


def test_bookmark_extracts_og_description(http_client):
    html = '<meta property="og:description" content="A nice page" />'
    with _mock_httpx(html):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["description"] == "A nice page"


def test_bookmark_falls_back_to_meta_description(http_client):
    html = '<meta name="description" content="Meta desc" />'
    with _mock_httpx(html):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["description"] == "Meta desc"


def test_bookmark_extracts_og_image(http_client):
    html = '<meta property="og:image" content="https://example.com/img.png" />'
    with _mock_httpx(html):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.json()["image"] == "https://example.com/img.png"


def test_bookmark_sets_favicon_from_origin(http_client):
    with _mock_httpx(""):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com/page"})
    assert resp.json()["favicon"] == "https://example.com/favicon.ico"


def test_bookmark_graceful_on_network_error(http_client):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=Exception("timeout"))
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com"


def test_bookmark_requires_auth(http_client):
    with patch("app.media.router.validate_token", return_value=False):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.status_code == 401


# ─── Drive-file move ──────────────────────────────────────────────────────────


def test_move_drive_file_returns_200(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".docx")
    resp = http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": src_block,
            "target_block_id": tgt_block,
        },
    )
    assert resp.status_code == 200


def test_move_drive_file_response_contains_new_url(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".docx")
    resp = http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": src_block,
            "target_block_id": tgt_block,
        },
    )
    new_url = resp.json()["url"]
    assert tgt_block in new_url
    assert src_block not in new_url


def test_move_drive_file_removes_from_source(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    src_path = _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".pdf")
    http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": src_block,
            "target_block_id": tgt_block,
        },
    )
    assert not src_path.exists()


def test_move_drive_file_places_in_target(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".pdf")
    http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": src_block,
            "target_block_id": tgt_block,
        },
    )
    matches = list((tmp_upload_dir / "drives" / tgt_block).glob(f"{file_uuid}*"))
    assert len(matches) == 1


def test_move_drive_file_creates_target_dir(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".txt")
    assert not (tmp_upload_dir / "drives" / tgt_block).exists()
    http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": src_block,
            "target_block_id": tgt_block,
        },
    )
    assert (tmp_upload_dir / "drives" / tgt_block).is_dir()


def test_move_drive_file_not_found_returns_404(http_client, tmp_upload_dir):
    resp = http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": str(uuid.uuid4()),
            "source_block_id": str(uuid.uuid4()),
            "target_block_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404


def test_move_drive_file_requires_auth(http_client):
    with patch("app.media.router.validate_token", return_value=False):
        resp = http_client.post(
            "/api/media/drive-file/move",
            json={
                "file_uuid": str(uuid.uuid4()),
                "source_block_id": str(uuid.uuid4()),
                "target_block_id": str(uuid.uuid4()),
            },
        )
    assert resp.status_code == 401
