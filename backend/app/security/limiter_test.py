from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.security.limiter as lm


def test_limiter_is_limiter_instance():
    assert isinstance(lm.limiter, Limiter)


def test_limiter_uses_remote_address_as_key():
    assert lm.limiter._key_func is get_remote_address


def test_limiter_is_singleton():
    from app.security.limiter import limiter as second_import
    assert lm.limiter is second_import


# ─── Proxy-header trust chain ─────────────────────────────────────────────────
# The rate limiter is only per-client if uvicorn resolves the real caller from
# X-Forwarded-For. These tests pin the two halves that deployment depends on:
# what --proxy-headers does with the header, and what the absence of the flag
# costs. They exercise ProxyHeadersMiddleware directly because the middleware
# is applied by the uvicorn process, not by the FastAPI application.


def _probe_client(trusted_hosts=None) -> TestClient:
    """
    Return a client for an app that echoes the limiter key it would use.

    ``trusted_hosts=None`` builds the bare app without the middleware, which
    is what runs when ``--proxy-headers`` is missing from entrypoint.sh.
    """
    async def probe(request):
        return PlainTextResponse(get_remote_address(request))

    app = Starlette(routes=[Route("/probe", probe)])
    if trusted_hosts is None:
        return TestClient(app)
    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=trusted_hosts))


def test_forwarded_for_becomes_the_limiter_key():
    """A single forwarded address, as nginx now sends it, is used as the key."""
    client = _probe_client(trusted_hosts="*")
    response = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.text == "203.0.113.7"


def test_forwarded_for_is_ignored_without_proxy_headers():
    """
    Without the middleware the header has no effect and every caller collapses
    onto the peer address — the global bucket this batch removes.
    """
    client = _probe_client()
    response = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.text == "testclient"


def test_wildcard_trust_takes_the_leftmost_forwarded_address():
    """
    Pins the behaviour that forces nginx to overwrite rather than append.

    While every peer is trusted, uvicorn uses the leftmost entry. With an
    appending proxy configuration that entry is client-supplied, so the caller
    would choose its own rate-limit key. If a uvicorn upgrade changes this,
    the nginx configuration has to be re-evaluated rather than silently kept.
    """
    client = _probe_client(trusted_hosts="*")
    response = client.get(
        "/probe",
        headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.7"},
    )
    assert response.text == "198.51.100.9"


def test_restricted_trust_skips_known_proxies_from_the_right():
    """
    Documents the alternative configuration.

    With an explicit trust list uvicorn walks the header from the right and
    returns the first address it does not trust, which makes an appending
    proxy safe. This is the path to take if FORWARDED_ALLOW_IPS is ever
    narrowed to the nginx container address.
    """
    client = _probe_client(trusted_hosts="testclient, 192.0.2.1")
    response = client.get(
        "/probe",
        headers={"X-Forwarded-For": "198.51.100.9, 192.0.2.1"},
    )
    assert response.text == "198.51.100.9"


def test_untrusted_peer_cannot_set_the_limiter_key():
    """A peer outside the trust list has its forwarded header ignored."""
    client = _probe_client(trusted_hosts="192.0.2.1")
    response = client.get("/probe", headers={"X-Forwarded-For": "198.51.100.9"})
    assert response.text == "testclient"
