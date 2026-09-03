"""Local verification of the caller's Keycloak bearer token.

This sits *in front of*, not instead of, the Control Plane call in
`require_entitled`. Keycloak authenticates, OpenFGA authorizes -- no
exceptions: this module answers only "is this a genuine, unexpired,
correctly-signed token", using Keycloak's own public JWKS, so a garbage or
expired bearer fails fast before the network round trip to the Control Plane
even happens. It never answers "may this team use this app" -- Fred
deliberately strips permission data out of the token, so that question has
no local answer and stays entirely with the Control Plane call this module
runs ahead of.
"""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from fastapi import HTTPException

KEYCLOAK_REALM_URL = os.environ.get("KEYCLOAK_REALM_URL", "")
KEYCLOAK_ISSUER = os.environ.get("KEYCLOAK_ISSUER") or KEYCLOAK_REALM_URL
ALGORITHMS = ["RS256"]


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    """Built once and reused across requests; PyJWKClient caches keys itself."""

    return jwt.PyJWKClient(f"{KEYCLOAK_REALM_URL}/protocol/openid-connect/certs")


def verify_bearer(authorization: str | None) -> str:
    """Verify the bearer's RS256 signature, expiry and issuer; return its `sub`.

    Fails closed: a missing/malformed header, missing configuration, or any
    verification error all raise rather than letting the caller through.
    """

    if not authorization or " " not in authorization:
        raise HTTPException(status_code=401, detail="missing_bearer")
    if not KEYCLOAK_REALM_URL:
        raise HTTPException(status_code=403, detail="jwt_verification_unconfigured")
    token = authorization.split(" ", 1)[1]

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError:
        raise HTTPException(status_code=401, detail="unknown_signing_key")
    except jwt.DecodeError:
        raise HTTPException(status_code=401, detail="malformed_token")

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            issuer=KEYCLOAK_ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="expired_token")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="bad_issuer")
    except jwt.InvalidSignatureError:
        raise HTTPException(status_code=401, detail="invalid_signature")
    except jwt.MissingRequiredClaimError:
        raise HTTPException(status_code=401, detail="missing_required_claim")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid_token")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="missing_sub")
    return sub
