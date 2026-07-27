"""
Tests for the block router.

All tests exercise the HTTP layer via FastAPI's TestClient. Authentication is
supplied by overriding ``app.session.deps.get_current_user``, the same gate the
router uses in production — there is no longer a module-local ``validate_token``
to monkeypatch, because the router no longer has its own auth path.

Unauthenticated behaviour is checked with ``anon_client``, which carries no
override and therefore runs the real dependency chain.

A workspace root block is seeded before each test via the ``http_client`` or
``workspace_root`` fixture so that foreign key constraints on ``parent_id`` are
satisfied from the first request.

Authorization tests live at the end of the file. They cover the object-level
guard that every block-addressed endpoint now applies.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user, require_session
from app.users.model import User

# Module-level client with no dependency overrides, for 401 checks. Created
# once at import time; the overrides it must not see are installed and torn
# down per test by the fixtures below.
anon_client = TestClient(app)


def _make_user(role="member", is_active=True, persist=True):
    """Build a User, optionally inserting the row the permission layer expects."""
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=is_active,
    )
    if persist:
        with s.SessionLocal() as db:
            db.add(user)
            db.commit()
            # refresh before expunge: the session expires attributes on commit,
            # and a detached instance cannot reload them.
            db.refresh(user)
            db.expunge(user)
    return user


def _seed_root():
    """Insert the workspace root that Alembic normally provides."""
    with s.SessionLocal() as db:
        db.merge(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        db.commit()


def _seed_block(owner_id=None, mode="private", grants=(), block_type="page",
                state="active"):
    """
    Create a block under the workspace root with an explicit permission row.

    Returns the new block id as a string, which is the form every endpoint URL
    below needs.
    """
    block_id = uuid.uuid4()
    with s.SessionLocal() as db:
        block = Block(
            id=block_id,
            parent_id=WORKSPACE_ROOT_ID,
            type=block_type,
            position=1.0,
            state=state,
        )
        block.owner_id = owner_id
        db.add(block)
        db.flush()
        perm_repo.set_permission(db, block_id, mode, list(grants))
        db.commit()
    return str(block_id)


@pytest.fixture
def workspace_root(isolated_db):
    """Seed the workspace root without installing any auth override."""
    _seed_root()


@pytest.fixture
def client_factory(isolated_db):
    """
    Return a builder for TestClients authenticated as a specific user.

    Used by the authorization tests, which need more than one identity in a
    single test — an owner and an unrelated member, for instance.
    """
    def _make(user):
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        return client

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def http_client(isolated_db):
    """
    TestClient with workspace root pre-seeded and session cookie set on the
    client instance (avoids the per-request cookies DeprecationWarning).

    ``isolated_db`` is explicitly requested to guarantee fixture ordering:
    the in-memory DB must be ready before we seed the workspace root.

    ``get_current_user`` is overridden via FastAPI dependency overrides so
    that endpoints receive a fake admin user without a live session. Admins
    bypass the permission layer, so these tests exercise endpoint behaviour
    rather than authorization; authorization has its own section below.
    """
    fake_user = User(
        id=uuid.uuid4(),
        username="testuser",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user

    _seed_root()

    client = TestClient(app)
    client.cookies.set("session", "test-token")
    yield client

    app.dependency_overrides.clear()


# ─── Auth guard ───────────────────────────────────────────────────────────────


def test_create_block_requires_auth(workspace_root):
    """No session cookie, no override: the shared dependency must reject it."""
    response = anon_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert response.status_code == 401


def test_get_block_requires_auth(workspace_root):
    response = anon_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 401


def test_session_for_deleted_user_is_rejected(workspace_root):
    """
    A token that still validates but resolves to no user row must be refused.

    This is the fail-open that used to live in ``_resolve_user_from_session``:
    an unresolvable user returned None and every permission check was skipped.
    ``get_current_user`` raises 401 instead.
    """
    app.dependency_overrides[require_session] = lambda: uuid.uuid4()
    try:
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        response = client.patch(
            f"/api/blocks/{WORKSPACE_ROOT_ID}", json={"content": {"title": "x"}}
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_session_for_deactivated_user_is_rejected(workspace_root):
    """A live session belonging to a deactivated account must not authenticate."""
    inactive = _make_user(role="member", is_active=False)
    app.dependency_overrides[require_session] = lambda: inactive.id
    try:
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        response = client.patch(
            f"/api/blocks/{WORKSPACE_ROOT_ID}", json={"content": {"title": "x"}}
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


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


# ─── Cover upload: type and size ──────────────────────────────────────────────
#
# The cover endpoint used to be a second upload implementation with no type
# allowlist and a single read() of the whole body. It now goes through
# app.media.upload, the same path the media router uses.


def _put_cover(http_client, block_id, filename, content=b"img", content_type="image/png"):
    return http_client.post(
        f"/api/blocks/{block_id}/cover",
        files={"file": (filename, content, content_type)},
    )


@pytest.fixture
def cover_page(http_client, tmp_path, monkeypatch):
    """A page block with cover storage redirected into a temp directory."""
    import app.media.router as media_module
    monkeypatch.setattr(media_module, "STATIC_ROOT", tmp_path)

    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    return resp.json()["id"], tmp_path / "covers"


@pytest.mark.parametrize(
    "filename,content_type",
    [
        ("payload.svg", "image/svg+xml"),
        ("payload.html", "text/html"),
        ("payload.exe", "application/octet-stream"),
        ("clip.mp4", "video/mp4"),
        ("doc.pdf", "application/pdf"),
    ],
)
def test_cover_refuses_a_type_outside_the_image_list(
    http_client, cover_page, filename, content_type
):
    block_id, _ = cover_page
    resp = _put_cover(http_client, block_id, filename, content_type=content_type)
    assert resp.status_code == 415


def test_cover_refuses_a_file_with_no_usable_extension(http_client, cover_page):
    block_id, _ = cover_page
    resp = _put_cover(
        http_client, block_id, "noextension", content_type="application/x-unknown"
    )
    assert resp.status_code == 415


def test_cover_accepts_a_name_without_an_extension_by_content_type(
    http_client, cover_page
):
    """A browser that sends no filename suffix still uploads a valid PNG."""
    block_id, covers_dir = cover_page
    resp = _put_cover(http_client, block_id, "clipboard", content_type="image/png")
    assert resp.status_code == 200
    assert (covers_dir / f"{block_id}.png").exists()


def test_refused_cover_writes_nothing_to_disk(http_client, cover_page):
    block_id, covers_dir = cover_page
    _put_cover(http_client, block_id, "payload.svg", content_type="image/svg+xml")
    existing = list(covers_dir.glob("*")) if covers_dir.exists() else []
    assert existing == []


def test_refused_cover_leaves_the_previous_one_in_place(http_client, cover_page):
    """
    The type is settled before anything on disk is touched, so a rejected
    upload must not cost the caller the cover they already had.
    """
    block_id, covers_dir = cover_page
    assert _put_cover(http_client, block_id, "good.png", content=b"original").status_code == 200

    _put_cover(http_client, block_id, "payload.svg", content_type="image/svg+xml")

    assert (covers_dir / f"{block_id}.png").read_bytes() == b"original"


def test_cover_over_the_size_ceiling_returns_413(http_client, cover_page, monkeypatch):
    from app.media import upload as upload_module
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 16)

    block_id, _ = cover_page
    resp = _put_cover(http_client, block_id, "big.png", content=b"x" * 64)
    assert resp.status_code == 413


def test_oversized_cover_leaves_no_partial_file(http_client, cover_page, monkeypatch):
    from app.media import upload as upload_module

    block_id, covers_dir = cover_page
    assert _put_cover(http_client, block_id, "small.png", content=b"ok").status_code == 200

    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 16)
    _put_cover(http_client, block_id, "big.png", content=b"x" * 64)

    # The previous cover survives and no staging file is left behind.
    assert (covers_dir / f"{block_id}.png").read_bytes() == b"ok"
    assert list(covers_dir.glob("*.part")) == []
    assert len(list(covers_dir.glob("*"))) == 1


def test_cover_replacement_removes_the_other_extension(http_client, cover_page):
    block_id, covers_dir = cover_page
    _put_cover(http_client, block_id, "first.png", content=b"first")
    _put_cover(http_client, block_id, "second.jpg", content=b"second", content_type="image/jpeg")

    remaining = list(covers_dir.glob(f"{block_id}.*"))
    assert len(remaining) == 1
    assert remaining[0].suffix == ".jpg"
    assert remaining[0].read_bytes() == b"second"


def test_cover_replacement_with_the_same_extension_overwrites(http_client, cover_page):
    block_id, covers_dir = cover_page
    _put_cover(http_client, block_id, "a.png", content=b"first")
    _put_cover(http_client, block_id, "b.png", content=b"second")

    assert list(covers_dir.glob(f"{block_id}.*")) == [covers_dir / f"{block_id}.png"]
    assert (covers_dir / f"{block_id}.png").read_bytes() == b"second"


def test_cover_upload_leaves_no_staging_file(http_client, cover_page):
    block_id, covers_dir = cover_page
    _put_cover(http_client, block_id, "cover.png")
    assert list(covers_dir.glob("*.part")) == []


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


# ─── owner_id in responses ────────────────────────────────────────────────────


def test_create_block_response_has_owner_id_field(http_client):
    """BlockResponse must include owner_id regardless of its value."""
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert resp.status_code == 201
    assert "owner_id" in resp.json()


def test_get_block_response_has_owner_id_field(http_client):
    resp = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert resp.status_code == 200
    assert "owner_id" in resp.json()


# ─── Object-level authorization ───────────────────────────────────────────────
#
# Every endpoint that addresses a block by id now asks the permission layer as
# well as the auth layer. The deny cases are parametrised because the guard is
# uniform: it fires before any service or filesystem work, so the shape of the
# request past the block id does not matter. The allow cases are written out
# individually, because past the guard each endpoint has its own preconditions.


def _update(client, bid):
    return client.patch(f"/api/blocks/{bid}", json={"content": {"title": "x"}})


def _appearance(client, bid):
    return client.patch(f"/api/blocks/{bid}/appearance", json={"icon": "star"})


def _upload_cover(client, bid):
    return client.post(
        f"/api/blocks/{bid}/cover",
        files={"file": ("cover.png", b"img", "image/png")},
    )


def _remove_cover(client, bid):
    return client.delete(f"/api/blocks/{bid}/cover")


def _move(client, bid):
    return client.post(
        f"/api/blocks/{bid}/move",
        json={"new_parent_id": str(WORKSPACE_ROOT_ID), "new_position": 5.0},
    )


def _duplicate(client, bid):
    return client.post(f"/api/blocks/{bid}/duplicate")


def _soft_delete(client, bid):
    return client.delete(f"/api/blocks/{bid}")


def _restore(client, bid):
    return client.post(f"/api/blocks/{bid}/restore")


def _purge(client, bid):
    return client.delete(f"/api/blocks/{bid}/purge")


def _rebalance(client, bid):
    return client.post(f"/api/blocks/{bid}/rebalance-children")


def _upsert_preference(client, bid):
    return client.put(f"/api/blocks/{bid}/preferences/view", json={"value": 1})


def _revert(client, bid):
    return client.post(f"/api/blocks/{bid}/revert/{uuid.uuid4()}")


WRITE_ENDPOINTS = [
    ("update_block", _update),
    ("update_appearance", _appearance),
    ("upload_cover", _upload_cover),
    ("remove_cover", _remove_cover),
    ("move_block", _move),
    ("duplicate_block", _duplicate),
    ("soft_delete_block", _soft_delete),
    ("restore_block", _restore),
    ("purge_block", _purge),
    ("rebalance_children", _rebalance),
    ("upsert_preference", _upsert_preference),
    ("revert_event", _revert),
]

_IDS = [name for name, _ in WRITE_ENDPOINTS]


@pytest.mark.parametrize("name,call", WRITE_ENDPOINTS, ids=_IDS)
def test_write_on_foreign_private_block_returns_403(
    workspace_root, client_factory, name, call
):
    """A member must not reach a private block belonging to someone else."""
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert call(client, block_id).status_code == 403


@pytest.mark.parametrize("name,call", WRITE_ENDPOINTS, ids=_IDS)
def test_write_on_whitelist_block_without_grant_returns_403(
    workspace_root, client_factory, name, call
):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="whitelist", grants=[])
    client = client_factory(member)
    assert call(client, block_id).status_code == 403


@pytest.mark.parametrize("name,call", WRITE_ENDPOINTS, ids=_IDS)
def test_write_denied_before_any_side_effect(
    workspace_root, client_factory, name, call
):
    """The block must be untouched after a refused write."""
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    call(client, block_id)
    with s.SessionLocal() as db:
        block = db.get(Block, uuid.UUID(block_id))
    assert block is not None
    assert block.state == "active"


# ── Allow cases ───────────────────────────────────────────────────────────────


def test_owner_may_update_own_private_block(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert _update(client, block_id).status_code == 200


def test_owner_may_change_appearance(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert _appearance(client, block_id).status_code == 200


def test_owner_may_soft_delete_own_block(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert _soft_delete(client, block_id).status_code == 200


def test_owner_may_write_preference(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert _upsert_preference(client, block_id).status_code == 200


def test_owner_may_duplicate_own_block(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert _duplicate(client, block_id).status_code == 201


def test_whitelisted_member_may_update(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="whitelist", grants=[member.id])
    client = client_factory(member)
    assert _update(client, block_id).status_code == 200


def test_everyone_mode_allows_any_member_to_update(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="everyone")
    client = client_factory(member)
    assert _update(client, block_id).status_code == 200


def test_admin_bypasses_block_permissions(workspace_root, client_factory):
    admin = _make_user(role="admin")
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(admin)
    assert _update(client, block_id).status_code == 200


def test_unknown_block_still_returns_404_not_403(workspace_root, client_factory):
    """The guard must not turn a genuine 404 into a permission error."""
    member = _make_user()
    client = client_factory(member)
    assert _update(client, str(uuid.uuid4())).status_code == 404


# ── Reads answer 404, so they do not confirm the id exists ────────────────────


def test_get_foreign_private_block_returns_404(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}").status_code == 404


def test_owner_may_read_own_private_block(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}").status_code == 200


def test_history_of_foreign_private_block_returns_404(workspace_root, client_factory):
    """History carries full before/after content and is gated like a read."""
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/history").status_code == 404


def test_preferences_of_foreign_private_block_returns_404(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/preferences").status_code == 404


def test_single_preference_of_foreign_private_block_returns_404(
    workspace_root, client_factory
):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    resp = client.get(f"/api/blocks/{block_id}/preferences/view")
    assert resp.status_code == 404


def test_children_of_foreign_private_block_returns_404(workspace_root, client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/children").status_code == 404


def test_trash_excludes_blocks_owned_by_others(workspace_root, client_factory):
    member = _make_user()
    _seed_block(owner_id=uuid.uuid4(), mode="private", state="trash")
    mine = _seed_block(owner_id=member.id, mode="private", state="trash")
    client = client_factory(member)
    ids = [b["id"] for b in client.get("/api/blocks/trash").json()]
    assert mine in ids
    assert len(ids) == 1


# ── Creation is checked against the parent ────────────────────────────────────


def test_create_under_foreign_private_parent_returns_403(workspace_root, client_factory):
    member = _make_user()
    parent_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    resp = client.post("/api/blocks", json={"type": "page", "parent_id": parent_id})
    assert resp.status_code == 403


def test_create_under_own_parent_is_allowed(workspace_root, client_factory):
    member = _make_user()
    parent_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    resp = client.post("/api/blocks", json={"type": "page", "parent_id": parent_id})
    assert resp.status_code == 201


def test_create_stamps_the_authenticated_user_as_owner(workspace_root, client_factory):
    member = _make_user()
    parent_id = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    resp = client.post("/api/blocks", json={"type": "page", "parent_id": parent_id})
    assert resp.json()["owner_id"] == str(member.id)


# ── Move is checked at both ends ──────────────────────────────────────────────


def test_move_into_inaccessible_parent_returns_403(workspace_root, client_factory):
    """Owning the block being moved is not enough; the destination counts too."""
    member = _make_user()
    own = _seed_block(owner_id=member.id, mode="private")
    foreign_parent = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    resp = client.post(
        f"/api/blocks/{own}/move",
        json={"new_parent_id": foreign_parent, "new_position": 1.0},
    )
    assert resp.status_code == 403


def test_move_within_own_blocks_is_allowed(workspace_root, client_factory):
    member = _make_user()
    own = _seed_block(owner_id=member.id, mode="private")
    own_parent = _seed_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    resp = client.post(
        f"/api/blocks/{own}/move",
        json={"new_parent_id": own_parent, "new_position": 1.0},
    )
    assert resp.status_code == 200
