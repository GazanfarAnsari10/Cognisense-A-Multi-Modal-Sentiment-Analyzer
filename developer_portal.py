"""Browser entry points and owner-scoped dashboard data for developers."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request, url_for
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from auth import _settings, get_authenticated_session
from database.models import ApiKey, Plan, Project, UsageRecord
from database.session import get_session_factory

logger = logging.getLogger(__name__)
developer_blueprint = Blueprint("developer", __name__)


def _session_factory():
    return current_app.config.get("DATABASE_SESSION_FACTORY") or get_session_factory()


def _safe_destination(value: str | None) -> str:
    if not value:
        return url_for("developer.dashboard")
    host = urlsplit(request.host_url)
    target = urlsplit(urljoin(request.host_url, value))
    if target.scheme not in {"http", "https"} or target.netloc != host.netloc:
        return url_for("developer.dashboard")
    if not (target.path == "/developer/dashboard" or target.path.startswith("/developer/dashboard/")):
        return url_for("developer.dashboard")
    return target.path + (("?" + target.query) if target.query else "")


def _current_user():
    try:
        with _session_factory()() as session:
            auth_session = get_authenticated_session(session, _settings())
            if auth_session is None:
                return None
            user = auth_session.user
            return {"name": user.name, "email": user.email, "status": user.status, "role": user.role}
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Developer portal authentication lookup failed.")
        return None


def _no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return response


def _login_page():
    response = make_response(
        render_template("developer_login.html", next_url=_safe_destination(request.args.get("next")))
    )
    return _no_store(response)


@developer_blueprint.get("/developer")
def entry():
    if _current_user() is not None:
        return _no_store(redirect(url_for("developer.dashboard")))
    return _login_page()


@developer_blueprint.get("/developer/login")
def login_page():
    if _current_user() is not None:
        return _no_store(redirect(_safe_destination(request.args.get("next"))))
    return _login_page()


@developer_blueprint.get("/developer/dashboard")
def dashboard():
    user = _current_user()
    if user is None:
        return _no_store(redirect(url_for("developer.login_page", next="/developer/dashboard")))
    response = make_response(render_template("developer_dashboard.html", user=user))
    return _no_store(response)


@developer_blueprint.get("/api/v1/developer/dashboard")
def dashboard_data():
    try:
        now = datetime.now(timezone.utc)
        day_start = now - timedelta(days=1)
        with _session_factory()() as session:
            auth_session = get_authenticated_session(session, _settings())
            if auth_session is None:
                response = make_response(jsonify(error={"code": "unauthorized", "message": "Authentication is required."}), 401)
                return _no_store(response)

            projects = session.scalars(
                select(Project)
                .where(Project.owner_user_id == auth_session.user_id, Project.status != "deleted")
                .order_by(Project.created_at.desc(), Project.id.desc())
            ).all()
            project_data = []
            project_ids = [project.id for project in projects]
            default_plan = session.scalar(
                select(Plan).where(Plan.is_default.is_(True)).order_by(Plan.id).limit(1)
            )
            active_projects = sum(project.status == "active" for project in projects)
            platform_project_limit = int(os.getenv("COGNISENSE_MAX_PROJECTS_PER_USER", "20"))
            workspace_plan = default_plan
            workspace_project_limit = (
                workspace_plan.max_active_projects if workspace_plan else platform_project_limit
            )
            for project in projects:
                assigned_plan = session.get(Plan, project.plan_id) if project.plan_id is not None else None
                plan = assigned_plan or default_plan
                if plan is None:
                    rate_window = int(os.getenv("COGNISENSE_RATE_LIMIT_WINDOW_SECONDS", "60"))
                    rate_limit = int(os.getenv("COGNISENSE_RATE_LIMIT_REQUESTS", "60"))
                    quota_limit_value = int(os.getenv("COGNISENSE_DEFAULT_DAILY_QUOTA", "0"))
                    project_limit = platform_project_limit
                else:
                    rate_window = 60
                    rate_limit = plan.request_limit_per_minute
                    quota_limit_value = plan.quota_limit_per_day
                    project_limit = plan.max_active_projects
                if rate_window < 1 or rate_limit < 0 or quota_limit_value < 0 or project_limit < 0:
                    raise ValueError("Invalid effective limit configuration")
                quota_limit = quota_limit_value if quota_limit_value > 0 else None
                key_count = session.scalar(
                    select(func.count(ApiKey.id)).where(
                        ApiKey.project_id == project.id,
                        ApiKey.status == "active",
                        ApiKey.revoked_at.is_(None),
                        (ApiKey.expires_at.is_(None) | (ApiKey.expires_at > now)),
                    )
                ) or 0
                counts = session.execute(
                    select(
                        func.count(UsageRecord.id),
                        func.sum(case((UsageRecord.http_status_code.between(200, 399), 1), else_=0)),
                        func.sum(case(((UsageRecord.http_status_code >= 400) & (UsageRecord.http_status_code != 429), 1), else_=0)),
                        func.sum(case((UsageRecord.http_status_code == 429, 1), else_=0)),
                    ).where(
                        UsageRecord.project_id == project.id,
                        UsageRecord.request_timestamp >= day_start,
                    )
                ).one()
                quota_used = session.scalar(
                    select(func.count(UsageRecord.id)).where(
                        UsageRecord.project_id == project.id,
                        UsageRecord.request_timestamp >= day_start,
                        UsageRecord.http_status_code != 429,
                    )
                ) or 0
                rate_count = session.scalar(
                    select(func.count(UsageRecord.id)).where(
                        UsageRecord.project_id == project.id,
                        UsageRecord.request_timestamp >= now - timedelta(seconds=rate_window),
                        UsageRecord.http_status_code != 429,
                    )
                ) or 0
                created_at = project.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                project_data.append({
                    "project_id": project.public_project_id,
                    "name": project.display_name,
                    "status": project.status,
                    "created_at": created_at.astimezone(timezone.utc).isoformat(),
                    "active_keys": key_count,
                    "requests_24h": int(counts[0] or 0),
                    "successful_24h": int(counts[1] or 0),
                    "failed_24h": int(counts[2] or 0),
                    "limit_responses_24h": int(counts[3] or 0),
                    "quota_used": int(quota_used),
                    "quota_limit": quota_limit,
                    "rate_count": int(rate_count),
                    "rate_limit": rate_limit if rate_limit > 0 else None,
                    "rate_window_seconds": rate_window,
                    "max_active_projects": project_limit if project_limit > 0 else None,
                    "plan": plan.name if plan else None,
                })

            recent = []
            if project_ids:
                records = session.execute(
                    select(UsageRecord, Project.display_name)
                    .join(Project, Project.id == UsageRecord.project_id)
                    .where(Project.owner_user_id == auth_session.user_id, Project.status != "deleted")
                    .order_by(UsageRecord.request_timestamp.desc(), UsageRecord.id.desc())
                    .limit(20)
                ).all()
                for record, project_name in records:
                    timestamp = record.request_timestamp
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    recent.append({
                        "project": project_name,
                        "endpoint": record.endpoint,
                        "status": record.http_status_code,
                        "duration_ms": record.processing_duration_ms,
                        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
                    })

            data = {
                "available": True,
                "generated_at": now.isoformat(),
                "projects": project_data,
                "recent": recent,
                "summary": {
                    "total_projects": len(projects),
                    "active_projects": active_projects,
                    "active_project_limit": workspace_project_limit if workspace_project_limit > 0 else None,
                    "active_keys": sum(item["active_keys"] for item in project_data),
                    "requests_24h": sum(item["requests_24h"] for item in project_data),
                    "successful_24h": sum(item["successful_24h"] for item in project_data),
                    "failed_24h": sum(item["failed_24h"] for item in project_data),
                    "limit_responses_24h": sum(item["limit_responses_24h"] for item in project_data),
                },
            }
            response = make_response(jsonify(success=True, data=data))
            return _no_store(response)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Developer dashboard data could not be loaded.")
        response = make_response(jsonify(error={"code": "service_unavailable", "message": "Dashboard data is temporarily unavailable."}), 503)
        return _no_store(response)


__all__ = ["developer_blueprint"]