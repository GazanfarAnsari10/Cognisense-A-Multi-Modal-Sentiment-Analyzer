import assert from "node:assert/strict";
import test from "node:test";
import { CognisenseApiError, CognisenseClient } from "../sdk/cognisense-client.mjs";

const API_KEY = "test-secret-key-that-is-long-enough";
const success = {
  success: true,
  request_id: "request-123",
  project_id: "project-test",
  data: { status: "complete", results: [{ modality: "text", result: { overall: { label: "positive", score: 0.8 } } }] },
};

function makeClient(fetchImpl) {
  return new CognisenseClient({
    baseUrl: "https://gateway.example/",
    projectId: "project-test",
    apiKey: API_KEY,
    fetchImpl,
  });
}

test("sends text JSON through the gateway and returns its response envelope", async () => {
  let request;
  const client = makeClient(async (url, options) => {
    request = { url, ...options };
    return new Response(JSON.stringify(success), { status: 200 });
  });

  const response = await client.analyzeText("Helpful support");

  assert.equal(request.url, "https://gateway.example/api/v1/analyze");
  assert.equal(request.method, "POST");
  assert.equal(request.headers["X-API-Key"], API_KEY);
  assert.equal(request.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(request.body), { project_id: "project-test", text: "Helpful support" });
  assert.deepEqual(response, success);
});

test("sends files as multipart without setting its content type", async () => {
  let request;
  const client = makeClient(async (url, options) => {
    request = { url, ...options };
    return new Response(JSON.stringify(success), { status: 200 });
  });
  const audio = new Blob(["audio bytes"], { type: "audio/wav" });

  await client.analyze({ text: "Interview notes", files: [{ filename: "call.wav", data: audio }] });

  assert.equal(request.headers["Content-Type"], undefined);
  assert.equal(request.body.get("project_id"), "project-test");
  assert.equal(request.body.get("text"), "Interview notes");
  assert.equal(request.body.getAll("files")[0].name, "call.wav");
});

test("supports all gateway file extension families", async () => {
  const client = makeClient(async () => new Response(JSON.stringify(success), { status: 200 }));
  const extensions = ["txt", "pdf", "wav", "mp3", "m4a", "ogg", "flac", "aiff", "aif", "mp4", "mov", "avi", "mkv", "webm", "jpg", "jpeg", "png", "webp"];
  const files = extensions.map((extension) => ({
    filename: `input.${extension}`,
    data: new Blob(["sample"]),
  }));
  for (let index = 0; index < files.length; index += 5) {
    await client.analyzeFiles(files.slice(index, index + 5));
  }
});

test("rejects empty input, malformed files, unsupported extensions, and too many files", async () => {
  const client = makeClient(async () => {
    throw new Error("fetch should not be called for invalid input");
  });

  await assert.rejects(client.analyze(), TypeError);
  await assert.rejects(client.analyze({ text: 12 }), /text must be a string/);
  await assert.rejects(client.analyzeFiles([{ filename: "bad.exe", data: new Blob(["x"]) }]), /Unsupported file type/);
  await assert.rejects(client.analyzeFiles([{ filename: "no-data.wav" }]), /must be a File or an object containing a Blob/);
  await assert.rejects(client.analyzeFiles(Array(6).fill({ filename: "x.txt", data: new Blob(["x"]) })), /maximum of five files/);
});

test("surfaces authentication and gateway errors with stable metadata", async (context) => {
  const cases = [
    [401, "unauthorized", "A valid X-API-Key header is required."],
    [403, "forbidden", "project_id does not match the API key."],
    [422, "validation_error", "Provide non-empty text or one or more files."],
  ];
  for (const [status, code, message] of cases) {
    await context.test(`${status} ${code}`, async () => {
      const client = makeClient(async () => new Response(JSON.stringify({
        success: false,
        request_id: "request-error",
        error: { code, message },
      }), { status }));

      await assert.rejects(client.analyzeText("feedback"), (error) => {
        assert.ok(error instanceof CognisenseApiError);
        assert.equal(error.status, status);
        assert.equal(error.code, code);
        assert.equal(error.requestId, "request-error");
        assert.equal(error.message, message);
        return true;
      });
    });
  }
});