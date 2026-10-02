import base64
import binascii
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


CIPHERTEXT_PREFIX = "fernet$"


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialDecryptionError(ValueError):
    pass


@dataclass(frozen=True)
class DecryptedCredential:
    value: str
    needs_rotation: bool


class CredentialCipher:
    def __init__(self, configured_keys: str):
        key_values = [value.strip() for value in configured_keys.split(",") if value.strip()]
        if not key_values:
            raise CredentialConfigurationError(
                "CREDENTIAL_ENCRYPTION_KEYS must contain at least one Fernet key"
            )
        try:
            self._fernets = [Fernet(value.encode()) for value in key_values]
        except (TypeError, ValueError) as exc:
            raise CredentialConfigurationError(
                "CREDENTIAL_ENCRYPTION_KEYS contains an invalid Fernet key"
            ) from exc

    def encrypt(self, value: str) -> str:
        token = self._fernets[0].encrypt(value.encode()).decode()
        return f"{CIPHERTEXT_PREFIX}{token}"

    def decrypt(self, stored_value: str) -> DecryptedCredential:
        if stored_value.startswith(CIPHERTEXT_PREFIX):
            token = stored_value.removeprefix(CIPHERTEXT_PREFIX).encode()
            for index, fernet in enumerate(self._fernets):
                try:
                    plaintext = fernet.decrypt(token).decode()
                except (InvalidToken, UnicodeDecodeError):
                    continue
                return DecryptedCredential(plaintext, needs_rotation=index != 0)
            raise CredentialDecryptionError("Credential ciphertext cannot be decrypted")

        return DecryptedCredential(_decode_legacy_base64(stored_value), needs_rotation=True)


def _decode_legacy_base64(stored_value: str) -> str:
    try:
        decoded = base64.b64decode(
            stored_value.encode(),
            altchars=b"-_",
            validate=True,
        )
        plaintext = decoded.decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CredentialDecryptionError("Legacy credential value is invalid") from exc

    if base64.urlsafe_b64encode(decoded).decode() != stored_value:
        raise CredentialDecryptionError("Legacy credential value is not canonical Base64")
    return plaintext
