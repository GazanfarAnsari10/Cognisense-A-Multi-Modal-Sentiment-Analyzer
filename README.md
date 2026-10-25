<h1>Cognisense: A Multi-Modal Sentiment Analyzer</h1>

Cognisense is an AI-powered Multi-Modal Sentiment Analysis system that detects and analyzes human emotions using multiple input modalities including text, audio, image, and video. The system combines Natural Language Processing (NLP), Computer Vision, and Audio Signal Processing techniques to generate comprehensive sentiment insights.

<hr>

<h2><b>Project Overview:</b></h2>

Traditional sentiment analysis focuses only on text. Cognisense goes beyond that by integrating:
<ul>
<li>Text Sentiment Analysis</li>
<li>Audio Emotion Detection</li>
<li>Image-Based Facial Emotion Recognition</li>
<li>Video Sentiment Analysis</li>
<li>Real-Time Video Emotion Analysis</li>
</ul>

By combining multiple modalities, Cognisense provides more accurate and context-aware sentiment predictions.


<hr>

<h2>Features</h2>
<ul>
<li>Multi-modal emotion prediction</li>
<li>Real-time detection using webcam</li>
<li>Interactive user interface</li>
<li>Modular analyzer architecture</li>
<li>Visualization using charts</li>
</ul>

<hr>

<h2>Project Structure</h2>

<pre>
Cognisense/
│
├── analyzers/
│   ├── text_analyzer.py
│   ├── audio_analyzer.py
│   ├── image_analyzer.py
│   ├── video_analyzer.py
│   └── realtime_video_analyzer.py
│
├── static/
│   ├── css/styles.css
│   └── js/main.js
│
├── templates/
│
├── app.py
├── utils.py
├── requirements.txt
└── README.md
</pre>

<hr>

<h2>Technologies Used</h2>
<ul>
<li>Python</li>
<li>Flask</li>
<li>TensorFlow</li>
<li>OpenCV</li>
<li>Librosa</li>
<li>Scikit-learn</li>
<li>HTML, CSS, JavaScript</li>
</ul>

<hr>

<h2>Installation</h2>

<h3>Clone Repository</h3>
<pre>git clone https://github.com/GazanfarAnsari10/Cognisense-A-Multi-Modal-Sentiment-Analyzer.git</pre>

<h3>Create Virtual Environment</h3>
<pre>python -m venv venv</pre>

<h3>Activate Environment (Windows)</h3>
<pre>venv\Scripts\activate</pre>

<h3>Install Dependencies</h3>
<pre>pip install -r requirements.txt</pre>

<h3>Run Application</h3>
<pre>python app.py</pre>

<hr>

<h2>How It Works</h2>
<ul>
<li>Text processed using NLP models</li>
<li>Audio analyzed using MFCC and spectral features</li>
<li>Images and video processed using facial emotion recognition</li>
<li>Real-time webcam emotion detection</li>
</ul>

<hr>

<h2>Use Cases</h2>
<ul>
<li>Social media sentiment monitoring</li>
<li>Customer feedback analysis</li>
<li>Mental health analysis</li>
<li>Human-computer interaction</li>
<li>Market research</li>
</ul>

<hr>

<h2>Future Enhancements</h2>
<ul>
<li>Multimodal deep learning fusion model</li>
<li>Cloud deployment</li>
<li>API integration</li>
<li>Faster inference optimization</li>
</ul>

<hr>

<b>Authors</b>

<p>
<b>Mohammad Gazanfar Ansari</b><br>
B.E– Artificial Intelligence & Machine Learning
<br>
<a href="https://www.linkedin.com/in/mohammad-gazanfar-ansari" target="_blank">
LinkedIn</a>
&nbsp; | &nbsp;
<a href="mailto:gazi.freestyle@gmail.com">
Email</a>
</p>


<p>
<b>Sachin Chandraprakash Kanojiya</b><br>
B.E– Artificial Intelligence & Machine Learning
<br>
<a href="https://www.linkedin.com/in/sachin-kanojiya-5b8234372/" target="_blank">
LinkedIn</a>
&nbsp; | &nbsp;
<a href="mailto:Sachinkanojiya647@gmail.com">
Email</a>
</p>

<p>
<b>Suyash vivek ketkar </b><br>
B.E– Artificial Intelligence & Machine Learning
<br>
<a href="https://www.linkedin.com/in/suyashketkar/" target="_blank">
LinkedIn</a>
&nbsp; | &nbsp;
<a href="mailto:suyashketkar@gmail.com">
Email</a>

<p>
<b>Chhaya Achhelal Rajbhar </b><br>
B.E– Artificial Intelligence & Machine Learning
<br>
<a href="https://www.linkedin.com/in/chhaya-rajbhar-663656349/" target="_blank">
LinkedIn</a>
&nbsp; | &nbsp;
<a href="mailto:chhayarajbhar34@gmail.com">
Email</a>
</p>
<!-- 
<hr> -->

<!-- <h2>Connect With Me </h2>

<p>
<a href="https://www.linkedin.com/in/mohammad-gazanfar-ansari" target="_blank">
LinkedIn</a>
&nbsp; | &nbsp;
<a href="mailto:gazi.freestyle@gmail.com">
Email</a>
</p> -->

<!-- <hr> -->

<!-- <h2>License</h2> --><br>
<p>This project is for academic and research purposes.</p><hr>

## Feedback Gateway API

The separate FastAPI gateway exposes the existing Cognisense analyzers without changing the Flask web application. It supports plain text, TXT/PDF documents, audio, video, and images; image inputs are included because the repository already provides an image analyzer. Multiple modalities can be sent in one request. Each input is analyzed independently and returned as a result entry (there is no cross-modal fusion model in the current analyzers).

### Setup

Install the project requirements, then set the gateway environment variables. Copy `.env.example` as a reference; the application does not automatically load a `.env` file.

PowerShell example:

```powershell
$env:COGNISENSE_API_KEYS='{"my-project":"replace-this-with-a-long-random-secret"}'
$env:COGNISENSE_CORS_ORIGINS='https://portal.example.com'
$env:COGNISENSE_MAX_REQUEST_BYTES='26214400'
uvicorn api_gateway:app --host 0.0.0.0 --port 8000
```

`COGNISENSE_API_KEYS` is a JSON object mapping project IDs to unique API keys (minimum 16 characters). Keep keys in a secret manager in deployment; do not commit real keys. CORS origins are optional and should list only trusted browser origins. The request-size limit defaults to 25 MiB and includes multipart overhead.

### Endpoints

- `GET /api/v1/health` — public health check.
- `POST /api/v1/analyze` — authenticated analysis. Supply `X-API-Key` and the matching `project_id`. Add `?export_format=json`, `?export_format=csv`, or `?export_format=pdf` to download the newly generated results instead of the regular JSON response.
- `GET /docs` — interactive OpenAPI documentation.

Send plain text as JSON:

```powershell
curl.exe https://api.example.com/api/v1/analyze `
  -H "X-API-Key: YOUR_PROJECT_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{ "project_id": "my-project", "text": "The support team was very helpful." }'
```

Download the same authenticated text analysis as a PDF (use `json` or `csv` for the other export formats):

```powershell
curl.exe "https://api.example.com/api/v1/analyze?export_format=pdf" `
  -H "X-API-Key: YOUR_PROJECT_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{ "project_id": "my-project", "text": "The support team was very helpful." }' `
  -o feedback-report.pdf
```

Send multiple modalities as multipart form data; repeat the `files` field for additional files:

```powershell
curl.exe https://api.example.com/api/v1/analyze `
  -H "X-API-Key: YOUR_PROJECT_API_KEY" `
  -F "project_id=my-project" `
  -F "text=Feedback from the customer" `
  -F "files=@feedback.txt" `
  -F "files=@meeting.wav"
```

Add `?export_format=csv` or `?export_format=json` to that multipart URL to download an export for those inputs. Exports are generated from the analyzer results within the authenticated request, so clients cannot request another project's report by submitting a result or request ID. Export responses include a download filename and their corresponding JSON, CSV, or PDF content type. JSON and CSV include chart-ready sentiment, word, sentence, emotion, and video-frame series where the analyzers provide those values. PDF reports include sentiment/emotion/frame visualizations when available, plus per-input labels, scores, summaries, reasoning, and modality-specific details. Missing chart data is omitted rather than treated as an error; CSV retains the original result JSON in its rows.

Multipart uploads are limited to five files. The gateway accepts TXT/PDF, WAV/MP3/M4A/OGG/FLAC/AIFF audio, MP4/MOV/AVI/MKV/WEBM video, and JPG/JPEG/PNG/WEBP images. Actual codec support depends on the installed MoviePy/FFmpeg and audio dependencies. Documents are sent to the existing text analyzer after extraction; other files are sent to their existing modality analyzer.

Responses use a consistent envelope:

```json
{
  "success": true,
  "request_id": "c46f...",
  "project_id": "my-project",
  "data": {
    "status": "complete",
    "results": [
      {
        "modality": "text",
        "source": "text",
        "result": { "overall": { "label": "positive", "score": 0.7 } }
      }
    ]
  }
}
```

Errors use `success: false`, the same `request_id`, and an `error` object with a stable code and message. The gateway validates extensions, UTF-8 TXT content, extracted PDF text, and request size. Set up HTTPS at the deployment boundary (for example, a TLS-enabled reverse proxy or managed hosting platform); do not expose API keys or send them over plain HTTP. Audio transcription retains the existing analyzer's dependency on the Google Web Speech API.

Run the focused gateway tests with:

```powershell
python -m unittest discover -s tests -v
```
