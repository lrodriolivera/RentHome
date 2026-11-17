"""Cifrado en reposo con AES-256-GCM.

De la clave maestra (RENTHOME_SECRET_KEY) se deriva con HKDF una clave distinta por
usuario y uso, de modo que el contenido de un usuario no se descifra con la clave de otro.
"""
from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12
KEY_BYTES = 32


class DecryptionError(Exception):
    """El contenido está dañado o la clave no corresponde."""


def _derive_key(master: str, user_id: int, purpose: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=b"renthome-v1",
                info=f"{purpose}:{user_id}".encode())
    return hkdf.derive(master.encode())


def encrypt(master: str, user_id: int, purpose: str, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_derive_key(master, user_id, purpose)).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt(master: str, user_id: int, purpose: str, blob: bytes) -> bytes:
    try:
        nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        return AESGCM(_derive_key(master, user_id, purpose)).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("No se pudo descifrar el contenido") from exc
