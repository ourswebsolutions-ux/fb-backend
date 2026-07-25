import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings


def _get_fernet() -> Fernet:
    raw_key = (settings.encryption_key or "default_fb_automation_fernet_key").strip()
    try:
        # Check if raw_key is already a valid 32-byte url-safe base64 Fernet key
        key_bytes = raw_key.encode()
        return Fernet(key_bytes)
    except Exception:
        # Derive a valid 32-byte url-safe base64 key from raw_key string using SHA-256
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest())
        return Fernet(derived_key)


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password for DB storage."""
    if not plaintext:
        return ""
    # If already encrypted, don't re-encrypt
    if plaintext.startswith("gAAAAA"):
        return plaintext
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a stored password for use in automation."""
    if not encrypted:
        return ""
    # If not encrypted (plaintext password), return as-is
    if not encrypted.startswith("gAAAAA"):
        return encrypted
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Invalid encryption token or key mismatch")

