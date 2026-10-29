"""
Endpoints of the "API" settings and of Quick Print.

Settings (admin only)
---------------------
``GET  /api/settings/api``               current settings, secrets reported as set or not
``PUT  /api/settings/api``               replace the settings
``POST /api/settings/api/printer-test``  send a test text to the stored printer

Quick Print (any signed-in account)
-----------------------------------
``GET  /api/print/status``  whether a printer is set up and the account may use it
``POST /api/print``         send the text the frontend built for a block

The frontend builds the print text, because it already formats values the way
the view and the page show them. The backend checks that the account may read
the block and may print, and forwards the text. An account without the print
permission (``users.can_print``; admins always have it) gets a 403, and the
frontend then offers the text as a download instead.

Secret values are never returned. A header argument or the local AI key sent
back with ``null`` keeps what is stored.
"""
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.blocks.models import Block
from app.session.deps import get_current_user, get_db, require_admin, require_block_access
from app.settings import custom_printer
from app.settings.model import (
    CUSTOM_PRINTER_KEY,
    LOCAL_AI_KEY,
    ApiSettingsIn,
    ApiSettingsOut,
    AppSetting,
    CustomPrinterOut,
    LocalAiOut,
    PrinterArgumentOut,
    PrintRequest,
    PrintResult,
    PrintStatus,
)
from app.settings.secret_box import open_sealed, seal
from app.users.model import User

settings_router = APIRouter(prefix="/api/settings", tags=["settings"])
print_router = APIRouter(prefix="/api/print", tags=["print"])

#: Transport for the outgoing printer request. ``None`` uses the network;
#: tests replace it with an ``httpx.MockTransport``.
_transport: Optional[httpx.BaseTransport] = None


# ─── Storage ──────────────────────────────────────────────────────────────────


def _load(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


def _store(db: Session, key: str, value: dict) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def may_print(user: User) -> bool:
    """Admins may always print; everyone else needs the permission."""
    return user.role == "admin" or bool(user.can_print)


# ─── Conversion ───────────────────────────────────────────────────────────────


def _settings_out(db: Session) -> ApiSettingsOut:
    local_ai = _load(db, LOCAL_AI_KEY)
    printer = _load(db, CUSTOM_PRINTER_KEY)

    arguments: list[PrinterArgumentOut] = []
    for argument in printer.get("arguments") or []:
        if argument.get("target") == "header":
            arguments.append(PrinterArgumentOut(
                name=argument["name"],
                target="header",
                value=None,
                value_set=open_sealed(argument.get("sealed")) is not None,
            ))
        else:
            arguments.append(PrinterArgumentOut(
                name=argument["name"],
                target=argument["target"],
                value=argument.get("value") or "",
                value_set=True,
            ))

    return ApiSettingsOut(
        local_ai=LocalAiOut(
            host=local_ai.get("host") or "",
            key_set=open_sealed(local_ai.get("key")) is not None,
        ),
        custom_printer=CustomPrinterOut(
            url=printer.get("url") or "",
            text_field=printer.get("text_field") or "text",
            arguments=arguments,
        ),
    )


def _local_ai_to_store(payload: ApiSettingsIn, previous: dict) -> dict:
    key = payload.local_ai.key
    if key is None:
        sealed = previous.get("key")
    elif key == "":
        sealed = None
    else:
        sealed = seal(key)
    return {"host": payload.local_ai.host, "key": sealed}


def _printer_to_store(payload: ApiSettingsIn, previous: dict) -> dict:
    previous_headers = {
        (argument.get("name") or "").lower(): argument.get("sealed")
        for argument in previous.get("arguments") or []
        if argument.get("target") == "header"
    }
    arguments: list[dict] = []
    for argument in payload.custom_printer.arguments:
        if argument.target == "header":
            if argument.value is None:
                sealed = previous_headers.get(argument.name.lower())
            else:
                sealed = seal(argument.value)
            arguments.append({"name": argument.name, "target": "header", "sealed": sealed})
        else:
            arguments.append({
                "name": argument.name,
                "target": argument.target,
                "value": argument.value or "",
            })
    return {
        "url": payload.custom_printer.url,
        "text_field": payload.custom_printer.text_field,
        "arguments": arguments,
    }


# ─── Settings ─────────────────────────────────────────────────────────────────


@settings_router.get("/api", response_model=ApiSettingsOut)
def get_api_settings(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The stored API settings (admin only). Secrets come back as set or not set."""
    return _settings_out(db)


@settings_router.put("/api", response_model=ApiSettingsOut)
def put_api_settings(
    payload: ApiSettingsIn,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the API settings (admin only) and return them as stored."""
    _store(db, LOCAL_AI_KEY, _local_ai_to_store(payload, _load(db, LOCAL_AI_KEY)))
    _store(db, CUSTOM_PRINTER_KEY, _printer_to_store(payload, _load(db, CUSTOM_PRINTER_KEY)))
    db.commit()
    return _settings_out(db)


@settings_router.post("/api/printer-test", response_model=PrintResult)
def test_printer(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Send a short test text to the stored printer and return its answer
    (admin only). Only a 200 counts as success.
    """
    stored = _load(db, CUSTOM_PRINTER_KEY)
    if not custom_printer.is_configured(stored):
        raise HTTPException(status_code=409, detail="No custom printer is set up")
    prepared = custom_printer.build_request(stored, custom_printer.TEST_TEXT)
    # The database is not needed while waiting for the printer.
    db.close()
    return custom_printer.send(
        prepared, success=lambda status: status == 200, transport=_transport,
    )


# ─── Quick Print ──────────────────────────────────────────────────────────────


@print_router.get("/status", response_model=PrintStatus)
def print_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Whether Quick Print sends to the printer for the current account."""
    return PrintStatus(
        configured=custom_printer.is_configured(_load(db, CUSTOM_PRINTER_KEY)),
        allowed=may_print(current_user),
    )


@print_router.post("", response_model=PrintResult)
def quick_print(
    payload: PrintRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send *payload.text* to the custom printer.

    Refused with 403 without the print permission or without read access to
    the block, with 404 for a block that does not exist or is in the trash,
    and with 409 when no printer is set up. The printer's own
    answer, failure included, comes back as ``PrintResult``.
    """
    if not may_print(current_user):
        raise HTTPException(status_code=403, detail="Not allowed to print")
    block = db.get(Block, payload.block_id)
    if block is None or block.state != "active":
        raise HTTPException(status_code=404, detail="Block not found")
    require_block_access(db, payload.block_id, current_user, level="read")
    stored = _load(db, CUSTOM_PRINTER_KEY)
    if not custom_printer.is_configured(stored):
        raise HTTPException(status_code=409, detail="No custom printer is set up")
    prepared = custom_printer.build_request(stored, payload.text)
    db.close()
    return custom_printer.send(prepared, transport=_transport)
