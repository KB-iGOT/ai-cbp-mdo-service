from functools import lru_cache

import requests
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from .configs import settings
from .logger import logger

CERTS_URL = f"{settings.KB_BASE_URL}/auth/realms/sunbird/protocol/openid-connect/certs"
EXPECTED_ISSUER = f"{settings.KB_BASE_URL}/auth/realms/sunbird"
REALM_URL = f"{settings.SUNBIRD_SSO_URL}realms/{settings.SUNBIRD_SSO_REALM}"

def check_iss(iss: str) -> bool:
    """Validate the token issuer against the configured SSO realm URL."""
    realm_url = REALM_URL.lower()
    logger.info(f"Validating token issuer. Expected realm URL: {realm_url}, Token iss: {iss}")
    return realm_url.lower() == iss.lower()

# Custom header scheme for iGOT token
_token_header = APIKeyHeader(name="x-authenticated-user-token", auto_error=False)


@lru_cache(maxsize=10)
def _get_public_key(kid: str) -> dict:
    """Fetch and cache the JWK for a given kid from the iGOT certs endpoint."""
    response = requests.get(CERTS_URL, timeout=10)
    response.raise_for_status()
    for key in response.json().get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


def require_role(
    required_roles: list[str]
):
    """
    FastAPI dependency that validates an iGOT JWT and enforces the required roles.
    Returns (user_id, raw_token) on success so callers can forward the token to
    downstream APIs (e.g. CBP plan create).
    Raises HTTP 401 for invalid/expired tokens and HTTP 403 for missing role.
    """
    # token is read directly from X-Authenticated-User-Token header
    async def role_checker(token: str = Security(_token_header)) -> tuple[str, str]:
        # Extract kid from unverified header
        try:
            unverified_header = jwt.get_unverified_header(token)
            logger.debug(f"Token header: {unverified_header}")
        except JWTError:
            logger.exception(f"Failed to parse token header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )

        kid = unverified_header.get("kid")
        if not kid:
            logger.exception(f"Token header missing 'kid'. Header: {unverified_header}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )

        # Fetch public key (cached by kid)
        public_key = _get_public_key(kid)
        if not public_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token validation failed.",
            )

        # Validate signature, expiry, and issuer
        try:
            decoded = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                issuer=EXPECTED_ISSUER,
                options={"verify_aud": False},
            )
        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
            )
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token validation failed: {str(e)}",
            )

        # Validate issuer against SSO config
        if not check_iss(decoded.get("iss", "")):
            logger.exception(f"Invalid token issuer: {decoded.get('iss')}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )

        # Enforce required roles
        user_roles = decoded.get("user_roles", [])
        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have the required role to make this request.",
            )

        # Extract the actual user ID from the sub claim and return with token
        raw_sub = decoded.get("sub", "")
        user_id = raw_sub.split(":")[-1]
        name = decoded.get("name", "")
        org = decoded.get("org", "")
        return user_id, token, name, org
    return role_checker