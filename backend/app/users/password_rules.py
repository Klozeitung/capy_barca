"""
Shared password constraints.

Every schema that ends up handing a string to bcrypt uses the types defined
here, so the two rules that matter live in one place instead of being repeated
per endpoint.

The upper bound is not cosmetic. bcrypt refuses an input longer than 72 bytes
outright, so without it a long passphrase reaches the hashing call, raises, and
surfaces to the caller as a 500 on an ordinary registration or password change.

The bound counts *bytes*, not characters, because that is what bcrypt counts.
A plain ``max_length`` would let 72 characters of anything non-ASCII through
and still fail: forty umlauts are forty characters and eighty bytes.
"""
from typing import Annotated

from pydantic import AfterValidator, Field

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72


def within_bcrypt_limit(value: str) -> str:
    """Reject a password bcrypt would refuse to hash."""
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must not be longer than {MAX_PASSWORD_BYTES} bytes"
        )
    return value


# For a password being set: both bounds apply.
NewPassword = Annotated[
    str,
    Field(min_length=MIN_PASSWORD_LENGTH),
    AfterValidator(within_bcrypt_limit),
]

# For a password being checked against a stored hash. Only the upper bound
# applies: an account created before the minimum existed must still be able to
# log in and change its password, and a lower bound here would lock it out of
# both.
ExistingPassword = Annotated[str, AfterValidator(within_bcrypt_limit)]
