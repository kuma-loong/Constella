from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from constella_lab.auth import CloudflareAccessVerifier
from constella_lab.config import LabConfig


def test_cloudflare_verifier_checks_signature_issuer_audience_and_type(monkeypatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    verifier = CloudflareAccessVerifier(
        team_domain="https://team.cloudflareaccess.com",
        audience="app-aud",
    )
    monkeypatch.setattr(
        verifier._jwks,
        "get_signing_key_from_jwt",
        lambda _token: SimpleNamespace(key=public_key),
    )
    now = int(time.time())
    claims = {
        "iss": "https://team.cloudflareaccess.com",
        "sub": "subject-1",
        "email": "member@example.com",
        "aud": ["app-aud"],
        "iat": now,
        "nbf": now - 1,
        "exp": now + 3600,
        "type": "app",
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})

    identity = asyncio.run(verifier.verify(token))

    assert identity.subject == "subject-1"
    assert identity.email == "member@example.com"

    wrong_type = jwt.encode(
        {**claims, "type": "service"},
        private_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    with pytest.raises(jwt.InvalidTokenError):
        asyncio.run(verifier.verify(wrong_type))


def test_lab_config_fails_closed_and_reads_required_values(tmp_path, monkeypatch) -> None:
    for name in (
        "CONSTELLA_AUTH_MODE",
        "CONSTELLA_LAB_DB_PATH",
        "CONSTELLA_ACCESS_TEAM_DOMAIN",
        "CONSTELLA_ACCESS_AUD",
        "CONSTELLA_BOOTSTRAP_ADMIN_EMAIL",
        "CONSTELLA_PUBLIC_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        LabConfig.from_env()

    monkeypatch.setenv("CONSTELLA_AUTH_MODE", "cloudflare-access")
    monkeypatch.setenv("CONSTELLA_LAB_DB_PATH", str(tmp_path / "identity.sqlite3"))
    monkeypatch.setenv(
        "CONSTELLA_ACCESS_TEAM_DOMAIN", "https://team.cloudflareaccess.com"
    )
    monkeypatch.setenv("CONSTELLA_ACCESS_AUD", "app-aud")
    monkeypatch.setenv("CONSTELLA_BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("CONSTELLA_PUBLIC_ORIGIN", "https://gpu.example.com")

    config = LabConfig.from_env()

    assert config.db_path == tmp_path / "identity.sqlite3"
    assert config.account_uid_min == 1000

    monkeypatch.setenv("CONSTELLA_PUBLIC_ORIGIN", "https://gpu.example.com/unsafe")
    with pytest.raises(ValueError, match="HTTPS origin"):
        LabConfig.from_env()
