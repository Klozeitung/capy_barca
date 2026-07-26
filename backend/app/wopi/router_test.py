"""
Tests for the WOPI router.

All filesystem writes are redirected to a per-test temp directory via the
``tmp_drive_dir`` fixture.

Authentication goes through the shared dependency in ``app.session.deps``
rather than a router-local token check, so the tests override
``get_current_user`` instead of patching a module-level ``validate_token``.
The identity the router sees is therefore a real ``User`` with a role and an
id, which is what makes the block permission checks testable at all.

The file endpoints carry no session — Collabora has none — so they are driven
with a plain client and a token, exactly as Collabora would.
"""
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.wopi.router as wopi_module
from app.main import app
from app.session.deps import get_current_user
from app.users.model import User


# ─── Identities and clients ───────────────────────────────────────────────────


def _make_user(username: str, role: str) -> User:
    """
    Create a real user row and return a detached copy of it.

    A row is needed because the file endpoints resolve the account from the
    token's claims, and the detached copy is what the dependency override
    hands back. Built through the repository rather than by constructing the
    model directly, so the test does not have to know which columns carry
    defaults.
    """
    import app.database.database as db_module
    from app.users.repository import create_user

    with db_module.SessionLocal() as db:
        row = create_user(db, username, "test-password", role=role)
        db.commit()
        db.refresh(row)
        return User(
            id=row.id,
            username=row.username,
            password_hash=row.password_hash,
            role=row.role,
            is_active=row.is_active,
        )


@pytest.fixture
def member_user():
    return _make_user("member", "member")


@pytest.fixture
def admin_user():
    return _make_user("admin", "admin")


@pytest.fixture
def client_factory():
    """Return a builder for a TestClient authenticated as a given user."""
    def _make(user: User) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def http_client(client_factory, member_user):
    return client_factory(member_user)


@pytest.fixture
def wopi_client():
    """
    Client without an identity: this is how Collabora talks to the file
    endpoints, carrying nothing but the token.
    """
    app.dependency_overrides.pop(get_current_user, None)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """The token endpoint is throttled and every test shares a client address."""
    from app.security.limiter import limiter

    limiter._storage.reset()
    yield


@pytest.fixture
def test_secret(monkeypatch):
    monkeypatch.setattr(wopi_module, "SECRET_KEY", "test-secret-key-for-wopi")
    return "test-secret-key-for-wopi"


@pytest.fixture
def tmp_drive_dir(tmp_path, monkeypatch):
    """Redirect STATIC_ROOT to a temp directory."""
    import app.media.router as media_module

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", upload_dir)
    yield upload_dir


FILE_UUID = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
FILENAME = "test-document.docx"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE_CONTENT = b"PK\x03\x04fake docx content"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _seed_block(owner_id=None, mode=None, grants=()) -> uuid.UUID:
    """
    Insert a block into the isolated database and give it a permission row.

    ``mode=None`` leaves the block without an explicit row, which the
    permission layer resolves to 'everyone'.
    """
    import app.database.database as db_module
    from app.blocks.models import Block
    from app.permissions import repository as perm_repo

    block_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with db_module.SessionLocal() as db:
        db.add(
            Block(
                id=block_id,
                parent_id=None,
                reference_id=None,
                type="page",
                position=0.0,
                state="active",
                content={"title": "Drive"},
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        if mode is not None:
            perm_repo.set_permission(db, block_id, mode, list(grants))
        db.commit()
    return block_id


def _seed_file(root: Path, block_id) -> Path:
    """Create a fake drive file for the given block."""
    drive_dir = root / "drives" / str(block_id)
    drive_dir.mkdir(parents=True, exist_ok=True)
    path = drive_dir / f"{FILE_UUID}.docx"
    path.write_bytes(FILE_CONTENT)
    return path


def _token(secret: str, block, user, ttl: int = 3600, **overrides) -> str:
    """
    Mint a token the way the endpoint would, with room to bend a claim.

    The positional parameters are named ``block`` and ``user`` rather than
    after the claims they fill, so that ``overrides`` can address every claim
    by its real name without colliding with the signature.
    """
    now = int(time.time())
    claims = {
        "file_uuid": FILE_UUID,
        "block_id": str(block),
        "filename": FILENAME,
        "mime": MIME,
        "user_id": str(user),
        "username": "member",
        "iat": now,
        "exp": now + ttl,
    }
    claims.update(overrides)

    old_key = wopi_module.SECRET_KEY
    wopi_module.SECRET_KEY = secret
    token = wopi_module._encode_token(claims)
    wopi_module.SECRET_KEY = old_key
    return token


def _issue(client, block_id, file_uuid: str = FILE_UUID):
    return client.post(
        "/api/wopi/token",
        json={
            "file_uuid": file_uuid,
            "block_id": str(block_id),
            "filename": FILENAME,
            "mime": MIME,
        },
    )


# ─── Token endpoint: session ──────────────────────────────────────────────────


def test_token_requires_session(wopi_client):
    resp = _issue(wopi_client, uuid.uuid4())
    assert resp.status_code == 401


def test_token_returns_editor_url(http_client, test_secret):
    block_id = _seed_block()
    resp = _issue(http_client, block_id)
    assert resp.status_code == 200
    assert "/collabora/browser/dist/cool.html" in resp.json()["editor_url"]


def test_token_editor_url_contains_wopi_src(http_client, test_secret):
    resp = _issue(http_client, _seed_block())
    assert "WOPISrc=" in resp.json()["editor_url"]


def test_token_editor_url_contains_access_token(http_client, test_secret):
    resp = _issue(http_client, _seed_block())
    assert "access_token=" in resp.json()["editor_url"]


# ─── Token endpoint: block permission ─────────────────────────────────────────


def test_token_for_a_foreign_private_block_returns_403(
    client_factory, member_user, test_secret
):
    """
    The finding in one line: a session used to be enough to mint an editing
    token for any drive file whose ids you could name.
    """
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    resp = _issue(client_factory(member_user), block_id)
    assert resp.status_code == 403


def test_token_for_an_own_private_block_is_issued(
    client_factory, member_user, test_secret
):
    block_id = _seed_block(owner_id=member_user.id, mode="private")
    assert _issue(client_factory(member_user), block_id).status_code == 200


def test_token_for_a_whitelisted_block_is_issued(
    client_factory, member_user, test_secret
):
    block_id = _seed_block(
        owner_id=uuid.uuid4(), mode="whitelist", grants=[member_user.id]
    )
    assert _issue(client_factory(member_user), block_id).status_code == 200


def test_token_admin_bypasses_block_permissions(
    client_factory, admin_user, test_secret
):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    assert _issue(client_factory(admin_user), block_id).status_code == 200


@pytest.mark.parametrize("field", ["file_uuid", "block_id"])
@pytest.mark.parametrize("value", ["../../../etc", "..", "not-a-uuid", "a/../b"])
def test_token_rejects_non_uuid_identifiers(http_client, test_secret, field, value):
    """
    Both identifiers end up in a filesystem path once the signed token comes
    back, and the signature would carry a relative segment just as faithfully
    as a real id.
    """
    body = {
        "file_uuid": FILE_UUID,
        "block_id": str(uuid.uuid4()),
        "filename": FILENAME,
        "mime": MIME,
    }
    body[field] = value
    resp = http_client.post("/api/wopi/token", json=body)
    assert resp.status_code == 422


def test_token_rate_limit_blocks_after_threshold(http_client, test_secret):
    block_id = _seed_block()
    threshold = int(wopi_module._WOPI_TOKEN_RATE_LIMIT.split("/")[0])
    for _ in range(threshold):
        _issue(http_client, block_id)
    assert _issue(http_client, block_id).status_code == 429


# ─── Token encode / decode ────────────────────────────────────────────────────


def test_encode_decode_roundtrip(test_secret):
    block_id = uuid.uuid4()
    user_id = uuid.uuid4()
    decoded = wopi_module._decode_token(_token(test_secret, block_id, user_id))
    assert decoded["file_uuid"] == FILE_UUID
    assert decoded["block_id"] == str(block_id)
    assert decoded["user_id"] == str(user_id)


def test_decode_rejects_tampered_signature(test_secret):
    token = _token(test_secret, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(Exception):
        wopi_module._decode_token(token[:-4] + "xxxx")


def test_decode_rejects_expired_token(test_secret):
    token = _token(test_secret, uuid.uuid4(), uuid.uuid4(), ttl=-3600)
    with pytest.raises(Exception):
        wopi_module._decode_token(token)


def test_decode_rejects_malformed_token(test_secret):
    with pytest.raises(Exception):
        wopi_module._decode_token("notavalidtoken")


def test_decode_rejects_a_token_signed_with_another_secret(test_secret):
    """A token minted under a different key must not verify under this one."""
    foreign = _token("some-other-secret", uuid.uuid4(), uuid.uuid4())
    with pytest.raises(Exception):
        wopi_module._decode_token(foreign)


@pytest.mark.parametrize("field", ["file_uuid", "block_id", "user_id"])
def test_decode_rejects_a_non_uuid_claim(test_secret, field):
    """
    A valid signature says the claims were not altered in transit. It says
    nothing about them being sane, and these three go into a path or a lookup.
    """
    token = _token(test_secret, uuid.uuid4(), uuid.uuid4(), **{field: "../../etc"})
    with pytest.raises(Exception):
        wopi_module._decode_token(token)


def test_decode_rejects_a_missing_user_claim(test_secret):
    token = _token(test_secret, uuid.uuid4(), uuid.uuid4(), user_id=None)
    with pytest.raises(Exception):
        wopi_module._decode_token(token)


# ─── CheckFileInfo ────────────────────────────────────────────────────────────


@pytest.fixture
def prepared(tmp_drive_dir, test_secret, member_user):
    """An accessible block, a file in it, a persisted user, and a valid token."""
    block_id = _seed_block(owner_id=member_user.id, mode="private")
    path = _seed_file(tmp_drive_dir, block_id)
    return {
        "block_id": block_id,
        "path": path,
        "token": _token(test_secret, block_id, member_user.id),
    }


def test_check_file_info_returns_200(wopi_client, prepared):
    assert wopi_client.get(f"/api/wopi/files/{prepared['token']}").status_code == 200


def test_check_file_info_base_filename(wopi_client, prepared):
    data = wopi_client.get(f"/api/wopi/files/{prepared['token']}").json()
    assert data["BaseFileName"] == FILENAME


def test_check_file_info_size(wopi_client, prepared):
    data = wopi_client.get(f"/api/wopi/files/{prepared['token']}").json()
    assert data["Size"] == len(FILE_CONTENT)


def test_check_file_info_user_can_write(wopi_client, prepared):
    data = wopi_client.get(f"/api/wopi/files/{prepared['token']}").json()
    assert data["UserCanWrite"] is True


def test_check_file_info_invalid_token_returns_401(wopi_client, tmp_drive_dir):
    resp = wopi_client.get("/api/wopi/files/invalid.token.here")
    assert resp.status_code == 401


def test_check_file_info_missing_file_returns_404(
    wopi_client, tmp_drive_dir, test_secret, member_user
):
    block_id = _seed_block(owner_id=member_user.id, mode="private")
    token = _token(test_secret, block_id, member_user.id)
    assert wopi_client.get(f"/api/wopi/files/{token}").status_code == 404


# ─── Live authorization on the file endpoints ─────────────────────────────────


def test_file_endpoint_refuses_a_token_whose_user_lost_access(
    wopi_client, tmp_drive_dir, test_secret, member_user
):
    """
    A token used to stay usable for its whole lifetime no matter what happened
    to the permissions in the meantime. The block is handed to someone else
    after the token was minted.
    """
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, member_user.id)
    assert wopi_client.get(f"/api/wopi/files/{token}").status_code == 403


def test_file_endpoint_refuses_a_token_of_a_deactivated_account(
    wopi_client, tmp_drive_dir, test_secret, member_user
):
    import app.database.database as db_module

    block_id = _seed_block(owner_id=member_user.id, mode="private")
    _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, member_user.id)

    with db_module.SessionLocal() as db:
        stored = db.get(User, member_user.id)
        stored.is_active = False
        db.commit()

    assert wopi_client.get(f"/api/wopi/files/{token}").status_code == 401


def test_file_endpoint_refuses_a_token_of_an_unknown_account(
    wopi_client, tmp_drive_dir, test_secret
):
    block_id = _seed_block()
    _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, uuid.uuid4())
    assert wopi_client.get(f"/api/wopi/files/{token}").status_code == 401


def test_file_endpoint_allows_an_admin_token_on_a_foreign_block(
    wopi_client, tmp_drive_dir, test_secret, admin_user
):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, admin_user.id)
    assert wopi_client.get(f"/api/wopi/files/{token}").status_code == 200


# ─── GetFile ──────────────────────────────────────────────────────────────────


def test_get_file_returns_200(wopi_client, prepared):
    resp = wopi_client.get(f"/api/wopi/files/{prepared['token']}/contents")
    assert resp.status_code == 200


def test_get_file_returns_correct_bytes(wopi_client, prepared):
    resp = wopi_client.get(f"/api/wopi/files/{prepared['token']}/contents")
    assert resp.content == FILE_CONTENT


def test_get_file_invalid_token_returns_401(wopi_client, tmp_drive_dir):
    assert wopi_client.get("/api/wopi/files/bad.token/contents").status_code == 401


def test_get_file_refuses_a_token_whose_user_lost_access(
    wopi_client, tmp_drive_dir, test_secret, member_user
):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, member_user.id)
    assert wopi_client.get(f"/api/wopi/files/{token}/contents").status_code == 403


# ─── PutFile ──────────────────────────────────────────────────────────────────


def test_put_file_returns_200(wopi_client, prepared):
    resp = wopi_client.post(
        f"/api/wopi/files/{prepared['token']}/contents", content=b"updated content"
    )
    assert resp.status_code == 200


def test_put_file_persists_bytes(wopi_client, prepared):
    new_content = b"new document content after save"
    wopi_client.post(
        f"/api/wopi/files/{prepared['token']}/contents", content=new_content
    )
    assert prepared["path"].read_bytes() == new_content


def test_put_file_invalid_token_returns_401(wopi_client, tmp_drive_dir):
    resp = wopi_client.post("/api/wopi/files/bad.token/contents", content=b"data")
    assert resp.status_code == 401


def test_put_file_status_ok_in_response(wopi_client, prepared):
    data = wopi_client.post(
        f"/api/wopi/files/{prepared['token']}/contents", content=b"content"
    ).json()
    assert data["status"] == "ok"


def test_put_file_refuses_a_token_whose_user_lost_access(
    wopi_client, tmp_drive_dir, test_secret, member_user
):
    """Write is the one that matters most here: the document is overwritten."""
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    path = _seed_file(tmp_drive_dir, block_id)
    token = _token(test_secret, block_id, member_user.id)

    resp = wopi_client.post(f"/api/wopi/files/{token}/contents", content=b"overwritten")
    assert resp.status_code == 403
    assert path.read_bytes() == FILE_CONTENT


def test_put_file_refuses_a_body_over_the_size_ceiling(
    wopi_client, prepared, monkeypatch
):
    import app.media.router as media_module

    monkeypatch.setattr(media_module, "_MAX_UPLOAD_BYTES", 1024)
    resp = wopi_client.post(
        f"/api/wopi/files/{prepared['token']}/contents", content=b"x" * 4096
    )
    assert resp.status_code == 413


def test_put_file_over_the_ceiling_leaves_the_document_intact(
    wopi_client, prepared, monkeypatch
):
    import app.media.router as media_module

    monkeypatch.setattr(media_module, "_MAX_UPLOAD_BYTES", 1024)
    wopi_client.post(
        f"/api/wopi/files/{prepared['token']}/contents", content=b"x" * 4096
    )
    assert prepared["path"].read_bytes() == FILE_CONTENT


def test_put_file_leaves_no_temporary_file_behind(wopi_client, prepared):
    wopi_client.post(f"/api/wopi/files/{prepared['token']}/contents", content=b"saved")
    leftovers = [p.name for p in prepared["path"].parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_find_file_ignores_a_stale_temporary_file(prepared):
    """
    The temporary name is hidden so the glob cannot pick it up. Previously it
    was a sibling ``.tmp`` that matched, so a crashed save could shadow the
    document for every later request.
    """
    stale = prepared["path"].parent / f".{prepared['path'].name}.tmp"
    stale.write_bytes(b"garbage from a crashed save")
    found = wopi_module._find_file(FILE_UUID, str(prepared["block_id"]))
    assert found == prepared["path"]
