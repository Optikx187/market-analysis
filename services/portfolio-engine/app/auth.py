"""Optional JWT authentication for multi-user support.

Enable by setting AUTH_ENABLED=true and JWT_SECRET to a strong secret.
When disabled, all requests are treated as belonging to the default user.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar, Token
from typing import Optional

from fastapi import HTTPException, Request

from app.config import settings


DEFAULT_USER_KEY = "default"
DEFAULT_JWT_SECRET = "change-me-in-production"
MINIMUM_JWT_SECRET_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LENGTH = 32
SCRYPT_SALT_LENGTH = 16
DUMMY_PASSWORD_HASH = (
    "scrypt$16384$8$1$000102030405060708090a0b0c0d0e0f$"
    "6564ee0d8d0e201312b33983f0f00b39f9c1706827d5ac221ce7329822bf314e"
)
_current_user_key: ContextVar[str] = ContextVar("portfolio_user_key", default=DEFAULT_USER_KEY)


def current_user_key() -> str:
    return _current_user_key.get()


def set_current_user_key(user_key: str) -> Token[str]:
    return _current_user_key.set(user_key)


def reset_current_user_key(token: Token[str]) -> None:
    _current_user_key.reset(token)


def validate_auth_configuration() -> None:
    if not settings.AUTH_ENABLED:
        return
    secret = settings.JWT_SECRET.strip()
    if (
        secret == DEFAULT_JWT_SECRET
        or len(secret.encode()) < MINIMUM_JWT_SECRET_BYTES
        or len(set(secret)) < 8
    ):
        raise RuntimeError(
            "AUTH_ENABLED requires JWT_SECRET with at least 32 bytes of non-default secret material"
        )
    if settings.JWT_ALGORITHM != "HS256":
        raise RuntimeError("JWT_ALGORITHM must be HS256")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid token encoding") from exc


def create_token(user_id: str, username: str) -> str:
    now = int(time.time())
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + settings.JWT_EXPIRY_HOURS * 3600,
    }).encode())
    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(settings.JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


def decode_token(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid token") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise ValueError("invalid token header")
    if not isinstance(payload, dict):
        raise ValueError("invalid token claims")

    sig_input = f"{parts[0]}.{parts[1]}".encode()
    expected_sig = hmac.new(settings.JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(parts[2])
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("invalid signature")

    subject = payload.get("sub")
    username = payload.get("username")
    expires_at = payload.get("exp")
    issued_at = payload.get("iat")
    if not isinstance(subject, str) or not subject:
        raise ValueError("invalid token subject")
    if not isinstance(username, str) or not username:
        raise ValueError("invalid token username")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        raise ValueError("invalid token expiration")
    if expires_at <= time.time():
        raise ValueError("token expired")
    if issued_at is not None:
        if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
            raise ValueError("invalid token issue time")
        if issued_at > time.time() + 60:
            raise ValueError("token issued in the future")
    return payload


def get_current_user(request: Request) -> Optional[str]:
    """Extract user_id from JWT if auth is enabled, else return None."""
    if not settings.AUTH_ENABLED:
        return None
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    try:
        payload = decode_token(auth_header[7:])
        return str(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, str(exc)) from exc


def _derive_scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_KEY_LENGTH,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SCRYPT_SALT_LENGTH)
    derived_key = _derive_scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "$".join((
        "scrypt",
        str(SCRYPT_N),
        str(SCRYPT_R),
        str(SCRYPT_P),
        salt.hex(),
        derived_key.hex(),
    ))


def _verify_scrypt(password: str, hashed: str) -> bool:
    try:
        scheme, n_value, r_value, p_value, salt_value, digest_value = hashed.split("$")
        n = int(n_value)
        r = int(r_value)
        p = int(p_value)
        salt = bytes.fromhex(salt_value)
        expected = bytes.fromhex(digest_value)
    except (TypeError, ValueError):
        return False
    if (
        scheme != "scrypt"
        or n < SCRYPT_N
        or n > 2**18
        or n & (n - 1)
        or r <= 0
        or r > 16
        or p <= 0
        or p > 4
        or len(salt) < SCRYPT_SALT_LENGTH
        or len(expected) != SCRYPT_KEY_LENGTH
    ):
        return False
    try:
        actual = _derive_scrypt(password, salt, n, r, p)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def _is_legacy_sha256(hashed: str) -> bool:
    if len(hashed) != 64:
        return False
    try:
        bytes.fromhex(hashed)
    except ValueError:
        return False
    return True


def verify_password(password: str, hashed: str) -> bool:
    if hashed.startswith("scrypt$"):
        return _verify_scrypt(password, hashed)
    if _is_legacy_sha256(hashed):
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        matches = hmac.compare_digest(legacy_hash, hashed)
        _verify_scrypt(password, DUMMY_PASSWORD_HASH)
        return matches
    return False


def password_needs_rehash(hashed: str) -> bool:
    if not hashed.startswith("scrypt$"):
        return True
    try:
        scheme, n_value, r_value, p_value, _, _ = hashed.split("$")
        return (
            scheme != "scrypt"
            or int(n_value) != SCRYPT_N
            or int(r_value) != SCRYPT_R
            or int(p_value) != SCRYPT_P
        )
    except ValueError:
        return True
