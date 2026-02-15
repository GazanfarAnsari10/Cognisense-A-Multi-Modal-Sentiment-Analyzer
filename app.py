from flask import Flask, render_template, request, jsonify
from analyzers.text_analyzer import analyze_text_from_string
from analyzers.audio_analyzer import analyze_audio_file
from analyzers.video_analyzer import analyze_video_file
from analyzers.image_analyzer import analyze_image_file
import os
from werkzeug.utils import secure_filename
import os
import docx
import PyPDF2 
from flask_socketio import SocketIO, emit
from analyzers.realtime_video_analyzer import analyze_frame_from_b64



ALLOWED_EXTENSIONS = {"txt", "docx", "pdf"}  
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS




app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return render_template("index.html")

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
def analyze_text():
    text_data = request.form.get("text")
    uploaded_file = request.files.get("file")

    if text_data:  # Case 1: raw text
        result = analyze_text_from_string(text_data)
        return jsonify(result)

    elif uploaded_file and allowed_file(uploaded_file.filename):  # Case 2: file upload
        filename = secure_filename(uploaded_file.filename)
        filepath = os.path.join("uploads", filename)  # make sure "uploads" folder exists
        uploaded_file.save(filepath)

        # Detect file type
        file_ext = filename.rsplit(".", 1)[1].lower()

        if file_ext == "txt":
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

        elif file_ext == "docx":
            doc = docx.Document(filepath)
            content = "\n".join([para.text for para in doc.paragraphs])

        elif file_ext == "pdf":
            content = ""
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    content += page.extract_text() or ""

        else:
            return jsonify({"error": "Unsupported file type"}), 400

        # If file has no extractable content
        if not content.strip():
            return jsonify({"error": "File is empty or could not extract text"}), 400

        result = analyze_text_from_string(content)
        return jsonify(result)

    else:  # No text and no valid file
        return jsonify({"error": "No text or file provided"}), 400

@app.route("/api/analyze/audio", methods=["POST"])
def analyze_audio():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    path = os.path.join(UPLOAD_FOLDER, f.filename)
    f.save(path)
    result = analyze_audio_file(path)
    # optionally remove file
    try:
        os.remove(path)
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/analyze/video", methods=["POST"])
def analyze_video():
    if 'file' not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f = request.files['file']
    if f.filename == "":
        return jsonify({"error":"Empty filename"}), 400
    path = os.path.join(UPLOAD_FOLDER, f.filename)
    f.save(path)
    result = analyze_video_file(path)
    try:
        os.remove(path)
    except Exception:
        pass
    return jsonify(result)

@app.route("/api/analyze/image", methods=["POST"])
def analyze_image():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    upload_path = os.path.join("uploads", file.filename)
    file.save(upload_path)

    result = analyze_image_file(upload_path)
    return jsonify(result)

socketio = SocketIO(app, cors_allowed_origins="*")

@socketio.on("realtime_frame")
def handle_realtime_frame(payload):
    """
    payload expected: {"image": "data:image/jpeg;base64,...."}
    We'll analyze and emit 'realtime_analysis' event back.
    """
    try:
        img_b64 = payload.get("image")
        if not img_b64:
            emit("realtime_analysis", {"error": "no image provided"})
            return
        analysis = analyze_frame_from_b64(img_b64)
        # add timestamp
        analysis["timestamp"] = request.environ.get("REMOTE_ADDR", "")  # optional
        emit("realtime_analysis", analysis)
    except Exception as e:
        emit("realtime_analysis", {"error": str(e)})


if __name__ == "__main__":
    # use socketio.run to allow websocket/server usage
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)

