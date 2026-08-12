import hashlib
import hmac
import secrets

from app.application.ports import PasswordService
from app.config import MAX_PBKDF2_ITERATIONS, MIN_PBKDF2_ITERATIONS, settings
from app.dto.identity import (
    AccessDecision,
    PasswordHash,
    PasswordHashRequest,
    PasswordVerification,
)


class Pbkdf2PasswordService(PasswordService):
    def __init__(self, iterations: int = settings.pbkdf2_iterations) -> None:
        self._iterations = iterations

    def hash(self, request: PasswordHashRequest) -> PasswordHash:
        password = request.password.get_secret_value()
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self._iterations,
        )
        return PasswordHash(f"pbkdf2_sha256${self._iterations}${salt.hex()}${digest.hex()}")

    def verify(self, request: PasswordVerification) -> AccessDecision:
        try:
            algorithm, iterations, salt_hex, digest_hex = request.stored_hash.split("$")
            iteration_count = int(iterations)
            if algorithm != "pbkdf2_sha256":
                return AccessDecision(False)
            if not MIN_PBKDF2_ITERATIONS <= iteration_count <= MAX_PBKDF2_ITERATIONS:
                return AccessDecision(False)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                request.password.get_secret_value().encode("utf-8"),
                bytes.fromhex(salt_hex),
                iteration_count,
            )
        except (AttributeError, ValueError):
            return AccessDecision(False)
        return AccessDecision(hmac.compare_digest(digest.hex(), digest_hex))
