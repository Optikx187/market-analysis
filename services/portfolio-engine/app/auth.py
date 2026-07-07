"""Optional JWT authentication for multi-user support.

Enable by setting AUTH_ENABLED=true and JWT_SECRET to a strong secret.
When disabled, all requests are treated as belonging to the default user.
"""

import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request

from app.config import settings


def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def create_token(user_id: str, username: str) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub": user_id,
        "username": username,
        "exp": int(time.time()) + settings.JWT_EXPIRY_HOURS * 3600,
    }).encode())
    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(settings.JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


def decode_token(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid token")
    sig_input = f"{parts[0]}.{parts[1]}".encode()
    expected_sig = hmac.new(settings.JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(parts[2])
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("invalid signature")
    payload = json.loads(_b64url_decode(parts[1]))
    if payload.get("exp", 0) < time.time():
        raise ValueError("token expired")
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
        return payload["sub"]
    except ValueError as e:
        raise HTTPException(401, str(e))


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)
