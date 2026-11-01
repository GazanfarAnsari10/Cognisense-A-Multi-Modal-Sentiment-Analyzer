import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import BoundedSemaphore
from datetime import datetime, timedelta, timezone

from flask import Flask, make_response, render_template, request, jsonify
import api_gateway
from auth import auth_blueprint, _auth_secret, _bucket_key, _get_locked_buckets, _settings
from projects import projects_blueprint
from developer_portal import developer_blueprint
from admin_portal import admin_blueprint
from analyzers.text_analyzer import analyze_text_from_string
from analyzers.audio_analyzer import analyze_audio_file
from analyzers.video_analyzer import analyze_video_file
import os
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import tempfile
import docx
import PyPDF2 
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError
from database.session import get_session_factory



ALLOWED_EXTENSIONS = {"txt", "docx", "pdf"}  
PUBLIC_SESSION_COOKIE = "cognisense_public_session"
PUBLIC_SESSION_SALT = "cognisense-public-analysis-v1"
PUBLIC_RATE_LIMIT = int(os.getenv("COGNISENSE_PUBLIC_RATE_LIMIT", "20"))
PUBLIC_RATE_WINDOW = int(os.getenv("COGNISENSE_PUBLIC_RATE_WINDOW_SECONDS", "60"))
PUBLIC_SESSION_RATE_LIMIT = int(os.getenv("COGNISENSE_PUBLIC_SESSION_RATE_LIMIT", "10"))
PUBLIC_ANALYSIS_TIMEOUT = float(os.getenv("COGNISENSE_PUBLIC_ANALYSIS_TIMEOUT_SECONDS", "30"))
PUBLIC_ANALYSIS_CONCURRENCY = int(os.getenv("COGNISENSE_PUBLIC_ANALYSIS_CONCURRENCY", "2"))
if min(PUBLIC_RATE_LIMIT, PUBLIC_RATE_WINDOW, PUBLIC_SESSION_RATE_LIMIT, PUBLIC_ANALYSIS_CONCURRENCY) < 1:
    raise RuntimeError("Public analysis limits and concurrency must be positive integers.")
if PUBLIC_ANALYSIS_TIMEOUT <= 0:
    raise RuntimeError("COGNISENSE_PUBLIC_ANALYSIS_TIMEOUT_SECONDS must be positive.")
PUBLIC_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=PUBLIC_ANALYSIS_CONCURRENCY)
PUBLIC_ANALYSIS_SLOTS = BoundedSemaphore(PUBLIC_ANALYSIS_CONCURRENCY)
PUBLIC_FILE_EXTENSIONS = {
    "audio": {"wav", "mp3", "m4a", "ogg", "flac", "aiff", "aif"},
    "video": {"mp4", "mov", "avi", "mkv", "webm"},
    "image": {"jpg", "jpeg", "png", "webp"},
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _allowed_modality_file(filename, modality):
    return (
        isinstance(filename, str)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in PUBLIC_FILE_EXTENSIONS[modality]
    )


def analyze_frame_from_b64(data_url):
    from analyzers.realtime_video_analyzer import analyze_frame_from_b64 as analyze

    return analyze(data_url)


def analyze_image_file(path):
    from analyzers.image_analyzer import analyze_image_file as analyze

    return analyze(path)


def _public_serializer():
    secret = _auth_secret()
    if secret is None:
        return None
    return URLSafeTimedSerializer(secret, salt=PUBLIC_SESSION_SALT)


def _public_session_id():
    serializer = _public_serializer()
    token = request.cookies.get(PUBLIC_SESSION_COOKIE, "")
    if serializer is None or not token:
        return None
    try:
        value = serializer.loads(token, max_age=86_400)
    except (BadSignature, SignatureExpired):
        return None
    return value if isinstance(value, str) else None


def _public_error(code, message, status):
    return jsonify(error={"code": code, "message": message}), status


def _ensure_public_cookie(response):
    if _public_session_id() is None:
        serializer = _public_serializer()
        if serializer is not None:
            settings = _settings()
            response.set_cookie(
                PUBLIC_SESSION_COOKIE,
                serializer.dumps(secrets.token_urlsafe(24)),
                max_age=86_400,
                httponly=True,
                secure=settings["secure_cookie"],
                samesite=settings["same_site"],
                path="/",
            )
    return response


def _record_public_request():
    secret = _auth_secret()
    if secret is None:
        return "unavailable"
    remote_address = request.remote_addr or "unknown"
    now = datetime.now(timezone.utc)
    keys = [_bucket_key(secret, "public-analysis-ip", remote_address)]
    session_id = _public_session_id()
    if session_id:
        keys.append(_bucket_key(secret, "public-analysis-session", session_id))
    try:
        with get_session_factory()() as session, session.begin():
            buckets = _get_locked_buckets(session, keys, now)
            for bucket in buckets:
                if bucket.window_started_at.replace(tzinfo=timezone.utc) + timedelta(seconds=PUBLIC_RATE_WINDOW) <= now:
                    bucket.window_started_at = now
                    bucket.failures = 0
                    bucket.blocked_until = None
            if any(bucket.blocked_until and bucket.blocked_until.replace(tzinfo=timezone.utc) > now for bucket in buckets):
                return "limited"
            limits = {keys[0]: PUBLIC_RATE_LIMIT}
            if session_id:
                limits[keys[1]] = PUBLIC_SESSION_RATE_LIMIT
            for bucket in buckets:
                limit = limits[bucket.bucket_key]
                if bucket.failures >= limit:
                    bucket.blocked_until = now + timedelta(seconds=PUBLIC_RATE_WINDOW)
                    return "limited"
            for bucket in buckets:
                bucket.failures += 1
    except (SQLAlchemyError, RuntimeError, ValueError):
        return "unavailable"
    return "ok"


def _run_public_analysis(operation):
    if not PUBLIC_ANALYSIS_SLOTS.acquire(blocking=False):
        raise RuntimeError("busy")
    try:
        future = PUBLIC_ANALYSIS_EXECUTOR.submit(operation)
    except Exception:
        PUBLIC_ANALYSIS_SLOTS.release()
        raise
    future.add_done_callback(lambda _: PUBLIC_ANALYSIS_SLOTS.release())
    return future.result(timeout=PUBLIC_ANALYSIS_TIMEOUT)


def _check_public_admission():
    if request.content_length is not None and request.content_length > api_gateway.MAX_REQUEST_BYTES:
        return _public_error("request_too_large", "Request exceeds the configured size limit.", 413)
    if _public_session_id() is None:
        return _public_error("public_session_required", "Reload the analyzer page and try again.", 403)
    status = _record_public_request()
    if status == "limited":
        return _public_error("rate_limited", "Public analysis request limit reached. Try again later.", 429)
    if status != "ok":
        return _public_error("service_unavailable", "Public analysis is temporarily unavailable.", 503)
    return None




app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = api_gateway.MAX_REQUEST_BYTES


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    return _public_error("request_too_large", "Request exceeds the configured size limit.", 413)
try:
    trusted_proxy_hops = int(os.getenv("COGNISENSE_TRUSTED_PROXY_HOPS", "0"))
except ValueError:
    raise RuntimeError("COGNISENSE_TRUSTED_PROXY_HOPS must be a non-negative integer.") from None
if trusted_proxy_hops < 0:
    raise RuntimeError("COGNISENSE_TRUSTED_PROXY_HOPS must be a non-negative integer.")
if trusted_proxy_hops:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_hops, x_proto=trusted_proxy_hops)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.register_blueprint(auth_blueprint)
app.register_blueprint(projects_blueprint)
app.register_blueprint(developer_blueprint)
app.register_blueprint(admin_blueprint)

@app.route("/")
def index():
    return _ensure_public_cookie(make_response(render_template("index.html")))

def _save_upload(uploaded_file):
    safe_filename = secure_filename(uploaded_file.filename or "")
    if not safe_filename:
        raise ValueError("A valid filename is required.")
    suffix = os.path.splitext(safe_filename)[1]
    descriptor, path = tempfile.mkstemp(suffix=suffix, dir=UPLOAD_FOLDER)
    os.close(descriptor)
    try:
        uploaded_file.save(path)
    except Exception:
        os.remove(path)
        raise
    return path


def _run_public_file(uploaded_file, analyzer):
    path = _save_upload(uploaded_file)

    def operation():
        try:
            return analyzer(path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    try:
        return _run_public_analysis(operation)
    except FutureTimeout:
        raise
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _run_public_document(uploaded_file):
    filename = secure_filename(uploaded_file.filename or "")
    if not filename or not allowed_file(filename):
        return _public_error("unsupported_media_type", "Unsupported document file type.", 415)
    path = _save_upload(uploaded_file)

    def operation():
        try:
            file_ext = filename.rsplit(".", 1)[1].lower()
            if file_ext == "txt":
                with open(path, "r", encoding="utf-8-sig") as file:
                    content = file.read()
            elif file_ext == "docx":
                document = docx.Document(path)
                content = "\n".join(paragraph.text for paragraph in document.paragraphs)
            else:
                content = ""
                with open(path, "rb") as file:
                    reader = PyPDF2.PdfReader(file)
                    for page in reader.pages:
                        content += page.extract_text() or ""
            if not content.strip():
                return {"error": "File is empty or could not extract text"}, 400
            return analyze_text_from_string(content), 200
        finally:
            if os.path.exists(path):
                os.remove(path)

    try:
        result, status_code = _run_public_analysis(operation)
    except FutureTimeout:
        raise
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise
    return jsonify(result), status_code

@app.route("/text")
def text_page():
    return _ensure_public_cookie(make_response(render_template("text.html")))

@app.route("/audio")
def audio_page():
    return _ensure_public_cookie(make_response(render_template("audio.html")))

@app.route("/video")
def video_page():
    return _ensure_public_cookie(make_response(render_template("video.html")))

@app.route("/image")
def image():
    return _ensure_public_cookie(make_response(render_template("image.html")))

@app.route("/realtime_video")
def realtime_video_page():
    return _ensure_public_cookie(make_response(render_template("realtime_video.html")))



# Public analysis endpoints are separate from the developer API gateway.
def _analyze_public_text():
    text_data = request.form.get("text")
    uploaded_file = request.files.get("file")

    if text_data:  # Case 1: raw text
        result = _run_public_analysis(lambda: analyze_text_from_string(text_data))
        return jsonify(result)

    elif uploaded_file:
        try:
            return _run_public_document(uploaded_file)
        except ValueError:
            return jsonify({"error": "Invalid filename"}), 400

    else:  # No text and no valid file
        return jsonify({"error": "No text or file provided"}), 400

def _analyze_public_audio():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    if not _allowed_modality_file(f.filename, "audio"):
        return _public_error("unsupported_media_type", "Unsupported audio file type.", 415)
    try:
        return jsonify(_run_public_file(f, analyze_audio_file))
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400


def _analyze_public_video():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    if not _allowed_modality_file(f.filename, "video"):
        return _public_error("unsupported_media_type", "Unsupported video file type.", 415)
    try:
        return jsonify(_run_public_file(f, analyze_video_file))
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400

def _analyze_public_image():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    if not _allowed_modality_file(file.filename, "image"):
        return _public_error("unsupported_media_type", "Unsupported image file type.", 415)

    try:
        return jsonify(_run_public_file(file, analyze_image_file))
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400


@app.route("/api/analyze/<modality>", methods=["POST"])
def reject_legacy_analyzer_route(modality):
    return _public_error("not_found", "This analyzer endpoint is unavailable.", 404)


@app.route("/api/public/analyze/<modality>", methods=["POST"])
def public_analyze(modality):
    if modality not in {"text", "audio", "video", "image"}:
        return _public_error("not_found", "Unsupported analyzer.", 404)
    admission_failure = _check_public_admission()
    if admission_failure is not None:
        return admission_failure
    handlers = {
        "text": _analyze_public_text,
        "audio": _analyze_public_audio,
        "video": _analyze_public_video,
        "image": _analyze_public_image,
    }
    try:
        return handlers[modality]()
    except FutureTimeout:
        return _public_error("analysis_timeout", "Analysis took too long. Try a smaller file.", 504)
    except RuntimeError as error:
        if str(error) == "busy":
            return _public_error("analysis_busy", "Analysis capacity is currently full. Try again shortly.", 503)
        return _public_error("analysis_failed", "Analysis could not be completed.", 500)
    except Exception:
        return _public_error("analysis_failed", "Analysis could not be completed.", 500)

socketio_origins = [
    origin.strip()
    for origin in os.getenv("COGNISENSE_SOCKETIO_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
socketio = SocketIO(
    app,
    cors_allowed_origins=socketio_origins or None,
    max_http_buffer_size=api_gateway.MAX_REQUEST_BYTES,
)


@socketio.on("connect")
def authorize_socket(auth):
    if _public_session_id() is None:
        return False

@socketio.on("realtime_frame")
def reject_legacy_realtime_frame(payload):
    emit("realtime_analysis", {"error": "This realtime event is unavailable.", "status": 404})


@socketio.on("public_realtime_frame")
def handle_public_realtime_frame(payload):
    if _public_session_id() is None:
        emit("realtime_analysis", {"error": "A valid public session is required.", "status": 403})
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("image"), str):
        emit("realtime_analysis", {"error": "no image provided", "status": 400})
        return
    image = payload["image"]
    if len(image.encode("utf-8")) > api_gateway.MAX_REQUEST_BYTES:
        emit("realtime_analysis", {"error": "Frame exceeds the configured size limit.", "status": 413})
        return
    status = _record_public_request()
    if status != "ok":
        code = 429 if status == "limited" else 503
        message = "Public analysis request limit reached. Try again later." if code == 429 else "Public analysis is temporarily unavailable."
        emit("realtime_analysis", {"error": message, "status": code})
        return
    try:
        analysis = _run_public_analysis(lambda: analyze_frame_from_b64(image))
    except FutureTimeout:
        emit("realtime_analysis", {"error": "Analysis took too long. Try again.", "status": 504})
    except RuntimeError as error:
        if str(error) == "busy":
            emit("realtime_analysis", {"error": "Analysis capacity is currently full.", "status": 503})
        else:
            emit("realtime_analysis", {"error": "Frame analysis could not be completed.", "status": 500})
    except Exception:
        emit("realtime_analysis", {"error": "Frame analysis could not be completed.", "status": 500})
    else:
        analysis["timestamp"] = request.environ.get("REMOTE_ADDR", "")
        emit("realtime_analysis", analysis)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
