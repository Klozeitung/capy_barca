"""
Shared rate limiter.

``get_remote_address`` returns ``request.client.host``. Behind nginx that is
the proxy's own container address, which would put every caller into a single
global bucket. The real client address is restored by uvicorn's
ProxyHeadersMiddleware, enabled through ``--proxy-headers`` in
``backend/entrypoint.sh``.

That chain only holds because ``frontend/nginx.conf`` overwrites
``X-Forwarded-For`` with ``$remote_addr`` instead of appending to a
client-supplied value: uvicorn reads the leftmost entry of the header when it
trusts its peers, so an appending configuration would let the caller pick the
key it is limited by. ``limiter_test.py`` pins the behaviour both sides rely
on, so an upgrade that changes it fails the suite rather than the deployment.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
