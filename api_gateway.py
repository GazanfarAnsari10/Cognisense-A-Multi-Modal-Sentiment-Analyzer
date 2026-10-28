"""Authenticated API gateway for the existing Cognisense analyzers."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile
from starlette.middleware.cors import CORSMiddleware
from werkzeug.utils import secure_filename

from database.models import ApiKey, Project
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


def _error_response(request_id: str, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "request_id": request_id,
            "error": {"code": code, "message": message},
        },
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
        503: "service_unavailable",
    }.get(exc.status_code, "request_failed")
    return _error_response(request.state.request_id, exc.status_code, code, str(exc.detail))


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


def _authenticate_database_key(api_key: str) -> str | None:
    match = DATABASE_API_KEY_PATTERN.fullmatch(api_key)
    if match is None:
        return None

    key_prefix = match.group(1)
    presented_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    try:
        with get_session_factory()() as session:
            row = session.execute(
                select(ApiKey.key_hash, Project.public_project_id)
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
    return row.public_project_id


def authenticate_project(
    api_key: str | None = Depends(API_KEY_HEADER),
) -> str:
    if not api_key:
        raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")

    if api_key.startswith(DATABASE_API_KEY_PREFIX):
        project_id = _authenticate_database_key(api_key)
        if project_id is None:
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")
        return project_id

    if not _legacy_api_keys_enabled():
        raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")

    projects = _configured_api_keys()
    for project_id, configured_key in projects.items():
        if hmac.compare_digest(api_key, configured_key):
            return project_id
    raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")


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
    authenticated_project: str = Depends(authenticate_project),
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
        project_id = _validate_project_id(payload.get("project_id"), authenticated_project)
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
            project_id = _validate_project_id(form.get("project_id"), authenticated_project)
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
