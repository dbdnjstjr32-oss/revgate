"""Tokens, hashes, OTP, cookies, masking (blueprint §3 / §4 security flows)."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from typing import Any

from fastapi import Response

from .config import Settings

INTERNAL_COOKIE = "rg_sid"
EXTERNAL_COOKIE = "rg_ext_sid"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_token() -> str:
    """32 random bytes, URL-safe (share links, sessions, magic links)."""
    return secrets.token_urlsafe(32)


def sha256_hex(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def canonical_sha256(payload: dict[str, Any]) -> str:
    return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False))


def new_otp_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def otp_hash(settings: Settings, otp_session_id: str, code: str) -> str:
    """HMAC-SHA256(6-digit OTP) bound to its otp_session row."""
    return hmac.new(settings.otp_hmac_secret.encode(), f"{otp_session_id}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(settings: Settings, otp_session_id: str, code: str, expected_hash: str) -> bool:
    return hmac.compare_digest(otp_hash(settings, otp_session_id, code), expected_hash)


def ip_hash(settings: Settings, ip: str | None) -> str | None:
    return sha256_hex(f"{settings.ip_hash_salt}:{ip}") if ip else None


def mask_contact(contact: str) -> str:
    c = contact.strip()
    if "@" in c:
        local, _, domain = c.partition("@")
        return f"{local[:1]}{'*' * max(2, len(local) - 1)}@{domain}"
    digits = re.sub(r"\D", "", c)
    if len(digits) >= 4:
        return f"{'*' * (len(digits) - 4)}{digits[-4:]}"
    return "*" * len(c)


def set_cookie(response: Response, name: str, value: str, max_age: int, settings: Settings) -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")


def clear_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")


def is_rigid_transform(t: list[float], tol: float = 1e-6) -> bool:
    """Row-major 4x4: orthonormal rotation with det +1 and last row [0, 0, 0, 1]."""
    if len(t) != 16 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in t):
        return False
    r = [[t[0], t[1], t[2]], [t[4], t[5], t[6]], [t[8], t[9], t[10]]]
    for i in range(3):
        for j in range(3):
            dot = sum(r[k][i] * r[k][j] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > tol:
                return False
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1]) - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    return abs(det - 1.0) <= tol and all(abs(a - b) <= tol for a, b in zip(t[12:], (0.0, 0.0, 0.0, 1.0)))
