import time
from functools import wraps

from flask import Flask, make_response, render_template, request, jsonify
from fastapi import HTTPException
import api_gateway
from auth import auth_blueprint
from projects import projects_blueprint
from developer_portal import developer_blueprint
from admin_portal import admin_blueprint
from analyzers.text_analyzer import analyze_text_from_string
from analyzers.audio_analyzer import analyze_audio_file
from analyzers.video_analyzer import analyze_video_file
import os
from werkzeug.utils import secure_filename
import tempfile
import docx
import PyPDF2 
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix



ALLOWED_EXTENSIONS = {"txt", "docx", "pdf"}  
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def analyze_frame_from_b64(data_url):
    from analyzers.realtime_video_analyzer import analyze_frame_from_b64 as analyze

    return analyze(data_url)


def analyze_image_file(path):
    from analyzers.image_analyzer import analyze_image_file as analyze

    return analyze(path)


def _gateway_error(error):
    code = {401: "unauthorized", 403: "forbidden", 413: "request_too_large", 429: "rate_limited", 503: "service_unavailable"}.get(
        error.status_code, "request_failed"
    )
    response = make_response(jsonify(error=str(error.detail), code=code), error.status_code)
    if error.headers:
        for name, value in error.headers.items():
            response.headers[name] = value
    return response


def _metered_analysis(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            principal = api_gateway.authenticate_api_key(request.headers.get("X-API-Key"))
            usage_id = api_gateway._reserve_usage(principal, endpoint=request.path)
        except HTTPException as error:
            return _gateway_error(error)

        started_at = time.monotonic()
        try:
            if request.content_length and request.content_length > api_gateway.MAX_REQUEST_BYTES:
                response = make_response(jsonify(error={"code": "request_too_large", "message": "Request exceeds the configured size limit."}), 413)
            else:
                response = make_response(view(*args, **kwargs))
        except Exception:
            api_gateway._finish_usage(usage_id, 500, started_at)
            raise

        try:
            api_gateway._finish_usage(usage_id, response.status_code, started_at)
        except HTTPException as error:
            return _gateway_error(error)
        return response

    return wrapped




app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit
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
    return render_template("index.html")

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

@app.route("/text")
def text_page():
    return render_template("text.html")

@app.route("/audio")
def audio_page():
    return render_template("audio.html")

@app.route("/video")
def video_page():
    return render_template("video.html")

@app.route("/image")
def image():
    return render_template("image.html")

@app.route("/realtime_video")
def realtime_video_page():
    return render_template("realtime_video.html")



# API endpoints
@app.route("/api/analyze/text", methods=["POST"])
@_metered_analysis
def analyze_text():
    text_data = request.form.get("text")
    uploaded_file = request.files.get("file")

    if text_data:  # Case 1: raw text
        result = analyze_text_from_string(text_data)
        return jsonify(result)

    elif uploaded_file and allowed_file(uploaded_file.filename):  # Case 2: file upload
        filename = secure_filename(uploaded_file.filename)
        try:
            filepath = _save_upload(uploaded_file)
        except ValueError:
            return jsonify({"error": "Invalid filename"}), 400
        try:
            file_ext = filename.rsplit(".", 1)[1].lower()

            if file_ext == "txt":
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()

            elif file_ext == "docx":
                doc = docx.Document(filepath)
                content = "\n".join([para.text for para in doc.paragraphs])

            elif file_ext == "pdf":
                content = ""
                with open(filepath, "rb") as file:
                    reader = PyPDF2.PdfReader(file)
                    for page in reader.pages:
                        content += page.extract_text() or ""

            else:
                return jsonify({"error": "Unsupported file type"}), 400

            if not content.strip():
                return jsonify({"error": "File is empty or could not extract text"}), 400

            result = analyze_text_from_string(content)
            return jsonify(result)
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)

    else:  # No text and no valid file
        return jsonify({"error": "No text or file provided"}), 400

@app.route("/api/analyze/audio", methods=["POST"])
@_metered_analysis
def analyze_audio():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    try:
        path = _save_upload(f)
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400
    try:
        return jsonify(analyze_audio_file(path))
    finally:
        if os.path.exists(path):
            os.remove(path)


@app.route("/api/analyze/video", methods=["POST"])
@_metered_analysis
def analyze_video():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    try:
        path = _save_upload(f)
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400
    try:
        return jsonify(analyze_video_file(path))
    finally:
        if os.path.exists(path):
            os.remove(path)

@app.route("/api/analyze/image", methods=["POST"])
@_metered_analysis
def analyze_image():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    try:
        upload_path = _save_upload(file)
    except ValueError:
        return jsonify({"error": "Invalid filename"}), 400
    try:
        return jsonify(analyze_image_file(upload_path))
    finally:
        if os.path.exists(upload_path):
            os.remove(upload_path)

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
    api_key = auth.get("apiKey") if isinstance(auth, dict) else None
    try:
        api_gateway.authenticate_api_key(api_key)
    except HTTPException:
        return False

@socketio.on("realtime_frame")
def handle_realtime_frame(payload):
    """
    payload expected: {"image": "data:image/jpeg;base64,...."}
    We'll analyze and emit 'realtime_analysis' event back.
    """
    try:
        api_key = payload.get("apiKey") if isinstance(payload, dict) else None
        principal = api_gateway.authenticate_api_key(api_key)
        usage_id = api_gateway._reserve_usage(principal, endpoint="socketio:realtime_frame")
    except HTTPException as error:
        emit("realtime_analysis", {"error": str(error.detail), "status": error.status_code})
        return

    started_at = time.monotonic()
    status_code = 500
    try:
        if not isinstance(payload, dict):
            emit("realtime_analysis", {"error": "no image provided"})
            status_code = 400
            return
        img_b64 = payload.get("image")
        if not img_b64:
            emit("realtime_analysis", {"error": "no image provided"})
            status_code = 400
            return
        analysis = analyze_frame_from_b64(img_b64)
        # add timestamp
        analysis["timestamp"] = request.environ.get("REMOTE_ADDR", "")  # optional
        emit("realtime_analysis", analysis)
        status_code = 200
    except Exception:
        emit("realtime_analysis", {"error": "Frame analysis could not be completed."})
    finally:
        try:
            api_gateway._finish_usage(usage_id, status_code, started_at)
        except HTTPException:
            emit("realtime_analysis", {"error": "Request processing is temporarily unavailable.", "status": 503})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
