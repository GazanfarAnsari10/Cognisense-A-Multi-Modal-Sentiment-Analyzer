"""Protected Phase 7A admin access and audit-log foundation."""

from __future__ import annotations

import logging
from datetime import timezone

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from auth import _settings, get_authenticated_session
from database.models import AuditLog
from database.session import get_session_factory

logger = logging.getLogger(__name__)
admin_blueprint = Blueprint("admin", __name__)


def _session_factory():
    return current_app.config.get("DATABASE_SESSION_FACTORY") or get_session_factory()


def _no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return response


def _record_access(session, auth_session, outcome: str) -> None:
    user_id = auth_session.user_id if auth_session is not None else None
    session.add(AuditLog(
        actor_identifier=f"user:{user_id}" if user_id is not None else "anonymous",
        actor_user_id=user_id,
        action="admin.access",
        target_type="admin_area",
        target_identifier="foundation",
        outcome=outcome,
    ))


def _authorize(session):
    auth_session = get_authenticated_session(session, _settings())
    if auth_session is None:
        _record_access(session, None, "denied")
        return None, 401
    if auth_session.user.role != "admin":
        _record_access(session, auth_session, "denied")
        return None, 403
    _record_access(session, auth_session, "allowed")
    return auth_session, None


def _service_unavailable():
    response = make_response(jsonify(error={
        "code": "service_unavailable",
        "message": "Admin service is temporarily unavailable.",
    }), 503)
    return _no_store(response)


@admin_blueprint.get("/admin")
def dashboard():
    try:
        with _session_factory()() as session, session.begin():
            auth_session, denied_status = _authorize(session)
            if denied_status == 401:
                response = make_response(redirect(url_for("developer.login_page", next="/admin")))
            elif denied_status == 403:
                response = make_response("Forbidden", 403)
            else:
                response = make_response(render_template("admin_dashboard.html", user=auth_session.user))
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Admin dashboard access could not be recorded.")
        return _service_unavailable()


@admin_blueprint.get("/api/v1/admin")
def admin_status():
    try:
        with _session_factory()() as session, session.begin():
            _, denied_status = _authorize(session)
            if denied_status is not None:
                code = "unauthorized" if denied_status == 401 else "forbidden"
                message = "Authentication is required." if denied_status == 401 else "Administrator access is required."
                response = make_response(jsonify(error={"code": code, "message": message}), denied_status)
            else:
                response = make_response(jsonify(success=True, data={"available": True}))
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Admin API access could not be recorded.")
        return _service_unavailable()


@admin_blueprint.get("/api/v1/admin/audit")
def audit_records():
    try:
        with _session_factory()() as session, session.begin():
            _, denied_status = _authorize(session)
            if denied_status is not None:
                code = "unauthorized" if denied_status == 401 else "forbidden"
                message = "Authentication is required." if denied_status == 401 else "Administrator access is required."
                response = make_response(jsonify(error={"code": code, "message": message}), denied_status)
            else:
                records = session.scalars(
                    select(AuditLog).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(100)
                ).all()
                response = make_response(jsonify(success=True, data={"records": [
                    {
                        "actor": record.actor_identifier,
                        "action": record.action,
                        "target_type": record.target_type,
                        "target_identifier": record.target_identifier,
                        "occurred_at": record.occurred_at.replace(tzinfo=timezone.utc).isoformat()
                        if record.occurred_at.tzinfo is None else record.occurred_at.astimezone(timezone.utc).isoformat(),
                        "outcome": record.outcome,
                    }
                    for record in records
                ]}))
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Admin audit records could not be loaded.")
        return _service_unavailable()


__all__ = ["admin_blueprint"]