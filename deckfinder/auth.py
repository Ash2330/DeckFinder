import os

import jwt
from fastapi import Header, HTTPException

from .settings import REGION

POOL = os.environ["COGNITO_POOL"]
CLIENT = os.environ["COGNITO_CLIENT"]
DOMAIN = os.environ["COGNITO_DOMAIN"].rstrip("/")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", timeout=5)


def current_user(authorization: str = Header(default="")) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in to continue.")
    try:
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
        if claims.get("token_use") != "access" or claims.get("client_id") != CLIENT:
            raise jwt.InvalidTokenError("Unexpected token audience or type")
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Your sign-in has expired or is invalid.") from exc
    return {"id": claims["sub"], "teacher": "teachers" in claims.get("cognito:groups", [])}
