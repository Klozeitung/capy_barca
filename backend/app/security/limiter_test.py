from slowapi import Limiter

import app.security.limiter as lm


def test_limiter_is_limiter_instance():
    assert isinstance(lm.limiter, Limiter)


def test_limiter_uses_remote_address_as_key():
    from slowapi.util import get_remote_address
    assert lm.limiter._key_func is get_remote_address


def test_limiter_is_singleton():
    from app.security.limiter import limiter as second_import
    assert lm.limiter is second_import
