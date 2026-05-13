"""Security module: JWT token creation/verification, password hashing."""
import pytest
from datetime import timedelta

from app.core.security import create_access_token, verify_password, get_password_hash
from app.core.config import settings


# ══════════════════════════════════════════════════════════════════════
# JWT token
# ══════════════════════════════════════════════════════════════════════

class TestCreateAccessToken:
    def test_creates_token_string(self):
        token = create_access_token(subject=42)
        assert isinstance(token, str)
        assert len(token) > 20
        # JWT has 3 dot-separated parts
        assert token.count(".") == 2

    def test_subject_in_token(self):
        """Decoded token contains the subject."""
        from jose import jwt
        token = create_access_token(subject="user@test.com")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert payload["sub"] == "user@test.com"

    def test_token_has_expiration(self):
        from jose import jwt
        token = create_access_token(subject=1, expires_delta=timedelta(minutes=30))
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert "exp" in payload
        assert "iat" in payload

    def test_default_expiration(self):
        """Without explicit delta, uses ACCESS_TOKEN_EXPIRE_MINUTES."""
        from jose import jwt
        token = create_access_token(subject=1)
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert payload["exp"] > payload["iat"]

    def test_expired_token_rejected(self):
        """Token with past expiration should fail verification."""
        from jose import jwt, JWTError
        token = create_access_token(subject=1, expires_delta=timedelta(seconds=-1))
        with pytest.raises(JWTError):
            jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"],
                       options={"verify_exp": True})

    def test_int_subject_converted_to_str(self):
        from jose import jwt
        token = create_access_token(subject=42)
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        assert payload["sub"] == "42"

    def test_tampered_token_rejected(self):
        """Modified token should fail signature verification."""
        from jose import jwt, JWTError
        token = create_access_token(subject=1)
        # Change last char of payload (before signature)
        parts = token.split(".")
        parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        tampered = ".".join(parts)
        with pytest.raises(JWTError):
            jwt.decode(tampered, settings.SECRET_KEY, algorithms=["HS256"])


# ══════════════════════════════════════════════════════════════════════
# Password hashing
# ══════════════════════════════════════════════════════════════════════

class TestPasswordHashing:
    def test_hash_produces_different_values(self):
        """Same password hashed twice produces different hashes (salt)."""
        h1 = get_password_hash("test_password")
        h2 = get_password_hash("test_password")
        assert h1 != h2

    def test_verify_correct_password(self):
        hashed = get_password_hash("my_secure_password")
        assert verify_password("my_secure_password", hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_empty_password(self):
        hashed = get_password_hash("some_password")
        assert verify_password("", hashed) is False

    def test_hash_not_plaintext(self):
        """Hash must not contain the original password."""
        pw = "secret123"
        hashed = get_password_hash(pw)
        assert pw not in hashed

    def test_hash_starts_with_bcrypt_prefix(self):
        """Bcrypt hashes start with $2b$."""
        hashed = get_password_hash("anything")
        assert hashed.startswith("$2b$")

    def test_verify_unicode_password(self):
        """Unicode passwords work correctly."""
        pw = "密码123!@#"
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True

    def test_long_password(self):
        """bcrypt truncates at 72 bytes — verify still works."""
        pw = "a" * 100
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True


# ══════════════════════════════════════════════════════════════════════
# Secret key validation
# ══════════════════════════════════════════════════════════════════════

class TestSecretKeyConfig:
    def test_secret_key_is_configured(self):
        """SECRET_KEY must be set (not empty) for production safety."""
        assert len(settings.SECRET_KEY) > 0, (
            "SECRET_KEY is empty — JWT tokens cannot be signed securely"
        )

    def test_secret_key_minimum_length(self):
        """SECRET_KEY should be at least 32 chars for HS256."""
        assert len(settings.SECRET_KEY) >= 32, (
            f"SECRET_KEY is only {len(settings.SECRET_KEY)} chars, need >= 32 for HS256"
        )
