"""Authenticated API gateway for the existing Cognisense analyzers."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from werkzeug.utils import secure_filename

from database.models import ApiKey, Plan, Project, UsageRecord
from database.session import get_session_factory
from dotenv import load_dotenv
from result_export import build_export
from utils import extract_text_from_pdf

load_dotenv()


logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = int(os.getenv("COGNISENSE_MAX_REQUEST_BYTES", str(25 * 1024 * 1024)))
MAX_FILES = 5
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DATABASE_API_KEY_PATTERN = re.compile(r"^(cgs_live_[A-Za-z0-9_-]{16})_([A-Za-z0-9_-]{43})$")
DATABASE_API_KEY_PREFIX = "cgs_live_"
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
SUPPORTED_EXTENSIONS = {
    ".txt": "document",
    ".pdf": "document",
    ".wav": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".aiff": "audio",
    ".aif": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    ".webm": "video",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
}

if MAX_REQUEST_BYTES <= 0:
    raise ValueError("COGNISENSE_MAX_REQUEST_BYTES must be a positive integer")

app = FastAPI(
    title="Cognisense Feedback Gateway",
    version="1.0.0",
    description="Authenticated API gateway to Cognisense's existing sentiment analyzers.",
)

origins = [
    origin.strip()
    for origin in os.getenv("COGNISENSE_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
)


def _error_response(
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "request_id": request_id,
            "error": {"code": code, "message": message},
        },
        headers=headers,
    )


class RequestMetadataAndSizeMiddleware:
    """Attach request IDs and enforce a streaming HTTP body limit."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = dict(scope.get("headers", []))
        supplied_id = request_headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = (
            supplied_id
            if 0 < len(supplied_id) <= 128 and all(char.isprintable() for char in supplied_id)
            else str(uuid.uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id
        received_bytes = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Request exceeds the configured size limit.",
                    )
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, limited_receive, tracked_send)


app.add_middleware(RequestMetadataAndSizeMiddleware, max_bytes=MAX_REQUEST_BYTES)


@dataclass(frozen=True)
class AuthenticatedProject:
    public_project_id: str
    api_key_id: int | None


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        400: "invalid_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        413: "request_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(exc.status_code, "request_failed")
    return _error_response(
        request.state.request_id,
        exc.status_code,
        code,
        str(exc.detail),
        exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request.state.request_id,
        422,
        "validation_error",
        "Request parameters are invalid.",
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled gateway error (request_id=%s)", request.state.request_id)
    return _error_response(
        request.state.request_id, 500, "internal_error", "The request could not be completed."
    )


def _configured_api_keys() -> dict[str, str]:
    raw_keys = os.getenv("COGNISENSE_API_KEYS", "")
    if not raw_keys:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured on this server.",
        )
    try:
        parsed = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        logger.error("COGNISENSE_API_KEYS is not valid JSON")
        raise HTTPException(status_code=503, detail="API authentication configuration is invalid.") from exc
    if (
        not isinstance(parsed, dict)
        or not parsed
        or any(
            not isinstance(project_id, str)
            or not PROJECT_ID_PATTERN.fullmatch(project_id)
            or not isinstance(key, str)
            or len(key) < 16
            for project_id, key in parsed.items()
        )
    ):
        logger.error("COGNISENSE_API_KEYS must map valid project IDs to keys of at least 16 characters")
        raise HTTPException(status_code=503, detail="API authentication configuration is invalid.")
    return parsed


def _legacy_api_keys_enabled() -> bool:
    setting = os.getenv("COGNISENSE_LEGACY_API_KEYS_ENABLED", "true").strip().lower()
    if setting in {"1", "true", "yes", "on"}:
        return True
    if setting in {"0", "false", "no", "off"}:
        return False
    logger.error("COGNISENSE_LEGACY_API_KEYS_ENABLED must be a boolean value")
    raise HTTPException(status_code=503, detail="API authentication configuration is invalid.")


def _authenticate_database_key(api_key: str) -> AuthenticatedProject | None:
    match = DATABASE_API_KEY_PATTERN.fullmatch(api_key)
    if match is None:
        return None

    key_prefix = match.group(1)
    presented_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    try:
        with get_session_factory()() as session:
            row = session.execute(
                select(ApiKey.id, ApiKey.key_hash, Project.public_project_id)
                .join(Project, Project.id == ApiKey.project_id)
                .where(
                    ApiKey.key_prefix == key_prefix,
                    ApiKey.status == "active",
                    ApiKey.revoked_at.is_(None),
                    or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
                    Project.status == "active",
                )
            ).one_or_none()
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Database API key verification is temporarily unavailable.")
        raise HTTPException(
            status_code=503,
            detail="API authentication is temporarily unavailable.",
        ) from None

    if row is None or not hmac.compare_digest(row.key_hash, presented_hash):
        return None
    return AuthenticatedProject(row.public_project_id, row.id)


def _positive_setting(name: str, default: int, *, allow_zero: bool = False) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        logger.error("%s must be an integer", name)
        raise HTTPException(status_code=503, detail="Request limits are not configured correctly.") from None
    minimum = 0 if allow_zero else 1
    if value < minimum:
        logger.error("%s is outside its supported range", name)
        raise HTTPException(status_code=503, detail="Request limits are not configured correctly.")
    return value


def _retry_after(oldest_request: datetime | None, window_seconds: int, now: datetime) -> int:
    if oldest_request is None:
        return window_seconds
    if oldest_request.tzinfo is None:
        oldest_request = oldest_request.replace(tzinfo=timezone.utc)
    remaining = (oldest_request + timedelta(seconds=window_seconds) - now).total_seconds()
    return max(1, math.ceil(remaining))


def _usage_count(session, project_id: int, since: datetime):
    return session.execute(
        select(func.count(UsageRecord.id), func.min(UsageRecord.request_timestamp)).where(
            UsageRecord.project_id == project_id,
            UsageRecord.request_timestamp >= since,
            UsageRecord.http_status_code != 429,
        )
    ).one()


def _reserve_usage(principal: AuthenticatedProject) -> int:
    now = datetime.now(timezone.utc)
    rejection: tuple[int, str] | None = None
    reservation_id: int | None = None
    try:
        with get_session_factory()() as session, session.begin():
            project = session.scalar(
                select(Project)
                .where(Project.public_project_id == principal.public_project_id)
                .with_for_update()
            )
            if project is None or project.status != "active":
                raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")

            if session.get_bind().dialect.name == "sqlite":
                session.execute(
                    update(Project)
                    .where(Project.id == project.id)
                    .values(updated_at=Project.updated_at)
                )

            effective_plan = project.plan or session.scalar(
                select(Plan).where(Plan.is_default.is_(True)).order_by(Plan.id).limit(1)
            )
            if effective_plan is None:
                rate_limit = _positive_setting("COGNISENSE_RATE_LIMIT_REQUESTS", 60)
                rate_window = _positive_setting("COGNISENSE_RATE_LIMIT_WINDOW_SECONDS", 60)
                effective_quota = _positive_setting(
                    "COGNISENSE_DEFAULT_DAILY_QUOTA", 0, allow_zero=True
                )
            else:
                limits = (
                    effective_plan.request_limit_per_minute,
                    effective_plan.quota_limit_per_day,
                )
                if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in limits):
                    raise ValueError("Stored plan limits are invalid.")
                rate_limit = effective_plan.request_limit_per_minute
                rate_window = 60
                effective_quota = effective_plan.quota_limit_per_day

            if rate_limit > 0:
                rate_count, rate_oldest = _usage_count(
                    session, project.id, now - timedelta(seconds=rate_window)
                )
                if rate_count >= rate_limit:
                    rejection = (
                        _retry_after(rate_oldest, rate_window, now),
                        "The request limit has been reached. Try again later.",
                    )
            quota_oldest: datetime | None = None
            if rejection is None and effective_quota:
                quota_count, quota_oldest = _usage_count(
                    session,
                    project.id,
                    now - timedelta(days=1),
                )
                if quota_count >= effective_quota:
                    rejection = (
                        _retry_after(quota_oldest, 86_400, now),
                        "The project request quota has been reached. Try again later.",
                    )

            if rejection is None:
                usage = UsageRecord(
                    project_id=project.id,
                    api_key_id=principal.api_key_id,
                    endpoint="/api/v1/analyze",
                    http_status_code=500,
                    processing_duration_ms=0,
                    request_timestamp=now,
                )
                session.add(usage)
                session.flush()
                reservation_id = usage.id
            else:
                session.add(
                    UsageRecord(
                        project_id=project.id,
                        api_key_id=principal.api_key_id,
                        endpoint="/api/v1/analyze",
                        http_status_code=429,
                        processing_duration_ms=0,
                        request_timestamp=now,
                    )
                )
    except HTTPException:
        raise
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.error("Request admission and usage recording failed.")
        raise HTTPException(
            status_code=503,
            detail="Request processing is temporarily unavailable.",
        ) from None

    if rejection is not None:
        retry_after, message = rejection
        raise HTTPException(
            status_code=429,
            detail=message,
            headers={"Retry-After": str(retry_after)},
        )
    if reservation_id is None:
        logger.error("Request usage reservation was not created.")
        raise HTTPException(status_code=503, detail="Request processing is temporarily unavailable.")
    return reservation_id


def _finish_usage(usage_id: int, status_code: int, started_at: float) -> None:
    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
    try:
        with get_session_factory()() as session, session.begin():
            result = session.execute(
                update(UsageRecord)
                .where(UsageRecord.id == usage_id)
                .values(
                    http_status_code=status_code,
                    processing_duration_ms=duration_ms,
                )
            )
            if result.rowcount != 1:
                raise SQLAlchemyError("Usage reservation was not found.")
    except (SQLAlchemyError, RuntimeError, ValueError) as exc:
        logger.error("Request outcome could not be recorded (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Request processing is temporarily unavailable.",
        ) from None


class UsageFinalizationMiddleware:
    """Persist the response outcome before allowing response headers to leave."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        finalization_failed = False
        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal finalization_failed
            if message["type"] == "http.response.start":
                tracking = scope.get("state", {}).get("usage_tracking")
                if tracking is None:
                    await send(message)
                    return
                try:
                    await run_in_threadpool(
                        _finish_usage,
                        tracking["usage_id"],
                        message["status"],
                        tracking["started_at"],
                    )
                except HTTPException:
                    finalization_failed = True
                    request_id = scope.get("state", {}).get("request_id", "unknown")
                    content = json.dumps({
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "code": "service_unavailable",
                            "message": "Request processing is temporarily unavailable.",
                        },
                    }).encode("utf-8")
                    headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() not in {b"content-length", b"content-type", b"content-disposition"}
                    ]
                    headers.extend([
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(content)).encode("ascii")),
                    ])
                    await send({**message, "status": 503, "headers": headers})
                    await send({"type": "http.response.body", "body": content, "more_body": False})
                    return
            if not finalization_failed:
                await send(message)

        await self.app(scope, receive, tracked_send)


app.add_middleware(UsageFinalizationMiddleware)


def authenticate_project(
    request: Request,
    api_key: str | None = Depends(API_KEY_HEADER),
) -> AuthenticatedProject:
    if not api_key:
        raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")

    if api_key.startswith(DATABASE_API_KEY_PREFIX):
        principal = _authenticate_database_key(api_key)
        if principal is None:
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")
    else:
        if not _legacy_api_keys_enabled():
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")
        projects = _configured_api_keys()
        project_id = next(
            (
                project_id
                for project_id, configured_key in projects.items()
                if hmac.compare_digest(api_key, configured_key)
            ),
            None,
        )
        if project_id is None:
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")
        principal = AuthenticatedProject(project_id, None)

    usage_id = _reserve_usage(principal)
    request.state.usage_tracking = {"usage_id": usage_id, "started_at": time.monotonic()}
    return principal


def _validate_project_id(project_id: Any, authenticated_project: str) -> str:
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise HTTPException(status_code=422, detail="project_id is required and must be a valid identifier.")
    if project_id != authenticated_project:
        raise HTTPException(status_code=403, detail="project_id does not match the API key.")
    return project_id


async def _read_upload(upload: UploadFile, remaining_bytes: int) -> bytes:
    data = await upload.read(remaining_bytes + 1)
    if len(data) > remaining_bytes:
        raise HTTPException(status_code=413, detail="Uploaded files exceed the configured size limit.")
    return data


def _normalized_filename(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    safe_name = secure_filename(name)
    if not safe_name:
        raise HTTPException(status_code=422, detail="Each uploaded file must have a valid filename.")
    return safe_name


def _run_analyzer(modality: str, content: str | bytes, filename: str | None) -> Any:
    """Route data to an existing analyzer without reimplementing its analysis."""
    if modality == "text":
        if not isinstance(content, str):
            raise TypeError("Text analyzer input must be a string.")
        from analyzers.text_analyzer import analyze_text_from_string

        return analyze_text_from_string(content)

    if not isinstance(content, bytes):
        raise TypeError("File analyzer input must be bytes.")
    suffix = Path(filename or "").suffix.lower()
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            path = temporary_file.name
            temporary_file.write(content)
        if path is None:
            raise RuntimeError("Temporary file was not created.")
        if modality == "document":
            if suffix == ".txt":
                try:
                    text = content.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise HTTPException(status_code=422, detail="TXT files must use UTF-8 encoding.") from exc
            else:
                text = extract_text_from_pdf(path)
                if text.startswith("[ERROR extracting PDF text:"):
                    raise HTTPException(status_code=422, detail="PDF text could not be extracted.")
            if not text.strip():
                raise HTTPException(status_code=422, detail="Document is empty or has no extractable text.")
            from analyzers.text_analyzer import analyze_text_from_string

            return analyze_text_from_string(text)
        if modality == "audio":
            from analyzers.audio_analyzer import analyze_audio_file

            return analyze_audio_file(path)
        if modality == "video":
            from analyzers.video_analyzer import analyze_video_file

            return analyze_video_file(path)
        if modality == "image":
            from analyzers.image_analyzer import analyze_image_file

            return analyze_image_file(path)
        raise HTTPException(status_code=415, detail="Unsupported input modality.")
    finally:
        if path is not None and os.path.exists(path):
            os.unlink(path)


@app.get("/api/v1/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "success": True,
        "request_id": request.state.request_id,
        "data": {"status": "ok", "api_version": "v1"},
    }


@app.post(
    "/api/v1/analyze",
    summary="Analyze text and uploaded feedback",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["project_id"],
                        "properties": {
                            "project_id": {"type": "string", "description": "Project associated with the API key."},
                            "text": {"type": "string", "description": "Plain-text feedback."},
                        },
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["project_id"],
                        "properties": {
                            "project_id": {"type": "string"},
                            "text": {"type": "string"},
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "Repeat this field for each supported input file.",
                            },
                        },
                    }
                },
            },
        }
    },
)
async def analyze_feedback(
    request: Request,
    response: Response,
    authenticated_project: AuthenticatedProject = Depends(authenticate_project),
    export_format: str | None = Query(
        default=None,
        description="Optionally download the analyzer results as json, csv, or pdf.",
        json_schema_extra={"enum": ["json", "csv", "pdf"]},
    ),
) -> Response:
    if export_format is not None and export_format not in {"json", "csv", "pdf"}:
        raise HTTPException(
            status_code=422,
            detail="export_format must be one of: json, csv, pdf.",
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body must contain valid JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="JSON body must be an object.")
        project_id = _validate_project_id(payload.get("project_id"), authenticated_project.public_project_id)
        text = payload.get("text")
        if text is not None and not isinstance(text, str):
            raise HTTPException(status_code=422, detail="text must be a string.")
        inputs: list[tuple[str, str, bytes | str]] = []
        if isinstance(text, str) and text.strip():
            inputs.append(("text", "text", text))
        if not inputs:
            raise HTTPException(status_code=422, detail="Provide non-empty text or one or more files.")
    elif content_type == "multipart/form-data":
        async with request.form() as form:
            project_id = _validate_project_id(form.get("project_id"), authenticated_project.public_project_id)
            text = form.get("text")
            if text is not None and not isinstance(text, str):
                raise HTTPException(status_code=422, detail="text must be a string.")
            inputs = []
            if isinstance(text, str) and text.strip():
                inputs.append(("text", "text", text))
            uploads = [*form.getlist("files"), *form.getlist("file")]
            if len(uploads) > MAX_FILES:
                raise HTTPException(status_code=422, detail=f"A maximum of {MAX_FILES} files is allowed.")
            total_bytes = 0
            for upload in uploads:
                if not isinstance(upload, UploadFile):
                    raise HTTPException(status_code=422, detail="Each file field must contain an uploaded file.")
                filename = _normalized_filename(upload.filename)
                modality = SUPPORTED_EXTENSIONS.get(Path(filename).suffix.lower())
                if modality is None:
                    suffix = Path(filename).suffix or "unknown"
                    raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}.")
                content = await _read_upload(upload, MAX_REQUEST_BYTES - total_bytes)
                total_bytes += len(content)
                inputs.append((modality, filename, content))
            if not inputs:
                raise HTTPException(status_code=422, detail="Provide non-empty text or one or more files.")
    else:
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json or multipart/form-data.",
        )

    results = []
    for modality, source, content in inputs:
        try:
            result = _run_analyzer(modality, content, source if source != "text" else None)
            results.append(
                {
                    "modality": "text" if modality == "document" else modality,
                    "source": source,
                    "result": result,
                }
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Analyzer failed (request_id=%s, modality=%s, source=%s)",
                request.state.request_id,
                modality,
                source,
            )
            results.append(
                {
                    "modality": "text" if modality == "document" else modality,
                    "source": source,
                    "error": {
                        "code": "analysis_failed",
                        "message": "The analyzer failed for this input.",
                    },
                }
            )

    failed = any("error" in result for result in results)
    if failed and all("error" in result for result in results):
        response.status_code = 500
        return _error_response(request.state.request_id, 500, "analysis_failed", "All analyzers failed.")
    if export_format is not None:
        body, media_type, extension = build_export(
            export_format=export_format,
            project_id=project_id,
            request_id=request.state.request_id,
            results=results,
            analysis_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        filename = f"cognisense-export-{uuid.uuid4().hex}.{extension}"
        return Response(
            content=body,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return JSONResponse(
        content={
            "success": True,
            "request_id": request.state.request_id,
            "project_id": project_id,
            "data": {"status": "partial" if failed else "complete", "results": results},
        }
    )
