"""
Tests for the block router.

All tests exercise the HTTP layer via FastAPI's TestClient. Auth is handled
by monkeypatching ``validate_token`` to return ``True``, which avoids a
dependency on a live session in the database while still verifying that the
auth guard is wired correctly through a dedicated test that patches it to
return ``False``.

A workspace root block is seeded before each test via the ``http_client``
fixture so that foreign key constraints on ``parent_id`` are satisfied from
the first request.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app


@pytest.fixture(autouse=True)
def mock_auth():
    """Patch validate_token to return True for all router tests."""
    with patch("app.blocks.router.validate_token", return_value=True):
        yield


@pytest.fixture
def http_client(isolated_db):
    """
    TestClient with workspace root pre-seeded and session cookie set on the
    client instance (avoids the per-request cookies DeprecationWarning).

    ``isolated_db`` is explicitly requested to guarantee fixture ordering:
    the in-memory DB must be ready before we seed the workspace root.
    """
    with s.SessionLocal() as db:
        block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
        db.add(block)
        db.commit()

    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


# ─── Auth guard ───────────────────────────────────────────────────────────────


def test_create_block_requires_auth(http_client):
    with patch("app.blocks.router.validate_token", return_value=False):
        response = http_client.post(
            "/api/blocks",
            json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
        )
    assert response.status_code == 401


def test_get_block_requires_auth(http_client):
    with patch("app.blocks.router.validate_token", return_value=False):
        response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 401


# ─── POST /api/blocks ─────────────────────────────────────────────────────────


def test_create_block_returns_201(http_client):
    response = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert response.status_code == 201


def test_create_block_response_contains_id(http_client):
    response = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    data = response.json()
    assert "id" in data
    assert uuid.UUID(data["id"])


def test_create_block_response_contains_type(http_client):
    response = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert response.json()["type"] == "page"


def test_create_block_state_is_active(http_client):
    response = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert response.json()["state"] == "active"


def test_create_block_with_content(http_client):
    content = {"text": [{"plain_text": "Hello"}]}
    response = http_client.post(
        "/api/blocks",
        json={
            "type": "paragraph",
            "parent_id": str(WORKSPACE_ROOT_ID),
            "content": content,
        },
    )
    assert response.json()["content"] == content


def test_create_block_unknown_parent_returns_404(http_client):
    response = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_create_block_unknown_type_returns_422(http_client):
    """An unregistered block type must be rejected at the API boundary."""
    response = http_client.post(
        "/api/blocks",
        json={"type": "not_a_real_type", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert response.status_code == 422


# ─── GET /api/blocks/{block_id} ───────────────────────────────────────────────


def test_get_block_returns_200(http_client):
    response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 200


def test_get_block_returns_correct_id(http_client):
    response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.json()["id"] == str(WORKSPACE_ROOT_ID)


def test_get_block_unknown_id_returns_404(http_client):
    response = http_client.get(f"/api/blocks/{uuid.uuid4()}")
    assert response.status_code == 404


# ─── GET /api/blocks/{block_id}/children ─────────────────────────────────────


def test_list_children_returns_200(http_client):
    response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}/children")
    assert response.status_code == 200


def test_list_children_returns_list(http_client):
    response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}/children")
    assert isinstance(response.json(), list)


def test_list_children_contains_created_block(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    children_resp = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}/children")
    child_ids = [c["id"] for c in children_resp.json()]
    assert block_id in child_ids


def test_list_children_unknown_parent_returns_404(http_client):
    response = http_client.get(f"/api/blocks/{uuid.uuid4()}/children")
    assert response.status_code == 404


# ─── PATCH /api/blocks/{block_id} ────────────────────────────────────────────


def test_update_block_returns_200(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.patch(
        f"/api/blocks/{block_id}",
        json={"content": {"title": "Updated"}},
    )
    assert response.status_code == 200


def test_update_block_content_is_persisted(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    new_content = {"title": "Hello World"}
    http_client.patch(f"/api/blocks/{block_id}", json={"content": new_content})
    get_resp = http_client.get(f"/api/blocks/{block_id}")
    assert get_resp.json()["content"] == new_content


def test_update_block_unknown_id_returns_404(http_client):
    response = http_client.patch(
        f"/api/blocks/{uuid.uuid4()}",
        json={"content": {}},
    )
    assert response.status_code == 404


# ─── PATCH /api/blocks/{block_id}/appearance ─────────────────────────────────


def test_update_appearance_sets_icon(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.patch(
        f"/api/blocks/{block_id}/appearance",
        json={"icon": "mdi:star"},
    )
    assert response.status_code == 200
    assert response.json()["icon"] == "mdi:star"


def test_update_appearance_sets_cover(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.patch(
        f"/api/blocks/{block_id}/appearance",
        json={"cover": "gradient:linear-gradient(90deg,#f00,#00f)"},
    )
    assert response.status_code == 200
    assert response.json()["cover"] == "gradient:linear-gradient(90deg,#f00,#00f)"


# ─── POST /api/blocks/{block_id}/move ────────────────────────────────────────


def test_move_block_changes_parent(http_client):
    block_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = block_resp.json()["id"]
    new_parent_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    new_parent_id = new_parent_resp.json()["id"]
    move_resp = http_client.post(
        f"/api/blocks/{block_id}/move",
        json={"new_parent_id": new_parent_id, "new_position": 1.0},
    )
    assert move_resp.json()["parent_id"] == new_parent_id


def test_move_block_into_self_returns_409(http_client):
    block_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = block_resp.json()["id"]
    response = http_client.post(
        f"/api/blocks/{block_id}/move",
        json={"new_parent_id": block_id, "new_position": 1.0},
    )
    assert response.status_code == 409


def test_move_block_unknown_id_returns_404(http_client):
    response = http_client.post(
        f"/api/blocks/{uuid.uuid4()}/move",
        json={"new_parent_id": str(WORKSPACE_ROOT_ID), "new_position": 1.0},
    )
    assert response.status_code == 404


# ─── GET /api/blocks/trash ────────────────────────────────────────────────────


def test_list_trash_returns_200(http_client):
    response = http_client.get("/api/blocks/trash")
    assert response.status_code == 200


def test_list_trash_empty_initially(http_client):
    response = http_client.get("/api/blocks/trash")
    assert response.json() == []


def test_list_trash_contains_deleted_block(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    trash = http_client.get("/api/blocks/trash").json()
    assert any(b["id"] == block_id for b in trash)


def test_list_trash_does_not_contain_child_of_deleted_block(http_client):
    parent = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]
    child = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": parent},
    ).json()["id"]
    http_client.delete(f"/api/blocks/{parent}")
    trash = http_client.get("/api/blocks/trash").json()
    # Parent appears, child does NOT (it is a nested trashed item)
    ids = [b["id"] for b in trash]
    assert parent in ids
    assert child not in ids


def test_list_trash_restored_block_removed_from_trash(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    http_client.post(f"/api/blocks/{block_id}/restore")
    trash = http_client.get("/api/blocks/trash").json()
    assert not any(b["id"] == block_id for b in trash)


def test_list_trash_active_blocks_not_included(http_client):
    http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    trash = http_client.get("/api/blocks/trash").json()
    assert trash == []


# ─── DELETE /api/blocks/{block_id} ────────────────────────────────────────────


def test_soft_delete_returns_200(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.delete(f"/api/blocks/{block_id}")
    assert response.status_code == 200


def test_soft_delete_response_contains_affected_ids(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.delete(f"/api/blocks/{block_id}")
    assert block_id in response.json()["affected"]


def test_soft_delete_workspace_returns_409(http_client):
    response = http_client.delete(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 409


def test_soft_delete_unknown_id_returns_404(http_client):
    response = http_client.delete(f"/api/blocks/{uuid.uuid4()}")
    assert response.status_code == 404


# ─── POST /api/blocks/{block_id}/restore ─────────────────────────────────────


def test_restore_block_returns_200(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    response = http_client.post(f"/api/blocks/{block_id}/restore")
    assert response.status_code == 200


def test_restore_block_response_contains_restored_ids(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    response = http_client.post(f"/api/blocks/{block_id}/restore")
    assert block_id in response.json()["restored"]


def test_restore_active_block_returns_409(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.post(f"/api/blocks/{block_id}/restore")
    assert response.status_code == 409


# ─── DELETE /api/blocks/{block_id}/purge ─────────────────────────────────────


def test_purge_trashed_block_returns_204(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    response = http_client.delete(f"/api/blocks/{block_id}/purge")
    assert response.status_code == 204


def test_purge_active_block_returns_409(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    response = http_client.delete(f"/api/blocks/{block_id}/purge")
    assert response.status_code == 409


def test_purge_block_no_longer_retrievable(http_client):
    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    http_client.delete(f"/api/blocks/{block_id}/purge")
    get_resp = http_client.get(f"/api/blocks/{block_id}")
    assert get_resp.status_code == 404


def test_purge_unknown_block_returns_404(http_client):
    response = http_client.delete(f"/api/blocks/{uuid.uuid4()}/purge")
    assert response.status_code == 404


# ─── DELETE /api/blocks/{block_id}/purge – filesystem cleanup ────────────────


def test_purge_drive_block_removes_drive_directory(http_client, tmp_path, monkeypatch):
    """Purging a drive block must delete its entire drive directory."""
    import app.media.router as media_module
    import app.blocks.router as block_router_module

    fake_root = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", fake_root)

    # Create and trash a drive block
    resp = http_client.post(
        "/api/blocks",
        json={"type": "drive", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")

    # Seed a fake drive directory as if files had been uploaded
    drive_dir = fake_root / "drives" / block_id
    drive_dir.mkdir(parents=True)
    (drive_dir / "deadbeef.docx").write_bytes(b"fake content")

    http_client.delete(f"/api/blocks/{block_id}/purge")

    assert not drive_dir.exists(), "Drive directory must be removed on purge"


def test_purge_file_block_removes_file(http_client, tmp_path, monkeypatch):
    """Purging a file block must delete the file referenced in content.url."""
    import app.media.router as media_module

    fake_root = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", fake_root)

    fake_file = fake_root / "files" / "abc123.pdf"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_bytes(b"pdf data")

    resp = http_client.post(
        "/api/blocks",
        json={
            "type": "file",
            "parent_id": str(WORKSPACE_ROOT_ID),
            "content": {
                "file_uuid": "abc123",
                "url": "/static/uploads/files/abc123.pdf",
                "filename": "report.pdf",
                "size": 8,
                "mime": "application/pdf",
            },
        },
    )
    block_id = resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    http_client.delete(f"/api/blocks/{block_id}/purge")

    assert not fake_file.exists(), "Uploaded file must be removed on purge"


def test_purge_missing_files_does_not_raise(http_client, tmp_path, monkeypatch):
    """Purge must succeed even when the physical file is already gone."""
    import app.media.router as media_module

    empty_root = tmp_path / "uploads"
    empty_root.mkdir()
    monkeypatch.setattr(media_module, "STATIC_ROOT", empty_root)

    resp = http_client.post(
        "/api/blocks",
        json={"type": "drive", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    http_client.delete(f"/api/blocks/{block_id}")
    response = http_client.delete(f"/api/blocks/{block_id}/purge")

    assert response.status_code == 204


# ─── POST /api/blocks/{block_id}/rebalance-children ──────────────────────────


def test_rebalance_children_returns_200(http_client):
    response = http_client.post(
        f"/api/blocks/{WORKSPACE_ROOT_ID}/rebalance-children"
    )
    assert response.status_code == 200


def test_rebalance_children_response_has_rebalanced_key(http_client):
    response = http_client.post(
        f"/api/blocks/{WORKSPACE_ROOT_ID}/rebalance-children"
    )
    assert "rebalanced" in response.json()


def test_rebalance_children_returns_empty_when_no_children(http_client):
    response = http_client.post(
        f"/api/blocks/{WORKSPACE_ROOT_ID}/rebalance-children"
    )
    assert response.json()["rebalanced"] == []


def test_rebalance_children_unknown_block_returns_404(http_client):
    response = http_client.post(
        f"/api/blocks/{uuid.uuid4()}/rebalance-children"
    )
    assert response.status_code == 404


def test_rebalance_children_normalises_positions(http_client):
    """
    Create three blocks, manually set non-integer positions via PATCH,
    trigger rebalance, and verify all positions are now evenly spaced integers.
    """
    ids = []
    for _ in range(3):
        r = http_client.post(
            "/api/blocks",
            json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
        )
        ids.append(r.json()["id"])

    http_client.patch(f"/api/blocks/{ids[0]}", json={"position": 0.1})
    http_client.patch(f"/api/blocks/{ids[1]}", json={"position": 0.15})
    http_client.patch(f"/api/blocks/{ids[2]}", json={"position": 0.2})

    http_client.post(f"/api/blocks/{WORKSPACE_ROOT_ID}/rebalance-children")

    positions = []
    for block_id in ids:
        r = http_client.get(f"/api/blocks/{block_id}")
        positions.append(r.json()["position"])

    positions.sort()
    assert positions == [1.0, 2.0, 3.0]


# ─── POST /api/blocks/{block_id}/cover ───────────────────────────────────────


def test_upload_cover_returns_200(http_client, tmp_path, monkeypatch):
    """Uploading a cover image returns 200 and sets the cover field."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal PNG header
    response = http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("cover.png", image_bytes, "image/png")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cover"] is not None
    assert block_id in data["cover"]
    assert data["cover"].endswith(".png")


def test_upload_cover_file_is_written_to_disk(http_client, tmp_path, monkeypatch):
    """The uploaded cover file is actually written under STATIC_ROOT/covers/."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    image_bytes = b"fake-image-data"
    http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("photo.jpg", image_bytes, "image/jpeg")},
    )

    cover_file = tmp_path / "covers" / f"{block_id}.jpg"
    assert cover_file.exists()
    assert cover_file.read_bytes() == image_bytes


def test_upload_cover_replaces_previous_cover(http_client, tmp_path, monkeypatch):
    """Uploading a second cover deletes the first file and replaces it."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("first.png", b"first", "image/png")},
    )
    http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("second.jpg", b"second", "image/jpeg")},
    )

    covers_dir = tmp_path / "covers"
    remaining = list(covers_dir.glob(f"{block_id}.*"))
    assert len(remaining) == 1
    assert remaining[0].suffix == ".jpg"


def test_upload_cover_unknown_block_returns_404(http_client, tmp_path, monkeypatch):
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    response = http_client.post(
        f"/api/blocks/{uuid.uuid4()}/cover",
        files={"file": ("cover.png", b"data", "image/png")},
    )
    assert response.status_code == 404


# ─── DELETE /api/blocks/{block_id}/cover ─────────────────────────────────────


def test_remove_cover_returns_200_and_clears_field(http_client, tmp_path, monkeypatch):
    """Deleting a cover returns 200 and sets cover to null."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("cover.png", b"img", "image/png")},
    )

    response = http_client.delete(f"/api/blocks/{block_id}/cover")
    assert response.status_code == 200
    assert response.json()["cover"] is None


def test_remove_cover_deletes_file_from_disk(http_client, tmp_path, monkeypatch):
    """The physical cover file is removed when the cover is deleted."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": ("cover.png", b"img", "image/png")},
    )
    cover_file = tmp_path / "covers" / f"{block_id}.png"
    assert cover_file.exists()

    http_client.delete(f"/api/blocks/{block_id}/cover")
    assert not cover_file.exists()


def test_remove_cover_is_idempotent(http_client, tmp_path, monkeypatch):
    """DELETE /cover on a block without a cover still returns 200."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    create_resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = create_resp.json()["id"]

    response = http_client.delete(f"/api/blocks/{block_id}/cover")
    assert response.status_code == 200
    assert response.json()["cover"] is None


def test_remove_cover_unknown_block_returns_404(http_client, tmp_path, monkeypatch):
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    response = http_client.delete(f"/api/blocks/{uuid.uuid4()}/cover")
    assert response.status_code == 404
