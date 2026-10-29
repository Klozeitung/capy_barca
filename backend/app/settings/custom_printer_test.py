"""Tests for building and sending requests to the custom printer."""
import json

import httpx
import pytest

from app.settings import custom_printer
from app.settings.custom_printer import build_request, is_configured, parse_body_value, send


def _no_secrets(token):
    return None


def _plain_secrets(token):
    """Treat the stored token as the value itself, to keep tests readable."""
    return token


# ─── Configuration ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("stored,expected", [
    (None, False),
    ({}, False),
    ({"url": ""}, False),
    ({"url": "   "}, False),
    ({"url": "http://host-x:1234/print"}, True),
])
def test_is_configured_needs_a_url(stored, expected):
    assert is_configured(stored) is expected


@pytest.mark.parametrize("raw,expected", [
    ("true", True),
    ("false", False),
    ("42", 42),
    ("1.5", 1.5),
    ("null", None),
    ('"quoted"', "quoted"),
    ('{"a": 1}', {"a": 1}),
    ("plain text", "plain text"),
    ("", ""),
])
def test_body_values_are_json_where_valid_and_text_otherwise(raw, expected):
    assert parse_body_value(raw) == expected


# ─── Building ────────────────────────────────────────────────────────────────


def test_the_text_goes_under_the_configured_field():
    prepared = build_request(
        {"url": "http://host-x/print", "text_field": "content", "arguments": []},
        "Line A",
        _no_secrets,
    )
    assert prepared.url == "http://host-x/print"
    assert prepared.body == {"content": "Line A"}
    assert prepared.headers == {}


def test_the_text_field_defaults_to_text():
    prepared = build_request({"url": "http://host-x/print"}, "Line A", _no_secrets)
    assert prepared.body == {"text": "Line A"}


def test_body_arguments_join_the_text():
    prepared = build_request(
        {
            "url": "http://host-x/print",
            "text_field": "text",
            "arguments": [
                {"name": "option-a", "target": "body", "value": "true"},
                {"name": "option-b", "target": "body", "value": "label x"},
            ],
        },
        "Line A",
        _no_secrets,
    )
    assert prepared.body == {"option-a": True, "option-b": "label x", "text": "Line A"}


def test_no_argument_replaces_the_text():
    prepared = build_request(
        {
            "url": "http://host-x/print",
            "text_field": "text",
            "arguments": [{"name": "text", "target": "body", "value": "other"}],
        },
        "Line A",
        _no_secrets,
    )
    assert prepared.body == {"text": "Line A"}


def test_query_arguments_are_appended_to_the_existing_query():
    prepared = build_request(
        {
            "url": "http://host-x/print?mode=a",
            "arguments": [
                {"name": "level", "target": "query", "value": "high"},
                {"name": "note", "target": "query", "value": "a b&c"},
            ],
        },
        "Line A",
        _no_secrets,
    )
    assert prepared.url == "http://host-x/print?mode=a&level=high&note=a+b%26c"


def test_header_arguments_are_opened_from_their_sealed_value():
    prepared = build_request(
        {
            "url": "http://host-x/print",
            "arguments": [{"name": "X-Token", "target": "header", "sealed": "token-a"}],
        },
        "Line A",
        _plain_secrets,
    )
    assert prepared.headers == {"X-Token": "token-a"}


def test_a_header_that_cannot_be_opened_is_left_out():
    prepared = build_request(
        {
            "url": "http://host-x/print",
            "arguments": [{"name": "X-Token", "target": "header", "sealed": "token-a"}],
        },
        "Line A",
        _no_secrets,
    )
    assert prepared.headers == {}


# ─── Sending ─────────────────────────────────────────────────────────────────


def _transport(status=200, body="done", seen=None, error=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if error is not None:
            raise error
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


def _prepared(**overrides):
    return custom_printer.PreparedRequest(
        url=overrides.get("url", "http://host-x/print?level=high"),
        headers=overrides.get("headers", {"X-Token": "token-a"}),
        body=overrides.get("body", {"text": "Line A", "option-a": True}),
    )


def test_send_posts_the_body_as_json_with_query_and_headers():
    seen: list[httpx.Request] = []
    result = send(_prepared(), transport=_transport(seen=seen))

    assert result.ok is True
    assert result.status == 200
    assert result.body == "done"
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "http://host-x/print?level=high"
    assert request.headers["X-Token"] == "token-a"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"text": "Line A", "option-a": True}


@pytest.mark.parametrize("status,ok", [(200, True), (201, True), (204, True), (400, False), (503, False)])
def test_any_2xx_counts_as_printed(status, ok):
    result = send(_prepared(), transport=_transport(status=status, body=""))
    assert result.ok is ok
    assert result.status == status


def test_success_can_be_narrowed_to_200():
    result = send(
        _prepared(), success=lambda status: status == 200, transport=_transport(status=201),
    )
    assert result.ok is False
    assert result.status == 201


def test_redirects_are_not_followed():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "http://host-y/elsewhere"})

    result = send(_prepared(), transport=httpx.MockTransport(handler))
    assert result.status == 302
    assert result.ok is False
    assert len(seen) == 1


def test_long_answers_are_cut():
    result = send(_prepared(), transport=_transport(body="x" * 5000))
    assert len(result.body) == custom_printer.MAX_RESPONSE_LENGTH


def test_an_unreachable_printer_is_reported_without_a_status():
    result = send(_prepared(), transport=_transport(error=httpx.ConnectError("refused")))
    assert result.ok is False
    assert result.status is None
    assert result.body == "ConnectError"


def test_a_timeout_is_reported_as_such():
    result = send(_prepared(), transport=_transport(error=httpx.ReadTimeout("slow")))
    assert result.ok is False
    assert result.status is None
    assert result.body == "Timeout"
