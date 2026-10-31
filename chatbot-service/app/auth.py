"""Firebase ID token verification.

Every data endpoint depends on `get_current_uid`, so the uid always comes from a
verified token, never from the request body or URL.
"""

import logging
import threading

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)
_init_lock = threading.Lock()


def get_firebase_app(settings: Settings) -> firebase_admin.App:
    """Initialise the default Firebase app once, from the service account if configured."""
    with _init_lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            pass
        if settings.google_application_credentials:
            cred = credentials.Certificate(settings.google_application_credentials)
        else:
            cred = credentials.ApplicationDefault()
        options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
        return firebase_admin.initialize_app(cred, options)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_uid(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    """Return the uid from `Authorization: Bearer <Firebase ID token>`, or 401."""
    if creds is None or not creds.credentials:
        raise _unauthorized("Missing bearer token.")
    try:
        decoded = firebase_auth.verify_id_token(creds.credentials, app=get_firebase_app(settings))
    except firebase_auth.CertificateFetchError as exc:
        logger.warning("Could not fetch Firebase public keys: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not verify the token right now.") from exc
    except (firebase_auth.InvalidIdTokenError, ValueError) as exc:
        raise _unauthorized("Invalid or expired token.") from exc

    uid = decoded.get("uid")
    if not uid:
        raise _unauthorized("Token has no uid.")
    return uid
