import uuid

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_app_is_initialized():
    assert app.title == "CapyBarca API"
    assert app.version == "0.15.12"


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_body():
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "capybarca"


def test_login_route_is_registered():
    # Missing required fields give 422, which proves the route exists.
    response = client.post("/api/login", json={})
    assert response.status_code == 422


def test_cors_header_absent_for_unknown_origin():
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_header_absent_for_configured_origins():
    """
    Whatever this process is configured with, an origin it does not list gets
    no header. The construction of the list itself is asserted below against
    explicit inputs rather than against the ambient environment.
    """
    from app.main import _cors_origins

    response = client.get("/health", headers={"Origin": "https://not-listed.example"})
    assert "access-control-allow-origin" not in response.headers
    assert "https://not-listed.example" not in _cors_origins


# ─── CORS allowlist construction ──────────────────────────────────────────────


def test_cors_origins_are_empty_for_a_plain_production_install():
    """
    Browser and backend share an origin behind nginx, so nothing has to be
    allowed. The previous list carried the Vite development origins into
    production alongside allow_credentials.
    """
    from app.main import build_cors_origins

    assert build_cors_origins(False, None, None, "1701") == []


def test_cors_origins_exclude_localhost_outside_debug():
    from app.main import build_cors_origins

    origins = build_cors_origins(False, "100.1.2.3", "host.ts.net", "1701")
    assert not any("localhost" in origin for origin in origins)


def test_cors_origins_include_localhost_in_debug():
    from app.main import build_cors_origins

    origins = build_cors_origins(True, None, None, "1701")
    assert "http://localhost:5173" in origins
    assert "https://localhost:1701" in origins


def test_cors_origins_use_the_configured_frontend_port():
    """
    Every entry used to be pinned to 5173 while the frontend default is 1701,
    so the entries meant to matter never matched.
    """
    from app.main import build_cors_origins

    origins = build_cors_origins(False, "100.1.2.3", "host.ts.net", "1701")
    assert origins == ["https://100.1.2.3:1701", "https://host.ts.net:1701"]


def test_cors_origins_skip_absent_tailscale_values():
    from app.main import build_cors_origins

    assert build_cors_origins(False, "100.1.2.3", None, "1701") == [
        "https://100.1.2.3:1701"
    ]


# ─── Static delivery ──────────────────────────────────────────────────────────


@pytest.fixture
def static_client(tmp_path):
    """
    Mount the hardened static handler on a throwaway directory.

    Built here rather than reusing the application mount so the assertions do
    not depend on files existing in the real static tree.
    """
    from fastapi import FastAPI
    from app.main import HardenedStaticFiles

    root = tmp_path / "static"
    (root / "uploads").mkdir(parents=True)
    for name, body in {
        "a.png": b"\x89PNG",
        "doc.pdf": b"%PDF-1.4",
        "clip.mp4": b"fake mp4",
        "evil.html": b"<script>alert(document.cookie)</script>",
        "evil.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        "report.docx": b"PK",
        "notes.txt": b"plain",
    }.items():
        (root / "uploads" / name).write_bytes(body)

    static_app = FastAPI()
    static_app.mount("/static", HardenedStaticFiles(directory=str(root)), name="static")
    return TestClient(static_app)


@pytest.mark.parametrize(
    "name,content_type",
    [
        ("a.png", "image/png"),
        ("doc.pdf", "application/pdf"),
        ("clip.mp4", "video/mp4"),
    ],
)
def test_static_serves_known_media_inline(static_client, name, content_type):
    """Blocks embed these directly, so they have to keep rendering in place."""
    response = static_client.get(f"/static/uploads/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize("name", ["evil.html", "evil.svg", "report.docx", "notes.txt"])
def test_static_hands_out_everything_else_as_a_download(static_client, name):
    """
    An upload the browser would render runs in the application's own origin,
    which is stored XSS. SVG counts here: it is a document that can carry
    script, not merely a picture.
    """
    response = static_client.get(f"/static/uploads/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"


@pytest.mark.parametrize("name", ["a.png", "evil.html", "report.docx"])
def test_static_always_sets_nosniff(static_client, name):
    """Without this the browser may disregard the declared type."""
    response = static_client.get(f"/static/uploads/{name}")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_static_does_not_decorate_a_missing_file(static_client):
    response = static_client.get("/static/uploads/absent.png")
    assert response.status_code == 404
    assert "content-disposition" not in response.headers


def test_static_still_answers_range_requests(static_client):
    """Video seeking depends on partial responses surviving the change."""
    response = static_client.get(
        "/static/uploads/clip.mp4", headers={"Range": "bytes=0-3"}
    )
    assert response.status_code == 206
    assert response.content == b"fake"


def test_ws_route_is_registered():
    """
    Verify the /ws endpoint exists. Without a valid session the server
    closes with code 4401; the route being absent would raise a different
    error entirely.
    """
    from starlette.testclient import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4401


def test_automations_route_is_registered():
    """
    Verify /api/automations is registered.  Without auth it must return 401,
    not 404 (which would mean the router was never included).
    """
    response = client.get("/api/automations")
    assert response.status_code == 401


def test_comments_route_is_registered():
    """
    Verify /api/blocks/{id}/comments is registered.  Without auth it must
    return 401, not 404 (which would mean the router was never included).
    """
    response = client.get(f"/api/blocks/{uuid.uuid4()}/comments")
    assert response.status_code == 401
