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


_tailscale_ip = os.getenv("TAILSCALE_IP")
_tailscale_hostname = os.getenv("TAILSCALE_HOSTNAME")
_cors_origins = ["http://localhost:5173", "https://localhost:5173"]
if _tailscale_ip:
    _cors_origins.append(f"https://{_tailscale_ip}:5173")
if _tailscale_hostname:
    _cors_origins.append(f"https://{_tailscale_hostname}:5173")

_cors_credentials = True

app = FastAPI(
    title="CapyBarca API",
    version="0.14.16",
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

app.mount("/static", StaticFiles(directory="static"), name="static")


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
    # Keine internen Details an den Client zurückgeben (Information Disclosure)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
