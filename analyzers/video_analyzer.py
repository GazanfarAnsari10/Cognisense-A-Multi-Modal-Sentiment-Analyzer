# video_analyzer.py
import warnings
warnings.filterwarnings("ignore", "In file .* bytes wanted but 0 bytes read")

from moviepy.video.io.VideoFileClip import VideoFileClip
import os
from analyzers.audio_analyzer import analyze_audio_file
from analyzers.text_analyzer import analyze_text_from_string
from utils import tmp_filename
import numpy as np

def extract_audio_from_video(video_path, out_audio_path):
    """Extracts audio from a video file and saves as WAV."""
    clip = VideoFileClip(video_path)
    if clip.audio is None:
        clip.close()
        raise Exception("No audio stream found in video")
    clip.audio.write_audiofile(out_audio_path, logger=None)
    clip.close()
    return out_audio_path

def sample_frames_brightness(video_path, num_samples=6):
    """Samples frames and computes average brightness."""
    clip = VideoFileClip(video_path)
    duration = clip.duration
    times = np.linspace(0, max(0.001, duration - 0.001), min(num_samples, max(1, int(duration))))
    brightness_list = []

    for t in times:
        try:
            frame = clip.get_frame(t)  # RGB HxWx3
            brightness = float(frame.mean())
            brightness_list.append({"time": float(t), "brightness": brightness})
        except Exception as e:
            brightness_list.append({"time": float(t), "brightness": None, "error": str(e)})

    clip.close()
    return brightness_list

def analyze_video_file(path):
    "Analyzes a video file for audio sentiment and frame brightness, with AI reasoning."
    result = {}

    # --- 1) Audio sentiment ---
    audio_tmp = tmp_filename(suffix=".wav")
    try:
        extract_audio_from_video(path, audio_tmp)
        audio_result = analyze_audio_file(audio_tmp)

        # Ensure keys expected by frontend exist
        if 'analysis' not in audio_result:
            audio_result['analysis'] = {
                "overall": {"label": "neutral", "score": 0.0},
                "sentence_level": [],
                "word_level": [],
                "ai_reasoning": {
                    "summary": "No audio analysis available",
                    "top_positive_words": [],
                    "top_negative_words": [],
                    "top_positive_sentences": [],
                    "top_negative_sentences": [],
                    "why": ""
                }
            }

        result['audio_analysis'] = audio_result

    except Exception as e:
        result['audio_analysis'] = {
            "error": "No audio or audio extraction failed",
            "details": str(e),
            "analysis": {
                "overall": {"label": "neutral", "score": 0.0},
                "sentence_level": [],
                "word_level": [],
                "ai_reasoning": {
                    "summary": "Audio analysis failed",
                    "top_positive_words": [],
                    "top_negative_words": [],
                    "top_positive_sentences": [],
                    "top_negative_sentences": [],
                    "why": str(e)
                }
            }
        }
    finally:
        if os.path.exists(audio_tmp):
            os.remove(audio_tmp)

    # --- 2) Frame brightness heuristic ---
    try:
        brightness = sample_frames_brightness(path, num_samples=6)
        valid_frames = [b for b in brightness if b['brightness'] is not None]
        avg_brightness = float(sum(b['brightness'] for b in valid_frames) / max(1, len(valid_frames)))
        brightness_eval = "neutral"
        if avg_brightness > 120:
            brightness_eval = "positive/bright"
        elif avg_brightness < 80:
            brightness_eval = "negative/dark"

        summary = f"Average brightness: {avg_brightness:.2f}, Evaluation: {brightness_eval}"
        result['frame_brightness'] = {
            "frames": valid_frames,
            "summary": summary,
            "average": avg_brightness,
            "eval": brightness_eval
        }
    except Exception as e:
        result['frame_brightness'] = {"frames": [], "summary": "Frame analysis failed", "error": str(e)}

    # --- 3) AI reasoning ---
    result['ai_reasoning'] = {
        "summary": "Audio sentiment is primary. Frame brightness used as heuristic.",
        "top_positive_words": [],
        "top_negative_words": [],
        "top_positive_sentences": [],
        "top_negative_sentences": [],
        "why": "Audio transcription and frame brightness are combined for final label."
    }

    return result
