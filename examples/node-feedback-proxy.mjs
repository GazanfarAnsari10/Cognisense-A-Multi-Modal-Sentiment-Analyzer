import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { CognisenseClient } from "../sdk/cognisense-client.mjs";

const MAX_BODY_BYTES = 16 * 1024;
const MAX_FEEDBACK_LENGTH = 10_000;
const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEMO_ASSETS = new Map([
  ["/", [join(PROJECT_ROOT, "widget-demo.html"), "text/html; charset=utf-8"]],
  ["/widget-demo.html", [join(PROJECT_ROOT, "widget-demo.html"), "text/html; charset=utf-8"]],
  ["/sdk/cognisense-widget.mjs", [join(PROJECT_ROOT, "sdk", "cognisense-widget.mjs"), "text/javascript; charset=utf-8"]],
  ["/sdk/cognisense-widget.css", [join(PROJECT_ROOT, "sdk", "cognisense-widget.css"), "text/css; charset=utf-8"]],
]);

function sendJson(response, status, payload) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

export function createFeedbackProxy({ client, maxBodyBytes = MAX_BODY_BYTES } = {}) {
  if (!client || typeof client.analyzeText !== "function") {
    throw new TypeError("A Cognisense client is required.");
  }
  if (!Number.isInteger(maxBodyBytes) || maxBodyBytes <= 0) {
    throw new TypeError("maxBodyBytes must be a positive integer.");
  }

  return createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://localhost").pathname;
    if (request.method === "GET" || request.method === "HEAD") {
      const asset = DEMO_ASSETS.get(pathname);
      if (!asset) {
        sendJson(response, 404, { success: false, error: { code: "not_found", message: "Route not found." } });
        return;
      }
      const [filename, contentType] = asset;
      try {
        const content = await readFile(filename);
        response.writeHead(200, { "Content-Type": contentType, "Cache-Control": "no-store" });
        response.end(request.method === "HEAD" ? undefined : content);
      } catch {
        sendJson(response, 500, { success: false, error: { code: "asset_unavailable", message: "Demo asset could not be read." } });
      }
      return;
    }
    if (request.method !== "POST" || pathname !== "/api/cognisense-feedback") {
      sendJson(response, 404, { success: false, error: { code: "not_found", message: "Route not found." } });
      return;
    }
    if (!request.headers["content-type"]?.toLowerCase().startsWith("application/json")) {
      sendJson(response, 415, { success: false, error: { code: "unsupported_media_type", message: "Send JSON feedback." } });
      return;
    }

    const chunks = [];
    let bodySize = 0;
    for await (const chunk of request) {
      bodySize += chunk.length;
      if (bodySize <= maxBodyBytes) chunks.push(chunk);
    }
    if (bodySize > maxBodyBytes) {
      sendJson(response, 413, { success: false, error: { code: "request_too_large", message: "Feedback is too large." } });
      return;
    }

    let payload;
    try {
      payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      sendJson(response, 400, { success: false, error: { code: "invalid_request", message: "Request body must be valid JSON." } });
      return;
    }
    const text = payload?.text;
    if (typeof text !== "string" || !text.trim() || text.length > MAX_FEEDBACK_LENGTH) {
      sendJson(response, 422, { success: false, error: { code: "validation_error", message: `text must contain 1 to ${MAX_FEEDBACK_LENGTH} characters.` } });
      return;
    }

    try {
      const result = await client.analyzeText(text.trim());
      sendJson(response, 200, result);
    } catch {
      sendJson(response, 502, {
        success: false,
        error: { code: "upstream_error", message: "Feedback analysis is temporarily unavailable." },
      });
    }
  });
}

async function startExampleServer() {
  const { COGNISENSE_API_URL, COGNISENSE_PROJECT_ID, COGNISENSE_API_KEY } = process.env;
  if (!COGNISENSE_API_URL || !COGNISENSE_PROJECT_ID || !COGNISENSE_API_KEY) {
    throw new Error("Set COGNISENSE_API_URL, COGNISENSE_PROJECT_ID, and COGNISENSE_API_KEY.");
  }
  const client = new CognisenseClient({
    baseUrl: COGNISENSE_API_URL,
    projectId: COGNISENSE_PROJECT_ID,
    apiKey: COGNISENSE_API_KEY,
  });
  const server = createFeedbackProxy({ client });
  const port = Number(process.env.PORT || 3000);
  server.listen(port, process.env.HOST || "127.0.0.1", () => {
    console.log(`Cognisense feedback proxy listening on ${port}`);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  startExampleServer().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}