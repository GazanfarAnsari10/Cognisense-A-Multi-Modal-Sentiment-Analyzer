const SUPPORTED_EXTENSIONS = new Set([
  ".txt", ".pdf", ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aiff", ".aif",
  ".mp4", ".mov", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png", ".webp",
]);

export class CognisenseApiError extends Error {
  constructor(message, { status = 0, code = "request_failed", requestId = null, response = null } = {}) {
    super(message);
    this.name = "CognisenseApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.response = response;
  }
}

export class CognisenseClient {
  constructor({ baseUrl, projectId, apiKey, fetchImpl = globalThis.fetch, timeoutMs = 30_000 } = {}) {
    if (typeof baseUrl !== "string" || !baseUrl.trim()) {
      throw new TypeError("baseUrl is required.");
    }
    if (typeof projectId !== "string" || !projectId.trim()) {
      throw new TypeError("projectId is required.");
    }
    if (typeof apiKey !== "string" || apiKey.length < 16) {
      throw new TypeError("apiKey must contain at least 16 characters.");
    }
    if (typeof fetchImpl !== "function") {
      throw new TypeError("A Fetch API implementation is required.");
    }
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new TypeError("timeoutMs must be a positive number.");
    }

    const parsedBaseUrl = new URL(baseUrl);
    if (!new Set(["http:", "https:"]).has(parsedBaseUrl.protocol)) {
      throw new TypeError("baseUrl must use HTTP or HTTPS.");
    }

    this.endpoint = `${baseUrl.replace(/\/+$/, "")}/api/v1/analyze`;
    this.projectId = projectId;
    this.apiKey = apiKey;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  analyzeText(text) {
    return this.analyze({ text });
  }

  analyzeFiles(files, { text } = {}) {
    return this.analyze({ files, text });
  }

  async analyze({ text, files = [] } = {}) {
    if (text !== undefined && typeof text !== "string") {
      throw new TypeError("text must be a string when provided.");
    }
    if (!Array.isArray(files)) {
      throw new TypeError("files must be an array.");
    }
    if (!text?.trim() && files.length === 0) {
      throw new TypeError("Provide non-empty text or at least one file.");
    }
    if (files.length > 5) {
      throw new TypeError("A maximum of five files is allowed.");
    }

    const headers = { "X-API-Key": this.apiKey };
    let body;
    if (files.length === 0) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify({ project_id: this.projectId, text });
    } else {
      body = new FormData();
      body.append("project_id", this.projectId);
      if (text?.trim()) body.append("text", text);
      for (const [index, input] of files.entries()) {
        const file = this._normalizeFile(input, index);
        body.append("files", file.blob, file.filename);
      }
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(this.endpoint, {
        method: "POST",
        headers,
        body,
        signal: controller.signal,
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }

      if (!response.ok) {
        throw new CognisenseApiError(
          payload?.error?.message || `Cognisense request failed with HTTP ${response.status}.`,
          {
            status: response.status,
            code: payload?.error?.code || "request_failed",
            requestId: payload?.request_id || null,
            response: payload,
          },
        );
      }
      if (payload?.success !== true || !Array.isArray(payload?.data?.results)) {
        throw new CognisenseApiError("Cognisense returned an invalid analysis response.", {
          status: response.status,
          code: "invalid_response",
          requestId: payload?.request_id || null,
          response: payload,
        });
      }
      return payload;
    } finally {
      clearTimeout(timeout);
    }
  }

  _normalizeFile(input, index) {
    if (typeof Blob === "undefined") {
      throw new TypeError("This runtime does not support Blob uploads.");
    }
    const blob = input instanceof Blob ? input : input?.blob ?? input?.data;
    const filename = input instanceof Blob ? input.name : input?.filename ?? input?.name;
    if (!(blob instanceof Blob)) {
      throw new TypeError(`files[${index}] must be a File or an object containing a Blob.`);
    }
    if (typeof filename !== "string" || !filename.trim()) {
      throw new TypeError(`files[${index}] must include a filename.`);
    }
    const extension = filename.includes(".") ? filename.slice(filename.lastIndexOf(".")).toLowerCase() : "";
    if (!SUPPORTED_EXTENSIONS.has(extension)) {
      throw new TypeError(`Unsupported file type: ${extension || "unknown"}.`);
    }
    return { blob, filename };
  }
}

export const supportedExtensions = Object.freeze([...SUPPORTED_EXTENSIONS]);