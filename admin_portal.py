"""Protected Phase 7A admin access and audit-log foundation."""

from __future__ import annotations

import logging
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request, url_for
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from auth import _auth_secret, _settings, get_authenticated_session, validate_session_csrf
from database.models import ApiKey, AuditLog, AuthSession, Plan, Project, UsageRecord, User
from database.session import get_session_factory
from projects import _revoke_api_key_record

logger = logging.getLogger(__name__)
admin_blueprint = Blueprint("admin", __name__)
MAX_PAGE_SIZE = 100
MAX_PAGE_NUMBER = 100_000
MAX_REPORT_DAYS = 90
MAX_PLAN_LIMIT = 1_000_000
MAX_PLAN_RECORDS = 100
PLAN_NAME_PATTERN = re.compile(r"^[^\x00-\x1f]{1,80}$")


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


def _error(code: str, message: str, status: int):
    return _no_store(make_response(jsonify(error={"code": code, "message": message}), status))


def _admin_context(session, mutation: bool = False):
    auth_session, denied_status = _authorize(session)
    if denied_status is not None:
        code = "unauthorized" if denied_status == 401 else "forbidden"
        message = "Authentication is required." if denied_status == 401 else "Administrator access is required."
        return None, _error(code, message, denied_status)
    if not mutation:
        return auth_session, None
    secret = _auth_secret()
    try:
        settings = _settings()
    except ValueError:
        settings = None
    if secret is None or settings is None:
        return None, _error("service_unavailable", "Authentication service is not configured.", 503)
    if not validate_session_csrf(auth_session, secret, settings):
        return None, _error("csrf_failed", "A valid CSRF token is required.", 403)
    return auth_session, None


def _record_action(session, auth_session, action: str, target_type: str, target_identifier, outcome: str = "success"):
    session.add(AuditLog(
        actor_identifier=f"user:{auth_session.user_id}",
        actor_user_id=auth_session.user_id,
        action=action,
        target_type=target_type,
        target_identifier=None if target_identifier is None else str(target_identifier),
        outcome=outcome,
    ))


def _pagination():
    try:
        page = int(request.args.get("page", "1"))
        page_size = int(request.args.get("per_page", "25"))
    except ValueError:
        return None
    if not 1 <= page <= MAX_PAGE_NUMBER or not 1 <= page_size <= MAX_PAGE_SIZE:
        return None
    return page, page_size


def _page_data(page: int, page_size: int, total: int):
    return {"page": page, "per_page": page_size, "total": total, "pages": (total + page_size - 1) // page_size}


def _timestamp(value: datetime | None):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _date_range():
    now = datetime.now(timezone.utc)
    try:
        end = datetime.fromisoformat(request.args["to"].replace("Z", "+00:00")) if request.args.get("to") else now
        start = datetime.fromisoformat(request.args["from"].replace("Z", "+00:00")) if request.args.get("from") else end - timedelta(days=30)
    except (ValueError, KeyError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start >= end or end - start > timedelta(days=MAX_REPORT_DAYS) or end > now + timedelta(minutes=5):
        return None
    return start, end


def _database_failure(log_message: str):
    logger.error(log_message)
    return _service_unavailable()


def _usage_metrics(session, since: datetime):
    successful = func.sum(case((UsageRecord.http_status_code.between(200, 399), 1), else_=0))
    errors = func.sum(case(((UsageRecord.http_status_code >= 400) & (UsageRecord.http_status_code != 429), 1), else_=0))
    limited = func.sum(case((UsageRecord.http_status_code == 429, 1), else_=0))
    row = session.execute(
        select(func.count(UsageRecord.id), successful, errors, limited)
        .where(UsageRecord.request_timestamp >= since)
    ).one()
    return {"requests": int(row[0] or 0), "successful": int(row[1] or 0), "errors": int(row[2] or 0), "rate_limited": int(row[3] or 0)}


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
                response = make_response(render_template("admin_dashboard.html", user=auth_session.user, admin_user_id=auth_session.user_id))
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


@admin_blueprint.get("/api/v1/admin/overview")
def overview():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            since = datetime.now(timezone.utc) - timedelta(days=1)
            counts = {
                "users": int(session.scalar(select(func.count(User.id))) or 0),
                "active_users": int(session.scalar(select(func.count(User.id)).where(User.status == "active")) or 0),
                "projects": int(session.scalar(select(func.count(Project.id))) or 0),
                "active_projects": int(session.scalar(select(func.count(Project.id)).where(Project.status == "active")) or 0),
                "api_keys": int(session.scalar(select(func.count(ApiKey.id))) or 0),
                "active_api_keys": int(session.scalar(select(func.count(ApiKey.id)).where(ApiKey.status == "active", ApiKey.revoked_at.is_(None))) or 0),
            }
            counts.update(_usage_metrics(session, since))
            return _no_store(make_response(jsonify(success=True, data={"since": _timestamp(since), "metrics": counts})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin overview could not be loaded.")


@admin_blueprint.get("/api/v1/admin/users")
def users():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            pagination = _pagination()
            if pagination is None:
                return _error("invalid_request", "Pagination values are outside supported limits.", 400)
            page, page_size = pagination
            total = session.scalar(select(func.count(User.id))) or 0
            rows = session.scalars(select(User).order_by(User.created_at.desc(), User.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            data = [{"id": user.id, "name": user.name, "email": user.email, "status": user.status,
                     "role": user.role, "created_at": _timestamp(user.created_at),
                     "project_count": session.scalar(select(func.count(Project.id)).where(Project.owner_user_id == user.id)) or 0}
                    for user in rows]
            return _no_store(make_response(jsonify(success=True, data={"users": data, "pagination": _page_data(page, page_size, total)})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin user list could not be loaded.")


@admin_blueprint.get("/api/v1/admin/users/<int:user_id>")
def user_detail(user_id: int):
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            user = session.get(User, user_id)
            if user is None:
                return _error("not_found", "User not found.", 404)
            projects = session.scalars(select(Project).where(Project.owner_user_id == user.id).order_by(Project.id).limit(MAX_PAGE_SIZE)).all()
            return _no_store(make_response(jsonify(success=True, data={"user": {
                "id": user.id, "name": user.name, "email": user.email, "status": user.status,
                "role": user.role, "created_at": _timestamp(user.created_at), "updated_at": _timestamp(user.updated_at),
                "projects": [{"id": project.id, "project_id": project.public_project_id,
                              "name": project.display_name, "status": project.status,
                              "plan": project.plan.name if project.plan else None} for project in projects],
            }})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin user details could not be loaded.")


@admin_blueprint.patch("/api/v1/admin/users/<int:user_id>/status")
def set_user_status(user_id: int):
    try:
        with _session_factory()() as session, session.begin():
            auth_session, failure = _admin_context(session, mutation=True)
            if failure:
                return failure
            payload = request.get_json(silent=True)
            status = payload.get("status") if isinstance(payload, dict) and set(payload) == {"status"} else None
            if status not in {"active", "disabled"}:
                return _error("invalid_request", "Status must be active or disabled.", 400)
            user = session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None:
                return _error("not_found", "User not found.", 404)
            if user.id == auth_session.user_id and status != "active":
                return _error("invalid_request", "Administrators cannot disable their own account.", 400)
            user.status = status
            if status == "disabled":
                session.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
            _record_action(session, auth_session, f"admin.user.{status}", "user", user.id)
            return _no_store(make_response(jsonify(success=True, data={"user_id": user.id, "status": user.status})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin user status could not be changed.")


@admin_blueprint.get("/api/v1/admin/projects")
def projects():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            pagination = _pagination()
            if pagination is None:
                return _error("invalid_request", "Pagination values are outside supported limits.", 400)
            page, page_size = pagination
            total = session.scalar(select(func.count(Project.id))) or 0
            rows = session.execute(select(Project, User.name, User.email, Plan.name)
                                   .join(User, User.id == Project.owner_user_id)
                                   .outerjoin(Plan, Plan.id == Project.plan_id)
                                   .order_by(Project.created_at.desc(), Project.id.desc())
                                   .offset((page - 1) * page_size).limit(page_size)).all()
            output = []
            for project, owner_name, owner_email, plan_name in rows:
                output.append({"id": project.id, "project_id": project.public_project_id, "name": project.display_name,
                               "status": project.status, "owner_name": owner_name, "owner_email": owner_email,
                               "plan_id": project.plan_id, "plan": plan_name,
                               "requests_24h": int(session.scalar(select(func.count(UsageRecord.id)).where(
                                   UsageRecord.project_id == project.id,
                                   UsageRecord.request_timestamp >= datetime.now(timezone.utc) - timedelta(days=1))) or 0)})
            return _no_store(make_response(jsonify(success=True, data={"projects": output, "pagination": _page_data(page, page_size, total)})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin project list could not be loaded.")


@admin_blueprint.patch("/api/v1/admin/projects/<int:project_id>")
def update_project(project_id: int):
    try:
        with _session_factory()() as session, session.begin():
            auth_session, failure = _admin_context(session, mutation=True)
            if failure:
                return failure
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not payload or set(payload) - {"status", "plan_id"}:
                return _error("invalid_request", "Provide status and/or plan_id only.", 400)
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None:
                return _error("not_found", "Project not found.", 404)
            if "status" in payload and payload["status"] != "archived":
                return _error("invalid_request", "Projects may only be archived here.", 400)
            if "plan_id" in payload:
                plan_id = payload["plan_id"]
                if plan_id is not None and (not isinstance(plan_id, int) or isinstance(plan_id, bool) or session.get(Plan, plan_id) is None):
                    return _error("invalid_request", "plan_id must identify an existing plan or be null.", 400)
            if "status" in payload:
                project.status = "archived"
                _record_action(session, auth_session, "admin.project.archived", "project", project.id)
            if "plan_id" in payload:
                project.plan_id = plan_id
                _record_action(session, auth_session, "admin.project.plan_assigned", "project", project.id)
            return _no_store(make_response(jsonify(success=True, data={"project_id": project.id, "status": project.status, "plan_id": project.plan_id})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin project could not be changed.")


def _serialize_plan(plan: Plan):
    return {"id": plan.id, "name": plan.name, "description": plan.description,
            "request_limit_per_minute": plan.request_limit_per_minute,
            "quota_limit_per_day": plan.quota_limit_per_day,
            "active_api_key_limit": plan.active_api_key_limit, "is_default": plan.is_default,
            "is_selectable": plan.is_selectable,
            "created_at": _timestamp(plan.created_at), "updated_at": _timestamp(plan.updated_at)}


def _plan_payload(payload, existing: Plan | None = None):
    allowed = {"name", "description", "request_limit_per_minute", "quota_limit_per_day", "active_api_key_limit", "is_default", "is_selectable"}
    if not isinstance(payload, dict) or not payload or set(payload) - allowed:
        return None
    values = {}
    for field in allowed:
        if field not in payload:
            continue
        value = payload[field]
        if field in {"request_limit_per_minute", "quota_limit_per_day", "active_api_key_limit"}:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_PLAN_LIMIT:
                return None
        elif field == "is_default":
            if not isinstance(value, bool):
                return None
        elif field == "is_selectable":
            if not isinstance(value, bool):
                return None
        elif field == "name":
            if not isinstance(value, str) or not PLAN_NAME_PATTERN.fullmatch(value.strip()):
                return None
            value = value.strip()
        elif field == "description":
            if value is not None and (not isinstance(value, str) or len(value) > 255 or any(ord(char) < 32 for char in value)):
                return None
        values[field] = value
    return values


@admin_blueprint.get("/api/v1/admin/plans")
def plans():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            rows = session.scalars(select(Plan).order_by(Plan.id).limit(MAX_PLAN_RECORDS)).all()
            return _no_store(make_response(jsonify(success=True, data={"plans": [_serialize_plan(item) for item in rows]})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin plans could not be loaded.")


@admin_blueprint.post("/api/v1/admin/plans")
def create_plan():
    try:
        with _session_factory()() as session, session.begin():
            auth_session, failure = _admin_context(session, mutation=True)
            if failure:
                return failure
            values = _plan_payload(request.get_json(silent=True))
            required = {"name", "request_limit_per_minute", "quota_limit_per_day", "active_api_key_limit"}
            if values is None or not required.issubset(values):
                return _error("invalid_request", "Plan fields or limits are invalid.", 400)
            if (session.scalar(select(func.count(Plan.id))) or 0) >= MAX_PLAN_RECORDS:
                return _error("plan_limit_reached", "The maximum number of plans has been reached.", 409)
            if values.get("is_default"):
                session.execute(update(Plan).values(is_default=False))
            plan = Plan(**values)
            session.add(plan)
            session.flush()
            _record_action(session, auth_session, "admin.plan.created", "plan", plan.id)
            return _no_store(make_response(jsonify(success=True, data={"plan": _serialize_plan(plan)}), 201))
    except SQLAlchemyError as error:
        if "unique" in str(error).lower() or "duplicate" in str(error).lower():
            return _error("conflict", "A plan with that name already exists.", 409)
        return _database_failure("Admin plan could not be created.")
    except (RuntimeError, ValueError):
        return _database_failure("Admin plan could not be created.")


@admin_blueprint.patch("/api/v1/admin/plans/<int:plan_id>")
def update_plan(plan_id: int):
    try:
        with _session_factory()() as session, session.begin():
            auth_session, failure = _admin_context(session, mutation=True)
            if failure:
                return failure
            values = _plan_payload(request.get_json(silent=True))
            if values is None:
                return _error("invalid_request", "Plan fields or limits are invalid.", 400)
            plan = session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
            if plan is None:
                return _error("not_found", "Plan not found.", 404)
            if values.get("is_default"):
                session.execute(update(Plan).where(Plan.id != plan_id).values(is_default=False))
            for field, value in values.items():
                setattr(plan, field, value)
            _record_action(session, auth_session, "admin.plan.updated", "plan", plan.id)
            return _no_store(make_response(jsonify(success=True, data={"plan": _serialize_plan(plan)})))
    except SQLAlchemyError as error:
        if "unique" in str(error).lower() or "duplicate" in str(error).lower():
            return _error("conflict", "A plan with that name already exists.", 409)
        return _database_failure("Admin plan could not be updated.")
    except (RuntimeError, ValueError):
        return _database_failure("Admin plan could not be updated.")


@admin_blueprint.get("/api/v1/admin/keys")
def api_keys():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            pagination = _pagination()
            if pagination is None:
                return _error("invalid_request", "Pagination values are outside supported limits.", 400)
            page, page_size = pagination
            total = session.scalar(select(func.count(ApiKey.id))) or 0
            rows = session.execute(select(ApiKey, Project, User)
                                   .join(Project, Project.id == ApiKey.project_id)
                                   .join(User, User.id == Project.owner_user_id)
                                   .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
                                   .offset((page - 1) * page_size).limit(page_size)).all()
            output = []
            for key, project, owner in rows:
                usage_count = session.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.api_key_id == key.id)) or 0
                output.append({"key_id": key.id, "prefix": key.key_prefix, "status": key.status,
                               "created_at": _timestamp(key.created_at), "expires_at": _timestamp(key.expires_at),
                               "last_used_at": _timestamp(key.last_used_at), "revoked_at": _timestamp(key.revoked_at),
                               "project_id": project.public_project_id, "project_name": project.display_name,
                               "owner_id": owner.id, "owner_email": owner.email, "usage_count": int(usage_count)})
            return _no_store(make_response(jsonify(success=True, data={"keys": output, "pagination": _page_data(page, page_size, total)})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin API keys could not be loaded.")


@admin_blueprint.post("/api/v1/admin/keys/<int:key_id>/revoke")
def revoke_key(key_id: int):
    try:
        with _session_factory()() as session, session.begin():
            auth_session, failure = _admin_context(session, mutation=True)
            if failure:
                return failure
            key = session.scalar(select(ApiKey).where(ApiKey.id == key_id).with_for_update())
            if key is None:
                return _error("not_found", "API key not found.", 404)
            _revoke_api_key_record(key)
            _record_action(session, auth_session, "admin.api_key.revoked", "api_key", key.id)
            return _no_store(make_response(jsonify(success=True, data={"key_id": key.id, "status": key.status, "revoked_at": _timestamp(key.revoked_at)})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin API key could not be revoked.")


@admin_blueprint.get("/api/v1/admin/usage")
def usage_report():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
            date_range = _date_range()
            pagination = _pagination()
            if date_range is None or pagination is None:
                return _error("invalid_request", "Date range or pagination is invalid; maximum date range is 90 days.", 400)
            start, end = date_range
            page, page_size = pagination
            successful = func.sum(case((UsageRecord.http_status_code.between(200, 399), 1), else_=0))
            errors = func.sum(case(((UsageRecord.http_status_code >= 400) & (UsageRecord.http_status_code != 429), 1), else_=0))
            limited = func.sum(case((UsageRecord.http_status_code == 429, 1), else_=0))
            totals = session.execute(
                select(func.count(UsageRecord.id), successful, errors, limited).where(
                    UsageRecord.request_timestamp >= start,
                    UsageRecord.request_timestamp < end,
                )
            ).one()
            group_expr = request.args.get("group", "project")
            if group_expr not in {"project", "user", "day", "status"}:
                return _error("invalid_request", "group must be project, user, day, or status.", 400)
            if group_expr == "project":
                dimension = Project.public_project_id.label("group_key")
                label = Project.display_name.label("label")
                query = select(dimension, label, func.count(UsageRecord.id)).join(Project, Project.id == UsageRecord.project_id).join(User, User.id == Project.owner_user_id)
                group_by = (Project.public_project_id, Project.display_name)
            elif group_expr == "user":
                dimension, label = User.id.label("group_key"), User.email.label("label")
                query = select(dimension, label, func.count(UsageRecord.id)).join(Project, Project.id == UsageRecord.project_id).join(User, User.id == Project.owner_user_id)
                group_by = (User.id, User.email)
            elif group_expr == "day":
                dimension = func.date(UsageRecord.request_timestamp).label("group_key")
                label = dimension
                query = select(dimension, label, func.count(UsageRecord.id))
                group_by = (dimension,)
            else:
                dimension = UsageRecord.http_status_code.label("group_key")
                label = dimension
                query = select(dimension, label, func.count(UsageRecord.id))
                group_by = (dimension,)
            base = query.where(UsageRecord.request_timestamp >= start, UsageRecord.request_timestamp < end).group_by(*group_by)
            total = session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
            rows = session.execute(base.order_by(func.count(UsageRecord.id).desc(), dimension).offset((page - 1) * page_size).limit(page_size)).all()
            groups = [{"key": str(row[0]), "label": str(row[1]), "requests": int(row[2])} for row in rows]
            status_rows = session.execute(select(UsageRecord.http_status_code, func.count(UsageRecord.id))
                                          .where(UsageRecord.request_timestamp >= start, UsageRecord.request_timestamp < end)
                                          .group_by(UsageRecord.http_status_code).order_by(UsageRecord.http_status_code)).all()
            day_expr = func.date(UsageRecord.request_timestamp)
            trend_rows = session.execute(
                select(day_expr, func.count(UsageRecord.id), successful, errors, limited)
                .where(UsageRecord.request_timestamp >= start, UsageRecord.request_timestamp < end)
                .group_by(day_expr)
                .order_by(day_expr)
            ).all()
            return _no_store(make_response(jsonify(success=True, data={"from": _timestamp(start), "to": _timestamp(end),
                "group": group_expr, "groups": groups, "by_status": [{"status": int(row[0]), "requests": int(row[1])} for row in status_rows],
                "metrics": {"requests": int(totals[0] or 0), "successful": int(totals[1] or 0),
                            "errors": int(totals[2] or 0), "rate_limited": int(totals[3] or 0)},
                "trends": [{"day": str(row[0]), "requests": int(row[1] or 0), "successful": int(row[2] or 0),
                            "errors": int(row[3] or 0), "rate_limited": int(row[4] or 0)} for row in trend_rows],
                "pagination": _page_data(page, page_size, total)})))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin usage report could not be loaded.")


class _RejectHealthRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def _gateway_health_status() -> str:
    url = os.getenv("COGNISENSE_GATEWAY_HEALTH_URL", "http://127.0.0.1:8000/api/v1/health").strip()
    if not url:
        return "not_configured"
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "unavailable"
        opener = build_opener(ProxyHandler({}), _RejectHealthRedirects())
        with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=2) as response:
            if response.status != 200:
                return "unavailable"
            payload = json.loads(response.read(4096))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or payload["data"].get("status") != "ok":
            return "unavailable"
        return "healthy"
    except (OSError, ValueError, URLError, TimeoutError):
        return "unavailable"


@admin_blueprint.get("/api/v1/admin/health")
def health_status():
    try:
        with _session_factory()() as session, session.begin():
            _, failure = _admin_context(session)
            if failure:
                return failure
        try:
            with _session_factory()() as health_session:
                health_session.execute(select(1)).scalar_one()
            database_status = "healthy"
        except (SQLAlchemyError, RuntimeError, ValueError):
            logger.error("Admin database health check failed.")
            database_status = "unavailable"
        return _no_store(make_response(jsonify(success=True, data={
            "database": database_status,
            "gateway": _gateway_health_status(),
        })))
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _database_failure("Admin health status could not be loaded.")


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
                pagination = _pagination()
                date_range = _date_range() if request.args.get("from") or request.args.get("to") else None
                if pagination is None or ((request.args.get("from") or request.args.get("to")) and date_range is None):
                    return _error("invalid_request", "Pagination or date range is invalid; maximum date range is 90 days.", 400)
                page, page_size = pagination
                statement = select(AuditLog)
                count_statement = select(func.count(AuditLog.id))
                if date_range is not None:
                    start, end = date_range
                    date_filter = (AuditLog.occurred_at >= start, AuditLog.occurred_at < end)
                    statement = statement.where(*date_filter)
                    count_statement = count_statement.where(*date_filter)
                total = session.scalar(count_statement) or 0
                records = session.scalars(
                    statement.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
                    .offset((page - 1) * page_size).limit(page_size)
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
                ], "pagination": _page_data(page, page_size, total),
                    "from": _timestamp(date_range[0]) if date_range else None,
                    "to": _timestamp(date_range[1]) if date_range else None}))
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Admin audit records could not be loaded.")
        return _service_unavailable()


__all__ = ["admin_blueprint"]