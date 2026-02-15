import speech_recognition as sr
import os
from analyzers.text_analyzer import analyze_text_from_string
from pydub import AudioSegment
from utils import tmp_filename

def convert_to_wav(in_path, out_path):
    try:
        audio = AudioSegment.from_file(in_path)
        audio.export(out_path, format="wav")
        return out_path
    except Exception as e:
        raise

def transcribe_audio(path):
    # convert to wav for recognizer
    base_wav = tmp_filename(suffix=".wav")
    try:
        convert_to_wav(path, base_wav)
    except Exception:
        # maybe file already wav or converter not available
        base_wav = path
    r = sr.Recognizer()
    with sr.AudioFile(base_wav) as source:
        audio = r.record(source)
    # Use Google Web Speech API (requires internet)
    try:
        text = r.recognize_google(audio)
    except sr.UnknownValueError:
        text = ""
    except sr.RequestError:
        # API unreachable
        text = ""
    # cleanup if we created a tmp
    try:
        if base_wav != path:
            os.remove(base_wav)
    except Exception:
        pass
    return text

def analyze_audio_file(path):
    transcription = transcribe_audio(path)
    if not transcription:
        return {
            "transcription": "",
            "message": "Could not transcribe audio reliably (silent or speech not recognized).",
            "analysis": {}
        }
    analysis = analyze_text_from_string(transcription)
    return {
        "transcription": transcription,
        "analysis": analysis
    }
