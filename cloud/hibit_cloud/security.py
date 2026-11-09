from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

ITERATIONS = 200_000
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), rounds).hex()
        return hmac.compare_digest(check, digest)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hashes_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left or "", right or "")


def new_enrollment_key() -> str:
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "HB-" + "-".join(groups)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, moment: float | None = None, step: int = 30) -> str:
    padded = secret.strip().upper()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((time.time() if moment is None else moment) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def totp_valid(secret: str, code: str, drift: int = 1) -> bool:
    cleaned = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(cleaned) != 6 or not secret:
        return False
    now = time.time()
    try:
        for offset in range(-drift, drift + 1):
            if hmac.compare_digest(totp_code(secret, now + offset * 30), cleaned):
                return True
    except (ValueError, TypeError):
        return False
    return False


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(8):
        raw = "".join(secrets.choice(KEY_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def recovery_hash(code: str) -> str:
    compact = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def otpauth_uri(secret: str, email: str) -> str:
    from urllib.parse import quote

    label = quote(f"HiBit Signage:{email}")
    issuer = quote("HiBit Signage")
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&digits=6&period=30"


def normalize_enrollment_key(value: str) -> str:
    compact = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    if compact.startswith("HB"):
        compact = compact[2:]
    if len(compact) != 16:
        return ""
    groups = [compact[i : i + 4] for i in range(0, 16, 4)]
    return "HB-" + "-".join(groups)
