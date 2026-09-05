from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class LabConfig:
    db_path: Path
    access_team_domain: str
    access_audience: str
    bootstrap_admin_email: str
    public_origin: str
    account_uid_min: int = 1000
    account_uid_max: int = 2_147_483_647
    account_deny_users: frozenset[str] = frozenset({"root", "constella"})
    account_username_pattern: str = r"^[a-z_][a-z0-9_-]{0,31}$"
    account_lookup_timeout: float = 5.0

    @classmethod
    def from_env(cls) -> LabConfig:
        auth_mode = os.environ.get("CONSTELLA_AUTH_MODE", "").strip()
        if auth_mode != "cloudflare-access":
            raise ValueError("CONSTELLA_AUTH_MODE must be cloudflare-access for Constella Lab")
        required = {
            "CONSTELLA_LAB_DB_PATH": os.environ.get("CONSTELLA_LAB_DB_PATH"),
            "CONSTELLA_ACCESS_TEAM_DOMAIN": os.environ.get("CONSTELLA_ACCESS_TEAM_DOMAIN"),
            "CONSTELLA_ACCESS_AUD": os.environ.get("CONSTELLA_ACCESS_AUD"),
            "CONSTELLA_BOOTSTRAP_ADMIN_EMAIL": os.environ.get(
                "CONSTELLA_BOOTSTRAP_ADMIN_EMAIL"
            ),
            "CONSTELLA_PUBLIC_ORIGIN": os.environ.get("CONSTELLA_PUBLIC_ORIGIN"),
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if missing:
            raise ValueError(f"missing required Lab configuration: {', '.join(missing)}")

        team_domain = _https_origin(
            str(required["CONSTELLA_ACCESS_TEAM_DOMAIN"]),
            name="CONSTELLA_ACCESS_TEAM_DOMAIN",
        )
        public_origin = _https_origin(
            str(required["CONSTELLA_PUBLIC_ORIGIN"]),
            name="CONSTELLA_PUBLIC_ORIGIN",
        )
        username_pattern = os.environ.get(
            "CONSTELLA_ACCOUNT_USERNAME_PATTERN", r"^[a-z_][a-z0-9_-]{0,31}$"
        )
        re.compile(username_pattern)
        deny_users = frozenset(
            item.strip()
            for item in os.environ.get("CONSTELLA_ACCOUNT_DENY_USERS", "root,constella").split(",")
            if item.strip()
        )
        return cls(
            db_path=Path(str(required["CONSTELLA_LAB_DB_PATH"])),
            access_team_domain=team_domain,
            access_audience=str(required["CONSTELLA_ACCESS_AUD"]).strip(),
            bootstrap_admin_email=str(required["CONSTELLA_BOOTSTRAP_ADMIN_EMAIL"]).strip(),
            public_origin=public_origin,
            account_uid_min=int(os.environ.get("CONSTELLA_ACCOUNT_UID_MIN", "1000")),
            account_uid_max=int(
                os.environ.get("CONSTELLA_ACCOUNT_UID_MAX", "2147483647")
            ),
            account_deny_users=deny_users,
            account_username_pattern=username_pattern,
            account_lookup_timeout=float(
                os.environ.get("CONSTELLA_ACCOUNT_LOOKUP_TIMEOUT", "5")
            ),
        )


def _https_origin(value: str, *, name: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTPS origin without a path")
    return f"https://{parsed.netloc.lower()}"
