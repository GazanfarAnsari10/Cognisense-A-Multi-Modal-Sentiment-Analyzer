export function mountCognisenseWidget({
  target,
  endpoint = "/api/cognisense-feedback",
  title = "Share your feedback",
  prompt = "How was your experience?",
  placeholder = "Write your feedback...",
  submitLabel = "Send feedback",
  successMessage = "Thank you for your feedback.",
  errorMessage = "Your feedback could not be sent. Please try again.",
  fetchImpl = globalThis.fetch,
  documentRef = globalThis.document,
  onError,
} = {}) {
  if (!documentRef || typeof documentRef.createElement !== "function") {
    throw new TypeError("A browser document is required to mount the widget.");
  }
  if (typeof fetchImpl !== "function") {
    throw new TypeError("A Fetch API implementation is required.");
  }
  const mountTarget = typeof target === "string" ? documentRef.querySelector(target) : target;
  if (!mountTarget || typeof mountTarget.appendChild !== "function") {
    throw new TypeError("target must be an element or a selector matching an element.");
  }
  if (typeof endpoint !== "string" || !endpoint.trim()) {
    throw new TypeError("endpoint must be a non-empty URL.");
  }

  const origin = documentRef.defaultView?.location?.origin;
  if (origin && new URL(endpoint, origin).origin !== origin) {
    throw new TypeError("The feedback endpoint must be same-origin; use your server-side proxy.");
  }

  const form = documentRef.createElement("form");
  form.className = "cognisense-widget";
  const heading = documentRef.createElement("h2");
  heading.className = "cognisense-widget__title";
  heading.textContent = title;
  const fieldLabel = documentRef.createElement("label");
  fieldLabel.className = "cognisense-widget__label";
  fieldLabel.textContent = prompt;
  const textarea = documentRef.createElement("textarea");
  textarea.className = "cognisense-widget__input";
  textarea.name = "text";
  textarea.placeholder = placeholder;
  textarea.required = true;
  textarea.maxLength = 10_000;
  const submit = documentRef.createElement("button");
  submit.className = "cognisense-widget__submit";
  submit.type = "submit";
  submit.textContent = submitLabel;
  const status = documentRef.createElement("p");
  status.className = "cognisense-widget__status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  fieldLabel.appendChild(textarea);
  form.append(heading, fieldLabel, submit, status);
  mountTarget.appendChild(form);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = textarea.value.trim();
    if (!text) {
      status.textContent = "Please enter feedback before submitting.";
      textarea.focus();
      return;
    }

    submit.disabled = true;
    status.textContent = "Sending...";
    try {
      const response = await fetchImpl(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok || payload?.success !== true) {
        throw new Error(payload?.error?.message || errorMessage);
      }
      textarea.value = "";
      status.textContent = successMessage;
    } catch (error) {
      status.textContent = errorMessage;
      if (typeof onError === "function") onError(error);
    } finally {
      submit.disabled = false;
    }
  });

  return {
    element: form,
    destroy() {
      form.remove();
    },
  };
}