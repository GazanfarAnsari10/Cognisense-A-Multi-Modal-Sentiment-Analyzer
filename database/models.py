"""SQLAlchemy models for the Cognisense platform foundation."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user", server_default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="save-update")
    auth_sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user", cascade="save-update")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_limit_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_api_key_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="plan")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    public_project_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    plan: Mapped[Plan | None] = relationship(back_populates="projects")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="project", cascade="save-update")
    usage_records: Mapped[list["UsageRecord"]] = relationship(back_populates="project", cascade="save-update")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="api_keys")
    usage_records: Mapped[list["UsageRecord"]] = relationship(back_populates="api_key")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    http_status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        index=True,
    )
    usage_units: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="usage_records")
    api_key: Mapped[ApiKey | None] = relationship(back_populates="usage_records")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class AuthLoginAttempt(Base):
    __tablename__ = "auth_login_attempts"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now(), index=True
    )


__all__ = [
    "Base",
    "User",
    "Plan",
    "Project",
    "ApiKey",
    "UsageRecord",
    "AuthSession",
    "AuthLoginAttempt",
    "AuditLog",
]
