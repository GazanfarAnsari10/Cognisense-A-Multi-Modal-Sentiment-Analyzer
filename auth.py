"""Email/password authentication routes for the Cognisense platform."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import Blueprint, current_app, jsonify, make_response, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database.models import AuthLoginAttempt, AuthSession, User
from database.session import get_session_factory

logger = logging.getLogger(__name__)
auth_blueprint = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
password_hasher = PasswordHasher()
_dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CSRF_SALT = "cognisense-auth-csrf-v1"
_GENERIC_AUTH_ERROR = {"error": {"code": "invalid_credentials", "message": "Invalid email or password."}}


def _auth_secret() -> str | None:
    secret = os.getenv("AUTH_SECRET_KEY", "")
    if len(secret) < 32:
        return None
    return secret


def _settings() -> dict[str, Any]:
    try:
        session_ttl = int(os.getenv("AUTH_SESSION_TTL_SECONDS", "3600"))
        login_limit = int(os.getenv("AUTH_LOGIN_MAX_ATTEMPTS", "5"))
        login_window = int(os.getenv("AUTH_LOGIN_WINDOW_SECONDS", "900"))
    except ValueError:
        raise ValueError("Authentication duration and rate-limit settings must be integers.") from None
    if not 60 <= session_ttl <= 2_592_000 or not 1 <= login_limit <= 100 or not 60 <= login_window <= 86_400:
        raise ValueError("Authentication duration and rate-limit settings are outside supported limits.")

    same_site = os.getenv("AUTH_COOKIE_SAMESITE", "Lax").strip().capitalize()
    if same_site not in {"Lax", "Strict"}:
        raise ValueError("AUTH_COOKIE_SAMESITE must be Lax or Strict.")
    secure_setting = os.getenv("AUTH_COOKIE_SECURE", "true").strip().lower()
    if secure_setting not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("AUTH_COOKIE_SECURE must be a boolean value.")
    secure_cookie = secure_setting in {"1", "true", "yes", "on"}
    return {
        "session_ttl": session_ttl,
        "login_limit": login_limit,
        "login_window": login_window,
        "same_site": same_site,
        "secure_cookie": secure_cookie,
        "session_cookie": os.getenv("AUTH_SESSION_COOKIE_NAME", "cognisense_session"),
        "csrf_cookie": os.getenv("AUTH_CSRF_COOKIE_NAME", "cognisense_csrf"),
    }


def _session_factory():
    return current_app.config.get("DATABASE_SESSION_FACTORY") or get_session_factory()


def _service_unavailable():
    return jsonify(error={"code": "service_unavailable", "message": "Authentication service is not configured."}), 503


def _database_unavailable():
    logger.error("Authentication database operation failed.")
    return jsonify(error={"code": "service_unavailable", "message": "Authentication service is temporarily unavailable."}), 503


def _csrf_serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_CSRF_SALT)


def _new_csrf_token(secret: str) -> str:
    return _csrf_serializer(secret).dumps(secrets.token_urlsafe(32))


def _csrf_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _csrf_is_valid(secret: str, settings: dict[str, Any]) -> bool:
    cookie_token = request.cookies.get(settings["csrf_cookie"], "")
    header_token = request.headers.get("X-CSRF-Token", "")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        return False
    try:
        _csrf_serializer(secret).loads(cookie_token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return False
    return True


def _set_auth_cookies(response, settings: dict[str, Any], token: str, csrf_token: str) -> None:
    common = {
        "secure": settings["secure_cookie"],
        "samesite": settings["same_site"],
        "path": "/",
    }
    response.set_cookie(
        settings["session_cookie"],
        token,
        max_age=settings["session_ttl"],
        httponly=True,
        **common,
    )
    response.set_cookie(
        settings["csrf_cookie"],
        csrf_token,
        max_age=3600,
        httponly=False,
        **common,
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_auth_cookies(response, settings: dict[str, Any]) -> None:
    common = {
        "secure": settings["secure_cookie"],
        "samesite": settings["same_site"],
        "path": "/",
    }
    response.delete_cookie(settings["session_cookie"], httponly=True, **common)
    response.delete_cookie(settings["csrf_cookie"], httponly=False, **common)
    response.headers["Cache-Control"] = "no-store"


def _normalize_email(email: Any) -> str | None:
    if not isinstance(email, str):
        return None
    normalized = email.strip().casefold()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _valid_password(password: Any) -> bool:
    return isinstance(password, str) and 12 <= len(password) <= 128 and len(password.encode("utf-8")) <= 1024


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _bucket_key(secret: str, scope: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{scope}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _insert_bucket_if_missing(session: Session, bucket_key: str, now: datetime) -> None:
    values = {"bucket_key": bucket_key, "window_started_at": now, "failures": 0}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(AuthLoginAttempt).values(**values).on_conflict_do_nothing(
            index_elements=[AuthLoginAttempt.bucket_key]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(AuthLoginAttempt).values(**values).on_conflict_do_nothing(
            index_elements=[AuthLoginAttempt.bucket_key]
        )
    else:
        raise RuntimeError("Unsupported database dialect for login throttling.")
    session.execute(statement)


def _get_locked_buckets(session: Session, keys: list[str], now: datetime) -> list[AuthLoginAttempt]:
    for key in keys:
        _insert_bucket_if_missing(session, key, now)
    rows = list(
        session.scalars(
            select(AuthLoginAttempt)
            .where(AuthLoginAttempt.bucket_key.in_(keys))
            .order_by(AuthLoginAttempt.bucket_key)
            .with_for_update()
        )
    )
    return rows


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _password_matches(stored_hash: str | None, password: str) -> bool:
    candidate_hash = stored_hash or _dummy_password_hash
    try:
        matches = password_hasher.verify(candidate_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        matches = False
    return bool(matches and stored_hash)


def _user_response(user: User) -> dict[str, Any]:
    return {"name": user.name, "email": user.email, "status": user.status}


@auth_blueprint.get("/csrf")
def csrf_token():
    secret = _auth_secret()
    if secret is None:
        return _service_unavailable()
    try:
        settings = _settings()
    except ValueError:
        return _service_unavailable()
    token = _new_csrf_token(secret)
    session_token = request.cookies.get(settings["session_cookie"], "")
    if session_token:
        try:
            with _session_factory()() as session, session.begin():
                auth_session = _find_current_session(session, settings)
                if auth_session is not None:
                    auth_session.csrf_token_hash = _csrf_hash(token)
        except SQLAlchemyError:
            return _database_unavailable()
    response = make_response(jsonify(data={"csrf_token": token}))
    response.set_cookie(
        settings["csrf_cookie"],
        token,
        max_age=3600,
        httponly=False,
        secure=settings["secure_cookie"],
        samesite=settings["same_site"],
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_blueprint.post("/register")
def register():
    secret = _auth_secret()
    if secret is None:
        return _service_unavailable()
    try:
        settings = _settings()
    except ValueError:
        return _service_unavailable()
    if not _csrf_is_valid(secret, settings):
        return jsonify(error={"code": "csrf_failed", "message": "A valid CSRF token is required."}), 403

    payload = _json_object()
    email = _normalize_email(payload.get("email")) if payload else None
    name = payload.get("name", "").strip() if payload and isinstance(payload.get("name"), str) else ""
    password = payload.get("password") if payload else None
    if email is None or not name or len(name) > 255 or any(ord(char) < 32 for char in name) or not _valid_password(password):
        return jsonify(error={"code": "invalid_request", "message": "Name, valid email, or password is invalid."}), 400

    password_hash = password_hasher.hash(password)
    try:
        with _session_factory()() as session, session.begin():
            try:
                with session.begin_nested():
                    session.add(User(name=name, email=email, password_hash=password_hash, status="active"))
                    session.flush()
            except IntegrityError:
                pass
    except SQLAlchemyError:
        return _database_unavailable()

    return jsonify(
        success=True,
        message="If the address is eligible, the account has been created.",
    ), 202


@auth_blueprint.post("/login")
def login():
    secret = _auth_secret()
    if secret is None:
        return _service_unavailable()
    try:
        settings = _settings()
    except ValueError:
        return _service_unavailable()
    if not _csrf_is_valid(secret, settings):
        return jsonify(error={"code": "csrf_failed", "message": "A valid CSRF token is required."}), 403

    payload = _json_object()
    email = _normalize_email(payload.get("email")) if payload else None
    password = payload.get("password") if payload else None
    if email is None or not isinstance(password, str) or not password or len(password) > 128:
        return jsonify(error={"code": "invalid_request", "message": "Email or password is invalid."}), 400

    session_token = secrets.token_urlsafe(32)
    new_csrf_token = _new_csrf_token(secret)
    now = datetime.now(timezone.utc)
    remote_address = request.remote_addr or "unknown"
    bucket_keys = [
        _bucket_key(secret, "email", email),
        _bucket_key(secret, "address", remote_address),
    ]
    try:
        with _session_factory()() as session, session.begin():
            buckets = _get_locked_buckets(session, bucket_keys, now)
            for bucket in buckets:
                if _as_utc(bucket.window_started_at) + timedelta(seconds=settings["login_window"]) <= now:
                    bucket.window_started_at = now
                    bucket.failures = 0
                    bucket.blocked_until = None
            if any(bucket.blocked_until and _as_utc(bucket.blocked_until) > now for bucket in buckets):
                return jsonify(error={"code": "login_temporarily_unavailable", "message": "Login is temporarily unavailable."}), 429

            user = session.scalar(select(User).where(User.email == email))
            stored_hash = user.password_hash if user is not None else None
            password_valid = _password_matches(stored_hash, password)
            valid_user = user is not None and user.status == "active" and password_valid

            if not valid_user:
                for bucket in buckets:
                    bucket.failures += 1
                    if bucket.failures >= settings["login_limit"]:
                        bucket.blocked_until = now + timedelta(seconds=settings["login_window"])
                return jsonify(_GENERIC_AUTH_ERROR), 401

            for bucket in buckets:
                bucket.failures = 0
                bucket.blocked_until = None
            session.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=hashlib.sha256(session_token.encode("utf-8")).hexdigest(),
                    csrf_token_hash=_csrf_hash(new_csrf_token),
                    expires_at=now + timedelta(seconds=settings["session_ttl"]),
                )
            )
            response = make_response(jsonify(success=True, data={"user": _user_response(user)}))
    except SQLAlchemyError:
        return _database_unavailable()

    _set_auth_cookies(response, settings, session_token, new_csrf_token)
    return response


def _find_current_session(session: Session, settings: dict[str, Any]) -> AuthSession | None:
    token = request.cookies.get(settings["session_cookie"], "")
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    return auth_session


def get_authenticated_session(session: Session, settings: dict[str, Any]) -> AuthSession | None:
    auth_session = _find_current_session(session, settings)
    if auth_session is None or auth_session.user.status != "active":
        return None
    return auth_session


def validate_session_csrf(
    auth_session: AuthSession,
    secret: str,
    settings: dict[str, Any],
) -> bool:
    cookie_token = request.cookies.get(settings["csrf_cookie"], "")
    return (
        _csrf_is_valid(secret, settings)
        and hmac.compare_digest(auth_session.csrf_token_hash, _csrf_hash(cookie_token))
    )


@auth_blueprint.get("/me")
def me():
    secret = _auth_secret()
    if secret is None:
        return _service_unavailable()
    try:
        settings = _settings()
        with _session_factory()() as session:
            auth_session = get_authenticated_session(session, settings)
            if auth_session is None:
                return jsonify(error={"code": "unauthorized", "message": "Authentication is required."}), 401
            response = make_response(jsonify(success=True, data={"user": _user_response(auth_session.user)}))
    except SQLAlchemyError:
        return _database_unavailable()
    except ValueError:
        return _service_unavailable()
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_blueprint.post("/logout")
def logout():
    secret = _auth_secret()
    if secret is None:
        return _service_unavailable()
    try:
        settings = _settings()
    except ValueError:
        return _service_unavailable()
    try:
        with _session_factory()() as session, session.begin():
            auth_session = _find_current_session(session, settings)
            if auth_session is None:
                response = make_response(jsonify(error={"code": "unauthorized", "message": "Authentication is required."}), 401)
            elif not validate_session_csrf(auth_session, secret, settings):
                return jsonify(error={"code": "csrf_failed", "message": "A valid CSRF token is required."}), 403
            else:
                auth_session.revoked_at = datetime.now(timezone.utc)
                response = make_response(jsonify(success=True, message="Logged out."))
    except SQLAlchemyError:
        return _database_unavailable()

    _clear_auth_cookies(response, settings)
    return response


__all__ = ["auth_blueprint"]
