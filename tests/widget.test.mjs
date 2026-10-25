import assert from "node:assert/strict";
import test from "node:test";
import { CognisenseClient } from "../sdk/cognisense-client.mjs";
import { mountCognisenseWidget } from "../sdk/cognisense-widget.mjs";
import { createFeedbackProxy } from "../examples/node-feedback-proxy.mjs";

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.value = "";
    this.textContent = "";
  }

  append(...elements) {
    this.children.push(...elements);
  }

  appendChild(element) {
    this.children.push(element);
    return element;
  }

  addEventListener(name, listener) {
    this.listeners[name] = listener;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  focus() {
    this.focused = true;
  }

  remove() {
    this.removed = true;
  }
}

function fakeDocument(origin) {
  return {
    defaultView: { location: { origin } },
    createElement: (tagName) => new FakeElement(tagName),
  };
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject);
      resolve(`http://127.0.0.1:${server.address().port}`);
    });
  });
}

function close(server) {
  return new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

test("widget posts through a same-origin proxy and returns a gateway result", async (context) => {
  let gatewayRequest;
  let browserRequest;
  const client = new CognisenseClient({
    baseUrl: "https://gateway.example",
    projectId: "project-test",
    apiKey: "server-only-test-secret-long-enough",
    fetchImpl: async (url, options) => {
      gatewayRequest = { url, options };
      return new Response(JSON.stringify({
        success: true,
        request_id: "gateway-request",
        project_id: "project-test",
        data: { status: "complete", results: [{ modality: "text", source: "text", result: { overall: { label: "positive", score: 0.8 } } }] },
      }), { status: 200 });
    },
  });
  const server = createFeedbackProxy({ client });
  const origin = await listen(server);
  context.after(() => close(server));

  const documentRef = fakeDocument(origin);
  const mountTarget = new FakeElement("div");
  const widget = mountCognisenseWidget({
    target: mountTarget,
    endpoint: `${origin}/api/cognisense-feedback`,
    documentRef,
    fetchImpl: async (url, options) => {
      browserRequest = { url, options };
      return fetch(url, options);
    },
  });
  const form = widget.element;
  const textarea = form.children.find((element) => element.tagName === "LABEL").children[0];
  const button = form.children.find((element) => element.tagName === "BUTTON");
  const status = form.children.find((element) => element.tagName === "P");
  textarea.value = "A helpful experience";

  await form.listeners.submit({ preventDefault() {} });

  assert.equal(gatewayRequest.url, "https://gateway.example/api/v1/analyze");
  assert.equal(gatewayRequest.options.headers["X-API-Key"], "server-only-test-secret-long-enough");
  assert.equal(browserRequest.url, `${origin}/api/cognisense-feedback`);
  assert.equal(browserRequest.options.headers["X-API-Key"], undefined);
  assert.deepEqual(JSON.parse(gatewayRequest.options.body), {
    project_id: "project-test",
    text: "A helpful experience",
  });
  assert.equal(status.textContent, "Thank you for your feedback.");
  assert.equal(textarea.value, "");
  assert.equal(button.disabled, false);
  widget.destroy();
  assert.equal(form.removed, true);
});

test("widget requires text and rejects cross-origin endpoints", async () => {
  const documentRef = fakeDocument("https://site.example");
  const target = new FakeElement("div");
  const widget = mountCognisenseWidget({ target, documentRef, fetchImpl: async () => { throw new Error("unexpected request"); } });
  const textarea = widget.element.children.find((element) => element.tagName === "LABEL").children[0];
  const status = widget.element.children.find((element) => element.tagName === "P");

  await widget.element.listeners.submit({ preventDefault() {} });

  assert.equal(status.textContent, "Please enter feedback before submitting.");
  assert.equal(textarea.focused, true);
  assert.throws(
    () => mountCognisenseWidget({ target, endpoint: "https://other.example/collect", documentRef }),
    /same-origin/,
  );
});

test("widget shows a safe failure message when proxy analysis fails", async () => {
  const target = new FakeElement("div");
  const widget = mountCognisenseWidget({
    target,
    documentRef: fakeDocument("https://site.example"),
    fetchImpl: async () => new Response(JSON.stringify({ success: false, error: { message: "private upstream detail" } }), { status: 502 }),
  });
  const textarea = widget.element.children.find((element) => element.tagName === "LABEL").children[0];
  const status = widget.element.children.find((element) => element.tagName === "P");
  textarea.value = "Feedback";

  await widget.element.listeners.submit({ preventDefault() {} });

  assert.equal(status.textContent, "Your feedback could not be sent. Please try again.");
});

test("demo server serves HTML and widget assets from its own origin", async (context) => {
  const server = createFeedbackProxy({
    client: { analyzeText: async () => ({ success: true, data: { results: [] } }) },
  });
  const origin = await listen(server);
  context.after(() => close(server));

  const [home, demo, widget, styles, missing] = await Promise.all([
    fetch(`${origin}/`),
    fetch(`${origin}/widget-demo.html`),
    fetch(`${origin}/sdk/cognisense-widget.mjs`),
    fetch(`${origin}/sdk/cognisense-widget.css`),
    fetch(`${origin}/sdk/../api_gateway.py`),
  ]);
  assert.equal(home.status, 200);
  assert.equal(demo.status, 200);
  assert.match(await demo.text(), /\/api\/cognisense-feedback/);
  assert.equal(widget.status, 200);
  assert.match(widget.headers.get("content-type"), /javascript/);
  assert.equal(styles.status, 200);
  assert.match(styles.headers.get("content-type"), /text\/css/);
  assert.equal(missing.status, 404);
});