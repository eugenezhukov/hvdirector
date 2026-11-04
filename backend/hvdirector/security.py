"""Password hashing, secret encryption and opaque tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_config

_hasher = PasswordHasher()

ENC_PREFIX = "enc:v1:"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> str | None:
    """Return an error code or None."""
    if len(password) < 8:
        return "password_too_short"
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 2:
        return "password_too_simple"
    return None


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = get_config().secret_key.encode()
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        # Arbitrary passphrase: derive a Fernet key from it.
        derived = hashlib.sha256(b"hvdirector-secret-v1:" + key).digest()
        return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plain: str | None) -> str:
    if plain is None:
        plain = ""
    return ENC_PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        # Plain value (e.g. inserted manually into the database).
        return value
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # wrong master key
        raise RuntimeError("Cannot decrypt stored secret: HVD_SECRET_KEY does not match") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
