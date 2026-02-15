# analyzers/image_analyzer.py
import cv2
from fer import FER

def analyze_image_file(path):
    try:
        img = cv2.imread(path)
        if img is None:
            return {"error": "Could not read image file"}

        detector = FER(mtcnn=True)  # mtcnn=True for better face detection
        results = detector.detect_emotions(img)

        if not results:
            return {"error": "No face detected in image"}

        # For simplicity, analyze the first detected face
        face = results[0]
        emotions = face["emotions"]

        # Pick the top emotion
        top_emotion = max(emotions, key=emotions.get)
        score = emotions[top_emotion]

        return {
            "overall": {"label": top_emotion, "score": round(float(score), 3)},
            "emotions": emotions,
            "ai_reasoning": f"The face shows strongest emotion as '{top_emotion}' with confidence {round(float(score), 3)}."
        }
    except Exception as e:
        return {"error": str(e)}
