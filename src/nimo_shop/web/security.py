from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

PBKDF2_ITERATIONS = 210_000
SESSION_MAX_AGE_SECONDS = 60 * 60 * 12


class AuthError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_s, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations_s))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def sign_value(secret: str, value: str) -> str:
    if not secret:
        raise ValueError("session secret is required")
    signature = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return f"{value}.{_b64(signature)}"


def unsign_value(secret: str, signed_value: str) -> str:
    if not signed_value or "." not in signed_value:
        raise AuthError("missing signature")
    value, sig = signed_value.rsplit(".", 1)
    expected = sign_value(secret, value).rsplit(".", 1)[1]
    if not hmac.compare_digest(sig, expected):
        raise AuthError("bad signature")
    return value


@dataclass(frozen=True)
class Session:
    admin_id: int
    username: str
    role: str
    expires_at: int


def create_session(secret: str, *, admin_id: int, username: str, role: str, max_age_seconds: int = SESSION_MAX_AGE_SECONDS) -> str:
    payload = {
        "admin_id": admin_id,
        "username": username,
        "role": role,
        "exp": int(time.time()) + max_age_seconds,
        "nonce": secrets.token_urlsafe(10),
    }
    raw = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return sign_value(secret, raw)


def read_session(secret: str, token: str | None) -> Session | None:
    if not token:
        return None
    try:
        raw = unsign_value(secret, token)
        payload = json.loads(_unb64(raw).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return Session(
            admin_id=int(payload["admin_id"]),
            username=str(payload["username"]),
            role=str(payload.get("role") or "owner"),
            expires_at=int(payload["exp"]),
        )
    except Exception:
        return None


def csrf_token(secret: str, session_token: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"csrf:{session_token}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf(secret: str, session_token: str | None, provided_token: str | None) -> bool:
    if not session_token or not provided_token:
        return False
    return hmac.compare_digest(csrf_token(secret, session_token), provided_token)
