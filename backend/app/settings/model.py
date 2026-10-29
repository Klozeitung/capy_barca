"""
Installation-wide settings: the ``app_settings`` table and the shapes the
settings and Quick Print endpoints exchange.

One row per setting, addressed by a fixed key (``LOCAL_AI_KEY``,
``CUSTOM_PRINTER_KEY``), holding a JSON object. How each object is stored:

``local_ai``
    ``{"host": str, "key": <sealed token> | null}``

``custom_printer``
    ``{"url": str, "text_field": str, "arguments": [argument, ...]}`` where an
    argument is ``{"name", "target", "value"}`` for the targets ``body`` and
    ``query``, and ``{"name", "target": "header", "sealed": <token> | null}``
    for headers. Header values often carry credentials, so they are stored
    encrypted (``secret_box``) and never sent back to the frontend.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlsplit

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base

LOCAL_AI_KEY = "local_ai"
CUSTOM_PRINTER_KEY = "custom_printer"

#: Upper bound of a Quick Print text. Generous for a page of text and
#: properties, small enough that a request cannot be used to push large
#: payloads through the server.
MAX_PRINT_TEXT_LENGTH = 20_000

MAX_ARGUMENTS = 32

# An HTTP header field name (RFC 9110, "token").
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


class AppSetting(Base):
    """One installation-wide setting, stored as a JSON object under its key."""

    __tablename__ = "app_settings"

    key = Column(Text, primary_key=True, nullable=False)
    value = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=sa.text("now()"),
    )


ArgumentTarget = Literal["body", "query", "header"]


# ─── Requests ─────────────────────────────────────────────────────────────────


class PrinterArgumentIn(BaseModel):
    """
    One additional argument sent with every print.

    For a header, ``value`` ``None`` keeps the value stored for a header of
    the same name, since stored header values are never shown again.
    """

    name: str = Field(min_length=1, max_length=128)
    target: ArgumentTarget
    value: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Argument name must not be empty")
        return v

    @model_validator(mode="after")
    def _check_header_name(self) -> "PrinterArgumentIn":
        if self.target == "header" and not _HEADER_NAME_RE.match(self.name):
            raise ValueError(f"'{self.name}' is not a valid header name")
        return self


class CustomPrinterIn(BaseModel):
    """The custom printer. An empty ``url`` means no printer is set up."""

    url: str = Field(default="", max_length=2048)
    text_field: str = Field(default="text", min_length=1, max_length=128)
    arguments: list[PrinterArgumentIn] = Field(default_factory=list, max_length=MAX_ARGUMENTS)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("The URL must start with http:// or https:// and name a host")
        return v

    @field_validator("text_field")
    @classmethod
    def _strip_text_field(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("The text field name must not be empty")
        return v

    @model_validator(mode="after")
    def _check_names(self) -> "CustomPrinterIn":
        body_names: set[str] = set()
        header_names: set[str] = set()
        for argument in self.arguments:
            if argument.target == "body":
                if argument.name == self.text_field:
                    raise ValueError(
                        f"The body argument '{argument.name}' would replace the print text"
                    )
                if argument.name in body_names:
                    raise ValueError(f"The body argument '{argument.name}' appears twice")
                body_names.add(argument.name)
            elif argument.target == "header":
                lowered = argument.name.lower()
                if lowered in header_names:
                    raise ValueError(f"The header '{argument.name}' appears twice")
                header_names.add(lowered)
        return self


class LocalAiIn(BaseModel):
    """
    Connection to a local AI service, stored for later use.

    ``key`` ``None`` keeps the stored key, an empty string removes it.
    """

    host: str = Field(default="", max_length=255)
    key: Optional[str] = Field(default=None, max_length=4096)

    @field_validator("host")
    @classmethod
    def _strip_host(cls, v: str) -> str:
        return v.strip()


class ApiSettingsIn(BaseModel):
    local_ai: LocalAiIn = Field(default_factory=LocalAiIn)
    custom_printer: CustomPrinterIn = Field(default_factory=CustomPrinterIn)


class PrintRequest(BaseModel):
    """A Quick Print: the text the frontend built for the block *block_id*."""

    block_id: uuid.UUID
    text: str = Field(min_length=1, max_length=MAX_PRINT_TEXT_LENGTH)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("The print text must not be blank")
        return v


# ─── Responses ────────────────────────────────────────────────────────────────


class PrinterArgumentOut(BaseModel):
    name: str
    target: ArgumentTarget
    #: ``None`` for headers, whose values are never sent back.
    value: Optional[str]
    value_set: bool


class CustomPrinterOut(BaseModel):
    url: str
    text_field: str
    arguments: list[PrinterArgumentOut]


class LocalAiOut(BaseModel):
    host: str
    key_set: bool


class ApiSettingsOut(BaseModel):
    local_ai: LocalAiOut
    custom_printer: CustomPrinterOut


class PrintStatus(BaseModel):
    """Whether Quick Print goes to the printer for the current account."""

    configured: bool
    allowed: bool


class PrintResult(BaseModel):
    """
    What the printer answered.

    ``status`` is ``None`` when no answer arrived (unreachable, timeout), in
    which case ``body`` names the reason.
    """

    ok: bool
    status: Optional[int]
    body: str
