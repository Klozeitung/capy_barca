"""
Tests for the media upload router.

All filesystem writes are redirected to a per-test temp directory via the
``tmp_upload_dir`` autouse fixture, keeping the real ``static/`` tree clean.

Authentication goes through the shared dependency in ``app.session.deps``
rather than a router-local token check, so the tests override
``get_current_user`` instead of patching a module-level ``validate_token``.
That override is also what makes object-level authorization testable: the
identity the router sees is a real ``User`` with a role and an id, and the
permission layer resolves against blocks seeded into the in-memory database.
"""
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.session.deps import get_current_user
from app.users.model import User


# ─── Identities and clients ───────────────────────────────────────────────────


@pytest.fixture
def member_user():
    return User(
        id=uuid.uuid4(),
        username="member",
        password_hash="x",
        role="member",
        is_active=True,
    )


@pytest.fixture
def admin_user():
    return User(
        id=uuid.uuid4(),
        username="admin",
        password_hash="x",
        role="admin",
        is_active=True,
    )


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
    """
    Default client: an ordinary member, not an admin.

    Blocks that no test seeds have no permission row anywhere in their parent
    chain and resolve to 'everyone', so the functional tests below still pass
    while running through the real authorization path rather than around it.
    """
    return client_factory(member_user)


@pytest.fixture
def anon_client():
    """Client without an identity override: exercises the real session gate."""
    app.dependency_overrides.pop(get_current_user, None)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """
    Clear the shared rate-limit counter before each test.

    The bookmark endpoint is throttled, and every test here talks to the same
    client address, so without this the later bookmark tests would fail on the
    budget spent by the earlier ones.
    """
    from app.security.limiter import limiter

    limiter._storage.reset()
    yield


@pytest.fixture(autouse=True)
def tmp_upload_dir(tmp_path, monkeypatch):
    """Redirect STATIC_ROOT to a throwaway temp dir for every test."""
    import app.media.router as media_module

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(media_module, "STATIC_ROOT", upload_dir)
    yield upload_dir


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _seed_block(owner_id=None, mode=None, grants=()) -> uuid.UUID:
    """
    Insert a block into the isolated database and give it a permission row.

    ``mode=None`` leaves the block without an explicit permission row, which
    is what the permission layer resolves to 'everyone'.
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
                content={"title": "Test block"},
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


class _FakeResponse:
    """
    Minimal stand-in for the streaming response the bookmark endpoint reads.

    Written out rather than assembled from a mock because the endpoint now
    branches on redirect status and consumes the body in chunks, and a mock
    that answers every attribute would hide a mistake in either path.
    """

    def __init__(self, body: bytes = b"", status_code: int = 200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.encoding = "utf-8"

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and "location" in {
            k.lower() for k in self.headers
        }

    async def aiter_bytes(self):
        # Deliberately chunked so the size cap is exercised rather than assumed.
        for i in range(0, len(self._body), 1024):
            yield self._body[i:i + 1024]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _FakeAsyncClient:
    """httpx.AsyncClient replacement that replays a scripted list of responses."""

    def __init__(self, responses, requested):
        self._responses = list(responses)
        self._requested = requested

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, headers=None):
        self._requested.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self._responses.pop(0)


def _mock_httpx(html: str = "", responses=None, requested=None):
    """
    Patch httpx.AsyncClient and the DNS resolver for the bookmark endpoint.

    The resolver is stubbed alongside the client because the endpoint refuses
    any target it cannot resolve to a public address, and the test suite must
    not depend on name resolution being available.
    """
    scripted = responses if responses is not None else [_FakeResponse(html.encode())]
    sink = requested if requested is not None else []

    async def _public_resolver(host, port):
        return ["93.184.216.34"]

    return _patch_stack(
        patch("httpx.AsyncClient", lambda *a, **kw: _FakeAsyncClient(scripted, sink)),
        patch("app.media.router._resolve_host", _public_resolver),
    )


class _patch_stack:
    """Apply several patches as one context manager."""

    def __init__(self, *patchers):
        self._patchers = patchers

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()
        return False


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


@pytest.mark.parametrize("value", ["not-a-uuid", "12345", "drive", "null"])
def test_upload_non_uuid_block_id_returns_422(http_client, value):
    """
    The block id in the path is typed, so anything else is refused.

    Deliberately no literal "../" here: both httpx and nginx remove dot
    segments before the request is sent, so such a value never reaches the
    application and would only produce a 404 from an unmatched route. The
    defence that actually carries is the type on the parameter, and that is
    what this asserts. Body fields, which are not normalised by anyone, are
    covered separately under the drive-file move.
    """
    resp = _upload(http_client, "drive", value)
    assert resp.status_code == 422


def test_upload_requires_auth(anon_client):
    resp = _upload(anon_client, "image", str(uuid.uuid4()))
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


def test_delete_requires_auth(anon_client):
    resp = anon_client.delete(f"/api/media/image/{uuid.uuid4()}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ─── Object-level authorization ───────────────────────────────────────────────


def test_upload_to_foreign_private_block_returns_403(client_factory, member_user):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member_user)
    resp = _upload(client, "drive", str(block_id))
    assert resp.status_code == 403


def test_upload_to_own_private_block_is_allowed(client_factory, member_user):
    block_id = _seed_block(owner_id=member_user.id, mode="private")
    client = client_factory(member_user)
    resp = _upload(client, "drive", str(block_id))
    assert resp.status_code == 200


def test_upload_to_whitelisted_block_is_allowed(client_factory, member_user):
    block_id = _seed_block(
        owner_id=uuid.uuid4(), mode="whitelist", grants=[member_user.id]
    )
    client = client_factory(member_user)
    resp = _upload(client, "drive", str(block_id))
    assert resp.status_code == 200


def test_upload_to_whitelist_without_grant_returns_403(client_factory, member_user):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="whitelist", grants=[])
    client = client_factory(member_user)
    resp = _upload(client, "drive", str(block_id))
    assert resp.status_code == 403


def test_admin_bypasses_block_permissions(client_factory, admin_user):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(admin_user)
    resp = _upload(client, "drive", str(block_id))
    assert resp.status_code == 200


def test_delete_in_foreign_private_block_returns_403(
    client_factory, member_user, tmp_upload_dir
):
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    file_uuid = str(uuid.uuid4())
    path = _create_fake_file(tmp_upload_dir, "drive", str(block_id), file_uuid, ext=".txt")
    client = client_factory(member_user)
    resp = client.delete(f"/api/media/drive/{block_id}/{file_uuid}")
    assert resp.status_code == 403
    assert path.exists()


def test_delete_denied_before_existence_is_revealed(client_factory, member_user):
    """An unauthorized caller gets 403, not the 404 that would confirm absence."""
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member_user)
    resp = client.delete(f"/api/media/drive/{block_id}/{uuid.uuid4()}")
    assert resp.status_code == 403


def test_capacity_needs_no_block_permission(client_factory, member_user):
    """The capacity endpoint addresses no block and stays available."""
    client = client_factory(member_user)
    assert client.get("/api/media/capacity").status_code == 200


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


def test_capacity_requires_auth(anon_client):
    resp = anon_client.get("/api/media/capacity")
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
    """An allowed target that fails to answer still yields a usable bookmark."""
    class _Failing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def stream(self, *a, **kw):
            raise Exception("timeout")

    async def _public_resolver(host, port):
        return ["93.184.216.34"]

    with _patch_stack(
        patch("httpx.AsyncClient", lambda *a, **kw: _Failing()),
        patch("app.media.router._resolve_host", _public_resolver),
    ):
        resp = http_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com"
    assert resp.json()["title"] is None


def test_bookmark_requires_auth(anon_client):
    resp = anon_client.post("/api/media/bookmark", json={"url": "https://example.com"})
    assert resp.status_code == 401


# ─── Bookmark: outbound target validation ─────────────────────────────────────


def _bookmark(client, url: str):
    return client.post("/api/media/bookmark", json={"url": url})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "https://100.100.100.100/",
        "http://0.0.0.0/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
    ],
)
def test_bookmark_refuses_non_public_address(http_client, url):
    """
    Literal addresses outside the public internet are refused before a socket
    is opened. No client patch here on purpose: the real resolver is what has
    to reject these.
    """
    assert _bookmark(http_client, url).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "data:text/html,<h1>x</h1>",
        "//example.com/",
        "http:///no-host",
        "not a url at all",
    ],
)
def test_bookmark_refuses_unsupported_scheme(http_client, url):
    assert _bookmark(http_client, url).status_code == 400


def test_bookmark_refuses_container_service_by_name(http_client):
    """The neighbouring Compose services must not be reachable through this."""
    async def _internal_resolver(host, port):
        return ["172.18.0.2"]

    with patch("app.media.router._resolve_host", _internal_resolver):
        assert _bookmark(http_client, "http://collabora:9980/").status_code == 400


def test_bookmark_refuses_host_with_one_private_record(http_client):
    """A name answering with both a public and a private record is refused."""
    async def _mixed_resolver(host, port):
        return ["93.184.216.34", "127.0.0.1"]

    with patch("app.media.router._resolve_host", _mixed_resolver):
        assert _bookmark(http_client, "https://mixed.example/").status_code == 400


def test_bookmark_judges_a_literal_address_without_the_resolver(http_client):
    """
    A literal address is decided on its own merits.

    Pinned because the opposite is easy to reintroduce and hard to notice: as
    long as the verdict came from the resolver, anything able to answer that
    lookup could vouch for an address that is plainly internal.
    """
    async def _lying_resolver(host, port):
        return ["93.184.216.34"]

    with patch("app.media.router._resolve_host", _lying_resolver):
        assert _bookmark(http_client, "http://169.254.169.254/").status_code == 400
        assert _bookmark(http_client, "http://127.0.0.1/").status_code == 400


def test_bookmark_refuses_unresolvable_host(http_client):
    async def _failing_resolver(host, port):
        raise OSError("NXDOMAIN")

    with patch("app.media.router._resolve_host", _failing_resolver):
        assert _bookmark(http_client, "https://nope.example/").status_code == 400


def test_bookmark_refusal_message_is_uniform(http_client):
    """
    A refused public-but-unresolvable host and a refused private address give
    the same answer, so the endpoint cannot be used to probe internal names.
    """
    async def _failing_resolver(host, port):
        raise OSError("NXDOMAIN")

    private = _bookmark(http_client, "http://10.0.0.1/").json()
    with patch("app.media.router._resolve_host", _failing_resolver):
        unresolvable = _bookmark(http_client, "https://nope.example/").json()
    assert private == unresolvable


# ─── Bookmark: redirects ──────────────────────────────────────────────────────


def test_bookmark_follows_a_public_redirect(http_client):
    requested: list[str] = []
    responses = [
        _FakeResponse(status_code=302, headers={"location": "https://example.com/final"}),
        _FakeResponse(b"<title>Final</title>"),
    ]
    with _mock_httpx(responses=responses, requested=requested):
        resp = _bookmark(http_client, "https://example.com/start")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Final"
    assert requested == ["https://example.com/start", "https://example.com/final"]


def test_bookmark_refuses_a_redirect_to_an_internal_address(http_client):
    """
    The whole point of following redirects by hand: the first hop is public,
    the second is not, and automatic following would have taken it.
    """
    requested: list[str] = []
    responses = [
        _FakeResponse(status_code=302, headers={"location": "http://169.254.169.254/"}),
        _FakeResponse(b"<title>secrets</title>"),
    ]

    async def _public_resolver(host, port):
        return ["93.184.216.34"]

    with _patch_stack(
        patch(
            "httpx.AsyncClient",
            lambda *a, **kw: _FakeAsyncClient(responses, requested),
        ),
        patch("app.media.router._resolve_host", _public_resolver),
    ):
        resp = _bookmark(http_client, "https://example.com/start")

    assert resp.status_code == 400
    assert requested == ["https://example.com/start"]


def test_bookmark_stops_after_the_redirect_budget(http_client):
    requested: list[str] = []
    responses = [
        _FakeResponse(status_code=302, headers={"location": f"https://example.com/{i}"})
        for i in range(6)
    ]
    with _mock_httpx(responses=responses, requested=requested):
        resp = _bookmark(http_client, "https://example.com/start")
    assert resp.status_code == 400
    assert len(requested) <= 5


def test_bookmark_refuses_a_redirect_without_a_location(http_client):
    responses = [_FakeResponse(status_code=302, headers={})]
    with _mock_httpx(responses=responses):
        resp = _bookmark(http_client, "https://example.com/start")
    # No location header means the response is not a redirect and the empty
    # body is parsed as the page itself.
    assert resp.status_code == 200
    assert resp.json()["title"] is None


# ─── Bookmark: response size ──────────────────────────────────────────────────


def test_bookmark_reads_at_most_the_size_cap(http_client):
    """
    A page far beyond the cap must not be pulled into memory in full. The title
    sits at the very front, so it survives while the tail is discarded.
    """
    import app.media.router as media_module

    body = b"<title>Head</title>" + b"x" * (media_module._BOOKMARK_MAX_BYTES * 2)
    with _mock_httpx(responses=[_FakeResponse(body)]):
        resp = _bookmark(http_client, "https://example.com/huge")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Head"


# ─── Bookmark: preview assets handed to the browser ───────────────────────────


def test_bookmark_keeps_an_https_image(http_client):
    html = '<meta property="og:image" content="https://cdn.example/i.png" />'
    with _mock_httpx(html):
        resp = _bookmark(http_client, "https://example.com")
    assert resp.json()["image"] == "https://cdn.example/i.png"


def test_bookmark_drops_a_plain_http_image(http_client):
    html = '<meta property="og:image" content="http://cdn.example/i.png" />'
    with _mock_httpx(html):
        resp = _bookmark(http_client, "https://example.com")
    assert resp.json()["image"] is None


@pytest.mark.parametrize(
    "candidate",
    [
        "https://127.0.0.1/beacon.gif",
        "https://192.168.1.1/beacon.gif",
        "https://[::1]/beacon.gif",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
    ],
)
def test_bookmark_drops_an_unsafe_image(http_client, candidate):
    """
    These end up in an img tag in the user's browser, so a private address
    here is a request into the user's own network.
    """
    html = f'<meta property="og:image" content="{candidate}" />'
    with _mock_httpx(html):
        resp = _bookmark(http_client, "https://example.com")
    assert resp.json()["image"] is None


def test_bookmark_resolves_a_relative_image_against_the_page(http_client):
    html = '<meta property="og:image" content="/img/preview.png" />'
    with _mock_httpx(html):
        resp = _bookmark(http_client, "https://example.com/article")
    assert resp.json()["image"] == "https://example.com/img/preview.png"


def test_bookmark_favicon_omits_url_credentials(http_client):
    """Credentials in the URL must not be carried into a rendered attribute."""
    with _mock_httpx(""):
        resp = _bookmark(http_client, "https://user:secret@example.com/page")
    assert resp.json()["favicon"] == "https://example.com/favicon.ico"


# ─── Bookmark: rate limit ─────────────────────────────────────────────────────


def test_bookmark_rate_limit_blocks_after_threshold(http_client):
    """
    Without a limit the endpoint is a fast scanner for whatever the server can
    reach, so the budget is asserted here the same way the login route does it.
    """
    import app.media.router as media_module

    threshold = int(media_module._BOOKMARK_RATE_LIMIT.split("/")[0])
    for _ in range(threshold):
        _bookmark(http_client, "http://127.0.0.1/")
    assert _bookmark(http_client, "http://127.0.0.1/").status_code == 429


def test_bookmark_rate_limit_resets_after_storage_clear(http_client):
    from app.security.limiter import limiter
    import app.media.router as media_module

    threshold = int(media_module._BOOKMARK_RATE_LIMIT.split("/")[0])
    for _ in range(threshold):
        _bookmark(http_client, "http://127.0.0.1/")
    limiter._storage.reset()
    with _mock_httpx("<title>Back</title>"):
        assert _bookmark(http_client, "https://example.com").status_code == 200


# ─── Drive-file move ──────────────────────────────────────────────────────────


def _move(client, file_uuid, source_block_id, target_block_id):
    return client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": str(file_uuid),
            "source_block_id": str(source_block_id),
            "target_block_id": str(target_block_id),
        },
    )


def test_move_drive_file_returns_200(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".docx")
    resp = _move(http_client, file_uuid, src_block, tgt_block)
    assert resp.status_code == 200


def test_move_drive_file_response_contains_new_url(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".docx")
    resp = _move(http_client, file_uuid, src_block, tgt_block)
    new_url = resp.json()["url"]
    assert tgt_block in new_url
    assert src_block not in new_url


def test_move_drive_file_removes_from_source(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    src_path = _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".pdf")
    _move(http_client, file_uuid, src_block, tgt_block)
    assert not src_path.exists()


def test_move_drive_file_places_in_target(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".pdf")
    _move(http_client, file_uuid, src_block, tgt_block)
    matches = list((tmp_upload_dir / "drives" / tgt_block).glob(f"{file_uuid}*"))
    assert len(matches) == 1


def test_move_drive_file_creates_target_dir(http_client, tmp_upload_dir):
    src_block = str(uuid.uuid4())
    tgt_block = str(uuid.uuid4())
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", src_block, file_uuid, ext=".txt")
    assert not (tmp_upload_dir / "drives" / tgt_block).exists()
    _move(http_client, file_uuid, src_block, tgt_block)
    assert (tmp_upload_dir / "drives" / tgt_block).is_dir()


def test_move_drive_file_not_found_returns_404(http_client):
    resp = _move(http_client, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resp.status_code == 404


def test_move_drive_file_requires_auth(anon_client):
    resp = _move(anon_client, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resp.status_code == 401


# ─── Drive-file move: path traversal ──────────────────────────────────────────


@pytest.mark.parametrize(
    "field", ["file_uuid", "source_block_id", "target_block_id"]
)
@pytest.mark.parametrize(
    "value",
    [
        "../../../etc",
        "..",
        "a/../../b",
        "/etc/passwd",
        "%2e%2e%2f",
    ],
)
def test_move_drive_file_rejects_non_uuid_identifier(http_client, field, value):
    """
    Every identifier that ends up in a filesystem path must be a UUID.

    These fields were plain strings and were interpolated into the storage
    path directly, so a relative segment moved files out of the upload tree.
    """
    payload = {
        "file_uuid": str(uuid.uuid4()),
        "source_block_id": str(uuid.uuid4()),
        "target_block_id": str(uuid.uuid4()),
    }
    payload[field] = value
    resp = http_client.post("/api/media/drive-file/move", json=payload)
    assert resp.status_code == 422


def test_storage_dir_allows_a_normal_drive_path(tmp_upload_dir):
    import app.media.router as media_module

    path = media_module._storage_dir("drive", str(uuid.uuid4()))
    assert path.is_relative_to(tmp_upload_dir)


def test_storage_dir_refuses_a_path_outside_the_upload_root(tmp_upload_dir):
    """
    Direct coverage for the containment guard.

    No request can reach this today because every identifier feeding a storage
    path is typed as a UUID. The guard exists for the case where that stops
    being true, so it is exercised at the function level rather than over HTTP.
    """
    import app.media.router as media_module
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        media_module._storage_dir("drive", "../../escape")
    assert excinfo.value.status_code == 400


def test_move_drive_file_cannot_touch_files_outside_the_upload_root(
    http_client, tmp_path, tmp_upload_dir
):
    """A traversal attempt must leave a file next to the upload tree alone."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    file_uuid = str(uuid.uuid4())
    victim = outside_dir / f"{file_uuid}.txt"
    victim.write_text("do not move me")

    resp = http_client.post(
        "/api/media/drive-file/move",
        json={
            "file_uuid": file_uuid,
            "source_block_id": "../outside",
            "target_block_id": str(uuid.uuid4()),
        },
    )

    assert resp.status_code == 422
    assert victim.exists()
    assert victim.read_text() == "do not move me"


# ─── Drive-file move: object-level authorization ──────────────────────────────


def test_move_drive_file_from_foreign_source_returns_403(
    client_factory, member_user, tmp_upload_dir
):
    src_block = _seed_block(owner_id=uuid.uuid4(), mode="private")
    tgt_block = uuid.uuid4()
    file_uuid = str(uuid.uuid4())
    src_path = _create_fake_file(
        tmp_upload_dir, "drive", str(src_block), file_uuid, ext=".docx"
    )
    client = client_factory(member_user)
    resp = _move(client, file_uuid, src_block, tgt_block)
    assert resp.status_code == 403
    assert src_path.exists()


def test_move_drive_file_to_foreign_target_returns_403(
    client_factory, member_user, tmp_upload_dir
):
    """Access to the source does not imply the right to write into the target."""
    src_block = uuid.uuid4()
    tgt_block = _seed_block(owner_id=uuid.uuid4(), mode="private")
    file_uuid = str(uuid.uuid4())
    src_path = _create_fake_file(
        tmp_upload_dir, "drive", str(src_block), file_uuid, ext=".docx"
    )
    client = client_factory(member_user)
    resp = _move(client, file_uuid, src_block, tgt_block)
    assert resp.status_code == 403
    assert src_path.exists()


def test_move_drive_file_between_own_blocks_is_allowed(
    client_factory, member_user, tmp_upload_dir
):
    src_block = _seed_block(owner_id=member_user.id, mode="private")
    tgt_block = _seed_block(owner_id=member_user.id, mode="private")
    file_uuid = str(uuid.uuid4())
    _create_fake_file(tmp_upload_dir, "drive", str(src_block), file_uuid, ext=".docx")
    client = client_factory(member_user)
    resp = _move(client, file_uuid, src_block, tgt_block)
    assert resp.status_code == 200
