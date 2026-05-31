"""
Tests for the WOPI router.

All filesystem writes are redirected to a per-test temp directory via the
``tmp_drive_dir`` fixture.  Authentication is stubbed by ``mock_auth``.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.wopi.router as wopi_module


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_auth():
    with patch("app.wopi.router.validate_token", return_value=True):
        yield


@pytest.fixture
def http_client():
    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


@pytest.fixture
def test_secret(monkeypatch):
    monkeypatch.setattr(wopi_module, "SECRET_KEY", "test-secret-key-for-wopi")
    return "test-secret-key-for-wopi"


@pytest.fixture
def tmp_drive_dir(tmp_path, monkeypatch):
    """Redirect STATIC_ROOT to a temp directory and create a test drive file."""
    import app.media.router as media_module

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", upload_dir)
    yield upload_dir


FILE_UUID = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
BLOCK_ID = "11112222-3333-4444-5555-666677778888"
FILENAME = "test-document.docx"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE_CONTENT = b"PK\x03\x04fake docx content"


def _seed_file(root: Path) -> Path:
    """Create a fake drive file under the given STATIC_ROOT."""
    drive_dir = root / "drives" / BLOCK_ID
    drive_dir.mkdir(parents=True, exist_ok=True)
    path = drive_dir / f"{FILE_UUID}.docx"
    path.write_bytes(FILE_CONTENT)
    return path


def _valid_token(secret: str, ttl: int = 3600) -> str:
    """Generate a valid WOPI token using the module's _encode_token helper."""
    monkeypatch_secret(secret)
    claims = {
        "file_uuid": FILE_UUID,
        "block_id": BLOCK_ID,
        "filename": FILENAME,
        "mime": MIME,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
    }
    old_key = wopi_module.SECRET_KEY
    wopi_module.SECRET_KEY = secret
    token = wopi_module._encode_token(claims)
    wopi_module.SECRET_KEY = old_key
    return token


def monkeypatch_secret(secret: str):
    wopi_module.SECRET_KEY = secret


# ─── Token endpoint ───────────────────────────────────────────────────────────


def test_token_requires_session(http_client):
    """Token endpoint must return 401 when no session cookie is present."""
    anon = TestClient(app)
    resp = anon.post(
        "/api/wopi/token",
        json={"file_uuid": FILE_UUID, "block_id": BLOCK_ID, "filename": FILENAME, "mime": MIME},
    )
    assert resp.status_code == 401


def test_token_returns_editor_url(http_client, test_secret):
    resp = http_client.post(
        "/api/wopi/token",
        json={"file_uuid": FILE_UUID, "block_id": BLOCK_ID, "filename": FILENAME, "mime": MIME},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "editor_url" in data
    assert "/collabora/browser/dist/cool.html" in data["editor_url"]


def test_token_editor_url_contains_wopi_src(http_client, test_secret):
    resp = http_client.post(
        "/api/wopi/token",
        json={"file_uuid": FILE_UUID, "block_id": BLOCK_ID, "filename": FILENAME, "mime": MIME},
    )
    assert "WOPISrc=" in resp.json()["editor_url"]


def test_token_editor_url_contains_access_token(http_client, test_secret):
    resp = http_client.post(
        "/api/wopi/token",
        json={"file_uuid": FILE_UUID, "block_id": BLOCK_ID, "filename": FILENAME, "mime": MIME},
    )
    assert "access_token=" in resp.json()["editor_url"]


# ─── Token encode / decode ────────────────────────────────────────────────────


def test_encode_decode_roundtrip(test_secret):
    claims = {
        "file_uuid": FILE_UUID,
        "block_id": BLOCK_ID,
        "filename": FILENAME,
        "mime": MIME,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = wopi_module._encode_token(claims)
    decoded = wopi_module._decode_token(token)
    assert decoded["file_uuid"] == FILE_UUID
    assert decoded["block_id"] == BLOCK_ID
    assert decoded["filename"] == FILENAME


def test_decode_rejects_tampered_signature(test_secret):
    claims = {
        "file_uuid": FILE_UUID,
        "block_id": BLOCK_ID,
        "filename": FILENAME,
        "mime": MIME,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = wopi_module._encode_token(claims)
    tampered = token[:-4] + "xxxx"
    with pytest.raises(Exception):
        wopi_module._decode_token(tampered)


def test_decode_rejects_expired_token(test_secret):
    claims = {
        "file_uuid": FILE_UUID,
        "block_id": BLOCK_ID,
        "filename": FILENAME,
        "mime": MIME,
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
    }
    token = wopi_module._encode_token(claims)
    with pytest.raises(Exception):
        wopi_module._decode_token(token)


def test_decode_rejects_malformed_token(test_secret):
    with pytest.raises(Exception):
        wopi_module._decode_token("notavalidtoken")


# ─── CheckFileInfo ────────────────────────────────────────────────────────────


def test_check_file_info_returns_200(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    resp = http_client.get(f"/api/wopi/files/{token}")
    assert resp.status_code == 200


def test_check_file_info_base_filename(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    data = http_client.get(f"/api/wopi/files/{token}").json()
    assert data["BaseFileName"] == FILENAME


def test_check_file_info_size(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    data = http_client.get(f"/api/wopi/files/{token}").json()
    assert data["Size"] == len(FILE_CONTENT)


def test_check_file_info_user_can_write(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    data = http_client.get(f"/api/wopi/files/{token}").json()
    assert data["UserCanWrite"] is True


def test_check_file_info_invalid_token_returns_401(http_client, tmp_drive_dir):
    resp = http_client.get("/api/wopi/files/invalid.token.here")
    assert resp.status_code == 401


def test_check_file_info_missing_file_returns_404(http_client, tmp_drive_dir, test_secret):
    # Do not seed the file.
    token = _valid_token(test_secret)
    resp = http_client.get(f"/api/wopi/files/{token}")
    assert resp.status_code == 404


# ─── GetFile ──────────────────────────────────────────────────────────────────


def test_get_file_returns_200(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    resp = http_client.get(f"/api/wopi/files/{token}/contents")
    assert resp.status_code == 200


def test_get_file_returns_correct_bytes(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    resp = http_client.get(f"/api/wopi/files/{token}/contents")
    assert resp.content == FILE_CONTENT


def test_get_file_invalid_token_returns_401(http_client, tmp_drive_dir):
    resp = http_client.get("/api/wopi/files/bad.token/contents")
    assert resp.status_code == 401


# ─── PutFile ──────────────────────────────────────────────────────────────────


def test_put_file_returns_200(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    resp = http_client.post(f"/api/wopi/files/{token}/contents", content=b"updated content")
    assert resp.status_code == 200


def test_put_file_persists_bytes(http_client, tmp_drive_dir, test_secret):
    file_path = _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    new_content = b"new document content after save"
    http_client.post(f"/api/wopi/files/{token}/contents", content=new_content)
    assert file_path.read_bytes() == new_content


def test_put_file_invalid_token_returns_401(http_client, tmp_drive_dir):
    resp = http_client.post("/api/wopi/files/bad.token/contents", content=b"data")
    assert resp.status_code == 401


def test_put_file_status_ok_in_response(http_client, tmp_drive_dir, test_secret):
    _seed_file(tmp_drive_dir)
    token = _valid_token(test_secret)
    data = http_client.post(
        f"/api/wopi/files/{token}/contents", content=b"content"
    ).json()
    assert data["status"] == "ok"
