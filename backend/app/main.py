import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()

logger = logging.getLogger(__name__)


def _migrate_env_user_to_db() -> None:
    """
    One-time migration: if the users table is empty but CB_USERNAME and
    CB_PASSWORD are set in .env (legacy single-user setup), create an admin
    user in the database from those credentials and clear the .env keys.

    Safe to call on every startup – exits immediately if users already exist
    or if no credentials are present in .env.
    """
    import uuid

    from dotenv import dotenv_values, set_key

    from app.database.database import SessionLocal
    from app.users.model import User

    env_path = Path(__file__).resolve().parents[1] / ".env"
    config = dotenv_values(env_path)
    cb_username = config.get("CB_USERNAME", "").strip()
    cb_hash = config.get("CB_PASSWORD", "").strip()

    if not cb_username or not cb_hash:
        return

    with SessionLocal() as db:
        if db.query(User).count() > 0:
            return  # already migrated – nothing to do

        user = User(
            id=uuid.uuid4(),
            username=cb_username,
            password_hash=cb_hash,  # already bcrypt-hashed
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        logger.info("Migrated legacy user '%s' from .env to the users table", cb_username)

    # Remove credentials from .env so the migration does not run again.
    set_key(str(env_path), "CB_USERNAME", "")
    set_key(str(env_path), "CB_PASSWORD", "")
    logger.info("Cleared CB_USERNAME / CB_PASSWORD from .env after migration")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema creation is handled by Alembic (entrypoint.sh runs
    # `alembic upgrade head` before uvicorn starts). The lifespan hook is
    # responsible only for runtime housekeeping.
    from app.session.session import purge_expired

    purge_expired()
    _migrate_env_user_to_db()
    yield


def build_cors_origins(
    debug: bool,
    tailscale_ip: str | None,
    tailscale_hostname: str | None,
    frontend_port: str,
) -> list[str]:
    """
    Return the cross-origin allowlist for this installation.

    Two things were wrong with the previous list. It always carried the Vite
    development origins, including in production and in combination with
    ``allow_credentials=True``. And every entry was pinned to port 5173, while
    the frontend is actually served on PORT_FRONTEND, so the entries meant to
    be load-bearing never matched anything.

    In a normal installation the browser talks to nginx and the backend through
    the same origin, so the resulting list is empty and nothing needs to be
    allowed. It only fills up for the development server and for the Tailscale
    addresses that are genuinely reachable.

    Pure function with its arguments passed in, so the behaviour can be
    asserted for configurations other than the one this process happens to run
    under.
    """
    origins: list[str] = []

    if debug:
        # Vite's own dev server, which does not go through nginx.
        origins += ["http://localhost:5173", "https://localhost:5173"]
        origins += [
            f"http://localhost:{frontend_port}",
            f"https://localhost:{frontend_port}",
        ]

    for host in (tailscale_ip, tailscale_hostname):
        if host:
            origins.append(f"https://{host}:{frontend_port}")

    return origins


_tailscale_ip = os.getenv("TAILSCALE_IP")
_tailscale_hostname = os.getenv("TAILSCALE_HOSTNAME")
_frontend_port = os.getenv("PORT_FRONTEND", "1701")
_debug = os.getenv("DEBUG", "false").lower() == "true"

_cors_origins = build_cors_origins(
    _debug, _tailscale_ip, _tailscale_hostname, _frontend_port
)

_cors_credentials = True

app = FastAPI(
    title="CapyBarca API",
    version="0.15.14",
    lifespan=lifespan,
)

from app.security.limiter import limiter  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Cookie"],
)

from app.automations.automations_router import automations_router  # noqa: E402
from app.blocks.database_router import database_router  # noqa: E402
from app.blocks.router import block_router  # noqa: E402
from app.comments.comments_router import comments_router  # noqa: E402
from app.media.router import media_router  # noqa: E402
from app.permissions.router import permissions_router  # noqa: E402
from app.session.login_router import login_router  # noqa: E402
from app.setup_router import setup_router  # noqa: E402
from app.users.router import users_router  # noqa: E402
from app.wopi.router import wopi_router  # noqa: E402
from app.ws.router import ws_router  # noqa: E402

app.include_router(automations_router)
app.include_router(block_router)
app.include_router(comments_router)
app.include_router(database_router)
app.include_router(permissions_router)
app.include_router(media_router)
app.include_router(login_router)
app.include_router(setup_router)
app.include_router(users_router)
app.include_router(wopi_router)
app.include_router(ws_router)

# ── Static file serving ───────────────────────────────────────────────────────

Path("static").mkdir(exist_ok=True)
Path("static/uploads").mkdir(exist_ok=True)

from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import Response as StarletteResponse  # noqa: E402
from starlette.types import Scope  # noqa: E402

# Extensions served inline, each with the content type the server decides on.
# Everything absent from this mapping is handed out as a download with a
# neutral type, which is what keeps an uploaded document from being rendered
# as a page in this origin.
#
# SVG is not here on purpose: it is a document format that can carry script,
# and serving one inline from the same origin as the application would let an
# upload run in the session of whoever opens it. Kept in step with the upload
# allowlist in app/media/router.py.
INLINE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".pdf": "application/pdf",
}


class HardenedStaticFiles(StaticFiles):
    """
    Static delivery that decides the content type instead of guessing it.

    Uploads are reachable under the application's own origin, so anything the
    browser is willing to render there runs with the user's session. Two rules
    follow from that: the content type is chosen by the server from a fixed
    mapping rather than derived from the stored name, and any extension not in
    that mapping is delivered as a download. ``nosniff`` closes the remaining
    route, where the browser would otherwise disregard the declared type and
    decide from the content.
    """

    async def get_response(self, path: str, scope: Scope) -> StarletteResponse:
        response = await super().get_response(path, scope)

        if response.status_code >= 400:
            return response

        response.headers["X-Content-Type-Options"] = "nosniff"

        inline_type = INLINE_MEDIA_TYPES.get(Path(path).suffix.lower())
        if inline_type is not None:
            response.headers["Content-Type"] = inline_type
        else:
            response.headers["Content-Type"] = "application/octet-stream"
            response.headers["Content-Disposition"] = "attachment"

        return response


app.mount("/static", HardenedStaticFiles(directory="static"), name="static")


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "capybarca"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback

    logger.error(f"Unhandled exception on {request.method} {request.url.path}")
    logger.error(f"Exception type: {type(exc).__name__}")
    logger.error(f"Exception message: {str(exc)}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    # No internal detail goes back to the client (information disclosure).
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
