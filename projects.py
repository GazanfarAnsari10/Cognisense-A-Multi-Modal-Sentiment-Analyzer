"""Authenticated project management routes."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, request
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from auth import (
    _auth_secret,
    _settings,
    get_authenticated_session,
    validate_session_csrf,
)
from database.models import ApiKey, AuthSession, Project, User
from database.session import get_session_factory

logger = logging.getLogger(__name__)
projects_blueprint = Blueprint("projects", __name__, url_prefix="/api/v1/projects")
DEFAULT_PROJECT_LIMIT = 20
DEFAULT_ACTIVE_KEY_LIMIT = 10
MAX_KEY_EXPIRY_DAYS = 365
API_KEY_PREFIX = "cgs_live_"
MAX_PROJECT_ID_ATTEMPTS = 3


def _session_factory():
    return current_app.config.get("DATABASE_SESSION_FACTORY") or get_session_factory()


def _error(code: str, message: str, status: int):
    return jsonify(error={"code": code, "message": message}), status


def _service_unavailable():
    logger.error("Project database operation failed.")
    return _error("service_unavailable", "Project service is temporarily unavailable.", 503)


def _project_limit() -> int:
    configured = current_app.config.get(
        "PROJECT_MAX_PER_USER",
        int(os.getenv("COGNISENSE_MAX_PROJECTS_PER_USER", str(DEFAULT_PROJECT_LIMIT))),
    )
    if not isinstance(configured, int) or isinstance(configured, bool) or configured < 1:
        raise ValueError("PROJECT_MAX_PER_USER must be a positive integer.")
    return configured


def _active_key_limit() -> int:
    configured = current_app.config.get("MAX_ACTIVE_KEYS_PER_PROJECT")
    if configured is None:
        try:
            configured = int(os.getenv("COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT", str(DEFAULT_ACTIVE_KEY_LIMIT)))
        except ValueError:
            raise ValueError("COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT must be a positive integer.") from None
    if not isinstance(configured, int) or isinstance(configured, bool) or configured < 1:
        raise ValueError("COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT must be a positive integer.")
    return configured


def _normalize_project_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > 100 or any(unicodedata.category(char) == "Cc" for char in name):
        return None
    return name


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _serialize_project(project: Project) -> dict[str, Any]:
    return {
        "project_id": project.public_project_id,
        "name": project.display_name,
        "status": project.status,
        "created_at": _timestamp(project.created_at),
        "updated_at": _timestamp(project.updated_at),
    }


def _serialize_api_key(api_key: ApiKey) -> dict[str, Any]:
    return {
        "key_id": api_key.id,
        "prefix": api_key.key_prefix,
        "created_at": _timestamp(api_key.created_at),
        "expires_at": _timestamp(api_key.expires_at) if api_key.expires_at is not None else None,
        "last_used_at": _timestamp(api_key.last_used_at) if api_key.last_used_at is not None else None,
        "status": api_key.status,
        "revoked_at": _timestamp(api_key.revoked_at) if api_key.revoked_at is not None else None,
        "is_revoked": api_key.revoked_at is not None or api_key.status == "revoked",
    }


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _resolve_request_session(db_session):
    secret = _auth_secret()
    if secret is None:
        return None, None, _error(
            "service_unavailable",
            "Authentication service is not configured.",
            503,
        )
    try:
        settings = _settings()
    except ValueError:
        return None, None, _error(
            "service_unavailable",
            "Authentication service is not configured.",
            503,
        )
    auth_session = get_authenticated_session(db_session, settings)
    if auth_session is None:
        return None, None, _error("unauthorized", "Authentication is required.", 401)
    return secret, (settings, auth_session), None


def _require_csrf(secret: str, settings: dict[str, Any], auth_session: AuthSession):
    if not validate_session_csrf(auth_session, secret, settings):
        return _error("csrf_failed", "A valid CSRF token is required.", 403)
    return None


@projects_blueprint.post("")
def create_project():
    try:
        limit = _project_limit()
        with _session_factory()() as session, session.begin():
            secret, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            settings, auth_session = context
            failure = _require_csrf(secret, settings, auth_session)
            if failure is not None:
                return failure

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {"name"}:
                return _error("invalid_request", "Request must contain only a project name.", 400)
            name = _normalize_project_name(payload["name"])
            if name is None:
                return _error("invalid_request", "Project name must be 1 to 100 valid characters.", 400)

            session.scalar(
                select(User.id)
                .where(User.id == auth_session.user_id)
                .with_for_update()
            )
            active_count = session.scalar(
                select(func.count(Project.id)).where(
                    Project.owner_user_id == auth_session.user_id,
                    Project.status == "active",
                )
            )
            if active_count >= limit:
                return _error("project_limit_reached", "The project limit has been reached.", 409)

            project = None
            for attempt in range(MAX_PROJECT_ID_ATTEMPTS):
                candidate = Project(
                    owner_user_id=auth_session.user_id,
                    display_name=name,
                    public_project_id=str(uuid.uuid4()),
                    status="active",
                )
                try:
                    with session.begin_nested():
                        session.add(candidate)
                        session.flush()
                    project = candidate
                    break
                except IntegrityError:
                    if attempt == MAX_PROJECT_ID_ATTEMPTS - 1:
                        logger.error("Could not allocate a unique public project identifier.")
                        return _error(
                            "project_id_unavailable",
                            "A unique project identifier could not be allocated.",
                            503,
                        )

            return jsonify(success=True, data={"project": _serialize_project(project)}), 201
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.get("")
def list_projects():
    try:
        with _session_factory()() as session:
            _, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            _, auth_session = context
            projects = session.scalars(
                select(Project)
                .where(Project.owner_user_id == auth_session.user_id)
                .order_by(Project.created_at.desc(), Project.id.desc())
            ).all()
            return jsonify(
                success=True,
                data={"projects": [_serialize_project(project) for project in projects]},
            )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


def _owned_project(session, owner_user_id: int, public_project_id: str) -> Project | None:
    return session.scalar(
        select(Project).where(
            Project.public_project_id == public_project_id,
            Project.owner_user_id == owner_user_id,
        )
    )


def _owned_api_key(
    session,
    project_id: int,
    key_id: str,
) -> ApiKey | None:
    if not key_id.isascii() or not key_id.isdecimal() or len(key_id) > 10:
        return None
    return session.scalar(
        select(ApiKey).where(
            ApiKey.id == int(key_id),
            ApiKey.project_id == project_id,
        )
    )


def _active_key_conditions(project_id: int, now: datetime):
    return (
        ApiKey.project_id == project_id,
        ApiKey.status == "active",
        ApiKey.revoked_at.is_(None),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
    )


@projects_blueprint.post("/<project_id>/keys")
def create_api_key(project_id: str):
    try:
        limit = _active_key_limit()
        with _session_factory()() as session, session.begin():
            secret, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            settings, auth_session = context
            failure = _require_csrf(secret, settings, auth_session)
            if failure is not None:
                return failure

            project = session.scalar(
                select(Project)
                .where(
                    Project.public_project_id == project_id,
                    Project.owner_user_id == auth_session.user_id,
                )
                .with_for_update()
            )
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if project.status != "active":
                return _error("project_archived", "Archived projects cannot manage API keys.", 409)

            if session.get_bind().dialect.name == "sqlite":
                session.execute(
                    update(Project)
                    .where(Project.id == project.id)
                    .values(updated_at=Project.updated_at)
                )

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) - {"expires_in_days"}:
                return _error("invalid_request", "Request must be an object with optional expires_in_days.", 400)
            expiry_days = payload.get("expires_in_days")
            if expiry_days is not None and (
                not isinstance(expiry_days, int)
                or isinstance(expiry_days, bool)
                or not 1 <= expiry_days <= MAX_KEY_EXPIRY_DAYS
            ):
                return _error(
                    "invalid_request",
                    f"expires_in_days must be an integer from 1 to {MAX_KEY_EXPIRY_DAYS}.",
                    400,
                )

            now = datetime.now(timezone.utc)
            active_count = session.scalar(
                select(func.count(ApiKey.id)).where(*_active_key_conditions(project.id, now))
            )
            if active_count >= limit:
                return _error("active_key_limit_reached", "The active API key limit has been reached.", 409)

            key_prefix = API_KEY_PREFIX + secrets.token_urlsafe(12)
            api_key_secret = key_prefix + "_" + secrets.token_urlsafe(32)
            api_key = ApiKey(
                project_id=project.id,
                key_prefix=key_prefix,
                key_hash=hashlib.sha256(api_key_secret.encode("utf-8")).hexdigest(),
                status="active",
                expires_at=now + timedelta(days=expiry_days) if expiry_days is not None else None,
            )
            session.add(api_key)
            session.flush()
            response = make_response(
                jsonify(
                    success=True,
                    data={
                        "key": _serialize_api_key(api_key),
                        "secret": api_key_secret,
                    },
                ),
                201,
            )
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.get("/<project_id>/keys")
def list_api_keys(project_id: str):
    try:
        with _session_factory()() as session:
            _, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            _, auth_session = context
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if project.status != "active":
                return _error("project_archived", "Archived projects cannot manage API keys.", 409)
            api_keys = session.scalars(
                select(ApiKey)
                .where(ApiKey.project_id == project.id)
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
            ).all()
            response = make_response(
                jsonify(
                    success=True,
                    data={"keys": [_serialize_api_key(api_key) for api_key in api_keys]},
                )
            )
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.get("/<project_id>/keys/<key_id>")
def get_api_key(project_id: str, key_id: str):
    try:
        with _session_factory()() as session:
            _, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            _, auth_session = context
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if project.status != "active":
                return _error("project_archived", "Archived projects cannot manage API keys.", 409)
            api_key = _owned_api_key(session, project.id, key_id)
            if api_key is None:
                return _error("not_found", "API key not found.", 404)
            response = make_response(
                jsonify(success=True, data={"key": _serialize_api_key(api_key)})
            )
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.post("/<project_id>/keys/<key_id>/revoke")
def revoke_api_key(project_id: str, key_id: str):
    try:
        with _session_factory()() as session, session.begin():
            secret, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            settings, auth_session = context
            failure = _require_csrf(secret, settings, auth_session)
            if failure is not None:
                return failure
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if project.status != "active":
                return _error("project_archived", "Archived projects cannot manage API keys.", 409)
            api_key = _owned_api_key(session, project.id, key_id)
            if api_key is None:
                return _error("not_found", "API key not found.", 404)
            if api_key.revoked_at is None:
                api_key.status = "revoked"
                api_key.revoked_at = datetime.now(timezone.utc)
            response = make_response(
                jsonify(success=True, data={"key": _serialize_api_key(api_key)})
            )
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.get("/<project_id>")
def get_project(project_id: str):
    try:
        with _session_factory()() as session:
            _, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            _, auth_session = context
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            return jsonify(success=True, data={"project": _serialize_project(project)})
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.patch("/<project_id>")
def update_project(project_id: str):
    try:
        with _session_factory()() as session, session.begin():
            secret, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            settings, auth_session = context
            failure = _require_csrf(secret, settings, auth_session)
            if failure is not None:
                return failure

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {"name"}:
                return _error("invalid_request", "Only the project name can be updated.", 400)
            name = _normalize_project_name(payload["name"])
            if name is None:
                return _error("invalid_request", "Project name must be 1 to 100 valid characters.", 400)
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if project.status != "active":
                return _error("project_archived", "Archived projects cannot be updated.", 409)
            project.display_name = name
            project.updated_at = datetime.now(timezone.utc)
            return jsonify(success=True, data={"project": _serialize_project(project)})
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


@projects_blueprint.delete("/<project_id>")
def archive_project(project_id: str):
    try:
        with _session_factory()() as session, session.begin():
            secret, context, failure = _resolve_request_session(session)
            if failure is not None:
                return failure
            settings, auth_session = context
            failure = _require_csrf(secret, settings, auth_session)
            if failure is not None:
                return failure
            project = _owned_project(session, auth_session.user_id, project_id)
            if project is None:
                return _error("not_found", "Project not found.", 404)
            project.status = "archived"
            project.updated_at = datetime.now(timezone.utc)
            return jsonify(success=True, data={"project": _serialize_project(project)})
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _service_unavailable()


__all__ = ["projects_blueprint"]
