import pytest

from pkg.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hashing_and_verification():
    password = "MySecurePassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_token_lifecycle():
    user_payload = {"sub": "usr-12345", "email": "test@cloudtask.dev", "role": "user"}
    token = create_access_token(user_payload)

    assert isinstance(token, str)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "usr-12345"
    assert decoded["email"] == "test@cloudtask.dev"
    assert decoded["role"] == "user"
    assert "exp" in decoded


def test_invalid_jwt_token():
    with pytest.raises(ValueError):
        decode_access_token("invalid.token.structure")
