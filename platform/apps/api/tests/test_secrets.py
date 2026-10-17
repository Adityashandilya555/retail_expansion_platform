import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from zm_api.secrets import seal, unseal

DATA = {
    "zm_api_dsn": "postgresql://zm_api:p@db:5432/postgres",
    "zm_engine_dsn": "postgresql://zm_engine:p@db:5432/postgres",
    "service_account_password": "s3cret-ü",
    "service_role_key": "sb_secret_x",
}


@pytest.fixture(autouse=True)
def key(monkeypatch) -> str:
    value = Fernet.generate_key().decode()
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", value)
    return value


def test_round_trip_is_exact() -> None:
    assert unseal(seal(DATA)) == DATA


def test_sealed_bytes_are_opaque() -> None:
    sealed = seal(DATA)
    assert b"zm_api" not in sealed and b"s3cret" not in sealed
    with pytest.raises(json.JSONDecodeError):
        json.loads(sealed)


def test_wrong_key_cannot_open(monkeypatch) -> None:
    sealed = seal(DATA)
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        unseal(sealed)


def test_missing_key_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY")
    with pytest.raises(RuntimeError, match="SECRETS_ENCRYPTION_KEY"):
        seal(DATA)
