import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

PREFIX = "enc:"


def _fernet() -> Fernet:
    raw = getattr(settings, "DOCKRA_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Decryption failed; DOCKRA_ENCRYPTION_KEY or DJANGO_SECRET_KEY has changed") from exc


class EncryptedTextField(models.TextField):
    """Encrypt text stored in the database. Legacy values without the prefix are read as-is."""

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value is not None else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value is not None else value
