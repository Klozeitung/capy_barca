"""
Tests for the uploaded icons router (/api/icons).

Uploads land in a temporary directory (``icon_dir``), so the real ``static/``
tree stays clean. The acting admin is stored as a row, because an uploaded
icon records who uploaded it.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.database.database as db_module
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.icons import service
from app.icons.model import CustomIcon
from app.main import app
from app.users.model import User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


@pytest.fixture(autouse=True)
def icon_dir(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setattr(service, "STATIC_ROOT", root)
    return root / service.ICONS_DIRECTORY


@pytest.fixture
def stored_admin(admin_user):
    with db_module.SessionLocal() as db:
        db.merge(User(
            id=admin_user.id, username=admin_user.username,
            password_hash="x", role="admin", is_active=True,
        ))
        db.commit()
    return admin_user


@pytest.fixture
def announced(monkeypatch):
    """Record every ``custom_icons.changed`` the router sends."""
    import app.icons.router as router_module

    sent: list[dict] = []
    monkeypatch.setattr(
        router_module, "emit_installation_message",
        lambda message_type, payload, db=None: sent.append({"type": message_type, **payload}),
    )
    return sent


def _upload(client, content: bytes = PNG, filename: str = "Icon A.png", content_type="image/png"):
    return client.post("/api/icons", files={"file": (filename, content, content_type)})


def _files(directory) -> list[str]:
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


# ─── Listing and uploading ────────────────────────────────────────────────────


def test_the_list_starts_empty(http_client):
    assert http_client.get("/api/icons").json() == []


def test_an_admin_uploads_an_icon(http_client, stored_admin, icon_dir):
    resp = _upload(http_client)

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Icon A"
    assert body["icon"] == f"custom:{body['id']}"
    assert body["url"] == f"/static/uploads/icons/{body['id']}.png"
    assert (icon_dir / f"{body['id']}.png").read_bytes() == PNG
    assert http_client.get("/api/icons").json() == [body]
    with db_module.SessionLocal() as db:
        assert db.get(CustomIcon, uuid.UUID(body["id"])).uploaded_by == stored_admin.id


@pytest.mark.parametrize("content, filename", [
    (GIF, "a.gif"), (JPEG, "a.jpg"), (JPEG, "a.JPEG"), (WEBP, "a.webp"),
])
def test_every_raster_format_is_accepted(http_client, stored_admin, content, filename):
    assert _upload(http_client, content, filename, "application/octet-stream").status_code == 201


def test_every_account_may_list_the_icons(http_client, stored_admin, login_as, member_user):
    uploaded = _upload(http_client).json()
    login_as(member_user)
    assert http_client.get("/api/icons").json() == [uploaded]


def test_a_member_may_not_upload(member_client, icon_dir):
    assert _upload(member_client).status_code == 403
    assert _files(icon_dir) == []


def test_listing_needs_a_session():
    assert TestClient(app).get("/api/icons").status_code == 401


@pytest.mark.parametrize("content, filename", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg"),
    (PNG, "a.bmp"),
    (PNG, "a"),
    (b"plain text, not an image", "a.png"),
    (PNG, "a.gif"),
    (b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32, "a.webp"),
])
def test_anything_but_a_matching_raster_image_is_refused(
    http_client, stored_admin, icon_dir, content, filename,
):
    resp = _upload(http_client, content, filename, "application/octet-stream")
    assert resp.status_code == 415
    assert _files(icon_dir) == []
    assert http_client.get("/api/icons").json() == []


def test_an_oversized_icon_is_refused(http_client, stored_admin, icon_dir, monkeypatch):
    monkeypatch.setattr(service, "MAX_ICON_BYTES", 16)
    resp = _upload(http_client)
    assert resp.status_code == 413
    assert resp.json()["detail"] == service.TOO_LARGE
    assert _files(icon_dir) == []


def test_the_name_comes_from_the_file_name():
    assert service.display_name("Icon  B.final.png") == "Icon B.final"
    assert service.display_name("C:\\folder\\Icon C.png") == "Icon C"
    assert service.display_name("folder/Icon D.webp") == "Icon D"
    assert service.display_name(".png") == ".png"
    assert service.display_name("") == "icon"
    assert service.display_name(None) == "icon"
    assert len(service.display_name("x" * 200 + ".png")) == service.NAME_MAX_LENGTH


def test_uploading_announces_the_new_list(http_client, stored_admin, announced):
    body = _upload(http_client).json()
    assert announced == [{"type": "custom_icons.changed", "icons": [body]}]


# ─── Deleting ─────────────────────────────────────────────────────────────────


def _database(http_client) -> str:
    return http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]


def _entry(http_client, database_id: str, icon: str | None = None) -> str:
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    if icon:
        _set_icon(http_client, entry_id, icon)
    return entry_id


def _set_icon(http_client, block_id: str, icon: str) -> None:
    resp = http_client.patch(f"/api/blocks/{block_id}/appearance", json={"icon": icon})
    assert resp.status_code == 200, resp.text


def _icon_of(block_id: str):
    with db_module.SessionLocal() as db:
        return db.get(Block, uuid.UUID(block_id)).icon


def _default_of(http_client, database_id: str):
    return http_client.get(f"/api/databases/{database_id}/settings").json()["default_entry_icon"]


def _set_default(http_client, database_id: str, icon) -> None:
    resp = http_client.put(
        f"/api/databases/{database_id}/settings", json={"default_entry_icon": icon},
    )
    assert resp.status_code == 200, resp.text


def test_deleting_removes_the_icon_and_its_file(http_client, stored_admin, icon_dir):
    body = _upload(http_client).json()

    assert http_client.delete(f"/api/icons/{body['id']}").status_code == 204

    assert http_client.get("/api/icons").json() == []
    assert _files(icon_dir) == []


def test_deleting_an_unknown_icon_returns_404(http_client, stored_admin):
    assert http_client.delete(f"/api/icons/{uuid.uuid4()}").status_code == 404


def test_a_member_may_not_delete(http_client, stored_admin, login_as, member_user, icon_dir):
    body = _upload(http_client).json()
    login_as(member_user)
    assert http_client.delete(f"/api/icons/{body['id']}").status_code == 403
    assert len(_files(icon_dir)) == 1


def test_a_page_loses_the_deleted_icon(http_client, stored_admin):
    icon = _upload(http_client).json()["icon"]
    page = http_client.post(
        "/api/blocks", json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]
    _set_icon(http_client, page, icon)

    http_client.delete(f"/api/icons/{icon.removeprefix('custom:')}")

    assert _icon_of(page) is None


def test_an_entry_falls_back_to_the_default_icon_of_its_database(http_client, stored_admin):
    icon = _upload(http_client).json()["icon"]
    database = _database(http_client)
    _set_default(http_client, database, "mdi:star-outline")
    entry = _entry(http_client, database, icon)
    untouched = _entry(http_client, database, "mdi:alpha-a")

    http_client.delete(f"/api/icons/{icon.removeprefix('custom:')}")

    assert _icon_of(entry) == "mdi:star-outline"
    assert _icon_of(untouched) == "mdi:alpha-a"


def test_an_entry_loses_the_icon_when_it_was_the_database_default(http_client, stored_admin):
    icon = _upload(http_client).json()["icon"]
    database = _database(http_client)
    _set_default(http_client, database, icon)
    entry = _entry(http_client, database)
    assert _icon_of(entry) == icon

    http_client.delete(f"/api/icons/{icon.removeprefix('custom:')}")

    assert _default_of(http_client, database) is None
    assert _icon_of(entry) is None


def test_an_entry_template_loses_the_deleted_icon(http_client, stored_admin):
    icon = _upload(http_client).json()["icon"]
    database = _database(http_client)
    _set_default(http_client, database, "mdi:star-outline")
    template = http_client.post(f"/api/databases/{database}/entry-templates").json()["id"]
    _set_icon(http_client, template, icon)

    http_client.delete(f"/api/icons/{icon.removeprefix('custom:')}")

    assert _icon_of(template) is None


def test_the_recently_used_icons_forget_the_deleted_icon(http_client, stored_admin):
    icon = _upload(http_client).json()["icon"]
    stamp = "2045-07-17T00:00:00+00:00"
    with db_module.SessionLocal() as db:
        db.get(User, stored_admin.id).recent_icons = [
            {"icon": icon, "used_at": stamp}, {"icon": "mdi:alpha-a", "used_at": stamp},
        ]
        db.commit()

    http_client.delete(f"/api/icons/{icon.removeprefix('custom:')}")

    with db_module.SessionLocal() as db:
        names = [e["icon"] for e in db.get(User, stored_admin.id).recent_icons]
    assert names == ["mdi:alpha-a"]


def test_another_icon_is_left_in_place(http_client, stored_admin):
    kept = _upload(http_client, filename="Icon B.png").json()
    deleted = _upload(http_client).json()
    page = http_client.post(
        "/api/blocks", json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]
    _set_icon(http_client, page, kept["icon"])

    http_client.delete(f"/api/icons/{deleted['id']}")

    assert _icon_of(page) == kept["icon"]
    assert http_client.get("/api/icons").json() == [kept]


def test_deleting_announces_the_new_list(http_client, stored_admin, announced):
    body = _upload(http_client).json()
    announced.clear()
    http_client.delete(f"/api/icons/{body['id']}")
    assert announced == [{"type": "custom_icons.changed", "icons": []}]
