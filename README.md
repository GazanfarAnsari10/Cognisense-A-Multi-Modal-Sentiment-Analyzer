# Cognisense — Multimodal Sentiment Analysis & Developer Platform

Cognisense is an AI-powered multimodal sentiment analysis system that analyzes human emotions and sentiment across text, audio, images, and video.

Originally developed as an academic project, Cognisense has evolved into a developer-oriented platform with its own API gateway, SDK, API key management, and developer dashboard — making its analysis capabilities easier to integrate into external applications.

---

## Overview

Traditional sentiment analysis primarily focuses on text. Cognisense extends this approach by combining Natural Language Processing, Computer Vision, and Audio Signal Processing to analyze multiple forms of input.

### Supported modalities

| Modality | Capabilities |
|---|---|
| Text | Sentiment and emotion-related analysis |
| Audio | Speech and audio emotion analysis |
| Images | Facial emotion recognition |
| Video | Video-based sentiment and facial emotion analysis |
| Real-time video | Webcam-based emotion analysis |

Each modality uses its respective analyzer. Multiple inputs can also be submitted through the API and analyzed independently.

## Features

### Multimodal Analysis
- Text sentiment analysis.
- Audio emotion detection and transcription.
- Image-based facial emotion recognition.
- Video sentiment and frame-based analysis.
- Real-time webcam emotion analysis.

### Visualization & Reporting
- Interactive sentiment visualizations.
- Emotion and sentiment data where provided by the analyzers.
- JSON, CSV, and PDF report exports through the API.
- Modality-specific analysis results.

### Developer Platform
- Dedicated REST API gateway.
- Project-based API key management.
- Custom JavaScript SDK.
- Developer dashboard with usage statistics.
- Request tracking and rate limiting.
- Daily API quotas and configurable usage plans.
- Account authentication and session management.
- Admin dashboard for platform and plan management.
- Embeddable feedback widget.

---

## Developer Access

Cognisense includes a developer platform designed to make its analysis capabilities accessible without requiring external developers to clone and run the entire application.

The platform provides a centralized workflow for managing projects, API keys, and API usage.

### How it works

1. Create an account.
2. Create a project.
3. Generate a project-specific API key.
4. Configure the SDK in your backend application.
5. Submit analysis requests to the Cognisense API.
6. Monitor request activity and usage through the developer dashboard.

### Developer Dashboard

The dashboard provides:

- Project management.
- API key generation, inspection, and revocation.
- API usage statistics.
- Recent request history.
- HTTP status tracking.
- Applicable rate limits and daily quotas.
- Project plan management.

API keys are displayed in full only when generated. Subsequent dashboard views expose metadata rather than the complete secret.

### Deployment Status

The developer platform and integration workflow are currently being tested locally.

Production hardening and deployment preparations are underway. Public deployment will follow once the necessary testing and infrastructure checks are complete.

---

## Technology Stack

| Category | Technologies |
|---|---|
| Backend | Python, Flask, FastAPI |
| AI / Machine Learning | TensorFlow, Scikit-learn |
| Computer Vision | OpenCV, FER |
| Audio Processing | Librosa |
| Database | PostgreSQL |
| ORM & Migrations | SQLAlchemy, Alembic |
| Authentication | Argon2id, SHA-256 |
| SDK | JavaScript, Node.js |
| Frontend | HTML, CSS, JavaScript |
| API Documentation | OpenAPI / Swagger |

---

## Architecture

Cognisense consists of three primary layers:

**1. Analysis Engine**

The original Flask application handles multimodal analysis through independent analyzers for text, audio, images, video, and real-time video.

**2. Developer Platform**

The Flask-based platform provides user authentication, project management, API key administration, plan management, and usage visibility.

**3. API Gateway & Integration Layer**

A separate FastAPI gateway exposes the analysis capabilities through authenticated REST endpoints. A JavaScript SDK and an embeddable feedback widget provide additional integration options.

The platform and gateway share a PostgreSQL database for project ownership, API key verification, plans, and usage records.

---

## Getting Started

The following instructions are intended for local development and self-hosting.

### Prerequisites

- Python 3.10 or newer.
- PostgreSQL 14 or newer.
- Node.js 18 or newer (for the JavaScript SDK and demo).
- FFmpeg and compatible audio/video dependencies where required.
- Git.

### 1. Clone the repository

```bash
git clone https://github.com/GazanfarAnsari10/Cognisense-A-Multi-Modal-Sentiment-Analyzer.git

cd Cognisense-A-Multi-Modal-Sentiment-Analyzer
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure PostgreSQL

Create a PostgreSQL database named `cognisense`.

Configure the database connection and authentication secret through environment variables or an untracked `.env` file.

Example PowerShell configuration:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@localhost:5432/cognisense'

$env:AUTH_SECRET_KEY = '<unique-random-secret-at-least-32-characters>'

$env:AUTH_COOKIE_SECURE = 'false'
```

`AUTH_COOKIE_SECURE=false` is intended for local HTTP development only. Use `true` when deploying over HTTPS.

### 5. Apply database migrations

```bash
alembic upgrade head
```

This applies the database schema migrations without recreating the existing database.

### 6. Start the Flask application

```bash
python app.py
```

### 7. Start the API gateway

Open a separate terminal from the project root:

```bash
python -m uvicorn api_gateway:app --host 127.0.0.1 --port 8000
```

The Flask application and FastAPI gateway must use the same PostgreSQL database.

The local application and API documentation are accessible at:

- Flask application: `http://localhost:5000`
- Developer Access: `http://localhost:5000/developer`
- API documentation: `http://127.0.0.1:8000/docs`
- Gateway health: `http://127.0.0.1:8000/api/v1/health`

---

## Feedback Gateway API

The separate FastAPI gateway exposes the existing Cognisense analyzers through a REST API.

### Authentication

Analysis requests require:

- An active project API key.
- The corresponding project ID.

Pass the API key through the `X-API-Key` request header.

Database-managed API keys use the `cgs_live_` prefix. The gateway verifies these keys against the shared PostgreSQL database.

### API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | Gateway health check |
| POST | `/api/v1/analyze` | Submit analysis requests |
| GET | `/docs` | Interactive API documentation |

### Supported Inputs

The analysis endpoint accepts JSON and multipart form-data requests.

| Input | Supported formats |
|---|---|
| Text | Plain text |
| Documents | TXT, PDF |
| Audio | WAV, MP3, M4A, OGG, FLAC, AIFF |
| Video | MP4, MOV, AVI, MKV, WEBM |
| Images | JPG, JPEG, PNG, WEBP |

Multiple files can be submitted in one request, with a maximum of five files per request.

Documents are processed through the text analyzer after text extraction. Other file types are routed to their corresponding modality analyzers.

### Example: Text Analysis

```bash
curl.exe http://127.0.0.1:8000/api/v1/analyze `
  -H "X-API-Key: YOUR_PROJECT_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{ "project_id": "YOUR_PROJECT_ID", "text": "The support team was very helpful." }'
```

### Example: Multiple Inputs

```bash
curl.exe http://127.0.0.1:8000/api/v1/analyze `
  -H "X-API-Key: YOUR_PROJECT_API_KEY" `
  -F "project_id=YOUR_PROJECT_ID" `
  -F "text=Customer feedback" `
  -F "files=@feedback.txt" `
  -F "files=@meeting.wav"
```

### Response Format

The gateway returns a standardized JSON response envelope.

```json
{
  "success": true,
  "request_id": "c46f...",
  "project_id": "YOUR_PROJECT_ID",
  "data": {
    "status": "complete",
    "results": [
      {
        "modality": "text",
        "source": "text",
        "result": {
          "overall": {
            "label": "positive",
            "score": 0.7
          }
        }
      }
    ]
  }
}
```

The example illustrates the response structure; actual analyzer output varies by modality.

### Export Options

Analysis results can be exported by specifying the `export_format` query parameter.

| Format | Parameter |
|---|---|
| JSON | `?export_format=json` |
| CSV | `?export_format=csv` |
| PDF | `?export_format=pdf` |

Exports are generated directly from the authenticated analysis request.

Chart-ready sentiment, emotion, and frame-series data are included where the underlying analyzers provide them. Missing visualization data is omitted rather than fabricated.

---

## API Usage & Rate Limiting

Cognisense implements project-level request tracking, rate limiting, and daily quotas.

- Rate limits apply across all API keys belonging to a project.
- Requests are tracked in PostgreSQL.
- Configurable requests-per-minute limits.
- Configurable rolling 24-hour quotas.
- HTTP `429` responses when limits are exceeded.
- `Retry-After` headers communicate when another request may be admitted.

Plan-based limits take precedence over platform environment defaults.

The developer dashboard provides visibility into recorded requests, usage statistics, and applicable limits.

---

## JavaScript SDK

Cognisense includes a dependency-free JavaScript SDK for server-side JavaScript applications running Node.js 18 or newer.

The SDK provides a convenient interface to the Cognisense analysis gateway without requiring developers to construct API requests manually.

### Configuration

Configure the following environment variables in your application backend:

```env
COGNISENSE_API_URL=http://127.0.0.1:8000
COGNISENSE_PROJECT_ID=YOUR_PROJECT_ID
COGNISENSE_API_KEY=YOUR_PROJECT_API_KEY
```

Never expose API keys through frontend JavaScript, HTML, browser storage, or public build variables.

### Example Usage

```javascript
import { CognisenseClient } from "./sdk/cognisense-client.mjs";

const cognisense = new CognisenseClient({
  baseUrl: process.env.COGNISENSE_API_URL,
  projectId: process.env.COGNISENSE_PROJECT_ID,
  apiKey: process.env.COGNISENSE_API_KEY,
  timeoutMs: 30000
});

const result = await cognisense.analyzeText(
  "The team resolved my issue quickly."
);

console.log(result.data.results);
```

The SDK also supports file-based and mixed-input analysis through its `analyze()` method.

### Embeddable Feedback Widget

Cognisense includes an embeddable feedback widget for applications that want to collect customer feedback and analyze it through Cognisense.

The widget communicates with a same-origin backend proxy, which attaches the API key and forwards the request to the Cognisense gateway.

This keeps the API key on the server rather than exposing it to the browser.

The widget supports configurable titles, prompts, placeholders, submit labels, status messages, and error callbacks.

---

## Authentication & Security

The developer platform uses server-side session authentication and project-scoped API key management.

### Account Authentication

- Email/password registration and login.
- Argon2id password hashing.
- Opaque, HttpOnly session cookies.
- Server-side session storage and revocation.
- CSRF protection for state-changing operations.
- Database-backed login attempt throttling.

Email verification, password reset, and account recovery workflows are not currently included.

### API Key Security

- Cryptographically secure key generation.
- SHA-256 hashing of API key secrets.
- Keys displayed in full only upon creation.
- API key expiry and revocation.
- Project ownership validation.
- Generic authentication errors for invalid or inaccessible keys.

### Important Security Considerations

- Never commit API keys, passwords, database credentials, or authentication secrets.
- Keep API keys exclusively in trusted server-side environments.
- Use HTTPS for deployed environments.
- Store production secrets in a dedicated secret manager.
- Restrict database access and maintain regular backups.
- Configure trusted reverse proxies and gateway access appropriately.

The real-time webcam analyzer uses a separate application-level access path; API gateway key authentication should not be assumed to protect that interface.

---

## Testing

The repository includes Python and JavaScript regression tests.

Run the Python test suite:

```bash
python -m unittest discover -s tests -v
```

Run the JavaScript SDK and widget tests:

```bash
node --test tests/*.test.mjs
```

PostgreSQL migration integration tests require a dedicated test database configured through `TEST_DATABASE_URL`. Its database name must contain `test`.

**Never run migration integration tests against a development or production database.**

---

## Known Limitations

- Multimodal inputs are analyzed independently; the current analyzers do not implement cross-modal fusion.
- Analysis results and emotion visualizations depend on the data returned by the respective analyzers.
- Audio transcription retains the existing dependency on the Google Web Speech API.
- Audio/video processing capabilities depend on installed codecs and system dependencies.
- The developer platform does not currently include billing or payment integrations.
- Production deployment and infrastructure validation are still in progress.

---

## Future Improvements

- Multimodal deep-learning fusion.
- Further inference and processing optimizations.
- Expanded developer integrations.
- Production deployment and infrastructure improvements.

---

## Author

**Mohammad Gazanfar Ansari**

B.E. — Artificial Intelligence & Machine Learning

[LinkedIn](https://www.linkedin.com/in/mohammad-gazanfar-ansari) | [Email](mailto:mohammadgazanfaransari@gmail.com)

---

## License & Usage

This project was developed for academic and research purposes.

Please refer to the repository's license, if provided, for applicable usage and distribution permissions.
