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
<a href="mailto:mohammadgazanfaransari@gmail.com">
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

## Phase 6B user authentication

The Flask application provides JSON email/password authentication at `/api/v1/auth/register`, `/api/v1/auth/login`, `/api/v1/auth/logout`, and `/api/v1/auth/me`. It uses the existing SQLAlchemy/PostgreSQL configuration and stores Argon2id password hashes only. Login creates a random opaque cookie token; only its SHA-256 digest is stored in `auth_sessions`. Logout revokes that server-side record. Login attempts are throttled by a database-backed email and client-address bucket.

### Authentication configuration

Set `AUTH_SECRET_KEY` to a unique random secret with at least 32 characters. Configure it through a deployment secret manager or an untracked local environment file. Do not commit its value. The default session lifetime is one hour. Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` by default; set `AUTH_COOKIE_SECURE=false` only for local HTTP development. `AUTH_COOKIE_SAMESITE` may be `Lax` or `Strict`.

Before `register`, `login`, or `logout`, fetch `GET /api/v1/auth/csrf`; submit the returned token in `X-CSRF-Token` and allow the `cognisense_csrf` cookie to be sent. The token cookie is signed and checked against the request header; authenticated logout also verifies the token is bound to its server-side session. `/me` requires the session cookie and returns only the user's name, email, and status.

```powershell
$env:AUTH_SECRET_KEY = '<at-least-32-character-random-secret>'
$env:AUTH_SESSION_TTL_SECONDS = '3600'
$env:AUTH_COOKIE_SECURE = 'false' # local HTTP only
$env:AUTH_COOKIE_SAMESITE = 'Lax'
$env:AUTH_LOGIN_MAX_ATTEMPTS = '5'
$env:AUTH_LOGIN_WINDOW_SECONDS = '900'
```

Apply the additive schema migration to the existing database (this does not reset or recreate it):

```powershell
alembic upgrade head
```

`POST /register` accepts `{ "name": "...", "email": "...", "password": "..." }`; valid registrations receive a generic `202` response whether or not the normalized email already exists. Passwords must contain 12–128 characters. `POST /login` accepts email and password and returns the same generic credentials error for unknown users and incorrect passwords. Login throttles return `429` without disclosing account status. No email verification or password-reset flow is included: both require properly configured email delivery, token lifecycle, and operational controls before they are safe to enable.

### Authentication tests

```powershell
python -m unittest tests.test_auth -v
```

These auth tests use a dedicated in-memory SQLite database instance created only by the test suite; application configuration still requires PostgreSQL and never falls back to SQLite. Production deployment must use HTTPS, retain the secret and database URL in a secret manager, review trusted reverse-proxy handling for client addresses, and periodically clean up expired/revoked session and login-attempt rows. Configure PostgreSQL availability, backups, monitoring, and pool limits for the deployment. The migration leaves `password_hash` nullable so pre-existing user rows are preserved; those accounts cannot log in until a separately secured credential-enrollment process is provided. Email verification, password reset, and account recovery are deferred because no email-delivery/token-lifecycle service is configured. This phase is not a complete production account-security system until those recovery/verification requirements and deployment controls have been reviewed.

## Phase 6C project management

The authenticated Flask platform exposes project endpoints at `/api/v1/projects`. Each project is owned by the user resolved from the server-side Phase 6B session; owner IDs and public project IDs cannot be supplied or changed by the caller. Public project IDs are random UUIDv4 identifiers. Project names are trimmed, required, and limited to 100 characters. Each user may have up to 20 active projects by default; configure `COGNISENSE_MAX_PROJECTS_PER_USER` to change this limit.

- `POST /api/v1/projects` — create with JSON `{ "name": "Customer Feedback" }`. Returns `201`.
- `GET /api/v1/projects` — list the current user's projects, including archived projects.
- `GET /api/v1/projects/<project_id>` — retrieve a project the current user owns.
- `PATCH /api/v1/projects/<project_id>` — rename with JSON `{ "name": "New Name" }`. Other fields are rejected.
- `DELETE /api/v1/projects/<project_id>` — archive the project by setting its status; it does not delete associated API keys or usage history.

All endpoints require a valid session cookie. For POST, PATCH, and DELETE, first obtain a CSRF token from `/api/v1/auth/csrf`, then pass it in `X-CSRF-Token` while sending the matching CSRF cookie. Projects belonging to other users return the same `404` response as missing projects.

No schema migration is required: the existing `projects` table already provides ownership, a unique public ID, name, status, and timestamps. Run the project tests using their isolated SQLite fixture (no configured PostgreSQL database is reset or altered):

```powershell
python -m unittest tests.test_projects -v
```

Phase 6C project management itself does not create or verify API keys, change gateway authentication, enforce quotas, or provide project UI.

## Phase 6D secure API key management

Authenticated project owners can manage multiple API keys using the existing `api_keys` schema. Project IDs in these routes are the public project IDs returned by the project API. A project must be active; key operations on archived projects return `409`. Projects and keys that are missing or belong to another user return the same `404` response as inaccessible resources.

- `POST /api/v1/projects/<project_id>/keys` — create a key, optionally with JSON `{ "expires_in_days": 30 }`. Omit the field for a key without an expiry. Valid expiry values are integers from 1 through 365 days. The successful `201` response contains safe metadata and the complete secret in `data.secret` exactly once.
- `GET /api/v1/projects/<project_id>/keys` — list safe metadata for the project's keys.
- `GET /api/v1/projects/<project_id>/keys/<key_id>` — retrieve safe metadata for one key.
- `POST /api/v1/projects/<project_id>/keys/<key_id>/revoke` — permanently revoke a key. Repeating the request is safe and does not reactivate or otherwise change the key.

All routes require the authenticated `cognisense_session` cookie. For the create and revoke POST requests, first obtain a CSRF token from `GET /api/v1/auth/csrf`, send the matching `cognisense_csrf` cookie, and include the same token in the `X-CSRF-Token` header. A server-side client sends these as a `Cookie` header and an `X-CSRF-Token` header; browsers send the cookies automatically when configured for the API origin. The API returns JSON errors with stable `code` and `message` fields.

Keys use the recognizable `cgs_live_` prefix. The returned metadata includes the key ID, non-secret prefix, creation time, expiry, last-used time, status, and revocation time/status; list and detail responses never include the secret or stored hash. The full key is generated from cryptographically secure randomness and is returned only after the database transaction has committed. Only a SHA-256 digest of the complete high-entropy key is stored. SHA-256 is suitable here because keys contain 256 bits of random secret entropy; Phase 6E verification should locate the record using its public prefix, hash the presented complete key, and compare the digest in constant time. This phase does not make these keys authorize gateway analysis requests.

The default maximum is 10 active keys per project. Set `COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT` to a positive integer to change it. Creation serializes on the owned project row in PostgreSQL so concurrent creates cannot exceed the configured active-key limit. Expired and revoked keys do not count toward the active limit. Expiry is optional and capped at 365 days.

API consumers should store the secret in a server-side secret manager, restrict access to the smallest set of services possible, use HTTPS, and rotate or revoke exposed keys promptly. **Never put API keys in frontend JavaScript, HTML, browser storage, or public build variables, and never commit keys to source control.** The key is shown once; if it is lost, revoke it and create a replacement.

Run isolated key-management tests with:

```powershell
python -m unittest tests.test_api_keys -v
```

## Phase 6A database foundation

This repository now includes the initial PostgreSQL foundation for the multi-user platform planned in later phases. The implementation is intentionally isolated from the current gateway so the existing credentials-based API behavior remains unchanged.

### PostgreSQL prerequisites

- Install PostgreSQL 14+ and make sure a reachable database server is available for local development and testing.
- Create a dedicated database, for example `cognisense`.
- Keep connection details in environment variables instead of storing credentials in source control.

### Environment variables

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/cognisense'
$env:DATABASE_ECHO = 'false'
$env:DATABASE_POOL_SIZE = '5'
$env:DATABASE_MAX_OVERFLOW = '10'
$env:DATABASE_POOL_TIMEOUT = '30'
$env:DATABASE_CONNECT_TIMEOUT = '10'
```

### Database initialization and migration commands

```powershell
# create the database if needed, then apply migrations
alembic upgrade head
```

If the project is being set up from scratch, install the project dependencies first and then run Alembic with the configured PostgreSQL URL.

### Schema overview

The initial schema creates the following tables:

- `users` — internal user accounts and account status.
- `projects` — project metadata and ownership, including a unique public project identifier.
- `api_keys` — project-scoped API keys with hashed secrets, prefixes, expiry, and revocation metadata.
- `usage_records` — request summaries without storing full request bodies, transcripts, or uploaded files.
- `plans` — reusable platform policy data for request limits, quotas, and active-key limits.

The `projects` table references `users`, and `api_keys` and `usage_records` reference `projects`. Foreign keys use `RESTRICT` so audit and usage history is not silently erased when related records are removed.

### Running database tests

A dedicated PostgreSQL test database should be configured through `TEST_DATABASE_URL` to run the migration test. The database name must include `test`; the test never migrates the ordinary `DATABASE_URL`. The migration test skips when no dedicated test database is configured. Auth behavior tests use an isolated in-memory SQLite database and do not alter PostgreSQL.

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/cognisense_test'
python -m unittest tests.test_database_setup -v
```

### Deployment considerations

- Use a dedicated PostgreSQL service or managed database.
- Keep connection strings in a secret manager or environment management system.
- Configure pool limits and timeouts for production workloads.
- Do not add plaintext API keys, passwords, or raw request payloads to the platform schema.
- Keep the platform authentication endpoints separate from the existing API-key gateway contract.

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

## JavaScript SDK and feedback widget

The dependency-free SDK is for trusted server-side JavaScript runtimes with `fetch`, `FormData`, and `Blob` support (Node.js 18 or newer). It calls the existing `POST /api/v1/analyze` gateway endpoint and returns the gateway response envelope unchanged. File modality is selected by the gateway from the filename extension; the SDK does not analyze or classify content.

### Secure configuration

Configure the SDK only in your application backend. Keep `COGNISENSE_API_KEY` in a server-side secret manager or environment variable, and never place it in browser JavaScript, HTML, a public build variable, or a source-controlled file. The browser widget posts to a same-origin endpoint owned by your application; that backend attaches the secret and calls Cognisense. Use HTTPS, validate feedback server-side, and apply your application's authentication, rate limiting, abuse controls, and retention policy to the proxy route.

The included Node demo server serves the demo page and widget assets and handles the same-origin feedback POST. It has no npm dependencies. Do not use `python -m http.server` for this demo: Python's static server cannot handle the widget's POST request.

From the repository root, start the gateway in **PowerShell terminal 1**:

```powershell
$env:COGNISENSE_API_KEYS = '{"demo-project":"local-demo-key-change-before-deploy"}'
.\.venv\Scripts\python.exe -m uvicorn api_gateway:app --host 127.0.0.1 --port 8000
```

In **PowerShell terminal 2**, start the demo server on port 5500 with the matching project ID and key:

```powershell
$env:COGNISENSE_API_URL = 'http://127.0.0.1:8000'
$env:COGNISENSE_PROJECT_ID = 'demo-project'
$env:COGNISENSE_API_KEY = 'local-demo-key-change-before-deploy'
$env:HOST = '127.0.0.1'
$env:PORT = '5500'
node .\examples\node-feedback-proxy.mjs
```

Open <http://127.0.0.1:5500/> and submit feedback. The browser sends it to `http://127.0.0.1:5500/api/cognisense-feedback`; the Node server adds the API key and forwards it to the gateway at port 8000. The key is not served to the browser. The local example key is for development only; use a generated secret and HTTPS for deployment.

For deployment, run the proxy behind your website's HTTPS reverse proxy and expose `/api/cognisense-feedback` on the same origin as the page. The example accepts JSON `{ "text": "..." }`, limits body size and text length, and returns the gateway's standardized result envelope. It intentionally does not enable cross-origin access.

### SDK usage

Import the SDK from a server-side `.mjs` module:

```js
import { CognisenseClient } from "./sdk/cognisense-client.mjs";

const cognisense = new CognisenseClient({
  baseUrl: process.env.COGNISENSE_API_URL,
  projectId: process.env.COGNISENSE_PROJECT_ID,
  apiKey: process.env.COGNISENSE_API_KEY,
  timeoutMs: 30_000,
});

const textResult = await cognisense.analyzeText("The team resolved my issue quickly.");
```

For files, pass browser `File`/`Blob` values or `{ filename, data: Blob }` objects. The gateway accepts up to five files in one call; supported extensions are TXT/PDF, WAV/MP3/M4A/OGG/FLAC/AIFF/AIF, MP4/MOV/AVI/MKV/WEBM, and JPG/JPEG/PNG/WEBP.

```js
import { readFile } from "node:fs/promises";

const audio = new Blob([await readFile("./call.wav")], { type: "audio/wav" });
const result = await cognisense.analyze({
  text: "Notes from the call",
  files: [{ filename: "call.wav", data: audio }],
});
console.log(result.data.status, result.data.results);
```

Use `analyzeFiles(files, { text })` for file-only or mixed uploads. `analyzeText(text)` sends JSON; file requests use multipart form data, with the boundary generated by `fetch`. Empty input, unsupported extensions, malformed file objects, and more than five files are rejected before making a request.

### Embeddable widget

Load the widget as an ES module on your page. Keep its endpoint same-origin and point it to your application proxy, never directly to the authenticated Cognisense gateway:

```html
<div id="customer-feedback"></div>
<link rel="stylesheet" href="/sdk/cognisense-widget.css">
<script type="module">
  import { mountCognisenseWidget } from "/sdk/cognisense-widget.mjs";

  mountCognisenseWidget({
    target: "#customer-feedback",
    endpoint: "/api/cognisense-feedback",
    title: "Tell us what you think",
    prompt: "How did we do?",
    submitLabel: "Send feedback",
  });
</script>
```

The widget sends only `{ text }`, provides accessible status messages, and accepts custom `title`, `prompt`, `placeholder`, `submitLabel`, `successMessage`, `errorMessage`, and `onError` options. Its proxy route should use the server SDK shown above. The widget enforces a same-origin endpoint and never accepts or stores a Cognisense API key.

### Errors and tests

SDK HTTP failures throw `CognisenseApiError` with `status`, `code`, `requestId`, and the parsed gateway `response`; authentication errors such as 401/403 preserve the gateway error code. Network and timeout errors are surfaced by `fetch`. The widget shows a generic failure message and can report the underlying error to `onError`.

Run the dependency-free JavaScript SDK and widget tests with Node.js 18 or newer:

```powershell
node --test tests/*.test.mjs
```
