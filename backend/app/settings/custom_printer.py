"""
Requests to the custom printer.

CapyBarca knows nothing about any particular printer. It sends one HTTP POST
with a JSON body that holds the print text under the configured field name;
everything a device needs beyond that (options in the body, query parameters
such as a sound, an authorization header) is set up by the admin as
additional arguments. Converting the character set, wrapping lines and
interpreting markup are left to the receiving side.

The request is sent from the backend, never from the browser: the printer
usually allows no cross-origin calls, and the user's device need not be able
to reach it at all.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.settings.model import PrintResult
from app.settings.secret_box import open_sealed

#: Waiting time for the printer's answer. A printer may answer only once the
#: paper is out, which takes a few seconds.
TIMEOUT_SECONDS = 30.0

#: The printer's answer is shown to the admin; longer answers are cut here.
MAX_RESPONSE_LENGTH = 2000

#: Sent by "Check connection" in the settings.
TEST_TEXT = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."


@dataclass
class PreparedRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


def is_configured(stored: Optional[dict]) -> bool:
    """Whether *stored* names a printer to send to."""
    return bool(stored and (stored.get("url") or "").strip())


def parse_body_value(raw: str) -> Any:
    """
    The JSON value of a body argument.

    A value that is valid JSON is sent as such, so ``true`` arrives as a
    boolean and ``42`` as a number. Anything else is sent as the text it is.
    """
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _with_query(url: str, extra: list[tuple[str, str]]) -> str:
    """*url* with the parameters *extra* appended to the query it already has."""
    if not extra:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + extra
    return urlunsplit(parts._replace(query=urlencode(query)))


def build_request(
    stored: dict,
    text: str,
    open_secret: Callable[[Optional[str]], Optional[str]] = open_sealed,
) -> PreparedRequest:
    """
    Build the request that prints *text* with the stored printer settings.

    A header whose stored value cannot be read (never set, or ``SECRET_KEY``
    changed since) is left out rather than sent empty.
    """
    text_field = stored.get("text_field") or "text"
    body: dict[str, Any] = {}
    query: list[tuple[str, str]] = []
    headers: dict[str, str] = {}

    for argument in stored.get("arguments") or []:
        name = argument.get("name")
        target = argument.get("target")
        if not name:
            continue
        if target == "body":
            body[name] = parse_body_value(argument.get("value") or "")
        elif target == "query":
            query.append((name, argument.get("value") or ""))
        elif target == "header":
            value = open_secret(argument.get("sealed"))
            if value is not None:
                headers[name] = value

    # Set last, so no argument can replace the print text.
    body[text_field] = text
    return PreparedRequest(url=_with_query(stored["url"], query), headers=headers, body=body)


def send(
    prepared: PreparedRequest,
    *,
    success: Callable[[int], bool] = lambda status: 200 <= status < 300,
    transport: Optional[httpx.BaseTransport] = None,
) -> PrintResult:
    """
    Send *prepared* and report what came back.

    Never raises for network trouble: an unreachable printer or a timeout is
    reported with ``status`` ``None`` and the reason in ``body``. Redirects
    are not followed, so the request goes only to the address the admin set.
    *success* decides which status counts as success.
    """
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.post(prepared.url, json=prepared.body, headers=prepared.headers)
    except httpx.TimeoutException:
        return PrintResult(ok=False, status=None, body="Timeout")
    except httpx.HTTPError as exc:
        return PrintResult(ok=False, status=None, body=type(exc).__name__)

    return PrintResult(
        ok=success(response.status_code),
        status=response.status_code,
        body=response.text[:MAX_RESPONSE_LENGTH],
    )
