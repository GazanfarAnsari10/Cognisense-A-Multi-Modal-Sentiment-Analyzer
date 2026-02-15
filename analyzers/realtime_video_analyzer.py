# analyzers/realtime_video_analyzer.py
import base64
import cv2
import numpy as np
from io import BytesIO

# Try to import FER; if not available, fallback gracefully
try:
    from fer import FER
    _FER_AVAILABLE = True
except Exception:
    _FER_AVAILABLE = False

# Create a detector once (expensive to initialize repeatedly)
_detector = FER(mtcnn=True) if _FER_AVAILABLE else None

def b64_to_cv2_img(data_url: str):
    """Convert data URL (data:image/jpeg;base64,...) to OpenCV BGR image."""
    if "," in data_url:
        header, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    try:
        img_bytes = base64.b64decode(b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # BGR
        return img
    except Exception as e:
        raise

def analyze_cv2_frame(img):
    """Analyze a single OpenCV BGR image. Returns dict with brightness and optional emotions."""
    out = {}
    # brightness: mean of grayscale
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        out['brightness'] = round(brightness, 3)
    except Exception:
        out['brightness'] = None

    # FER emotions on faces if available
    if _FER_AVAILABLE and _detector is not None:
        try:
            # detector.detect_emotions expects RGB image
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            results = _detector.detect_emotions(rgb)
            if results:
                # For simplicity, return aggregated emotions (sum/avg over faces)
                agg = {}
                count = 0
                for face in results:
                    emotions = face.get("emotions", {})
                    for k, v in emotions.items():
                        agg[k] = agg.get(k, 0.0) + v
                    count += 1
                if count:
                    for k in list(agg.keys()):
                        agg[k] = round(float(agg[k] / count), 4)
                    # find top emotion
                    top_emotion = max(agg, key=agg.get)
                    out['emotions'] = agg
                    out['top_emotion'] = {"label": top_emotion, "score": agg[top_emotion]}
            else:
                out['emotions'] = {}
        except Exception as e:
            out['emotions'] = {}
    else:
        out['emotions'] = {}

    # Small AI reasoning heuristic
    reasoning = []
    if out.get('brightness') is not None:
        if out['brightness'] > 130:
            reasoning.append("Frame is bright -> positive visual cue")
        elif out['brightness'] < 90:
            reasoning.append("Frame is dark -> negative visual cue")
        else:
            reasoning.append("Frame brightness is neutral")
    if out.get('top_emotion'):
        reasoning.append(f"Detected face emotion '{out['top_emotion']['label']}'")

    out['ai_reasoning'] = " | ".join(reasoning) if reasoning else ""
    return out

def analyze_frame_from_b64(data_url: str):
    """Main helper: converts base64 frame to cv2 image and analyzes it."""
    img = b64_to_cv2_img(data_url)
    if img is None:
        return {"error": "Could not decode frame"}
    return analyze_cv2_frame(img)
