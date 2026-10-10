import pytest
from cryptography.exceptions import InvalidTag

from src.crypto import decrypt_db_url, encrypt_db_url

URL = "postgresql://user:pass@host:5432/db"


def test_roundtrip():
    assert decrypt_db_url(encrypt_db_url(URL, "s3cret"), "s3cret") == URL


def test_nonce_is_random():
    assert encrypt_db_url(URL, "s3cret") != encrypt_db_url(URL, "s3cret")


def test_wrong_secret_rejected():
    with pytest.raises(InvalidTag):
        decrypt_db_url(encrypt_db_url(URL, "right"), "wrong")


def test_tampered_token_rejected():
    token = encrypt_db_url(URL, "s3cret")
    bad = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(Exception):
        decrypt_db_url(bad, "s3cret")


def test_short_token_rejected():
    with pytest.raises(ValueError):
        decrypt_db_url("YWJj", "s3cret")
