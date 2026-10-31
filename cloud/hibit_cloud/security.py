from __future__ import annotations

import hashlib
import hmac
import secrets

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


def normalize_enrollment_key(value: str) -> str:
    compact = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    if compact.startswith("HB"):
        compact = compact[2:]
    if len(compact) != 16:
        return ""
    groups = [compact[i : i + 4] for i in range(0, 16, 4)]
    return "HB-" + "-".join(groups)
