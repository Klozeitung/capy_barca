"""
Downloads router.

Offers the Android app to every signed-in account, so a phone can fetch it
from the web app under Settings -> Downloads.

Where the app comes from
------------------------
The APK lies in ``android/`` at the root of the installation, which
docker-compose mounts read-only into the backend container. Files named
``capybarca-android-<a.b.c.d>[-debug].apk`` are considered; the highest
version wins, and at the same version a release build wins over a debug
build. Anything else in the folder is ignored, and the file served is always
one of the names found there, never one taken from the request.

Endpoints
---------
``GET /api/downloads/android``       what is on offer, or ``null``
``GET /api/downloads/android/file``  the APK itself (404 when there is none)
"""
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.session.deps import get_current_user
from app.users.model import User

downloads_router = APIRouter(prefix="/api/downloads", tags=["downloads"])

# Folder holding the Android app. Module level so tests can redirect it.
_ANDROID_DIR: Path = Path(__file__).resolve().parents[2] / "android"

APK_PATTERN = re.compile(r"^capybarca-android-(\d+)\.(\d+)\.(\d+)\.(\d+)(-debug)?\.apk$")

APK_MEDIA_TYPE = "application/vnd.android.package-archive"


class AndroidApp(BaseModel):
    """The APK on offer."""

    filename: str
    version: str
    debug: bool
    size: int


def _rank(match: re.Match[str]) -> tuple[int, int, int, int, int]:
    """Sort key: the version, then release before debug."""
    major, minor, patch, build = (int(match.group(i)) for i in range(1, 5))
    return major, minor, patch, build, 0 if match.group(5) else 1


def find_android_app(folder: Path) -> Optional[tuple[Path, AndroidApp]]:
    """The newest APK in *folder* with what the client shows of it, or None."""
    if not folder.is_dir():
        return None
    candidates: list[tuple[tuple[int, int, int, int, int], Path, re.Match[str]]] = []
    for path in folder.iterdir():
        match = APK_PATTERN.match(path.name)
        if match and path.is_file():
            candidates.append((_rank(match), path, match))
    if not candidates:
        return None
    _, path, match = max(candidates, key=lambda c: c[0])
    return path, AndroidApp(
        filename=path.name,
        version=".".join(match.group(i) for i in range(1, 5)),
        debug=match.group(5) is not None,
        size=path.stat().st_size,
    )


@downloads_router.get("/android", response_model=Optional[AndroidApp])
def android_app_info(_user: User = Depends(get_current_user)):
    """The Android app on offer, or ``null`` when the folder holds none."""
    found = find_android_app(_ANDROID_DIR)
    return found[1] if found else None


@downloads_router.get("/android/file")
def android_app_file(_user: User = Depends(get_current_user)):
    """Send the APK described by ``GET /api/downloads/android``."""
    found = find_android_app(_ANDROID_DIR)
    if found is None:
        raise HTTPException(status_code=404, detail="No Android app available")
    path, info = found
    return FileResponse(path, media_type=APK_MEDIA_TYPE, filename=info.filename)
