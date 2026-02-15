# utils.py
import tempfile
import os
import math

# For text extraction
import PyPDF2
import docx

def tmp_filename(suffix=".tmp"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path

def safe_number_format(x, precision=3):
    try:
        return round(float(x), precision)
    except:
        return x

# ----------------------------
# File text extraction helpers
# ----------------------------

def extract_text_from_pdf(file_path):
    """Extract text from a PDF file using PyPDF2."""
    text = ""
    try:
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ""
    except Exception as e:
        text = f"[ERROR extracting PDF text: {e}]"
    return text.strip()

def extract_text_from_docx(file_path):
    """Extract text from a DOCX file using python-docx."""
    text = ""
    try:
        doc = docx.Document(file_path)
        for para in doc.paragraphs:
            text += para.text + "\n"
    except Exception as e:
        text = f"[ERROR extracting DOCX text: {e}]"
    return text.strip()

def extract_text_from_txt(file_path, encoding="utf-8"):
    """Extract text from a TXT file."""
    text = ""
    try:
        with open(file_path, "r", encoding=encoding, errors="ignore") as f:
            text = f.read()
    except Exception as e:
        text = f"[ERROR extracting TXT text: {e}]"
    return text.strip()

def extract_text_from_file(file_path):
    """Auto-detect file type (by extension) and extract text accordingly."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext in [".txt", ".md", ".rtf"]:
        return extract_text_from_txt(file_path)
    else:
        return f"[Unsupported file type: {ext}]"

