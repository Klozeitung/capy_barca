"""Tests for the downloads router."""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.downloads.downloads_router as dr
from app.main import app
from app.session.deps import get_current_user
from app.users.model import User


def _account(role: str) -> User:
    return User(
        id=uuid.uuid4(), username=f"{role}-account", password_hash="x",
        role=role, is_active=True,
    )


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """An empty android/ folder the router reads from."""
    path = tmp_path / "android"
    path.mkdir()
    monkeypatch.setattr(dr, "_ANDROID_DIR", path)
    return path


@pytest.fixture
def member_client():
    app.dependency_overrides[get_current_user] = lambda: _account("member")
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _write(folder, name: str, content: bytes = b"apk") -> None:
    (folder / name).write_bytes(content)


# ── find_android_app ──────────────────────────────────────────────────────────


def test_the_highest_version_wins(folder):
    _write(folder, "capybarca-android-0.9.0.0-debug.apk")
    _write(folder, "capybarca-android-0.10.0.0-debug.apk")
    _write(folder, "capybarca-android-0.9.5.3-debug.apk")
    _, info = dr.find_android_app(folder)
    assert info.filename == "capybarca-android-0.10.0.0-debug.apk"
    assert info.version == "0.10.0.0"
    assert info.debug is True


def test_a_release_build_wins_over_a_debug_build_of_the_same_version(folder):
    _write(folder, "capybarca-android-1.0.0.0-debug.apk")
    _write(folder, "capybarca-android-1.0.0.0.apk")
    _, info = dr.find_android_app(folder)
    assert info.filename == "capybarca-android-1.0.0.0.apk"
    assert info.debug is False


def test_files_of_another_name_are_ignored(folder):
    _write(folder, "app-debug.apk")
    _write(folder, "capybarca-android-1.0.0.apk")
    _write(folder, "capybarca-android-2.0.0.0.apk.part")
    _write(folder, "notes.txt")
    (folder / "capybarca-android-3.0.0.0.apk").mkdir()
    assert dr.find_android_app(folder) is None


def test_a_missing_folder_offers_nothing(tmp_path):
    assert dr.find_android_app(tmp_path / "absent") is None


def test_the_size_is_reported(folder):
    _write(folder, "capybarca-android-0.1.0.0.apk", b"x" * 1234)
    _, info = dr.find_android_app(folder)
    assert info.size == 1234


# ── Endpoints ─────────────────────────────────────────────────────────────────


def test_info_requires_a_session(folder):
    assert TestClient(app).get("/api/downloads/android").status_code == 401


def test_file_requires_a_session(folder):
    _write(folder, "capybarca-android-0.1.0.0.apk")
    assert TestClient(app).get("/api/downloads/android/file").status_code == 401


def test_a_member_gets_the_info(folder, member_client):
    _write(folder, "capybarca-android-0.1.0.0-debug.apk", b"abc")
    response = member_client.get("/api/downloads/android")
    assert response.status_code == 200
    assert response.json() == {
        "filename": "capybarca-android-0.1.0.0-debug.apk",
        "version": "0.1.0.0",
        "debug": True,
        "size": 3,
    }


def test_info_is_null_without_an_app(folder, member_client):
    response = member_client.get("/api/downloads/android")
    assert response.status_code == 200
    assert response.json() is None


def test_a_member_downloads_the_file(folder, member_client):
    _write(folder, "capybarca-android-0.1.0.0.apk", b"package-bytes")
    response = member_client.get("/api/downloads/android/file")
    assert response.status_code == 200
    assert response.content == b"package-bytes"
    assert response.headers["content-type"] == dr.APK_MEDIA_TYPE
    assert 'filename="capybarca-android-0.1.0.0.apk"' in response.headers["content-disposition"]


def test_the_file_is_404_without_an_app(folder, member_client):
    assert member_client.get("/api/downloads/android/file").status_code == 404
